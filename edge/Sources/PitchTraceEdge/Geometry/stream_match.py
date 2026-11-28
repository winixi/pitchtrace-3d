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

    def __init__(self, path, hold=None):
        self.path = path
        # 暂停时分析线程在帧边界等待；跟随线程照常登记新行，积压留在磁盘。
        self.hold = hold or (lambda: None)
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
                self.hold()
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
    """索引原子落盘后登记段；三件均获 202 才删除该目标的待办。

    待办按登记先后发送，各机位交替推进。同一网关、机位和周期的 init.mp4 确认一次后不再重传。
    bytes_per_second 大于零时按令牌桶限速，计入全部上传字节。
    """

    def __init__(self, db, bytes_per_second=0):
        self.db = db
        self.rate = bytes_per_second
        self.ready_at = time.monotonic()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS stream_media(
                path TEXT PRIMARY KEY, index_sha TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS stream_gateways(gateway TEXT PRIMARY KEY) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS stream_pending(
                gateway TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY(gateway,path)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS stream_media_order(
                path TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS stream_init_receipts(
                gateway TEXT NOT NULL, camera TEXT NOT NULL, epoch TEXT NOT NULL, sha TEXT NOT NULL,
                PRIMARY KEY(gateway,camera,epoch)
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
                self.db.execute("""INSERT INTO stream_media_order
                                   SELECT ?, COALESCE(MAX(seq), 0) + 1 FROM stream_media_order""", (key,))
            elif self.db.execute("SELECT index_sha FROM stream_media WHERE path=?", (key,)).fetchone()[0] != sha:
                raise ValueError(f"直播段索引与已登记版本冲突：{key}")

    def pace(self, size):
        """按目标速率推迟下一次请求；单次突发不超过一个请求的字节数。"""
        if not self.rate:
            return
        now = time.monotonic()
        if self.ready_at > now:
            time.sleep(self.ready_at - now)
            now = self.ready_at
        self.ready_at = now + size / self.rate

    def send_one(self, gateway, match, media):
        # 旧运行目录中先登记、未记顺序的段排在最前。
        row = self.db.execute("""SELECT p.path,m.index_sha FROM stream_pending p JOIN stream_media m USING(path)
                                  LEFT JOIN stream_media_order o USING(path)
                                  WHERE p.gateway=? ORDER BY COALESCE(o.seq, 0), p.path LIMIT 1""",
                              (gateway,)).fetchone()
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
        receipt = self.db.execute("SELECT sha FROM stream_init_receipts WHERE gateway=? AND camera=? AND epoch=?",
                                  (gateway, camera, epoch)).fetchone()
        if receipt is not None and receipt[0] != meta["initSha256"]:
            raise ValueError(f"{camera}/{epoch} 的 init.mp4 与已确认版本不同")
        parts = [(index.with_suffix(".m4s"), f"/segments/{number}.m4s", meta["segmentSha256"]),
                 (index, f"/segments/{number}/index", None)]
        if receipt is None:
            parts.insert(0, (index.parent.parent / "init.mp4", "/init.mp4", meta["initSha256"]))
        url = urlsplit(gateway)
        host_type = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        with closing(host_type(url.hostname, url.port, timeout=15)) as connection:
            for local, endpoint, sha in parts:
                size = local.stat().st_size
                if sha and (not 0 < size <= 128 * 1024 * 1024 or digest(local) != sha):
                    raise ValueError(f"原片与曝光索引不一致：{local}")
                self.pace(size)
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
                if endpoint == "/init.mp4":
                    # 网关对同一周期的初始化文件幂等；确认后本地记住，不随每段重传。
                    with self.db:
                        self.db.execute("INSERT OR IGNORE INTO stream_init_receipts VALUES(?,?,?,?)",
                                        (gateway, camera, epoch, sha))
        with self.db:
            self.db.execute("DELETE FROM stream_pending WHERE gateway=? AND path=?", (gateway, relative))
        return 1


def upload_loop(name, send, finished, hold=None):
    delay = .5
    last_error = None
    while True:
        if hold:
            hold()
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
    media_rate = settings.get("mediaBytesPerSecond", 0)
    if type(media_rate) is not int or media_rate < 0:
        raise ValueError("mediaBytesPerSecond 必须为非负整数")
    wait = settings.get("streamWaitSeconds", 2)
    if type(wait) not in (int, float) or not math.isfinite(wait) or wait < 0:
        raise ValueError("streamWaitSeconds 必须为非负秒数")

    run.mkdir(parents=True, exist_ok=True)
    db, media = run / "scenes.sqlite", run / "media"
    pause_marker = run / "PAUSE"
    paused_since = {}

    def hold():
        """存在 PAUSE 标记时暂停推理和上行，录制与原片跟随不受影响。删除标记即恢复。"""
        while pause_marker.exists():
            paused_since.setdefault("unix", time.time())
            time.sleep(.25)
        paused_since.clear()
    with lock(run / ".stream-match.lock"):
        marker = run / "stream-id.json"
        identity = {"matchId": config.match_id, "streamEpoch": config.stream_epoch,
                    "captureRoot": str(capture), "captureConfigSha256": digest(capture_file)}
        if marker.exists():
            if read_object(marker) != identity:
                raise ValueError("直播运行目录已绑定另一输入或比赛周期")
        else:
            # 允许事先放置 PAUSE，以暂停状态开始。
            if any(p.name not in (".stream-match.lock", "PAUSE") for p in run.iterdir()):
                raise ValueError("首次直播运行需要新的或空的输出目录")
            write_json(marker, identity)
        processing = {k: v for k, v in settings.items() if k not in ("gateway", "backfillBytesPerSecond", "mediaBytesPerSecond")}
        versions, model = inference_inputs(processing, path.parent, Path(executable))
        from observations import Degradation
        from vision_model import validate_degradation
        shedding = validate_degradation(settings.get("detector", {}))
        degradation = Degradation(shedding) if shedding is not None else None
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
            journal = JournalReader(media / "frames.ndjson", hold)

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
                        queue = MediaQueue(local.db, media_rate)
                        def send():
                            return queue.send_one(gateway, config.match_id, media)
                    upload_loop(which, send, finished, hold)

            started = time.monotonic()
            state = {**identity, "status": "running", "frames": 0, "lastSceneUtcUs": None}
            status_path = run / "stream-state.json"
            scene_key = urlsplit(gateway).geturl().rstrip("/") if gateway else None
            def pending_uploads():
                # 归档为 WAL 模式，状态线程用只读连接计数，不与提交线程争用写锁。
                with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)) as reader:
                    scenes = reader.execute("""SELECT COUNT(*) FROM edge_scene_pending
                                               WHERE gateway=? AND match_id=? AND epoch=?""",
                                            (scene_key, config.match_id, config.stream_epoch)).fetchone()[0]
                    media_parts = reader.execute("SELECT COUNT(*) FROM stream_pending WHERE gateway=?",
                                                 (gateway,)).fetchone()[0]
                return {"scenes": scenes, "mediaSegments": media_parts}

            def save_status():
                if degradation is not None:
                    state["degradation"] = degradation.snapshot()
                state["paused"] = pause_marker.exists()
                state["pausedSinceUnix"] = paused_since.get("unix") if state["paused"] else None
                snapshot = {**state, **journal.metrics(), "updatedUnix": time.time(),
                            "processedFramesPerSecond": state["frames"] / max(.001, time.monotonic() - started)}
                if gateway:
                    try:
                        snapshot["pendingUploads"] = pending_uploads()
                    except sqlite3.Error:
                        snapshot["pendingUploads"] = None
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
                                                  model, executable, resume=True, degradation=degradation):
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
