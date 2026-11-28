"""从连续比赛画面按 10 Hz 配对运行 Edge 精搜开关，保存逐帧二维证据。

清单包含 clips 和 labels。每个 clip 填 id、fps、firstFrame、stopFrame（不含）和
images（如 img/{frame:06d}.jpg）或 video。视频的第 0 张解码帧可用 videoFrameZero
标明对应的原始帧号。labels 为 CSV，列为 clip,frame,status。visible 需原图像素框
x,y,w,h 或独立球心 cx,cy，可附 far=0/1 表示事先划定的远端场区。absent 为明确
无可见球，ignore 或缺行表示无法评分。路径相对于清单。
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
import csv
from fractions import Fraction
import gzip
import io
import json
import math
from pathlib import Path
import sys
from threading import Lock

import cv2

EDGE = Path(__file__).resolve().parents[2]
GEOMETRY = EDGE / "Sources/PitchTraceEdge/Geometry"
sys.path.insert(0, str(GEOMETRY))
from ball_search import BallSearch
from observations import Detector, validate_observation
from vision_model import digest, load_vision_model


def load_labels(path):
    labels = {}
    with path.open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            key = row["clip"], int(row["frame"])
            status = row["status"]
            if key in labels or status not in ("visible", "absent", "ignore"):
                raise ValueError(f"重复或无效标注 {key}")
            box = point = None
            if status == "visible":
                if row.get("w"):
                    box = [float(row[k]) for k in ("x", "y", "w", "h")]
                    if not all(map(math.isfinite, box)) or min(box[2:]) <= 0:
                        raise ValueError(f"无效球框 {key}")
                else:
                    point = [float(row[k]) for k in ("cx", "cy")]
                    if not all(map(math.isfinite, point)):
                        raise ValueError(f"无效球心 {key}")
            far = row.get("far", "")
            if far not in ("", "0", "1"):
                raise ValueError(f"无效远端标记 {key}")
            distance = row.get("cameraDistanceM")
            labels[key] = {"status": status, "box": box, "center": point,
                           "far": far == "1" if far else None,
                           "cameraDistanceM": float(distance) if distance else None,
                           "reason": row.get("reason") or None}
    return labels


def selected_images(clip, base, hz):
    fps = Fraction(str(clip["fps"]))
    first, stop = clip["firstFrame"], clip["stopFrame"]
    if fps <= 0 or stop <= first or ("video" in clip) == ("images" in clip):
        raise ValueError(f"无效片段 {clip['id']}")
    cap = None
    try:
        if "video" in clip:
            cap = cv2.VideoCapture(str(base / clip["video"]), cv2.CAP_FFMPEG)
            offset = first - clip.get("videoFrameZero", 0)
            if not cap.isOpened() or offset < 0 or (offset and not cap.set(cv2.CAP_PROP_POS_FRAMES, offset)):
                raise ValueError(f"无法打开或定位录像 {clip['video']}")
            if not math.isclose(cap.get(cv2.CAP_PROP_FPS), float(fps), abs_tol=0.001):
                raise ValueError(f"片段 {clip['id']} 的源帧率与清单不符")
        last_slot = -1
        for frame in range(first, stop):
            if cap is not None and not cap.grab():
                raise ValueError(f"录像提前结束 {clip['id']} {frame}")
            # 与 Edge 单路选帧一致，取每个采样时隙内的第一张源帧。
            time_us = round((frame - first) * 1_000_000 / fps)
            slot = time_us * hz // 1_000_000
            if slot == last_slot:
                continue
            last_slot = slot
            if cap is None:
                image = cv2.imread(str(base / clip["images"].format(frame=frame)),
                                   cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
            else:
                success, image = cap.retrieve()
                if not success:
                    image = None
            if image is None:
                raise ValueError(f"无法读取 {clip['id']} 第 {frame} 帧")
            yield frame, time_us, image
    finally:
        if cap is not None:
            cap.release()


def point_in_box(point, box):
    if box is None:
        return False
    x, y = point
    left, top, w, h = box
    return left <= x < left + w and top <= y < top + h


def hits(ball, box, point, radius):
    if ball is None:
        return False
    if point is not None:
        return math.dist(ball["pixel"], point) <= radius
    return point_in_box(ball["pixel"], box)


def search_statistics(rows):
    counts = {}
    for r in rows:
        event = r["search"]
        if event is None:
            continue
        stats = counts.setdefault(event["mode"], {"regions": 0, "executed": 0, "truthCenterCovered": 0,
                                                  "returned": 0, "hits": 0, "wrongLocationOnVisible": 0,
                                                  "detectionOnAbsent": 0, "unscoredDetections": 0,
                                                  "outcomesBySource": {}})
        stats["regions"] += event.get("region") is not None
        if not event["ran"]:
            continue
        stats["executed"] += 1
        center = r["center"]
        if r["box"] is not None:
            x, y, w, h = r["box"]
            center = [x + w / 2, y + h / 2]
        if center is not None and point_in_box(center, event["region"]):
            stats["truthCenterCovered"] += 1
        if not r["on"] or r["on"]["source"] not in ("coreML-ROI", "motion-appearance"):
            continue
        source = stats["outcomesBySource"].setdefault(r["on"]["source"], Counter())
        source["returned"] += 1
        stats["returned"] += 1
        if r["onHit"]:
            outcome = "hits"
        elif r["status"] == "visible":
            outcome = "wrongLocationOnVisible"
        elif r["status"] == "absent":
            outcome = "detectionOnAbsent"
        else:
            outcome = "unscoredDetections"
        stats[outcome] += 1
        source[outcome] += 1
    return counts


def save_scores(path, rows, limit):
    # 允许复核分母、配对命中和误检，公开的评分记录不携带人工坐标或图像。
    with io.StringIO(newline="") as buffer:
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["clip", "frame", "status", "small", "far", "offSource", "offHit",
                         "onSource", "onHit", "searchMode", "searchRan"])
        for r in rows:
            box, event = r["box"], r["search"]
            writer.writerow([r["clip"], r["frame"], r["status"], int(max(box[2:]) <= limit) if box else "",
                             int(r["far"]) if r["far"] is not None else "",
                             (r["off"] or {}).get("source"), int(r["offHit"]),
                             (r["on"] or {}).get("source"), int(r["onHit"]),
                             event["mode"] if event else "", int(bool(event and event["ran"]))])
        path.write_bytes(gzip.compress(buffer.getvalue().encode(), mtime=0))


def statistics(rows, limit):
    def paired(visible):
        visible = list(visible)
        counts = Counter((r["offHit"], r["onHit"]) for r in visible)
        total = sum(counts.values())
        off = counts[True, True] + counts[True, False]
        on = counts[True, True] + counts[False, True]
        return {"visible": total, "offHits": off, "onHits": on,
                "offRecall": off / total if total else None, "onRecall": on / total if total else None,
                "gain": counts[False, True], "loss": counts[True, False],
                "gainFromROI": sum(not r["offHit"] and r["onHit"] and r["on"]["source"] == "coreML-ROI"
                                   for r in visible),
                "gainFromMotionAppearance": sum(not r["offHit"] and r["onHit"] and
                                                r["on"]["source"] == "motion-appearance" for r in visible),
                "onHitsBySource": dict(Counter(r["on"]["source"] for r in visible if r["onHit"])),
                "wrongLocationBySource": dict(Counter(r["on"]["source"] for r in visible
                                                      if r["on"] is not None and not r["onHit"])),
                "searchByMode": search_statistics(visible),
                "both": counts[True, True], "neither": counts[False, False]}

    visible = [r for r in rows if r["status"] == "visible"]
    boxed = [r for r in visible if r["box"] is not None]
    result = {"sampled": len(rows), "statusCounts": dict(Counter(r["status"] for r in rows)),
              "reasonCounts": dict(Counter(r["reason"] for r in rows if r["reason"])),
              "visible": paired(visible), "small": paired(r for r in boxed if max(r["box"][2:]) <= limit),
              "larger": paired(r for r in boxed if max(r["box"][2:]) > limit),
              "farRegion": paired(r for r in visible if r["far"]),
              "farSmall": paired(r for r in boxed if r["far"] and max(r["box"][2:]) <= limit),
              "searchByMode": search_statistics(rows),
              "predictedRegions": sum(r["region"] is not None for r in rows),
              "roiSearches": sum(bool(r["search"] and r["search"]["ran"]) for r in rows),
              "roiDetections": sum(bool(r["on"] and r["on"]["source"] == "coreML-ROI") for r in rows)}
    for mode in ("off", "on"):
        result[mode + "FalsePositives"] = {
            "wrongLocationOnVisible": sum(r[mode] is not None and not r[mode + "Hit"] for r in visible),
            "detectionOnAbsent": sum(r["status"] == "absent" and r[mode] is not None for r in rows),
        }
        result[mode + "UnscoredDetections"] = sum(
            r["status"] in ("unlabeled", "ignore") and r[mode] is not None for r in rows)
    result["onUnscoredBySource"] = dict(Counter(r["on"]["source"] for r in rows
                                               if r["status"] in ("unlabeled", "ignore") and r["on"] is not None))
    return result


def evaluate_group(clips, model, labels, base, args, evidence, evidence_lock):
    per_clip = {}
    with ExitStack() as stack:
        off = stack.enter_context(closing(Detector({"ballSearch": False}, model, args.executable)))
        on = stack.enter_context(closing(Detector({"ballSearch": True}, model, args.executable)))
        off_thread = stack.enter_context(ThreadPoolExecutor(max_workers=1))
        for clip in clips:
            clip_id = clip["id"]
            search = BallSearch()
            rows = []
            for index, time_us, image in selected_images(clip, base, args.hz):
                size = list(image.shape[:2][::-1])
                key = {"cameraId": clip_id, "captureUtcUs": str(time_us), "imageSize": size,
                       "segment": 0, "pts": index}
                off_result = off_thread.submit(off.observe, image)
                on_observation = {**key, **search.observe(on, clip_id, key, image)}
                off_observation = {**key, **off_result.result()}
                validate_observation(on_observation)
                validate_observation(off_observation)
                on_ball = on_observation["ball"]
                off_ball = off_observation["ball"]
                search.record(clip_id, on_observation)
                # 标注只在两个模型结果都返回后用于评分，所有入选帧均推进搜索历史。
                label = labels.get((clip_id, index), {"status": "unlabeled", "box": None, "center": None,
                                                      "far": None, "cameraDistanceM": None, "reason": "noLabel"})
                event = on_observation.get("ballSearch")
                row = {"clip": clip_id, "frame": index, "timeUs": time_us, "imageSize": size, **label,
                       "search": event, "region": event.get("region") if event else None,
                       "off": off_ball, "on": on_ball,
                       "offHit": hits(off_ball, label["box"], label["center"], args.radius_px),
                       "onHit": hits(on_ball, label["box"], label["center"], args.radius_px)}
                with evidence_lock:
                    evidence.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                rows.append(row)
            with evidence_lock:
                evidence.flush()
            per_clip[clip_id] = rows
            print(clip_id, json.dumps(statistics(rows, args.small_px)["farSmall"]), file=sys.stderr, flush=True)
    return per_clip


def run(args):
    dataset = json.loads(args.dataset.read_text())
    base = args.dataset.parent
    labels = load_labels(base / dataset["labels"])
    ids = [clip["id"] for clip in dataset["clips"]]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("清单需要唯一的片段 ID")
    if args.hz <= 0 or args.small_px <= 0 or not math.isfinite(args.radius_px) or args.radius_px <= 0:
        raise ValueError("采样频率、小球尺寸门槛和球心容差必须为正数")
    model = load_vision_model({"modelManifest": str(args.model.resolve())}, base)
    if model.task != "person-and-ball":
        raise ValueError("需要实际比赛使用的人球联合检测模型")
    args.output.mkdir(parents=True, exist_ok=False)
    clips = dataset["clips"]
    # 每一组有两个独立原生进程。组内同时跑开关，各机位按原帧顺序推进搜索历史。
    with (args.output / "frames.ndjson").open("x", encoding="utf-8") as evidence, \
            ThreadPoolExecutor(max_workers=args.parallel_clips) as groups:
        lock = Lock()
        batches = [clips[i::args.parallel_clips] for i in range(min(args.parallel_clips, len(clips)))]
        futures = [groups.submit(evaluate_group, batch, model, labels, base, args, evidence, lock)
                   for batch in batches]
        all_clips = {}
        for future in futures:
            all_clips.update(future.result())
    all_rows = [row for name in ids for row in all_clips[name]]
    score_file = args.output / "scores.csv.gz"
    save_scores(score_file, all_rows, args.small_px)
    by_clip = {name: statistics(all_clips[name], args.small_px) for name in ids}
    report = {"model": model.provenance, "workerSha256": digest(args.executable), "opencv": cv2.__version__,
              "inferenceCodeSha256": {name: digest(GEOMETRY / name) for name in
                                      ("ball_search.py", "tracking.py", "observations.py",
                                       "observation_quality.py", "vision_model.py")},
              "datasetSha256": digest(args.dataset), "labelsSha256": digest(base / dataset["labels"]),
              "framesSha256": digest(args.output / "frames.ndjson"),
              "scoresSha256": digest(score_file),
              "videosSha256": {c["id"]: digest(base / c["video"]) for c in dataset["clips"] if "video" in c},
              "samplingHz": args.hz, "smallMaxSidePx": args.small_px, "parallelClips": args.parallel_clips,
              "boxHitRule": "center-inside-xywh", "pointHitRadiusPx": args.radius_px,
              "total": statistics(all_rows, args.small_px), "clips": by_clip}
    (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["total"], ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", type=Path, default=EDGE / ".build/models/yolo11n/yolo11n.json")
    parser.add_argument("--executable", type=Path, default=EDGE / ".build/release/pitchtrace-edge")
    parser.add_argument("--hz", type=int, default=10)
    parser.add_argument("--parallel-clips", type=int, choices=(1, 2), default=2,
                        help="同时测量的机位数，每机位使用两个开关对照工作进程")
    parser.add_argument("--small-px", type=int, default=16)
    parser.add_argument("--radius-px", type=float, default=3, help="只有独立球心标注时的欧氏距离容差")
    run(parser.parse_args())
