"""从单机位的实测球心或当前画面选择一次原始分辨率足球精搜。"""

from collections import deque
import os
import math
from time import perf_counter_ns

import cv2
import numpy as np

from tracking import PREDICTION_US


def appearance_candidates(image):
    """返回有限个当前曝光的小型亮斑 [中心 x, 中心 y, 直径, 外观分]。"""
    height, width = image.shape[:2]
    if min(width, height) < 32:
        return []
    # 在缩略图寻找周围被草地包围的小型亮斑。远端的白线也很亮，
    # 长宽比、填充率及邻域草地比例用来排除线段、广告牌和大片球衣。
    scale = min(1.0, 960 / max(width, height))
    sw, sh = max(1, round(width * scale)), max(1, round(height * scale))
    thumb = cv2.resize(image, (sw, sh), interpolation=cv2.INTER_AREA)
    blue, green, red = cv2.split(thumb)
    grass = (cv2.subtract(green, red) > 3) & (cv2.subtract(green, blue) > 5)
    gray = cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY)
    contrast = cv2.subtract(gray, cv2.blur(gray, (19, 19)))
    foreground = ((contrast >= 22) & ~grass).astype(np.uint8)
    contours, _ = cv2.findContours(foreground, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        diameter = max(w, h)
        if not 2 <= diameter <= 15 or min(w, h) < diameter * .52:
            continue
        area = cv2.contourArea(contour)
        fill = area / (w * h)
        if fill < .32:
            continue
        margin = max(4, diameter)
        l, t = max(0, x - margin), max(0, y - margin)
        r, b = min(sw, x + w + margin), min(sh, y + h + margin)
        background = grass[t:b, l:r]
        surrounding = (np.count_nonzero(background) - np.count_nonzero(grass[y:y + h, x:x + w])) / max(
            1, background.size - w * h)
        if surrounding < .58:
            continue
        perimeter = cv2.arcLength(contour, True)
        circularity = 4 * math.pi * area / (perimeter * perimeter)
        core = gray[y:y + h, x:x + w]
        signal = contrast[y:y + h, x:x + w]
        peak = int(core.max())
        if peak < 130:
            continue
        score = (surrounding * circularity * min(w, h) / diameter
                 * (float(signal.max()) / 160) * (peak / 230))
        candidates.append([round((x + w / 2) * width / sw, 1), round((y + h / 2) * height / sh, 1),
                           round(diameter / scale, 1), round(float(score), 3)])
    candidates.sort(key=lambda item: item[3], reverse=True)
    return candidates[:48]


def cold_region(size, utc_us, candidates, moving):
    width, height = size
    if min(width, height) < 32:
        return None
    side = min(min(width, height), max(128, round(min(width, height) / 6)))
    slot = utc_us // 100_000
    if moving is not None:
        x, y = moving[0][:2]
    elif candidates and slot % 8 != 0:
        # 轮换分数靠前且裁片互不覆盖的区域，避免被某一静态标记持续占用。
        distinct = []
        for x, y, _, _ in candidates:
            if all(max(abs(x - px), abs(y - py)) >= side / 2 for px, py in distinct):
                distinct.append((x, y))
                if len(distinct) == 3:
                    break
        x, y = distinct[slot % len(distinct)]
    else:
        # 无典型亮斑（如暗色球）时，按曝光时隙遍历全画幅，包含空中的球。
        cols, rows = (width + side - 1) // side, (height + side - 1) // side
        index = (slot // 8 if candidates else slot) % (cols * rows)
        x = ((index % cols) + .5) * width / cols
        y = ((index // cols) + .5) * height / rows
    return [min(max(0, round(x - side / 2)), width - side),
            min(max(0, round(y - side / 2)), height - side), side, side]


def motion_candidate(candidates, samples, utc):
    """只确认三个相邻曝光里有真实亮斑、尺寸相近且运动一致的唯一候选。"""
    if len(samples) < 2 or not candidates:
        return None
    (t0, older), (t1, previous) = samples
    if not (0 < utc - t1 <= PREDICTION_US and 0 < t1 - t0 <= PREDICTION_US):
        return None
    ratio = (utc - t1) / (t1 - t0)
    if not .4 <= ratio <= 2.5:
        return None
    # 多个独立亮斑一起平移时，用它们的共同位移剔除相机抖动。
    offsets = []
    for x, y, d, _ in candidates:
        neighbors = [(math.hypot(x - a, y - b), x - a, y - b) for a, b, da, _ in previous
                     if .7 <= da / d <= 1.4 and abs(x - a) <= 24 and abs(y - b) <= 24]
        if neighbors:
            _, dx, dy = min(neighbors)
            offsets.append((dx, dy))
    camera_shift = None
    if len(offsets) >= 4:
        dx, dy = np.median(offsets, axis=0)
        agreeing = sum(math.hypot(ax - dx, ay - dy) <= 2 for ax, ay in offsets)
        if math.hypot(dx, dy) >= 2 and agreeing >= .65 * len(offsets):
            camera_shift = (dx, dy)
    matches = []
    for current in candidates:
        x, y, d, strength = current
        if strength < .25:
            continue
        # 固定球场标记与镜头轻微抖动可在原地再次出现。
        if any(math.hypot(x - ax, y - ay) < max(2, d * .30) for ax, ay, _, _ in previous):
            continue
        best = 0
        for ax, ay, da, sa in previous:
            dx, dy = x - ax, y - ay
            step = math.hypot(dx, dy)
            if camera_shift is not None and math.dist((dx, dy), camera_shift) <= max(2, d * .3):
                continue
            if not (.67 <= da / d <= 1.5 and step <= d * 60 * (utc - t1) / 1_000_000):
                continue
            limit = max(2, d * .38, step * .20)
            for bx, by, db, sb in older:
                if not .67 <= db / d <= 1.5:
                    continue
                error = math.hypot(dx - (ax - bx) * ratio, dy - (ay - by) * ratio)
                if error <= limit:
                    best = max(best, min(strength, sa, sb) / (1 + error / limit))
        if best >= .25:
            matches.append((best, current))
    matches.sort(key=lambda match: match[0], reverse=True)
    if not matches or (len(matches) > 1 and matches[1][0] >= .7 * matches[0][0]):
        return None
    strength, candidate = matches[0]
    return candidate, strength


def outside_players(candidate, players):
    x, y, d, _ = candidate
    for player in players:
        l, t, w, h = player["box"]
        if l - d <= x <= l + w + d and t - d <= y <= t + h + d:
            return False
    return True


class BallSearch:
    def __init__(self):
        self.trails = {}
        self.appearances = {}
        self.timing = os.environ.get("PITCHTRACE_VISION_TIMING") == "1"

    def _trail(self, version, row, size):
        camera = row["cameraId"]
        context = (version, row.get("mediaEpoch"), tuple(size))
        previous = self.trails.get(camera)
        if previous is None or previous[0] != context:
            self.trails[camera] = context, deque(maxlen=2)
            self.appearances[camera] = deque(maxlen=2)
        return self.trails[camera][1]

    def record(self, version, row):
        points = self._trail(version, row, row["imageSize"])
        utc = int(row["captureUtcUs"])
        candidates = (row.get("ballSearch") or {}).get("candidates", [])
        self.appearances[row["cameraId"]].append((utc, candidates))
        if points and not 0 < utc - points[-1][0] <= PREDICTION_US:
            points.clear()
        # 输入来自二维检测证据，包含显式启用的球色检测。三维预测不会续期。
        if row["ball"] is not None:
            points.append((utc, *row["ball"]["pixel"]))

    def region(self, version, row, size):
        points = self._trail(version, row, size)
        if not points:
            return None
        last, x, y = points[-1]
        age = int(row["captureUtcUs"]) - last
        if not 0 < age <= PREDICTION_US:
            return None
        if len(points) == 2:
            earlier, old_x, old_y = points[0]
            ratio = age / (last - earlier)
            x += (x - old_x) * ratio
            y += (y - old_y) * ratio

        # 丢测期间由原图短边的四分之一扩至四分之三；靠边时平移裁片以保留上下文。
        width, height = size
        side = max(1, round(min(size) * (.25 + .5 * age / PREDICTION_US)))
        left = min(max(0, round(x - side / 2)), width - side)
        top = min(max(0, round(y - side / 2)), height - side)
        return [left, top, side, side]

    def observe(self, detector, version, row, image):
        size = list(image.shape[1::-1])
        region = self.region(version, row, size)
        mode = "history" if region is not None else "cold"
        start = perf_counter_ns() if self.timing else None
        utc = int(row["captureUtcUs"])
        candidates = appearance_candidates(image)
        samples = self.appearances[row["cameraId"]]
        moving = motion_candidate(candidates, samples, utc)
        if region is None:
            region = cold_region(size, utc, candidates, moving)
        proposal_ns = perf_counter_ns() - start if start is not None else 0
        result = detector.observe(image, region)
        now = perf_counter_ns() if self.timing else None
        ball = result["ball"]
        if ball is not None and ball.get("source") == "coreML-ROI":
            ball["searchMode"] = mode
        eligible = [c for c in candidates if outside_players(c, result["players"])]
        if ball is None and len(eligible) != len(candidates):
            moving = motion_candidate(eligible, samples, utc)
        if ball is None and moving is not None and region is not None:
            candidate, motion_strength = moving
            x, y, d, appearance = candidate
            l, t, w, h = region
            if l <= x < l + w and t <= y < t + h:
                left, top = max(0, x - d / 2), max(0, y - d / 2)
                right, bottom = min(size[0], x + d / 2), min(size[1], y + d / 2)
                result["ball"] = {"source": "motion-appearance", "searchMode": mode, "pixel": [x, y],
                                  "box": [left, top, right - left, bottom - top], "searchBox": region,
                                  "quality": {"appearanceScore": float(appearance), "motionScore": float(motion_strength)},
                                  "confidence": float(min(.9, motion_strength))}
        if now is not None and "timing" in result:
            result["timing"]["proposalNs"] = proposal_ns + perf_counter_ns() - now
        result["ballSearch"] = ({"mode": mode, "region": region, "candidates": eligible,
                                 "ran": ball is None or ball.get("source") != "coreML"}
                                if region is not None else None)
        return result
