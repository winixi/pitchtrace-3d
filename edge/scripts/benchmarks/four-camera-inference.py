"""按比赛原速投放四路推理任务，比较单工作进程轮询和每机位独立进程。"""

import argparse
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
import gzip
import json
import math
import os
from pathlib import Path
import platform
from queue import Queue
import subprocess
import sys
import time

import cv2

EDGE = Path(__file__).resolve().parents[2]
GEOMETRY = EDGE / "Sources/PitchTraceEdge/Geometry"
sys.path.insert(0, str(GEOMETRY))
from ball_search import BallSearch
from observations import Detector, validate_observation
from vision_model import digest, load_vision_model

CAMERAS = "ABCD"
STAGES = ("detection", "pose", "ocr")


def open_sources(stack, videos, offsets, seconds, hz):
    sources, schedule = [], []
    for camera, path, offset in zip(CAMERAS, videos, offsets, strict=True):
        cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
        stack.callback(cap.release)
        if not cap.isOpened():
            raise ValueError(f"无法打开 {path}")
        fps = cap.get(cv2.CAP_PROP_FPS)
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        first = round(offset * fps)
        if not math.isfinite(fps) or fps < hz or first < 0 or first + math.ceil(seconds * fps - 1e-7) > count:
            raise ValueError(f"机位 {camera} 不能按 {hz} Hz 完整提供 {seconds} 秒输入")
        if first and not cap.set(cv2.CAP_PROP_POS_FRAMES, first):
            raise ValueError(f"机位 {camera} 无法定位起始帧 {first}")
        sources.append({"cap": cap, "next": first, "info": {
            "cameraId": camera, "file": path.name, "startFrameZeroBased": first, "fps": fps,
            "imageSize": [int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))],
            "framesInFile": count,
        }})
        # 恒定帧率输入；每个 100 ms 时隙选时隙内首帧，25 fps 时曝光间隔交替为 120/80 ms。
        for slot in range(round(seconds * hz)):
            relative_frame = math.ceil(slot * fps / hz - 1e-7)
            schedule.append((relative_frame / fps, camera, first + relative_frame))
    return sources, sorted(schedule)


def process_frame(job, sources, detector, search, origin, timed):
    exposure, camera, index = job
    stream = sources[CAMERAS.index(camera)]
    cap = stream["cap"]
    begin = time.monotonic() - origin
    while stream["next"] <= index:
        if not cap.grab():
            raise ValueError(f"机位 {camera} 在帧 {stream['next']} 意外结束")
        stream["next"] += 1
    ok, image = cap.retrieve()
    if not ok or list(image.shape[1::-1]) != stream["info"]["imageSize"]:
        raise ValueError(f"机位 {camera} 的第 {index} 帧解码失败或尺寸不符")
    row = {"cameraId": camera, "captureUtcUs": str(round(exposure * 1e6)), "pts": index, "segment": 0}
    observation = search.observe(detector, "benchmark", row, image)
    row.update(observation)
    validate_observation(row)
    search.record("benchmark", row)
    end = time.monotonic() - origin
    players = row["players"]
    search_info = observation.get("ballSearch") or {}
    record = {"camera": camera, "frame": index, "exposure": exposure, "begin": begin, "end": end,
              "players": len(players), "roiOffered": bool(search_info.get("region")),
              "searchMode": search_info.get("mode"), "roiRan": bool(search_info.get("ran")),
              "ballSource": (row["ball"] or {}).get("source"),
              "posedPlayers": sum(bool(p.get("joints")) for p in players),
              "feet": sum(p.get("foot") is not None for p in players),
              "joints": sum(len(p.get("joints", {})) for p in players),
              "numberedPlayers": sum(p.get("number") is not None for p in players)}
    if timed:
        timing = observation.get("timing", {})
        if not all(type(timing.get(stage + suffix)) is int and timing[stage + suffix] >= 0
                   for stage in STAGES for suffix in ("Ns", "Requests")):
            raise ValueError("原生工作进程缺少有效的分阶段耗时或请求数")
        record["timing"] = timing
    return record


