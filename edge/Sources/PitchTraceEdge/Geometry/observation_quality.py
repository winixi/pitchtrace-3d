"""在原始解码帧的像素坐标系中计量模型框的清晰度和人框重叠。"""

import math

import cv2
import numpy as np


def _visible_box(box, width, height):
    if box is None:
        return None
    x, y, w, h = box
    left, top = max(0, math.floor(x)), max(0, math.floor(y))
    right, bottom = min(width, math.ceil(x + w)), min(height, math.ceil(y + h))
    if right > left and bottom > top:
        return left, top, right, bottom
    return None


def _measure(image, box, people, own_index=None):
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    covered = None
    for index, other in enumerate(people):
        if index == own_index or other is None:
            continue
        x0, y0 = max(left, other[0]), max(top, other[1])
        x1, y1 = min(right, other[2]), min(bottom, other[3])
        if x1 > x0 and y1 > y0:
            if covered is None:
                covered = np.zeros((height, width), dtype=bool)
            covered[y0 - top:y1 - top, x0 - left:x1 - left] = True
    overlap = np.count_nonzero(covered) / (width * height) if covered is not None else 0.0
    quality = {"personOverlapFraction": overlap}
    if width >= 4 and height >= 4:
        gray = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
        # 只计四邻域全部来自目标框内的像素，避免裁片边缘影响清晰度。
        response = cv2.Laplacian(gray, cv2.CV_16S)
        _, deviation = cv2.meanStdDev(response[1:-1, 1:-1])
        quality["laplacianVariance"] = float(deviation[0, 0]) ** 2
    return quality


def add_model_quality(image, result, *, model_people, search_box):
    height, width = image.shape[:2]
    players = result["players"]
    boxes = [_visible_box(player.get("box"), width, height) for player in players]
    if model_people:
        for index, box in enumerate(boxes):
            if box is not None:
                players[index]["quality"] = _measure(image, box, boxes, index)
    ball = result.get("ball")
    if ball is not None and ball.get("source") in ("coreML", "coreML-ROI"):
        box = _visible_box(ball.get("box"), width, height)
        if box is not None:
            ball["quality"] = _measure(image, box, boxes)
        if ball["source"] == "coreML-ROI" and search_box is not None:
            ball["searchBox"] = list(search_box)
