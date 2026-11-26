"""跟随各路已封口片段，在曝光映射齐备后提交媒体并有界归并直播输入。"""

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from capture import (bounded_media, check_header, identifier, load_config, lock, mapping_row,
                     read_json, validated_segment, write_json)


class CommittedConflict(RuntimeError):
    """已发布的本地媒体出现冲突，必须停止派发。"""


class NeedMapping(Exception):
    """曝光记录尚未写到该帧。"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save_blob(path, data, expected):
    if path.exists():
        if digest(bounded_media(path)) != expected:
            raise CommittedConflict(f"已提交媒体冲突：{path}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".media-", delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class Period:
    def __init__(self, camera, config, raw, output, match):
        self.camera = camera
        self.raw = raw
        self.epoch = identifier(raw.name, "周期")
        self.match = match
        self.mapping = (config["exposures"] if self.epoch == config.get("mediaEpoch") and "exposures" in config
                        else raw / "exposures.ndjson")
        self.output = output / camera / "epochs" / self.epoch
        self.number = self.offset = 0
        self.timing = (None, None, None)
        self.first = None
        self.finished_audit = False
        self.header = self.error = None
        self.waiting = False
        self.retry_stamp = None

    def accept(self, base, frames, numbers, offset, timing):
        reference = {"cameraId": self.camera, "mediaEpoch": self.epoch, "segment": self.number, "timeBase": base}
        rows = [{**reference, **frame} for frame in frames]
        for row, number in zip(rows, numbers):
            if number is not None:
                row["frameNumber"] = number
        if self.first is None:
            self.first = int(frames[0]["captureUtcUs"])
        self.offset, self.timing = offset, timing
        self.number += 1
        return rows

    def next_segment(self, final, peers):
        self.waiting = False
        segments = self.output / "segments"
        index_path = segments / f"{self.number}.json"
        cursor_path = self.output / "cursors" / f"{self.number}.json"
        if self.error:
            return []
        if index_path.exists():
            try:
                # 发布索引即完成提交。进程可能在索引改名之后、写日志或入上传队列之前终止。
                # 从提交件恢复直播输入；此时可不再依赖原映射文件或 FFmpeg 解码。
                index, cursor = read_json(index_path), read_json(cursor_path)
                self.header = read_json(self.output / "clock-source.json")
                check_header(self.header, self.match, self.camera, self.epoch)
                part = segments / f"{self.number}.m4s"
                if (digest(bounded_media(self.output / "init.mp4")) != index["initSha256"]
                        or digest(bounded_media(part)) != index["segmentSha256"]):
                    raise CommittedConflict(f"已提交媒体摘要不匹配：{part}")
                frames = index["frames"]
                numbers = cursor.get("frameNumbers", [])
                if numbers and len(numbers) != len(frames):
                    raise CommittedConflict(f"已提交原始帧号与索引不匹配：{part}")
                last = frames[-1]
                timing = int(last["captureUtcUs"]), last["pts"] + last["durationPts"], cursor["lastFrameNumber"]
                return self.accept(index["timeBase"], frames, numbers, cursor["offset"], timing)
            except (ValueError, KeyError, TypeError, IndexError) as error:
                raise CommittedConflict(f"已提交段的元数据损坏：{index_path}") from error
        part = self.raw / f"{self.number}.m4s"
        if not part.exists():
            if final and not self.finished_audit:
                self.finished_audit = True
                if any(int(path.stem) > self.number for path in self.raw.glob("*.m4s")):
                    raise ValueError(f"机位 {self.camera} 缺少已封口的片段 {self.number}")
                if self.header and self.mapping.is_file() and self.mapping.stat().st_size > self.offset:
                    raise ValueError("采集已结束，曝光清单仍有未匹配的记录")
            return []
        if not self.mapping.is_file():
            self.waiting = True
            return []
        stat = self.mapping.stat()
        stamp = (stat.st_size, stat.st_mtime_ns, final)
        if stamp == self.retry_stamp:
            self.waiting = True
            return []
        with self.mapping.open("rb") as file:

            def row():
                data = mapping_row(file, growing=not final)
                if data is None:
                    raise NeedMapping()
                return data

            try:
                header = row()
                check_header(header, self.match, self.camera, self.epoch)
                if self.offset:
                    if header != self.header or stat.st_size < self.offset:
                        raise ValueError("进行中的曝光清单被替换或截短")
                    file.seek(self.offset)
                self.header = header
                init = bounded_media(self.raw / "init.mp4")
                entries, frames, timing = validated_segment(init, part, self.number, self.header, row, self.timing)
            except NeedMapping:
                self.waiting = True
                self.retry_stamp = stamp
                return []
            offset = file.tell()

        first = int(frames[0]["captureUtcUs"]) if self.first is None else self.first
        for peer in peers:
            if peer is not self and peer.first is not None and first <= peer.timing[0] and peer.first <= timing[0]:
                raise ValueError("同机位跨周期曝光范围重叠")
        base = dict(zip(("numerator", "denominator"), map(int, self.header["timeBase"].split("/"))))
        segments.mkdir(parents=True, exist_ok=True)
        source = self.output / "clock-source.json"
        if source.exists():
            if read_json(source) != self.header:
                raise CommittedConflict("已提交周期的曝光时钟来源不符")
        else:
            write_json(source, self.header)
        segment = bounded_media(part)
        index = {"initSha256": digest(init), "segmentSha256": digest(segment), "segmentBytes": len(segment),
                 "timeBase": base, "frames": frames}
        save_blob(self.output / "init.mp4", init, index["initSha256"])
        save_blob(segments / part.name, segment, index["segmentSha256"])
        # 索引最后发布。读取者看到索引后即可打开对应的初始化和媒体段。
        numbers = [entry.get("frameNumber") for entry in entries]
        cursor = {"offset": offset, "lastFrameNumber": timing[2]}
        if any(number is not None for number in numbers):
            cursor["frameNumbers"] = numbers
        cursor_path.parent.mkdir(exist_ok=True)
        write_json(cursor_path, cursor)
        write_json(index_path, index)
        return self.accept(base, frames, numbers, offset, timing)



def key(row):
    return int(row["captureUtcUs"]), row["cameraId"]


def follow_rows(capture_config, capture_root, media_root, done, *, wait_seconds=2.0, poll_seconds=0.1, on_segment=None):
    """done() 由采集进程退出状态驱动。先重播已确认直播行，再跟随封口段直到排空。"""
    capture_root, media_root = Path(capture_root), Path(media_root)
    config = load_config(Path(capture_config))
    if wait_seconds < 0 or poll_seconds <= 0:
        raise ValueError("等待时间须非负，轮询间隔须为正")
    names = sorted(config["cameras"])
    match = config["matchId"]
    media_root.mkdir(parents=True, exist_ok=True)
    with lock(media_root / ".stream.lock"):
        marker = media_root / ".match.json"
        if marker.exists() and read_json(marker) != {"matchId": match}:
            raise ValueError("直播媒体目录属于另一场比赛")
        write_json(marker, {"matchId": match})
        with (media_root / "frames.ndjson").open("a+b") as journal:
            journal.seek(0)
            last = None
            while True:
                pos = journal.tell()
                line = journal.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    journal.truncate(pos)
                    break
                row = json.loads(line)
                current = key(row)
                if last is not None and current <= last:
                    raise ValueError("直播原帧日志顺序倒退")
                last = current
                yield row

            periods = {name: {} for name in names}
            buffers = {name: deque() for name in names}
            next_scan = {name: 0 for name in names}
            missing_since = {}
            late = 0
            checkpoint = last
            last_health = 0
            while True:
                final = done()
                now = time.monotonic()
                progressed = False
                scanned_history = False
                for name in names:
                    if buffers[name]:
                        continue
                    missing_since.setdefault(name, now)
                    if not final and now < next_scan[name]:
                        continue
                    next_scan[name] = now + poll_seconds
                    raw_camera = capture_root / "raw" / name
                    if raw_camera.exists() and read_json(capture_root / "match.json").get("matchId") != match:
                        raise ValueError("采集目录的比赛与直播配置不符")
                    discovered = []
                    saved = media_root / name / "epochs"
                    candidates = set(path.name for path in raw_camera.iterdir()) if raw_camera.exists() else set()
                    if saved.is_dir():
                        candidates.update(path.name for path in saved.iterdir() if (path / "segments/0.json").exists())
                    for epoch in candidates - periods[name].keys():
                        raw = raw_camera / epoch
                        meta_file = raw / "status.json"
                        first_index = saved / epoch / "segments/0.json"
                        if meta_file.exists():
                            meta = read_json(meta_file)
                            if (meta.get("matchId"), meta.get("cameraId"), meta.get("mediaEpoch")) != (match, name, epoch):
                                raise ValueError("原片机位、周期或比赛标识不符")
                        elif not first_index.exists():
                            continue
                        # 已提交周期优先按实际首帧曝光恢复；新周期按启动时刻。
                        order = ((0, int(read_json(first_index)["frames"][0]["captureUtcUs"])) if first_index.exists()
                                 else (1, meta.get("startedUnix", raw.stat().st_mtime)))
                        discovered.append((order, epoch, raw))
                    for _, _, raw in sorted(discovered):
                        periods[name][raw.name] = Period(name, config["cameras"][name], raw, media_root, match)
                    # 已开始的周期在前；缺少其曝光映射时仍允许探测其他周期。
                    for period in periods[name].values():
                        try:
                            rows = period.next_segment(final, periods[name].values())
                        except (ValueError, KeyError) as error:
                            # 只隔离无效输入。磁盘满、权限或提交件故障必须向上传递，供安全重试。
                            period.error = str(error)
                            continue
                        if rows:
                            progressed = True
                            number = rows[0]["segment"]
                            if on_segment:
                                on_segment(name, period.epoch, number, period.output / "segments" / f"{number}.json")
                            for row in rows:
                                current = key(row)
                                if last is None or current > last:
                                    buffers[name].append(row)
                                elif checkpoint is None or current > checkpoint:
                                    late += 1
                            next_scan[name] = 0
                            if buffers[name]:
                                missing_since.pop(name, None)
                            else:
                                scanned_history = True
                            break

                ready = [name for name in names if buffers[name]]
                exhausted = not ready and final and not progressed
                if exhausted or now - last_health >= 1:
                    issues = [{"cameraId": name, "mediaEpoch": p.epoch, "segment": p.number,
                               "reason": p.error or "等待逐帧曝光映射"}
                              for name in names for p in periods[name].values() if p.error or p.waiting]
                    write_json(media_root / "stream-health.json",
                               {"matchId": match, "lateFrames": late, "issues": issues,
                                "updatedUnix": time.time()})
                    last_health = now

                if scanned_history:
                    continue
                if ready and (final or all(now - start >= wait_seconds for start in missing_since.values())):
                    name = min(ready, key=lambda n: key(buffers[n][0]))
                    row = buffers[name].popleft()
                    last = key(row)
                    journal.write(json.dumps(row, separators=(",", ":")).encode() + b"\n")
                    journal.flush()
                    os.fsync(journal.fileno())
                    yield row
                elif exhausted:
                    return
                elif not progressed:
                    time.sleep(poll_seconds)