def consume(jobs, sources, model, settings, executable, origin):
    search = BallSearch()
    records = []
    timed = os.environ.get("PITCHTRACE_VISION_TIMING") == "1"
    with ExitStack() as stack:
        detector = Detector(settings, model, executable)
        stack.callback(detector.close)
        while (job := jobs.get()) is not None:
            records.append(process_frame(job, sources, detector, search, origin, timed))
            if len(records) % 100 == 0:
                print(f"工作进程 {detector.worker.pid} 已处理 {len(records)} 帧，耗时 {records[-1]['end']:.1f} 秒",
                      file=sys.stderr, flush=True)
    return records


def consume_exposure_groups(jobs, sources, model, settings, executable, origin):
    from camera_workers import CameraWorkers

    records = []
    timed = os.environ.get("PITCHTRACE_VISION_TIMING") == "1"
    with ExitStack() as stack:
        pool = stack.enter_context(CameraWorkers(CAMERAS, len(CAMERAS)))
        detectors, searches = [], []
        for _ in range(pool.worker_count):
            detector = Detector(settings, model, executable)
            stack.callback(detector.close)
            detectors.append(detector)
            searches.append(BallSearch())

        def process(lane, row):
            return process_frame(row["job"], sources, detectors[lane], searches[lane], origin, timed)

        while (first := jobs.get()) is not None:
            group = [first] + [jobs.get() for _ in range(len(CAMERAS) - 1)]
            if any(job is None for job in group) or {job[1] for job in group} != set(CAMERAS):
                raise ValueError("曝光组的机位不齐全")
            ready = pool.map_group([{"cameraId": job[1], "job": job} for job in group], process)
            available = time.monotonic() - origin
            for record in ready:
                record["available"] = available
            records.extend(ready)
            if len(records) % 100 == 0:
                print(f"生产调度已处理 {len(records)} 帧，耗时 {available:.1f} 秒", file=sys.stderr, flush=True)
    return records


def stage_costs(records):
    # 四进程时各机位的工作时间可重叠。总秒数为工作进程耗时之和。
    result = {}
    for stage in STAGES:
        values = sorted(row["timing"][stage + "Ns"] for row in records)
        total_ms = sum(values) / 1e6
        requests = sum(row["timing"][stage + "Requests"] for row in records)
        result[stage] = {"requests": requests, "totalWorkerSeconds": round(total_ms / 1e3, 4),
                         "meanMsPerFrame": round(total_ms / len(values), 3),
                         "p95MsPerFrame": round(values[math.ceil(.95 * len(values)) - 1] / 1e6, 3),
                         "meanMsPerRequest": round(total_ms / requests, 3) if requests else None}
    for key, name in (("proposalNs", "ballProposal"), ("ocrSheetNs", "ocrSheet")):
        if key in records[0]["timing"]:
            total = sum(row["timing"][key] for row in records)
            result[name] = {"totalWorkerSeconds": round(total / 1e9, 4),
                            "meanMsPerFrame": round(total / len(records) / 1e6, 3)}
    return result


def ready_at(record):
    return record.get("available", record["end"])


