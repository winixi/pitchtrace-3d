"""串联一场比赛的采集、重放、重建与独立补传。"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from urllib.parse import urlsplit

from calibrate import is_identifier
from capture import load_config as read_capture_config, write_json
from match_config import MatchConfig, read_object
from vision_model import digest, load_vision_model


def now():
    return datetime.now(timezone.utc).isoformat()


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_signature(path):
    if path.is_file():
        return digest(path)
    if not path.is_dir():
        raise ValueError(f"找不到输入文件或目录：{path}")
    # 包含目录名，以检测新增的空机位。计量时逐文件读，不把录像装入内存。
    files = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink() and item.is_dir():
            raise ValueError(f"输入树中的目录链接不能保证复算一致：{item}")
        if not item.is_dir() and not item.is_file():
            raise ValueError(f"输入必须由普通文件和目录组成：{item}")
        files[str(item.relative_to(path))] = None if item.is_dir() else digest(item)
    return signature(files)


def inference_inputs(data, base, executable):
    inputs = {"configuration": {k: v for k, v in data.items() if k != "gateway"},
              "reports": {entry["calibrationId"]: digest(base / entry["report"]) for entry in data["calibrations"]},
              "worker": digest(executable),
              "modules": {p.name: digest(p) for p in Path(__file__).parent.glob("*.py")}}
    model = load_vision_model(data.get("detector", {}), base)
    if model is not None:
        inputs.update(model.provenance)
    return signature(inputs), model


def recording_inputs(data, base, root):
    if ("recordings" in data) == ("captureConfig" in data):
        raise ValueError("请在 recordings 与 captureConfig 中选择一个输入")
    key = "recordings" if "recordings" in data else "captureConfig"
    if not isinstance(data[key], str) or not data[key].strip():
        raise ValueError(f"{key} 需要一个路径")
    path = (base / data[key]).resolve()
    if root == path or root in path.parents or path in root.parents:
        raise ValueError("输入路径与运行目录不能相互包含")
    if key == "recordings":
        if not path.is_dir():
            raise ValueError("recordings 必须是录像目录")
        return path, None, file_signature(path)
    settings = read_capture_config(path)
    if settings["matchId"] != data["matchId"] or len(settings["cameras"]) < 2:
        raise ValueError("采集配置必须属于本场比赛并至少配置两路机位")
    files = {str(path): digest(path)}
    for cam in settings["cameras"].values():
        if cam["type"] == "file":
            source = Path(cam["source"])
            files[str(source)] = digest(source) if source.is_file() else None
        if "exposures" in cam:
            mapping = cam["exposures"]
            files[str(mapping)] = digest(mapping) if mapping.is_file() else None
    return path, settings, signature(files)


def archive_signature(path, match, epoch):
    if not path.is_file():
        raise ValueError("本地场景归档缺失")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT provenance FROM edge_runs WHERE match_id=? AND epoch=?", (match, epoch)).fetchone()
        if row is None:
            raise ValueError("场景归档缺少本场运行记录")
        result = hashlib.sha256(row[0].encode())
        # 发布会更新回执表，运行完整性只核对本场不可变的场景与证据。
        cursor = db.execute("SELECT seq,payload,evidence FROM edge_scenes WHERE match_id=? AND epoch=? ORDER BY seq",
                            (match, epoch))
        for frame in cursor:
            result.update(json.dumps(frame, separators=(",", ":")).encode())
    return result.hexdigest()


class Run:
    def __init__(self, root, executable, lock, state):
        self.root = root
        self.executable = executable
        self.lock = lock
        self.state = state

    def save(self, stage=None):
        if stage:
            self.state.update(status=stage, updatedAtUtc=now())
            self.state.pop("error", None)
            self.state.pop("failedStage", None)
        write_json(self.root / "run-state.json", self.state)

    def command(self, *args):
        # 子进程若在父进程退出后继续写盘，继承的文件锁可防止另一编排器重入。
        result = subprocess.run([self.executable, *args], stdout=sys.stderr, pass_fds=(self.lock.fileno(),))
        if result.returncode:
            raise ValueError(f"{args[0]} 退出码为 {result.returncode}，请查看该命令的诊断")

    def capture(self, config_path, settings):
        attempt = self.state.get("captureAttempt", 0)
        capture_root = self.root / f"capture-{attempt:03d}"
        expected = set(settings["cameras"])

        def ready():
            paths = capture_root / "recordings"
            available = {camera for camera in expected if any((paths / camera / "epochs").glob("*/clock.json"))}
            self.state["capturedCameras"] = sorted(available)
            self.state["missingCaptureCameras"] = sorted(expected - available)
            return len(available) >= 2

        if attempt and ready():
            return capture_root / "recordings"
        has_raw = attempt and any((capture_root / "raw").glob("*/*/*.m4s"))
        if has_raw and (self.state.get("captureFinished") or
                        any(c["type"] != "file" for c in settings["cameras"].values())):
            raise ValueError(f"等待各机位的曝光映射，请通过 attach-exposures 补齐 {capture_root}")
        # 离线文件采集失败可在下一次尝试重新录制，保留前次原片。
        attempt += 1
        capture_root = self.root / f"capture-{attempt:03d}"
        self.state["captureAttempt"] = attempt
        self.save("capturing")
        try:
            self.command("capture", config_path, capture_root)
        except ValueError as error:
            self.state["captureDiagnostic"] = str(error)
            if not ready():
                raise
        self.state["captureFinished"] = True
        self.save()
        if not ready():
            raise ValueError(f"原片已落盘，等待 attach-exposures 补齐 {capture_root} 的逐帧曝光")
        return capture_root / "recordings"

    def execute(self, path, data, config, source, capture, source_hash, inference_hash, gateway):
        recordings = self.capture(source, capture) if capture else source
        media = self.root / "media"
        db = self.root / "scenes.sqlite"
        recording_hash = file_signature(recordings) if capture else source_hash
        if media.exists() and "recordingSha256" not in self.state:
            raise ValueError("运行目录内已有外部创建的 media")
        if self.state.setdefault("recordingSha256", recording_hash) != recording_hash:
            raise ValueError("已选用的录像内容发生变化，请使用新运行目录")
        self.save("replaying")
        if not media.exists():
            if "mediaSha256" in self.state:
                raise ValueError("已导出的媒体目录缺失")
            self.command("replay", recordings, media)
            if file_signature(recordings) != recording_hash:
                self.state["invalidated"] = True
                raise ValueError("录像在重放期间发生变化，请使用新运行目录")
        media_hash = file_signature(media)
        if db.exists() and "mediaSha256" not in self.state:
            raise ValueError("运行目录内已有外部创建的 scenes.sqlite")
        if self.state.setdefault("mediaSha256", media_hash) != media_hash:
            raise ValueError("运行目录内媒体内容发生变化")
        self.save("processing")
        if "sceneSha256" not in self.state:
            self.command("process-match", path, media, db)
            current_hash, _ = inference_inputs(read_object(path), path.parent, self.executable)
            if file_signature(media) != media_hash or current_hash != inference_hash:
                self.state["invalidated"] = True
                raise ValueError("输入在重建期间发生变化，请使用新运行目录")
        scene_hash = archive_signature(db, config.match_id, config.stream_epoch)
        if self.state.setdefault("sceneSha256", scene_hash) != scene_hash:
            raise ValueError("运行目录内场景或证据发生变化")
        self.save("localComplete")
        if gateway:
            self.publish(config, data, media, db, gateway)
        if self.state.get("retentionDays") is not None:
            from retention import record_owned_outputs
            record_owned_outputs(self.root)
        self.save("completed")

    def publish(self, config, data, media, db, gateway):
        destinations = self.state.setdefault("destinations", {})
        jobs = destinations.setdefault(gateway, {})
        commands = {"scenes": ["publish-scenes", db, gateway, config.match_id, config.stream_epoch]}
        if data.get("backfillBytesPerSecond", 0):
            commands["scenes"].extend(["--backfill-bytes-per-second", str(data["backfillBytesPerSecond"])])
        for camera in sorted(p for p in media.iterdir() if p.is_dir() and not p.name.startswith(".")):
            args = ["publish-media", gateway, config.match_id]
            if not (camera / "epochs").is_dir():
                args.append(data.get("mediaEpoch", config.stream_epoch))
            commands["media/" + camera.name] = args + [camera]
        self.save("publishing")
        errors = []
        with ThreadPoolExecutor(max_workers=len(commands)) as pool:
            pending = {pool.submit(self.command, *args): name for name, args in commands.items()
                       if jobs.get(name) != "confirmed"}
            for future in as_completed(pending):
                name = pending[future]
                try:
                    future.result()
                    jobs[name] = "confirmed"
                except (OSError, ValueError) as error:
                    jobs[name] = str(error)
                    errors.append(name)
                self.save()
        if errors:
            raise ValueError("待重传：" + "、".join(sorted(errors)))


def run_match(path, root, executable):
    config = MatchConfig.load(path)
    data = read_object(path)
    gateway = data.get("gateway")
    if gateway is not None:
        if not isinstance(gateway, str):
            raise ValueError("gateway 必须为 HTTP(S) 网关地址")
        url = urlsplit(gateway)
        if (url.scheme not in ("http", "https") or not url.hostname or url.username is not None
                or url.query or url.fragment):
            raise ValueError("gateway 必须为不含帐号、查询或片段的 HTTP(S) 网关基址")
        gateway = gateway.rstrip("/")
    if not is_identifier(data.get("mediaEpoch", config.stream_epoch)):
        raise ValueError("mediaEpoch 必须是有效的 v2 标识")
    rate = data.get("backfillBytesPerSecond", 0)
    if type(rate) is not int or rate < 0:
        raise ValueError("backfillBytesPerSecond 必须是非负整数")
    days = data.get("retentionDays")
    if days is not None and (type(days) is not int or days <= 0):
        raise ValueError("retentionDays 必须是正整数")
    source, capture, source_hash = recording_inputs(data, path.parent, root)
    inference_hash, _ = inference_inputs(data, path.parent, executable)
    identity = signature({"configDirectory": str(path.parent), "input": source_hash, "inference": inference_hash})
    root.mkdir(parents=True, exist_ok=True)
    with (root / "run.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("该比赛运行目录正被使用") from error
        if (root / "run-retention.jsonl").exists():
            raise ValueError("该目录已经开始留存清理，请使用新的运行目录")
        state_path = root / "run-state.json"
        if state_path.exists():
            state = read_object(state_path)
            if state.get("format") != "pitchtrace-run-v1" or state.get("inputSha256") != identity or state.get("invalidated"):
                raise ValueError("配置、源文件或处理程序已变化，请使用新运行目录")
        else:
            if any(p.name != "run.lock" for p in root.iterdir()):
                raise ValueError("首次运行需要空目录")
            state = {"format": "pitchtrace-run-v1", "matchId": config.match_id, "streamEpoch": config.stream_epoch,
                     "createdAtUtc": now(), "inputSha256": identity, "retentionDays": days}
        run = Run(root, executable, lock, state)
        run.save("starting")
        try:
            run.execute(path, data, config, source, capture, source_hash, inference_hash, gateway)
        except (OSError, ValueError, sqlite3.Error, KeyboardInterrupt) as error:
            state.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                         failedStage=state["status"], error=str(error), updatedAtUtc=now())
            run.save()
            raise
        return state


if __name__ == "__main__":
    try:
        config_path, directory, binary = (Path(arg).resolve() for arg in sys.argv[1:])
        print(json.dumps(run_match(config_path, directory, binary), ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, sqlite3.Error, ImportError) as error:
        sys.exit(f"比赛运行失败：{error}")
