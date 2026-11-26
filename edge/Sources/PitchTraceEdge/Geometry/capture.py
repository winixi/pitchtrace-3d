"""独立录制各机位，核对设备的逐帧曝光映射后提交可重放的媒体周期。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from calibrate import is_identifier, unique_object

SAFE_INT = 2**53 - 1
MAX_MEDIA = 128 * 1024 * 1024


def read_json(path):
    with path.open(encoding="utf-8") as file:
        return json.load(file, object_pairs_hook=unique_object)


def write_json(path, data):
    # 同一文件系统的临时文件保证场务读取时总能看到完整状态。
    temporary = path.with_suffix(".tmp-" + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def lock(path, wait=False):
    with path.open("a+b") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as error:
            raise ValueError(f"目录正在使用：{path.parent}") from error
        yield file


def identifier(value, label):
    if not is_identifier(value):
        raise ValueError(f"{label} 需要有效的 v2 标识")
    return value


def integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} 须为 {minimum} 至 {maximum} 的整数")
    return value


def raw_period(root, camera, epoch):
    return root / "raw" / identifier(camera, "机位") / identifier(epoch, "媒体周期")


def load_config(path):
    config = read_json(path)
    if not isinstance(config, dict):
        raise ValueError("采集配置需要 JSON 对象")
    identifier(config.get("matchId"), "比赛")
    cameras = config.get("cameras")
    if not isinstance(cameras, dict) or not 1 <= len(cameras) <= 4:
        raise ValueError("需要 1 至 4 个机位")
    if len({name.lower() for name in cameras}) != len(cameras):
        raise ValueError("机位目录名不能仅大小写不同")
    integer(config.get("minFreeBytes", 0), 0, SAFE_INT, "磁盘保留字节")
    integer(config.get("segmentSeconds", 2), 1, 60, "目标段长")
    for name, camera in cameras.items():
        identifier(name, "机位")
        if not isinstance(camera, dict) or camera.get("type") not in ("file", "rtsp", "avfoundation"):
            raise ValueError(f"机位 {name} 的输入类型无效")
        source = camera.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"机位 {name} 缺少输入地址")
        if camera["type"] == "file":
            camera["source"] = str((path.parent / source).resolve())
        if camera["type"] == "rtsp" and not source.startswith(("rtsp://", "rtsps://")):
            raise ValueError(f"机位 {name} 的 RTSP 地址无效")
        if camera.get("videoMode", "encode") not in ("encode", "copy"):
            raise ValueError(f"机位 {name} 的 videoMode 无效")
        if "mediaEpoch" in camera:
            identifier(camera["mediaEpoch"], "媒体周期")
        if "exposures" in camera:
            if not isinstance(camera["exposures"], str) or not camera["exposures"] or "mediaEpoch" not in camera:
                raise ValueError("预先提供曝光清单时需指定清单路径和 mediaEpoch")
            camera["exposures"] = (path.parent / camera["exposures"]).resolve()
        integer(camera.get("reconnectAttempts", 3), 0, 100, "重连次数")
        if camera["type"] == "avfoundation":
            integer(camera.get("framerate", 30), 1, 240, "采集帧率")
            if "videoSize" in camera and not re.fullmatch(r"[1-9]\d{0,4}x[1-9]\d{0,4}", str(camera["videoSize"])):
                raise ValueError("videoSize 需要 宽x高")
    return config


def capture_command(camera, segment_seconds):
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror"]
    if camera["type"] == "rtsp":
        args += ["-rtsp_transport", "tcp", "-timeout", "5000000"]
    if camera["type"] == "avfoundation":
        args += ["-f", "avfoundation", "-framerate", str(camera.get("framerate", 30))]
        if "videoSize" in camera:
            args += ["-video_size", camera["videoSize"]]
    args += ["-i", camera["source"], "-map", "0:v:0"]
    if camera.get("videoMode") == "copy":
        # 这个过滤器也会拒绝非 H.264 输入，避免产生 replay 不支持的分段。
        args += ["-c:v", "copy", "-bsf:v", "h264_metadata"]
    else:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-bf", "0",
                 "-fps_mode", "passthrough", "-force_key_frames", f"expr:gte(t,n_forced*{segment_seconds})"]
    return args + ["-f", "hls", "-hls_time", str(segment_seconds), "-hls_list_size", "0",
                   "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
                   "-hls_flags", "independent_segments+temp_file", "-hls_segment_filename", "%d.m4s", "preview.m3u8"]


def bounded_media(path):
    if not 0 < path.stat().st_size <= MAX_MEDIA:
        raise ValueError(f"{path.name} 的大小超出 v2 范围")
    return path.read_bytes()


def probe(init, part):
    payload = init + bounded_media(part)
    result = subprocess.run(["ffprobe", "-v", "error", "-f", "mov", "-select_streams", "v:0",
                             "-show_frames", "-show_streams", "-show_entries",
                             "stream=codec_name,time_base:frame=key_frame,pts,duration,pkt_duration",
                             "-of", "json", "pipe:0"], input=payload, capture_output=True)
    if result.returncode or result.stderr:
        raise ValueError(f"{part.name} 无法完整解码")
    video = json.loads(result.stdout)
    streams, frames = video["streams"], video.get("frames", [])
    if len(streams) != 1 or streams[0].get("codec_name") != "h264" or not 1 <= len(frames) <= 4096:
        raise ValueError(f"{part.name} 的编码或帧数超出 v2 范围")
    frames.sort(key=lambda row: row["pts"])
    if frames[0].get("key_frame") != 1:
        raise ValueError(f"{part.name} 首个展示帧需要关键帧")
    return streams[0]["time_base"], frames


def mapping_row(file, growing=False):
    offset = file.tell()
    line = file.readline(65537)
    if len(line) > 65536:
        raise ValueError("曝光清单行超过 64 KiB")
    newline = b"\n" if isinstance(line, bytes) else "\n"
    if growing and line and not line.endswith(newline):
        file.seek(offset)
        return None
    if not line:
        return None
    row = json.loads(line, object_pairs_hook=unique_object)
    if not isinstance(row, dict):
        raise ValueError("曝光清单行需要 JSON 对象")
    return row


def check_header(header, match, camera, epoch):
    if not header or (header.get("matchId"), header.get("cameraId"), header.get("mediaEpoch")) != (match, camera, epoch):
        raise ValueError("曝光清单的比赛、机位或周期不匹配")
    clock = header.get("clockSource")
    if not isinstance(clock, str) or not clock.strip():
        raise ValueError("曝光清单需说明设备曝光时钟来源")
    return clock


def validated_segment(init, part, number, header, read_row, previous):
    previous_utc, previous_end, previous_frame = previous
    base, frames = probe(init, part)
    if base != header.get("timeBase"):
        raise ValueError(f"片段 {number} 时基不匹配，应为 {base}")
    num, den = map(int, base.split("/"))
    entries = []
    media_frames = []
    for frame in frames:
        pts = integer(frame["pts"], -SAFE_INT, SAFE_INT, "PTS")
        duration = integer(frame.get("duration", frame.get("pkt_duration")), 1, SAFE_INT, "展示时长")
        if (previous_end is not None and pts < previous_end or pts + duration > SAFE_INT
                or not den <= duration * num * 1_000_000 <= 1_000_000 * den):
            raise ValueError(f"片段 {number} 的展示区间无效")
        previous_end = pts + duration
        row = read_row()
        if (row is None or type(row.get("pts")) is not int or type(row.get("segment")) is not int
                or row["segment"] != number or row["pts"] != pts):
            raise ValueError(f"映射需按展示顺序匹配片段 {number}、PTS {pts}")
        utc = row.get("captureUtcUs")
        if not isinstance(utc, str) or not re.fullmatch(r"0|[1-9][0-9]{0,18}", utc) or int(utc) >= 2**63:
            raise ValueError("曝光 UTC 需要可表示的非负十进制微秒字符串")
        if previous_utc is not None and int(utc) <= previous_utc:
            raise ValueError("曝光 UTC 未严格递增")
        previous_utc = int(utc)
        entry = {"pts": pts, "captureUtcUs": utc}
        if "frameNumber" in row:
            current = integer(row["frameNumber"], 0, 2**63 - 1, "原帧号")
            if previous_frame is not None and current <= previous_frame:
                raise ValueError("原帧号未严格递增")
            entry["frameNumber"] = current
            previous_frame = current
        entries.append(entry)
        media_frames.append({"pts": pts, "durationPts": duration, "captureUtcUs": utc})
    if (previous_end - frames[0]["pts"]) * num > 120 * den:
        raise ValueError(f"片段 {number} 超过 120 秒")
    return entries, media_frames, (previous_utc, previous_end, previous_frame)


def validate_and_stage(period, stage, mapping, camera, epoch, match):
    init = bounded_media(period / "init.mp4")
    os.link(period / "init.mp4", stage / "init.mp4")
    segments = stage / "segments"
    segments.mkdir()
    parts = sorted(period.glob("*.m4s"), key=lambda p: int(p.stem))
    if not parts:
        raise ValueError("当前周期没有已封口的片段")
    first = previous_utc = previous_end = previous_frame = None
    digest = hashlib.sha256()
    with mapping.open(encoding="utf-8") as file:
        def read_row():
            return mapping_row(file)

        header = read_row()
        clock = check_header(header, match, camera, epoch)
        for part in parts:
            number = integer(int(part.stem), 0, SAFE_INT, "片段号")
            entries, _, previous = validated_segment(init, part, number, header, read_row,
                                                      (previous_utc, previous_end, previous_frame))
            previous_utc, previous_end, previous_frame = previous
            if first is None:
                first = int(entries[0]["captureUtcUs"])
            os.link(part, segments / part.name)
            index = segments / f"{number}.exposure.json"
            write_json(index, {"frames": entries})
            digest.update(f"{number}:".encode())
            digest.update(index.read_bytes())
        if read_row() is not None:
            raise ValueError("曝光清单含多余帧")
    return {"matchId": match, "cameraId": camera, "mediaEpoch": epoch, "clockSource": clock,
            "firstUtcUs": str(first), "lastUtcUs": str(previous_utc), "indexSha256": digest.hexdigest()}


def attach(root, camera, epoch, mapping):
    period = raw_period(root, camera, epoch)
    if not period.is_dir():
        raise ValueError("找不到机位的原片周期")
    match = read_json(root / "match.json")["matchId"]
    meta = read_json(period / "status.json")
    if (meta.get("matchId"), meta.get("cameraId"), meta.get("mediaEpoch")) != (match, camera, epoch):
        raise ValueError("采集目录中的机位、周期或比赛标识不符")
    with lock(period / ".active"), tempfile.TemporaryDirectory(dir=root, prefix=".attach-") as work:
        stage = Path(work) / "period"
        stage.mkdir()
        info = validate_and_stage(period, stage, mapping, camera, epoch, match)
        write_json(stage / "clock.json", info)
        with lock(root / ".publish.lock", wait=True):
            parent = root / "recordings" / camera / "epochs"
            target = parent / epoch
            if target.exists():
                if read_json(target / "clock.json") != info:
                    raise ValueError("同一周期已提交不同的曝光映射")
                return
            for existing in parent.glob("*/clock.json"):
                other = read_json(existing)
                if int(info["firstUtcUs"]) <= int(other["lastUtcUs"]) and int(other["firstUtcUs"]) <= int(info["lastUtcUs"]):
                    raise ValueError("同机位两周期曝光范围重叠")
            parent.mkdir(parents=True, exist_ok=True)
            stage.rename(target)


def recording_rate(period, last_segment):
    # HLS 列表在片段封口后由 FFmpeg 原子替换，用最近一段的实测码率估算。
    playlist = period / "preview.m3u8"
    if last_segment < 0 or not playlist.exists():
        return None
    lines = playlist.read_text(encoding="utf-8").splitlines()
    filename = f"{last_segment}.m4s"
    for index in range(len(lines) - 1, 0, -1):
        if lines[index] == filename and lines[index - 1].startswith("#EXTINF:"):
            seconds = float(lines[index - 1][8:].split(",")[0])
            if seconds > 0:
                return (period / filename).stat().st_size / seconds
    return None


def record_camera(root, match, name, camera, segment_seconds, stop, progress):
    live = camera["type"] != "file"
    retries = camera.get("reconnectAttempts", 3) if live else 0
    for attempt in range(retries + 1):
        epoch = camera.get("mediaEpoch") if attempt == 0 else None
        epoch = epoch or uuid.uuid4().hex
        period = raw_period(root, name, epoch)
        period.mkdir(parents=True, exist_ok=False)
        state = {"matchId": match, "cameraId": name, "mediaEpoch": epoch, "type": camera["type"],
                 "clockSource": "unknown", "startedUnix": time.time(), "segments": 0}
        status_file = period / "status.json"
        count = 0
        try:
            with lock(period / ".active") as active:
                state["status"] = "recording"
                write_json(status_file, state)
                progress[name] = dict(state)
                # 只向状态文件写退出码，RTSP 鉴权信息可能出现在 FFmpeg 的诊断中。
                with subprocess.Popen(capture_command(camera, segment_seconds), cwd=period, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      start_new_session=True, pass_fds=(active.fileno(),)) as process:
                    try:
                        while process.poll() is None:
                            if stop.wait(0.5):
                                process.send_signal(signal.SIGINT)
                                try:
                                    process.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                break
                            while (period / f"{count}.m4s").exists():
                                count += 1
                            if count != state["segments"]:
                                state["segments"] = count
                                state["bytesPerVideoSecond"] = recording_rate(period, count - 1)
                                write_json(status_file, state)
                                progress[name] = dict(state)
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.wait()
                state["segments"] = sum(1 for _ in period.glob("*.m4s"))
                state["bytesPerVideoSecond"] = recording_rate(period, state["segments"] - 1)
                if stop.is_set():
                    state["status"] = "stopped"
                elif process.returncode or live or not state["segments"]:
                    raise ValueError(f"视频输入结束或写盘失败，FFmpeg 退出码 {process.returncode}")
                else:
                    state["status"] = "completed"
            if attempt == 0 and "exposures" in camera and state["segments"]:
                attach(root, name, epoch, camera["exposures"])
                state["clockSource"] = read_json(root / "recordings" / name / "epochs" / epoch / "clock.json")["clockSource"]
            state["endedUnix"] = time.time()
            write_json(status_file, state)
            return state
        except (OSError, ValueError, KeyError) as error:
            state.update(status="failed", error=str(error), endedUnix=time.time())
            write_json(status_file, state)
            progress[name] = dict(state)
            if attempt == retries or stop.wait(min(2**attempt, 10)):
                return state


def capture(config_path, root):
    config = load_config(config_path)
    root.mkdir(parents=True, exist_ok=True)
    match = config["matchId"]
    stop = threading.Event()
    with lock(root / ".capture.lock"):
        identity = root / "match.json"
        if identity.exists():
            if read_json(identity).get("matchId") != match:
                raise ValueError("输出目录属于另一场比赛")
        else:
            write_json(identity, {"matchId": match})
        if (root / "STOP").exists():
            raise ValueError("目录有停止标记 STOP，请在下一次启动前移走该文件")
        supervisor = os.getppid()
        old = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
        states = {}
        progress = {}
        low_disk = False
        try:
            with ThreadPoolExecutor(max_workers=len(config["cameras"])) as pool:
                jobs = {name: pool.submit(record_camera, root, match, name, camera,
                                          config.get("segmentSeconds", 2), stop, progress)
                        for name, camera in config["cameras"].items()}
                try:
                    while jobs:
                        if (root / "STOP").exists() or os.getppid() != supervisor:
                            stop.set()
                        free = shutil.disk_usage(root).free
                        if free <= config.get("minFreeBytes", 0):
                            low_disk = True
                            stop.set()
                        for name, future in list(jobs.items()):
                            if future.done():
                                try:
                                    states[name] = future.result()
                                except (OSError, ValueError) as error:
                                    states[name] = {"status": "failed", "error": str(error)}
                                del jobs[name]
                        channels = {name: progress.get(name, {"status": "starting"}) for name in jobs}
                        rates = [channel.get("bytesPerVideoSecond") for channel in channels.values()]
                        health = {"matchId": match, "freeBytes": free, "lowDisk": low_disk,
                                  "active": channels, "finished": states, "updatedUnix": time.time()}
                        if rates and all(rates):
                            usable = max(0, free - config.get("minFreeBytes", 0))
                            health["remainingRecordingSeconds"] = int(usable / sum(rates))
                        write_json(root / "health.json", health)
                        if jobs:
                            time.sleep(0.5)
                finally:
                    stop.set()
        finally:
            for sig, handler in old.items():
                signal.signal(sig, handler)
        print(json.dumps(states, ensure_ascii=False, indent=2))
        if low_disk or any(state["status"] == "failed" for state in states.values()):
            raise ValueError("采集存在失败或磁盘余量不足，请查看 health.json")


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3:
            capture(Path(sys.argv[1]), Path(sys.argv[2]).resolve())
        elif len(sys.argv) == 6 and sys.argv[1] == "attach":
            attach(Path(sys.argv[2]).resolve(), sys.argv[3], sys.argv[4], Path(sys.argv[5]))
        else:
            sys.exit("用法：capture.py <配置.json> <目录> 或 attach <目录> <机位> <媒体周期> <曝光映射.ndjson>")
    except (OSError, ValueError, KeyError) as error:
        sys.exit(f"采集处理失败：{error}")