def summary(records, seconds, hz):
    ends = sorted(ready_at(r) for r in records)
    elapsed = ends[-1]
    latencies = sorted(ready_at(r) - r["exposure"] for r in records)
    trace = []
    for second in range(1, seconds + 1):
        arrived = sum(r["exposure"] < second for r in records)
        done = bisect_right(ends, second)
        trace.append({"second": second, "due": arrived, "completed": done, "outstanding": arrived - done})
    outstanding = peak = busy = peak_busy = 0
    for _, change in sorted([(r["exposure"], 1) for r in records] + [(ready_at(r), -1) for r in records]):
        outstanding += change
        peak = max(peak, outstanding)
    for _, change in sorted([(r["begin"], 1) for r in records] + [(r["end"], -1) for r in records]):
        busy += change
        peak_busy = max(peak_busy, busy)
    cut = hz * len(CAMERAS)
    by_camera = {}
    for camera in CAMERAS:
        rows = [r for r in records if r["camera"] == camera]
        by_camera[camera] = {"samples": len(rows), "decodedSourceFrames": rows[-1]["frame"] - rows[0]["frame"] + 1,
                             "fpsOverEntireRun": round(len(rows) / elapsed, 3),
                             "fpsUntilCameraFinished": round(len(rows) / ready_at(rows[-1]), 3),
                             "fpsAfterFirstSecondOfCameraSamples": round((len(rows) - hz) / (ready_at(rows[-1]) - ready_at(rows[hz - 1])), 3),
                             "completedByInputEnd": sum(ready_at(r) <= seconds for r in rows),
                             "firstResultSeconds": round(ready_at(rows[0]), 4),
                             "lastResultSeconds": round(ready_at(rows[-1]), 4),
                             "meanPlayers": round(sum(r["players"] for r in rows) / len(rows), 2),
                             "roiOffered": sum(r["roiOffered"] for r in rows),
                             "roiSearches": sum(r["roiRan"] for r in rows),
                             "coldSearches": sum(r["roiRan"] and r["searchMode"] == "cold" for r in rows),
                             "coldBallDetections": sum(r["ballSource"] in ("coreML-ROI", "motion-appearance")
                                                       and r["searchMode"] == "cold" for r in rows),
                             "motionBallDetections": sum(r["ballSource"] == "motion-appearance" for r in rows),
                             "fullBallHits": sum(r["ballSource"] == "coreML" for r in rows),
                             "roiBallHits": sum(r["ballSource"] == "coreML-ROI" for r in rows)}
    quality = {key: sum(r[key] for r in records) for key in ("players", "posedPlayers", "feet", "joints", "numberedPlayers")}
    return {"observations": quality, **({"stageTimes": stage_costs(records)} if "timing" in records[0] else {}),
            "selectedFrames": len(records), "completedFrames": len(ends), "elapsedSeconds": round(elapsed, 4),
            "firstResultSeconds": round(ends[0], 4), "aggregateFramesPerSecond": round(len(records) / elapsed, 3),
            "processingOverRequiredRate": round(seconds / elapsed, 4),
            "fpsAfterFirstSecondOfCompletedSamples": round((len(ends) - cut) / (elapsed - ends[cut - 1]), 3),
            "endOfInputOutstanding": trace[-1]["outstanding"], "peakOutstandingIncludesInflight": peak,
            "peakConcurrentFrameProcessing": peak_busy, "drainSeconds": round(max(0, elapsed - seconds), 4),
            "exposureToResultP95Seconds": round(latencies[math.ceil(.95 * len(latencies)) - 1], 4),
            "exposureToResultMaxSeconds": round(latencies[-1], 4), "byCamera": by_camera, "duringInput": trace}


