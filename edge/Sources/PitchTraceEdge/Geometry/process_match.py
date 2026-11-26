"""离线比赛主流程：按曝光选帧、单视角观测、几何融合、分析与逐帧提交。"""

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from calibrate import unique_object


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def file_identity(path):
    if path.is_file():
        return digest(path)
    if not path.is_dir():
        raise ValueError(f"找不到模型：{path}")
    return {str(p.relative_to(path)): digest(p) for p in sorted(path.rglob("*")) if p.is_file()}


def media_identity(source, verify):
    result = hashlib.sha256()
    init_hashes = {}
    for path in sorted(source.glob("**/segments/*.json")):
        result.update(json.dumps([str(path.relative_to(source)), digest(path)]).encode())
        if verify:
            index = json.loads(path.read_bytes(), object_pairs_hook=unique_object)
            init = path.parent.parent / "init.mp4"
            if init not in init_hashes:
                init_hashes[init] = digest(init)
            if init_hashes[init] != index["initSha256"] or digest(path.with_suffix(".m4s")) != index["segmentSha256"]:
                raise ValueError(f"恢复时原片与已登记索引不匹配：{path}")
    return result.hexdigest()


def load_rows(stream, replay):
    from observations import validate_observation

    for line_number, line in enumerate(stream, 1):
        try:
            row = json.loads(line, object_pairs_hook=unique_object,
                             parse_constant=invalid_number)
            if not isinstance(row, dict):
                raise ValueError("每行必须是一条原帧记录")
            if not replay:
                validate_observation(row)
            yield row
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"第 {line_number} 行数据无效：{error}") from error


def invalid_number(value):
    raise ValueError(f"JSON 中含非有限数值 {value}")


class SceneBuilder:
    def __init__(self, config):
        from match_analysis import MatchAnalysis
        from tracking import Tracker

        self.config = config
        self.analysis = MatchAnalysis(config.roster, corrections=config.identity_corrections,
                                      pitch_length_m=config.pitch_length_m, goal_width_m=config.goal_width_m)
        self.tracker = Tracker()
        self.previous_version = None
        self.previous_cameras = {}
        self.previous_utc = None
        self.seq = 0

    def append(self, archive, utc, version, cameras, group, inputs):
        if self.previous_version is not None and self.previous_version != version:
            affected = {name for name in self.previous_cameras.keys() | cameras.keys()
                        if self.previous_cameras.get(name) != cameras.get(name)}
            if affected:
                self.tracker.reset_geometry(affected)
                self.analysis.reset_geometry()
        self.previous_version, self.previous_cameras = version, cameras
        players, ball, sources = self.tracker.update(utc, cameras, inputs)
        players, events = self.analysis.update(utc, players, ball, cameras, sources)
        frame = {"v": 2, "matchId": self.config.match_id, "streamEpoch": self.config.stream_epoch,
                 "seq": self.seq, "captureUtcUs": str(utc), "matchTimeMs": (utc - self.config.start_us) // 1000,
                 "calibrationId": version, "players": players, "ball": ball, "events": events}
        evidence = {"reportId": self.config.calibration_sources[version],
                    "frames": [{k: row[k] for k in ("cameraId", "mediaEpoch", "segment", "pts", "captureUtcUs")
                                if k in row} for row in group],
                    "activeCameras": sorted(cameras), "observations": inputs,
                    "tracks": sources, "measurements": self.analysis.measurements}
        if self.previous_utc is not None:
            interval = self.config.frame_interval_us
            previous_slot = (self.previous_utc - self.config.start_us) // interval
            current_slot = (utc - self.config.start_us) // interval
            if current_slot > previous_slot + 1:
                evidence["gapBefore"] = {"afterCaptureUtcUs": str(self.previous_utc),
                                         "beforeCaptureUtcUs": str(utc),
                                         "missingSlots": current_slot - previous_slot - 1}
        archive.append(frame, evidence)
        self.previous_utc = utc
        self.seq += 1
        return frame


