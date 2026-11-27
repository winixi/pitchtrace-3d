"""在采集过程中读取已封口的媒体段，独立调度原片与场景补传。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import fcntl
import http.client
import json
import math
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import cv2
import numpy as np

from capture import load_config, lock, write_json
from match_config import MatchConfig, read_object
from process_match import analyze_rows
from run_match import inference_inputs
from vision_model import digest
from scene_archive import SceneArchive, ScenePublishError
from stream_capture import follow_rows


def capture_done(root):
    # 采集器持有排它锁直至全部机位结束。进程意外退出也会释放该锁。
    with (root / ".capture.lock").open("rb") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


class JournalReader:
    """采集跟随线程写磁盘日志，分析线程按已发布行数消费，积压只占磁盘。"""

    def __init__(self, path):
        self.path = path
        self.ready = threading.Condition()
        self.count = 0
        self.consumed = 0
        self.offset = 0
        self.latest_utc = None
        self.ended = False
        self.error = None

    def consume(self, source, stop):
        try:
            with closing(source) as rows:
                for row in rows:
                    with self.ready:
                        self.count += 1
                        self.latest_utc = int(row["captureUtcUs"])
                        self.ready.notify()
                    if stop.is_set():
                        break
        except Exception as error:
            self.error = error
        finally:
            with self.ready:
                self.ended = True
                self.ready.notify()

    def rows(self):
        offset = 0
        with self.ready:
            self.ready.wait_for(lambda: self.count or self.ended)
        if not self.count:
            if self.error:
                raise self.error
            return
        with self.path.open("rb") as journal:
            while True:
                with self.ready:
                    self.ready.wait_for(lambda: offset < self.count or self.ended)
                    if offset == self.count:
                        if self.error:
                            raise self.error
                        return
                line = journal.readline()
                if not line.endswith(b"\n"):
                    raise ValueError("已提交直播原帧行不完整")
                offset += 1
                with self.ready:
                    self.consumed = offset
                    self.offset = journal.tell()
                yield json.loads(line)

    def metrics(self):
        with self.ready:
            queued = self.count - self.consumed
            offset, latest = self.offset, self.latest_utc
            result = {"committedRows": self.count, "consumedRows": self.consumed,
                      "backlogRows": queued, "backlogExposureSeconds": 0.0}
        if queued:
            with self.path.open("rb") as file:
                file.seek(offset)
                first = int(json.loads(file.readline())["captureUtcUs"])
            result["backlogExposureSeconds"] = (latest - first) / 1e6
        return result


class MediaQueue:
    """索引原子落盘后登记段；三件均获 202 才删除该目标的待办。"""

    def __init__(self, db):
        self.db = db
        db.executescript("""
            CREATE TABLE IF NOT EXISTS stream_media(
                path TEXT PRIMARY KEY, index_sha TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS stream_gateways(gateway TEXT PRIMARY KEY) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS stream_pending(
                gateway TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY(gateway,path)
            ) WITHOUT ROWID;
        """)

    def register(self, gateway):
        with self.db:
            if self.db.execute("INSERT OR IGNORE INTO stream_gateways VALUES(?)", (gateway,)).rowcount:
                self.db.execute("INSERT INTO stream_pending SELECT ?,path FROM stream_media", (gateway,))

    def add(self, key, index_path):
        sha = digest(index_path)
        with self.db:
            if self.db.execute("INSERT OR IGNORE INTO stream_media VALUES(?,?)", (key, sha)).rowcount:
                self.db.execute("INSERT INTO stream_pending SELECT gateway,? FROM stream_gateways", (key,))
            elif self.db.execute("SELECT index_sha FROM stream_media WHERE path=?", (key,)).fetchone()[0] != sha:
                raise ValueError(f"直播段索引与已登记版本冲突：{key}")

    def send_one(self, gateway, match, media):
        row = self.db.execute("""SELECT p.path,m.index_sha FROM stream_pending p JOIN stream_media m USING(path)
                                  WHERE p.gateway=? ORDER BY p.path LIMIT 1""", (gateway,)).fetchone()
        if not row:
            return 0
        relative, expected = row
        index = media / relative
        if digest(index) != expected:
            raise ValueError(f"原片索引摘要已改变：{index}")
        camera, _, epoch, _, filename = Path(relative).parts
        number = Path(filename).stem
        meta = read_object(index)
        path = f"/v2/matches/{match}/cameras/{camera}/epochs/{epoch}"
        url = urlsplit(gateway)
        host_type = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        with closing(host_type(url.hostname, url.port, timeout=15)) as connection:
            for local, endpoint, sha in (
                (index.parent.parent / "init.mp4", "/init.mp4", meta["initSha256"]),
                (index.with_suffix(".m4s"), f"/segments/{number}.m4s", meta["segmentSha256"]),
                (index, f"/segments/{number}/index", None),
            ):
                size = local.stat().st_size
                if sha and (not 0 < size <= 128 * 1024 * 1024 or digest(local) != sha):
                    raise ValueError(f"原片与曝光索引不一致：{local}")
                headers = {"Content-Length": str(size), "Content-Type": "application/json"}
                if sha:
                    headers.update({"X-Content-SHA256": sha, "Content-Type": "video/mp4"})
                with local.open("rb") as stream:
                    connection.request("PUT", url.path.rstrip("/") + path + endpoint, stream, headers)
                    response = connection.getresponse()
                    detail = response.read(1024).decode(errors="replace")
                    if response.status != 202:
                        raise ScenePublishError(f"原片 {path}{endpoint} 返回 HTTP {response.status} {detail}",
                                                status=response.status)
        with self.db:
            self.db.execute("DELETE FROM stream_pending WHERE gateway=? AND path=?", (gateway, relative))
        return 1


def upload_loop(name, send, finished):
    delay = .5
    last_error = None
    while True:
        # 提前读取状态，确保退出前至少检查一次生产者的最终提交。
        final = finished.is_set()
        try:
            count = send()
            if last_error:
                print(f"{name}补传已恢复", file=sys.stderr, flush=True)
            last_error = None
            delay = .5
            if count:
                continue
            if final:
                return
            finished.wait(.25)
        except (ScenePublishError, OSError, http.client.HTTPException) as error:
            if str(error) != last_error:
                print(f"{name}待补传：{error}", file=sys.stderr, flush=True)
            last_error = str(error)
            status = getattr(error, "status", None)
            if final or (status is not None and 400 <= status < 500 and status not in (408, 429)):
                raise
            finished.wait(delay)
            delay = min(15, delay * 2)


def stream_match(path, capture, run, executable):
    config, settings = MatchConfig.load(path), read_object(path)
    if not isinstance(settings.get("captureConfig"), str) or not settings["captureConfig"]:
        raise ValueError("stream-match 需配置 captureConfig 路径")
    capture_file = (path.parent / settings["captureConfig"]).resolve()
    source = load_config(capture_file)
    if source["matchId"] != config.match_id or read_object(capture / "match.json").get("matchId") != config.match_id:
        raise ValueError("采集配置、采集目录与比赛配置须属于同一比赛")
    if run == capture or run in capture.parents or capture in run.parents:
        raise ValueError("直播运行目录与采集目录不能相互包含")
    gateway = settings.get("gateway")
    if gateway is not None:
        if not isinstance(gateway, str):
            raise ValueError("gateway 必须为 HTTP(S) 地址")
        url = urlsplit(gateway)
        if url.scheme not in ("http", "https") or not url.hostname or url.username is not None or url.query or url.fragment:
            raise ValueError("gateway 需要不含帐号、查询或片段的 HTTP(S) 网关基址")
        _ = url.port
        gateway = gateway.rstrip("/")
    rate = settings.get("backfillBytesPerSecond", 0)
    if type(rate) is not int or rate < 0:
        raise ValueError("backfillBytesPerSecond 必须为非负整数")
    wait = settings.get("streamWaitSeconds", 2)
    if type(wait) not in (int, float) or not math.isfinite(wait) or wait < 0:
        raise ValueError("streamWaitSeconds 必须为非负秒数")

    run.mkdir(parents=True, exist_ok=True)
    db, media = run / "scenes.sqlite", run / "media"
    with lock(run / ".stream-match.lock"):
        marker = run / "stream-id.json"
        identity = {"matchId": config.match_id, "streamEpoch": config.stream_epoch,
                    "captureRoot": str(capture), "captureConfigSha256": digest(capture_file)}
        if marker.exists():
            if read_object(marker) != identity:
                raise ValueError("直播运行目录已绑定另一输入或比赛周期")
        else:
            if any(p.name != ".stream-match.lock" for p in run.iterdir()):
                raise ValueError("首次直播运行需要新的或空的输出目录")
            write_json(marker, identity)
        processing = {k: v for k, v in settings.items() if k not in ("gateway", "backfillBytesPerSecond")}
        versions, model = inference_inputs(processing, path.parent, Path(executable))
        provenance = dict(identity, command="stream-match", sampleSource=config.sample_source,
                          configuration=processing, versions=versions,
                          python=platform.python_version(), opencv=cv2.__version__, numpy=np.__version__)
        if model is not None:
            provenance.update(model.provenance)
        with SceneArchive(db) as archive:
            archive.register_run(config.match_id, config.stream_epoch, provenance)
            media_queue = MediaQueue(archive.db)
            if gateway:
                media_queue.register(gateway)

            stop, finished = threading.Event(), threading.Event()
            journal = JournalReader(media / "frames.ndjson")

            def ingest():
                with SceneArchive(db) as local:
                    queue = MediaQueue(local.db)
                    def record(camera, epoch, number, index):
                        queue.add(str(index.relative_to(media)), index)
                    rows = follow_rows(capture_file, capture, media, lambda: stop.is_set() or capture_done(capture),
                                       wait_seconds=wait, on_segment=record)
                    yield from rows

            def publish(which):
                with SceneArchive(db) as local:
                    if which == "场景":
                        def send():
                            return local.publish(gateway, config.match_id, config.stream_epoch, rate, max_frames=16)
                    else:
                        queue = MediaQueue(local.db)
                        def send():
                            return queue.send_one(gateway, config.match_id, media)
                    upload_loop(which, send, finished)

            started = time.monotonic()
            state = {**identity, "status": "running", "frames": 0, "lastSceneUtcUs": None}
            status_path = run / "stream-state.json"
            def save_status():
                snapshot = {**state, **journal.metrics(), "updatedUnix": time.time(),
                            "processedFramesPerSecond": state["frames"] / max(.001, time.monotonic() - started)}
                write_json(status_path, snapshot)
                return snapshot

            def monitor():
                while not finished.wait(.5):
                    save_status()

            save_status()
            try:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    status = pool.submit(monitor)
                    producer = pool.submit(journal.consume, ingest(), stop)
                    uploads = {name: pool.submit(publish, name) for name in ("场景", "原片")} if gateway else {}
                    try:
                        for frame in analyze_rows(config, journal.rows(), archive, media, settings.get("detector", {}),
                                                  model, executable, resume=True):
                            state.update(frames=frame["seq"] + 1, lastSceneUtcUs=frame["captureUtcUs"])
                        producer.result()
                    finally:
                        stop.set()
                        finished.set()
                    status.result()
                    errors = {}
                    for name, future in uploads.items():
                        try:
                            future.result()
                        except Exception as error:
                            errors[name] = str(error)
                    if errors:
                        state["pendingDelivery"] = errors
                        raise ValueError("仍有待提交的" + "、".join(errors))
                health = read_object(media / "stream-health.json")
                if not state["frames"] or health["issues"]:
                    raise ValueError("采集曝光数据缺失或无效，请核对 media/stream-health.json")
                state["status"] = "completed"
            except BaseException as error:
                state.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed", error=str(error))
                raise
            finally:
                snapshot = save_status()
            return snapshot


if __name__ == "__main__":
    try:
        config, capture, run, executable = sys.argv[1:]
        print(json.dumps(stream_match(Path(config).resolve(), Path(capture).resolve(), Path(run).resolve(), executable),
                         ensure_ascii=False))
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(f"直播重建失败：{error}")
