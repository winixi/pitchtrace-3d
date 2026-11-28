"""用相同标注画面对照 YOLO 原始权重和 Core ML 检测结果。"""

import argparse
from collections import Counter
import importlib.util
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ball_recall", HERE / "ball-search-recall.py")
recall = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recall)
from vision_model import model_identity

BALL_NAMES = {"sports ball", "soccer ball", "football", "ball"}


def normalized(result):
    """保留人球检测框；坐标统一为原图左上角 xywh。"""
    rows = []
    for box in result.boxes:
        name = str(result.names[int(box.cls.item())]).lower()
        category = "person" if name == "person" else "ball" if name in BALL_NAMES else None
        if category is None:
            continue
        x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
        rows.append({"class": category, "box": [x1, y1, x2 - x1, y2 - y1],
                     "confidence": float(box.conf.item())})
    return rows


def iou(a, b):
    x, y, w, h = a
    u, v, s, t = b
    intersection = max(0, min(x + w, u + s) - max(x, u)) * max(0, min(y + h, v + t) - max(y, v))
    union = w * h + s * t - intersection
    return intersection / union if union > 0 else 0


def agreement(before, after, threshold):
    # 贪心按 IoU 从高到低配对，保留未匹配项以便逐帧复核。
    pairs = sorted(((iou(a["box"], b["box"]), i, j) for i, a in enumerate(before)
                    for j, b in enumerate(after) if a["class"] == b["class"]), reverse=True)
    used_a, used_b, matches = set(), set(), []
    for overlap, i, j in pairs:
        if overlap < threshold:
            break
        if i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
            matches.append({"before": i, "after": j, "iou": overlap,
                            "confidenceDelta": after[j]["confidence"] - before[i]["confidence"]})
    return {"matches": matches, "beforeOnly": [i for i in range(len(before)) if i not in used_a],
            "afterOnly": [i for i in range(len(after)) if i not in used_b]}


def score(detections, label, radius):
    balls = [x for x in detections if x["class"] == "ball"]
    ball = max(balls, key=lambda x: x["confidence"], default=None)
    if label is None or label["status"] == "ignore":
        return "unscored"
    if label["status"] == "absent":
        return "falsePositive" if ball else "trueNegative"
    if not ball:
        return "miss"
    x, y, w, h = ball["box"]
    hit = recall.hits({"pixel": [x + w / 2, y + h / 2]}, label["box"], label["center"], radius)
    return "hit" if hit else "wrongLocation"


def summarize(rows, side, group):
    subset = [r for r in rows if r["label"] and r["label"]["status"] == "visible" and
              (group == "all" or r["label"]["far"] is True) and
              (group != "farSmall" or (r["label"]["box"] and max(r["label"]["box"][2:]) <= 16))]
    counts = Counter(r[side + "Score"] for r in subset)
    return {"visible": len(subset), "hits": counts["hit"], "misses": counts["miss"],
            "wrongLocation": counts["wrongLocation"], "recall": counts["hit"] / len(subset) if subset else None}


def run(args):
    from ultralytics import YOLO, __version__
    from importlib.metadata import version
    data = json.loads(args.dataset.read_text(encoding="utf-8"))
    labels = recall.load_labels(args.dataset.parent / data["labels"])
    if not data.get("clips") or len({c["id"] for c in data["clips"]}) != len(data["clips"]):
        raise ValueError("数据集须包含 ID 不重复的片段")
    for model in (args.before, args.after):
        if not model.exists():
            raise ValueError(f"模型不存在：{model}")
    before, after = YOLO(str(args.before), task="detect"), YOLO(str(args.after), task="detect")
    rows = []
    for clip in data["clips"]:
        for frame, time_us, image in recall.selected_images(clip, args.dataset.parent, args.hz):
            key = clip["id"], frame
            label = labels.get(key)
            outputs = []
            for model in (before, after):
                result = model.predict(image, imgsz=args.imgsz, conf=args.conf, iou=args.nms_iou,
                                       verbose=False, device="cpu", rect=False)[0]
                outputs.append(normalized(result))
            a, b = outputs
            rows.append({"clip": key[0], "frame": frame, "timeUs": time_us,
                         "imageSize": list(image.shape[1::-1]), "label": label,
                         "before": a, "after": b, "beforeScore": score(a, label, args.radius_px),
                         "afterScore": score(b, label, args.radius_px),
                         "agreement": agreement(a, b, args.match_iou)})
    if not rows:
        raise ValueError("数据集没有采样画面")
    summary = {side: {group: summarize(rows, side, group) for group in ("all", "far", "farSmall")}
               for side in ("before", "after")}
    for side in ("before", "after"):
        summary[side]["outcomes"] = dict(Counter(r[side + "Score"] for r in rows))
    summary["agreement"] = {key: sum(len(row["agreement"][key]) for row in rows)
                            for key in ("matches", "beforeOnly", "afterOnly")}
    report = {"dataset": str(args.dataset.resolve()), "datasetSha256": recall.digest(args.dataset),
              "labelsSha256": recall.digest(args.dataset.parent / data["labels"]),
              "models": {"before": {"path": str(args.before.resolve()), "sha256": model_identity(args.before)},
                         "after": {"path": str(args.after.resolve()), "sha256": model_identity(args.after)}},
              "versions": {"ultralytics": __version__, "torch": version("torch"), "coremltools": version("coremltools"), "numpy": version("numpy"), "opencv": version("opencv-python")}, "settings": {"hz": args.hz, "imgsz": args.imgsz, "confidence": args.conf,
              "nmsIou": args.nms_iou, "matchIou": args.match_iou, "radiusPx": args.radius_px, "torchDevice": "cpu",
              "coreMLComputeUnits": "Ultralytics 默认（CPU_AND_NE，加载失败时 CPU_ONLY）"},
              "frames": len(rows), "summary": summary, "rows": rows}
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write("\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset", type=Path, help="球标注数据集 JSON")
    p.add_argument("before", type=Path, help="原始 .pt 权重")
    p.add_argument("after", type=Path, help="转换后的 .mlpackage")
    p.add_argument("output", type=Path, help="新建 JSON 报告")
    p.add_argument("--hz", type=int, default=10)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--conf", type=float, default=0.35)
    p.add_argument("--nms-iou", type=float, default=0.7)
    p.add_argument("--match-iou", type=float, default=0.5)
    p.add_argument("--radius-px", type=float, default=8)
    args = p.parse_args()
    if args.before.suffix != ".pt" or args.after.suffix != ".mlpackage":
        p.error("依次提供 .pt 和 .mlpackage 模型")
    if args.hz <= 0 or args.imgsz <= 0 or not all(math.isfinite(v) and 0 <= v <= 1 for v in (args.conf, args.nms_iou, args.match_iou)) or not math.isfinite(args.radius_px) or args.radius_px <= 0:
        p.error("频率、尺寸、半径须为正数，阈值须在 0–1 内")
    try:
        run(args)
    except (ValueError, OSError, KeyError) as exc:
        p.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
