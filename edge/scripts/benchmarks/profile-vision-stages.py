"""用比赛中固定的同帧裁片核对 Vision 阶段耗时及逐人观测，不使用足球真值。"""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
from contextlib import closing
import time

import cv2

EDGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EDGE / "Sources/PitchTraceEdge/Geometry"))
from ball_search import BallSearch
from observations import Detector, validate_observation
from vision_model import load_vision_model


def profile(args):
    paths = [args.videos / f"cam{i}.avi" for i in range(3, 7)]
    # 每个机位在一致的三个时段各取六个连续的 10 Hz 样本。
    frames = [start + offset for start in (400, 900, 1400) for offset in (0, 3, 5, 8, 10, 13)]
    os.environ["PITCHTRACE_VISION_TIMING"] = "1"
    settings = {"modelManifest": str(args.manifest.resolve()), "ballSearch": args.ball_search, "ocrMode": args.ocr_mode}
    model = load_vision_model(settings, Path.cwd())
    samples = []
    for path in paths:
        cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
        try:
            for index in frames:
                if not cap.set(cv2.CAP_PROP_POS_FRAMES, index):
                    raise ValueError(f"无法定位 {path} 的第 {index} 帧")
                ok, image = cap.read()
                if not ok:
                    raise ValueError(f"无法读取 {path} 的第 {index} 帧")
                ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
                if not ok:
                    raise ValueError("无法编码原帧")
                samples.append((path.stem, index, image, hashlib.sha256(jpeg).hexdigest()))
        finally:
            cap.release()
    records = []
    with closing(Detector(settings, model, str(args.executable))) as detector:
        detector.observe(samples[0][2])  # 单张预热图不进入正式记录和搜索历史。
        search = BallSearch()
        for camera, index, image, sha in samples:
            key = {"cameraId": camera, "captureUtcUs": str((index - frames[0]) * 40_000)}
            begin = time.monotonic_ns()
            if args.ball_search:
                result = search.observe(detector, "profile", key, image)
                search.record("profile", {**key, **result})
            else:
                result = detector.observe(image)
            validate_observation({**key, "pts": index, "segment": 0, **result})
            elapsed = time.monotonic_ns() - begin
            records.append(dict(camera=camera, frame=index, jpegSha256=sha, elapsedNs=elapsed, **result))
    args.records.parent.mkdir(parents=True, exist_ok=True)
    args.records.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    counts = Counter()
    for r in records:
        counts.update(r["timing"])
        counts["elapsedNs"] += r["elapsedNs"]
        for player in r["players"]:
            counts["players"] += 1
            counts["joints"] += len(player["joints"])
            counts["feet"] += "foot" in player
            counts["numbers"] += "number" in player
    print(json.dumps(dict(frames=len(records), **counts), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("videos", type=Path)
    parser.add_argument("records", type=Path)
    parser.add_argument("--executable", type=Path, default=EDGE / ".build/release/pitchtrace-edge")
    parser.add_argument("--manifest", type=Path, default=EDGE / ".build/models/yolo11n/yolo11n.json")
    parser.add_argument("--ball-search", action="store_true")
    parser.add_argument("--ocr-mode", choices=["batch", "individual"], default="batch")
    profile(parser.parse_args())
