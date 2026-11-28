"""诊断尺度：用人工球标注居中裁片；结果不能作为可部署检测器的召回率。"""
import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ball_recall", HERE / "ball-search-recall.py")
recall = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recall)
from vision_model import model_identity


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset", type=Path)
    p.add_argument("model", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--side", type=int, default=180)
    p.add_argument("--hz", type=int, default=10)
    args = p.parse_args()
    if args.side <= 0 or args.hz <= 0:
        p.error("裁片尺寸与频率须大于零")
    from ultralytics import YOLO, __version__
    data = json.loads(args.dataset.read_text(encoding="utf-8"))
    labels = recall.load_labels(args.dataset.parent / data["labels"])
    model = YOLO(str(args.model), task="detect")
    rows = []
    for clip in data["clips"]:
        for frame, _, image in recall.selected_images(clip, args.dataset.parent, args.hz):
            label = labels.get((clip["id"], frame))
            if not label or label["status"] != "visible" or label["far"] is not True or not label["box"] or max(label["box"][2:]) > 16:
                continue
            x, y, w, h = label["box"]
            cx, cy = x + w / 2, y + h / 2
            height, width = image.shape[:2]
            side = min(args.side, width, height)
            left = min(max(round(cx - side / 2), 0), width - side)
            top = min(max(round(cy - side / 2), 0), height - side)
            predictions = model.predict(image[top:top + side, left:left + side], imgsz=640, conf=0.35,
                                        iou=0.5, verbose=False, device="cpu", rect=False)[0]
            balls = []
            for box in predictions.boxes:
                if str(predictions.names[int(box.cls.item())]).lower() not in {"sports ball", "soccer ball", "football", "ball"}:
                    continue
                x1,y1,x2,y2 = (float(v) for v in box.xyxy[0].tolist())
                balls.append({"pixel": [left + (x1+x2)/2, top + (y1+y2)/2], "confidence": float(box.conf.item())})
            ball = max(balls, key=lambda b: b["confidence"], default=None)
            rows.append({"clip":clip["id"], "frame":frame, "label":label, "crop":[left,top,side,side],
                         "ball":ball, "hit":recall.hits(ball, label["box"], None, 8)})
    if not rows:
        raise ValueError("没有远端小球标注样本")
    hits = sum(r["hit"] for r in rows)
    result = {"diagnostic":"ground-truth-centered crop; not deployable", "datasetSha256":recall.digest(args.dataset),
              "labelsSha256":recall.digest(args.dataset.parent / data["labels"]), "modelSha256":model_identity(args.model),
              "ultralytics":__version__, "side":args.side, "confidence":0.35, "nmsIou":0.5, "samplingHz":args.hz,
              "visible":len(rows), "hits":hits, "wrongLocation":sum(r["ball"] is not None and not r["hit"] for r in rows),
              "recall":hits/len(rows), "byClip":{c:dict(Counter("hit" if r["hit"] else "miss" for r in rows if r["clip"]==c)) for c in sorted({r["clip"] for r in rows})}, "rows":rows}
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write("\n")
    print(json.dumps({k:v for k,v in result.items() if k not in ("rows", "modelSha256")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
