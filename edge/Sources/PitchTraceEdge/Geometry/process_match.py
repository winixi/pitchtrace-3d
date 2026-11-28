"""离线比赛主流程：按曝光选帧、单视角观测、几何融合、分析与逐帧提交。"""

from contextlib import ExitStack, closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from calibrate import unique_object
from camera_workers import CameraWorkers
from vision_model import digest, load_vision_model


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

    def append(self, archive, utc, version, cameras, group, inputs, degraded=None):
        if self.previous_version is not None and self.previous_version != version:
            affected = {name for name in self.previous_cameras.keys() | cameras.keys()
                        if self.previous_cameras.get(name) != cameras.get(name)}
            if affected:
                self.tracker.reset_geometry(affected)
                self.analysis.reset_geometry()
        self.previous_version, self.previous_cameras = version, cameras
        players, ball, sources = self.tracker.update(utc, cameras, inputs)
        players, events = self.analysis.update(utc, players, ball, cameras, sources)
        self.sources = sources
        frame = {"v": 2, "matchId": self.config.match_id, "streamEpoch": self.config.stream_epoch,
                 "seq": self.seq, "captureUtcUs": str(utc), "matchTimeMs": (utc - self.config.start_us) // 1000,
                 "calibrationId": version, "players": players, "ball": ball, "events": events}
        evidence = {"reportId": self.config.calibration_sources[version],
                    "frames": [{k: row[k] for k in ("cameraId", "mediaEpoch", "segment", "pts", "captureUtcUs")
                                if k in row} for row in group],
                    "activeCameras": sorted(cameras), "observations": inputs,
                    "tracks": sources, "measurements": self.analysis.measurements}
        if degraded:
            evidence["degraded"] = degraded
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

    def confirmed_numbers(self):
        return {track for track, (_, number) in self.analysis.votes.items() if number.confirmed is not None}