def run_mode(args, workers, model, settings, production_groups):
    with ExitStack() as stack:
        sources, schedule = open_sources(stack, args.video, args.start, args.seconds, args.hz)
        # 生产组织按曝光齐组后交给实际 CameraWorkers，独立队列用于复现历史比较组。
        queue_count = 1 if production_groups else workers
        consume_jobs = consume_exposure_groups if production_groups else consume
        queues = [Queue() for _ in range(queue_count)]
        # 队列只存磁盘上的帧号；积压时仍保留所有到期任务，不缓存解码后的大图。
        with ThreadPoolExecutor(max_workers=queue_count) as pool:
            origin = time.monotonic()
            futures = [pool.submit(consume_jobs, jobs, sources, model, settings, args.executable, origin) for jobs in queues]
            max_jitter = 0.
            try:
                for exposure, camera, index in schedule:
                    for future in futures:
                        if future.done():
                            future.result()
                    delay = origin + exposure - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    max_jitter = max(max_jitter, time.monotonic() - origin - exposure)
                    queues[CAMERAS.index(camera) % queue_count].put((exposure, camera, index))
            finally:
                for jobs in queues:
                    jobs.put(None)
            records = [r for future in futures for r in future.result()]
        if len(records) != len(schedule):
            raise RuntimeError("部分时隙未处理")
        result = summary(records, args.seconds, args.hz)
        result.update(workers=workers, scheduleMode="productionExposureGroups" if production_groups else "independentQueues",
                      maxReleaseJitterSeconds=round(max_jitter, 4))
        if args.traces:
            args.traces.mkdir(parents=True, exist_ok=True)
            suffix = "-groups" if production_groups else ""
            path = args.traces / f"{args.hz}hz-{workers}-workers{suffix}.ndjson.gz"
            with path.open("wb") as output, gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as stream:
                for record in sorted(records, key=lambda r: (r["exposure"], r["camera"])):
                    stream.write((json.dumps(record, separators=(",", ":")) + "\n").encode())
            result.update(traceFile=os.path.relpath(path, args.output.parent), traceSha256=digest(path))
        return [s["info"] for s in sources], result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, nargs=4, required=True, help="按 A–D 排列的四段恒定帧率本地录像")
    parser.add_argument("--start", type=float, nargs=4, default=[0.] * 4, help="各路起点秒数")
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--hz", type=int, default=10)
    parser.add_argument("--workers", type=int, choices=[1, 4], nargs="*", default=[1, 4],
                        help="依次运行的工作进程数，默认先单进程再四进程")
    parser.add_argument("--production-groups", action="store_true", help="追加正式 CameraWorkers 的四进程按曝光组调度")
    parser.add_argument("--stage-times", action="store_true", help="采集原生检测、姿态和 OCR 的各帧耗时与调用数")
    parser.add_argument("--model", type=Path, default=EDGE / ".build/models/yolo11n/yolo11n.json")
    parser.add_argument("--executable", type=Path, default=EDGE / ".build/release/pitchtrace-edge")
    parser.add_argument("--output", type=Path, required=True, help="保存汇总 JSON 的路径")
    parser.add_argument("--traces", type=Path, help="保存逐帧时刻的压缩 NDJSON 目录")
    args = parser.parse_args()
    if args.seconds < 2 or args.hz <= 0:
        parser.error("测量至少两秒且每路采样率为正")
    if not args.workers and not args.production_groups:
        parser.error("至少选择一种调度配置")
    if args.stage_times:
        os.environ["PITCHTRACE_VISION_TIMING"] = "1"
    settings = {"modelManifest": str(args.model.resolve()), "ballSearch": True, "ocrMode": "batch"}
    model = load_vision_model(settings, Path.cwd())
    chip, memory = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string", "hw.memsize"], text=True).splitlines()
    report = {"dateUtc": datetime.now(timezone.utc).isoformat(), "chip": chip, "memoryBytes": int(memory),
              "macOS": platform.mac_ver()[0], "python": platform.python_version(), "openCV": cv2.__version__,
              "binarySha256": digest(args.executable), **model.provenance, "settings": {"sampleHzPerCamera": args.hz,
              "cameraCount": 4, "inputSeconds": args.seconds, "ballSearch": True, "ocrMode": settings["ocrMode"],
              "warmupSamples": 0,
              "stageTiming": os.environ.get("PITCHTRACE_VISION_TIMING") == "1"}, "runs": []}
    sources_to_hash = ["observations.py", "observation_quality.py", "vision_model.py", "ball_search.py", "tracking.py"]
    if args.production_groups:
        sources_to_hash.append("camera_workers.py")
    report["inferenceCodeSha256"] = {name: digest(GEOMETRY / name) for name in sources_to_hash}
    hashes = {path: digest(path) for path in set(args.video)}
    modes = [(workers, False) for workers in args.workers]
    if args.production_groups:
        modes.append((len(CAMERAS), True))
    for workers, production_groups in modes:
        started = datetime.now(timezone.utc).isoformat()
        sources, result = run_mode(args, workers, model, settings, production_groups)
        result.update(startedAtUtc=started, finishedAtUtc=datetime.now(timezone.utc).isoformat())
        for source, path in zip(sources, args.video):
            source["sha256"] = hashes[path]
        report["sources"] = sources
        report["runs"].append(result)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(f"{workers} 个工作进程，合计 {result['aggregateFramesPerSecond']} 帧/秒，"
              f"输入结束时积压 {result['endOfInputOutstanding']} 帧", flush=True)


if __name__ == "__main__":
    main()