def analyze_rows(config, rows, archive, media_root, detector_settings, config_dir, executable, *, resume=False):
    from observations import Detector, validate_observation
    from replay_images import ReplayImages

    builder = SceneBuilder(config)
    replay = media_root is not None
    # 恢复仅保存起始高水位，避免跨帧持有 SQLite 读游标妨碍同时提交上传回执。
    last_seq = archive.last_seq(config.match_id, config.stream_epoch) if resume else None
    with ExitStack() as stack:
        if replay:
            images = ReplayImages(media_root)
            stack.callback(images.close)
            detector = Detector(detector_settings, config_dir, executable)
            stack.callback(detector.close)
        for utc, version, cameras, group in config.groups(rows, replay=replay):
            if last_seq is not None and builder.seq <= last_seq:
                stored = archive.frame(config.match_id, config.stream_epoch, builder.seq)
                if (stored["seq"], stored["captureUtcUs"], stored["calibrationId"]) != (builder.seq, str(utc), version):
                    raise ValueError("恢复时原帧顺序或标定改变，请指定新的场景周期")
                proof = archive.evidence(config.match_id, config.stream_epoch, builder.seq)
                inputs = proof["observations"]
                # append 在同一事务核对完整快照与原帧引用，已有帧可避免再次推理。
                frame = builder.append(archive, utc, version, cameras, group, inputs)
                yield frame
                continue
            inputs = []
            for row in group:
                if row["cameraId"] not in cameras:
                    continue
                if replay:
                    image = images.read(row)
                    if list(image.shape[:2][::-1]) != cameras[row["cameraId"]]["imageSize"]:
                        raise ValueError(f'机位 {row["cameraId"]} 原片尺寸与当前标定不一致')
                    row = {**row, **detector.observe(image)}
                    validate_observation(row)
                inputs.append(row)
            yield builder.append(archive, utc, version, cameras, group, inputs)
        if last_seq is not None and builder.seq <= last_seq:
            raise ValueError("输入提前结束，无法恢复全部已提交场景")


def process(command, config_file, source, database, executable):
    import cv2
    import numpy
    import platform

    from match_config import MatchConfig
    from scene_archive import SceneArchive

    replay = command == "process-match"
    config = MatchConfig.load(config_file)
    with config_file.open(encoding="utf-8") as stream:
        settings = json.load(stream, object_pairs_hook=unique_object)
    manifest = source / "frames.ndjson" if replay else source
    if database.resolve() in (manifest.resolve(), config_file.resolve()):
        raise ValueError("归档不能与输入文件同名")
    detector_settings = settings.get("detector", {})
    delivery_keys = {"gateway", "recordings", "captureConfig", "mediaEpoch", "backfillBytesPerSecond", "retentionDays"}
    provenance = {
        "command": command, "sampleSource": config.sample_source,
        "configuration": {k: v for k, v in settings.items() if k not in delivery_keys},
        "inputSha256": digest(manifest), "reportSha256": {
            item["calibrationId"]: digest(config_file.parent / item["report"]) for item in settings["calibrations"]
        }, "software": {name: digest(Path(__file__).with_name(name)) for name in (
            "process_match.py", "observations.py", "replay_images.py", "match_config.py", "calibrate.py",
            "camera_geometry.py", "tracking.py", "match_analysis.py", "match_events.py", "pose.py")},
        "opencv": cv2.__version__, "numpy": numpy.__version__, "python": platform.python_version(),
    }
    if replay and detector_settings.get("backend", "vision") == "vision":
        provenance.update(system=platform.platform(), workerSha256=digest(Path(executable)))
        if "model" in detector_settings:
            provenance["modelSha256"] = file_identity(config_file.parent / detector_settings["model"])
    count = 0
    with manifest.open(encoding="utf-8") as stream, SceneArchive(database) as archive:
        if replay:
            resume = archive.last_seq(config.match_id, config.stream_epoch) is not None
            provenance["mediaIndexSha256"] = media_identity(source, verify=resume)
        archive.register_run(config.match_id, config.stream_epoch, provenance)
        for _ in analyze_rows(config, load_rows(stream, replay), archive, source if replay else None,
                              detector_settings, config_file.parent, executable, resume=True):
            count += 1
    return {"matchId": config.match_id, "streamEpoch": config.stream_epoch, "frames": count}


if __name__ == "__main__":
    try:
        mode, config, source, db, executable = sys.argv[1:]
        report = process(mode, Path(config), Path(source), Path(db), executable)
        print(json.dumps(report, ensure_ascii=False))
    except (ImportError, ValueError, TypeError, KeyError, AttributeError, OSError,
            sqlite3.Error, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(f"比赛场景重建失败：{error}")