def analyze_rows(config, rows, archive, media_root, detector_settings, model, executable, *, resume=False,
                 degradation=None):
    """degradation 仅由连续处理传入；离线复算总是完整观测，以便与直播结果对照。"""
    from ball_search import BallSearch
    from observations import Detector, OcrSchedule, validate_observation
    from vision_model import ocr_refresh_us
    from replay_images import ReplayImages

    builder = SceneBuilder(config)
    replay = media_root is not None
    refresh_us = ocr_refresh_us(detector_settings) if replay else None
    schedule = OcrSchedule(refresh_us) if refresh_us is not None else None
    ocr_skip = {}
    controls = {}
    # 恢复仅保存起始高水位，避免跨帧持有 SQLite 读游标妨碍同时提交上传回执。
    last_seq = archive.last_seq(config.match_id, config.stream_epoch) if resume else None
    with ExitStack() as stack:
        observers = []
        if replay:
            cameras_in_match = {cam for entry in config.timeline for cam in entry.enabled}
            workers = CameraWorkers(cameras_in_match, detector_settings.get("workers", 1))
            searches = [BallSearch() if detector_settings.get("ballSearch", False) else None
                        for _ in range(workers.worker_count)]

        def observe(lane, row, version, cameras):
            images, detector = observers[lane]
            search = searches[lane]
            image = images.read(row)
            if list(image.shape[:2][::-1]) != cameras[row["cameraId"]]["imageSize"]:
                raise ValueError(f'机位 {row["cameraId"]} 原片尺寸与当前标定不一致')
            skip = ocr_skip.get(row["cameraId"], ())
            control = controls.get(row["cameraId"])
            observed = (search.observe(detector, version, row, image, skip, control=control) if search
                        else detector.observe(image, None, skip, control=control))
            # 性能采样里的墙钟耗时不参与比赛证据或幂等比较。
            observed.pop("timing", None)
            result = {**row, **observed}
            validate_observation(result)
            return result

        for utc, version, cameras, group in config.groups(rows, replay=replay):
            if schedule is not None:
                ocr_skip = schedule.hints(utc)
            if last_seq is not None and builder.seq <= last_seq:
                stored = archive.frame(config.match_id, config.stream_epoch, builder.seq)
                if (stored["seq"], stored["captureUtcUs"], stored["calibrationId"]) != (builder.seq, str(utc), version):
                    raise ValueError("恢复时原帧顺序或标定改变，请指定新的场景周期")
                proof = archive.evidence(config.match_id, config.stream_epoch, builder.seq)
                inputs = proof["observations"]
                degraded = proof.get("degraded")
            else:
                enabled = [row for row in group if row["cameraId"] in cameras]
                degraded = None
                if replay and degradation is not None and degradation.update(utc):
                    skipped = degradation.skipped_cameras(enabled)
                    enabled = [row for row in enabled if row["cameraId"] not in skipped]
                    controls = {row["cameraId"]: degradation.control(row["cameraId"], utc,
                                                                     cameras[row["cameraId"]]["imageSize"][0])
                                for row in enabled}
                    degraded = {"level": degradation.level, "backlogSeconds": round(degradation.backlog_seconds, 3),
                                "skippedCameras": skipped}
                else:
                    controls = {}
                # 已归档的观测足以重建历史；第一组新原帧到来时再启动模型和解码器。
                if replay and enabled and not observers:
                    for _ in range(workers.worker_count):
                        images = stack.enter_context(closing(ReplayImages(media_root)))
                        detector = stack.enter_context(closing(Detector(detector_settings, model, executable)))
                        observers.append((images, detector))
                    # 停工作线程后再释放其解码器和原生推理进程。
                    stack.enter_context(workers)
                inputs = workers.map_group(enabled, observe, version, cameras) if replay else enabled
            # 恢复与首跑均以同一份已选原帧证据推进预测，避免重复解码和推理。
            if replay:
                for row in inputs:
                    search = searches[workers.lane(row["cameraId"])]
                    if search:
                        search.record(version, row)
            if degradation is not None:
                degradation.record(utc, inputs)
            frame = builder.append(archive, utc, version, cameras, group, inputs, degraded)
            if schedule is not None:
                schedule.update(utc, builder.sources, builder.confirmed_numbers())
            yield frame
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
    model = load_vision_model(detector_settings, config_file.parent) if replay else None
    delivery_keys = {"gateway", "recordings", "captureConfig", "mediaEpoch", "backfillBytesPerSecond", "mediaBytesPerSecond", "retentionDays"}
    provenance = {
        "command": command, "sampleSource": config.sample_source,
        "configuration": {k: v for k, v in settings.items() if k not in delivery_keys},
        "inputSha256": digest(manifest), "reportSha256": {
            item["calibrationId"]: digest(config_file.parent / item["report"]) for item in settings["calibrations"]
        }, "software": {name: digest(Path(__file__).with_name(name)) for name in (
            "process_match.py", "camera_workers.py", "observations.py", "observation_quality.py", "vision_model.py", "ball_search.py",
            "replay_images.py", "match_config.py", "calibrate.py",
            "camera_geometry.py", "tracking.py", "match_analysis.py", "match_events.py", "pose.py")},
        "opencv": cv2.__version__, "numpy": numpy.__version__, "python": platform.python_version(),
    }
    if replay and detector_settings.get("backend", "vision") == "vision":
        provenance.update(system=platform.platform(), workerSha256=digest(Path(executable)))
        if model is not None:
            provenance.update(model.provenance)
    count = 0
    with manifest.open(encoding="utf-8") as stream, SceneArchive(database) as archive:
        if replay:
            resume = archive.last_seq(config.match_id, config.stream_epoch) is not None
            provenance["mediaIndexSha256"] = media_identity(source, verify=resume)
        archive.register_run(config.match_id, config.stream_epoch, provenance)
        for _ in analyze_rows(config, load_rows(stream, replay), archive, source if replay else None,
                              detector_settings, model, executable, resume=True):
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
