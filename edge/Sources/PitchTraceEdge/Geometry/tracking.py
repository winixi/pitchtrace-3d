"""将同一曝光窗口的二维人球观测融合为 v2 场地轨迹。"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
import math

import cv2
import numpy as np

from camera_geometry import _optics, _undistort, triangulate


# 单位米、像素和微秒。缺测只从最后一次实测起外推，避免累计漂移。
FOOT_GATE_M = 1.25
BALL_RMS_PX = 3.0
PREDICTION_US = 300_000
REACQUIRE_US = 1_000_000
PLAYER_LIMIT = 32
# 三路及以上的足点组中，偏离中位数超过此距离的视角视为离群。
FOOT_OUTLIER_M = 0.75
# 轨迹与观测的号码均已读出且不一致时加到匹配代价上，OCR 可能出错，故不直接禁配。
NUMBER_MISMATCH_M = 1.5


def assign(costs):
    """最小费用二分匹配。不可达的边填 inf，双方都允许未匹配。"""
    if not costs or not costs[0]:
        return []
    n, m = len(costs), len(costs[0])
    # 每一行可去一个虚拟列；丢掉一个匹配的代价高于所有合法边的费用总和。
    maximum = max((v for row in costs for v in row if math.isfinite(v)), default=0)
    penalty = (1 + maximum) * (1 + n)
    size = m + n
    u = [0.] * (n + 1)
    v = [0.] * (size + 1)
    owner = [0] * (size + 1)
    parent = [0] * (size + 1)
    for i in range(1, n + 1):
        owner[0] = i
        column = 0
        distance = [math.inf] * (size + 1)
        visited = [False] * (size + 1)
        while owner[column]:
            visited[column] = True
            row = owner[column]
            delta = math.inf
            next_column = 0
            for j in range(1, size + 1):
                if visited[j]:
                    continue
                cost = costs[row - 1][j - 1] if j <= m else penalty
                reduced = cost - u[row] - v[j]
                if reduced < distance[j]:
                    distance[j], parent[j] = reduced, column
                if distance[j] < delta:
                    delta, next_column = distance[j], j
            for j in range(size + 1):
                if visited[j]:
                    u[owner[j]] += delta
                    v[j] -= delta
                else:
                    distance[j] -= delta
            column = next_column
        while column:
            previous = parent[column]
            owner[column] = owner[previous]
            column = previous
    return [(owner[j] - 1, j - 1) for j in range(1, m + 1) if owner[j]]


def pixel_at(value, size):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2 or not all(
        type(x) in (int, float) and math.isfinite(x) for x in value
    ):
        raise ValueError("像素坐标需要两个有限数值")
    if not (0 <= value[0] < size[0] and 0 <= value[1] < size[1]):
        raise ValueError("观测像素超出原图尺寸")
    return np.array(value, dtype=float)


def confidence(observation):
    value = observation.get("confidence", 1.0)
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("观测置信度必须在 0 到 1 之间")
    return value


def in_scene(point):
    return np.isfinite(point).all() and np.max(np.abs(point)) <= 200 and point[1] >= 0


class Camera:
    def __init__(self, report):
        self.report = report
        self.matrix, self.distortion = _optics(report)
        self.rotation = np.array(report["rotationWorldToCamera"], dtype=float)
        self.translation = np.array(report["translationM"], dtype=float)
        self.center = -self.rotation.T @ self.translation

    def ground(self, pixel):
        try:
            uv = _undistort(pixel, self.matrix, self.distortion)[0]
        except (ValueError, cv2.error):
            return None
        ray = self.rotation.T @ np.array([*uv, 1.])
        if abs(ray[1]) < 1e-8:
            return None
        depth = -self.center[1] / ray[1]
        point = self.center + depth * ray
        point[1] = 0
        return point if depth > 0 and in_scene(point) else None


@dataclass
class Foot:
    position: np.ndarray
    reference: dict
    quality: float


def group_position(group):
    return np.average([foot.position for foot in group], axis=0, weights=[foot.quality for foot in group])


def team(group):
    values = {foot.reference["player"].get("teamId") for foot in group} - {None}
    return next(iter(values)) if len(values) == 1 else None


def number(group):
    """组内置信度总和最高的号码，没有读出时为 None。"""
    weights = {}
    for foot in group:
        value = foot.reference["player"].get("number")
        if value is not None:
            weights[value] = weights.get(value, 0.) + foot.quality
    if not weights:
        return None
    best = max(weights.values())
    winners = [v for v, w in weights.items() if w == best]
    return winners[0] if len(winners) == 1 else None


def reject_outliers(group):
    """三个及以上视角时剔除偏离中位足点过远的视角，保留至少两个。"""
    if len(group) < 3:
        return group
    median = np.median([foot.position for foot in group], axis=0)
    kept = [foot for foot in group if math.dist(foot.position, median) <= FOOT_OUTLIER_M]
    return kept if len(kept) >= 2 else group


def cost_to_group(group, foot):
    known_team = team(group)
    other = foot.reference["player"].get("teamId")
    if known_team and other and other != known_team:
        return math.inf
    distances = [math.dist(member.position, foot.position) for member in group]
    return sum(distances) / len(group) if max(distances) <= FOOT_GATE_M else math.inf


def collect(cameras, rows):
    groups = []
    ball_views = []
    seen = set()
    for row in sorted(rows, key=lambda item: item["cameraId"]):
        name = row["cameraId"]
        if name in seen:
            raise ValueError(f"曝光窗口中机位 {name} 重复")
        seen.add(name)
        if name not in cameras:
            continue
        camera = Camera(cameras[name])
        size = camera.report["imageSize"]
        if row["imageSize"] != size:
            raise ValueError(f"机位 {name} 的观测与标定分辨率不符")
        reference = {key: row[key] for key in ("cameraId", "captureUtcUs", "segment", "pts", "mediaEpoch") if key in row}
        feet = []
        for player in row["players"]:
            quality = confidence(player)
            pixel = pixel_at(player.get("foot"), size)
            if pixel is None or quality == 0:
                continue
            position = camera.ground(pixel)
            if position is not None:
                feet.append(Foot(position, {**reference, "player": player}, quality))
        feet.sort(key=lambda f: (-f.quality, tuple(f.position)))
        feet = feet[:PLAYER_LIMIT]
        pairs = assign([[cost_to_group(group, foot) for foot in feet] for group in groups])
        matched = set()
        for i, j in pairs:
            groups[i].append(feet[j])
            matched.add(j)
        groups.extend([foot] for j, foot in enumerate(feet) if j not in matched)

        ball = row.get("ball")
        if ball is not None and confidence(ball) > 0:
            pixel = pixel_at(ball.get("pixel"), size)
            if pixel is not None:
                view = (camera.report, camera.rotation, camera.translation, pixel)
                ball_views.append(({**reference, "ball": ball}, view, confidence(ball)))
    return [reject_outliers(group) for group in groups], ball_views


def measure_ball(views):
    # 从支持视角数最多的组合开始。三、四路中存在一个离群像点时可退到一致的子集。
    minimum = max(2, len(views) // 2 + 1)
    for count in range(len(views), minimum - 1, -1):
        fits = []
        for subset in combinations(views, count):
            result = triangulate([view for _, view, _ in subset], 1.0)
            if result["status"] != "measured" or result["reprojectionRmsPx"] > BALL_RMS_PX:
                continue
            point = np.array(result["reconstructedM"])
            if -1e-9 <= point[1] < 0:
                point[1] = 0
            if in_scene(point):
                fits.append((result["reprojectionRmsPx"], point, subset))
        if fits:
            fits.sort(key=lambda item: item[0])
            rms, point, subset = fits[0]
            # 多个几何解释同样吻合却指向相距很远的球心，保留缺测。
            if any(err <= rms + .5 and math.dist(other, point) > .5 for err, other, _ in fits[1:]):
                return None
            quality = min(q for _, _, q in subset) / (1 + rms / BALL_RMS_PX)
            return point, [reference["cameraId"] for reference, _, _ in subset], quality
    return None


@dataclass
class Motion:
    position: np.ndarray
    time: int
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(3))
    quality: float = 1.
    team_id: str | None = None
    number: int | None = None

    def extrapolate(self, utc):
        seconds = min((utc - self.time) / 1e6, PREDICTION_US / 1e6)
        return self.position + seconds * self.velocity

    def update(self, point, utc, speed_cap):
        velocity = (point - self.position) / ((utc - self.time) / 1e6)
        speed = np.linalg.norm(velocity)
        self.velocity = velocity * min(1., speed_cap / max(speed, 1e-8))
        self.position, self.time = point, utc

    def missing(self, utc):
        age = utc - self.time
        point = self.extrapolate(utc)
        if age <= PREDICTION_US and in_scene(point):
            return {"state": "predicted", "positionM": point.tolist(), "views": [],
                    "confidence": self.quality / (1 + age / PREDICTION_US)}
        return {"state": "lost", "positionM": None, "views": []}


def track_cost(track, point, team_id, utc, number_value=None):
    if track.team_id and team_id and track.team_id != team_id:
        return math.inf
    delta = (utc - track.time) / 1e6
    gap = math.dist(track.extrapolate(utc), point)
    if gap > min(4., .65 + 12 * delta):
        return math.inf
    if track.number is not None and number_value is not None and track.number != number_value:
        gap += NUMBER_MISMATCH_M
    return gap


class Tracker:
    def __init__(self):
        self.tracks = {}
        self.last_feet = {}
        self.next_id = 1
        self.ball = None
        self.last_ball_views = []
        self.utc = None

    def reset_geometry(self, affected=None):
        """标定切换后仅沿用来源仍有效的实测，轨迹编号始终向前。"""
        if affected is None:
            self.tracks.clear()
            self.last_feet.clear()
            self.ball = None
            self.last_ball_views = []
            return
        if not affected:
            return
        for key, old in list(self.last_feet.items()):
            remaining = [f for f in old if f.reference["cameraId"] not in affected]
            if not remaining:
                del self.tracks[key]
                del self.last_feet[key]
                continue
            track = self.tracks[key]
            track.velocity = np.zeros(3)
            track.position = group_position(remaining)
            track.quality = sum(f.quality for f in remaining) / len(remaining)
            self.last_feet[key] = remaining
        self.last_ball_views = [view for view in self.last_ball_views if view[0]["cameraId"] not in affected]
        measurement = measure_ball(self.last_ball_views)
        if measurement is None:
            self.ball = None
            self.last_ball_views = []
        else:
            point, names, quality = measurement
            self.ball = Motion(point, self.ball.time, quality=quality)
            self.last_ball_views = [view for view in self.last_ball_views if view[0]["cameraId"] in names]

    def update(self, utc_us, cameras, rows):
        if self.utc is not None and utc_us <= self.utc:
            raise ValueError("跟踪曝光时间必须严格递增")
        groups, ball_views = collect(cameras, rows)
        groups.sort(key=lambda group: (-len(group), -sum(f.quality for f in group), tuple(group_position(group))))
        groups = groups[:PLAYER_LIMIT]
        points = [group_position(group) for group in groups]
        teams = [team(group) for group in groups]
        numbers = [number(group) for group in groups]
        active = {k: v for k, v in self.tracks.items() if utc_us - v.time <= REACQUIRE_US}
        track_ids = sorted(active)
        pairs = assign([[track_cost(active[k], p, t, utc_us, n) for p, t, n in zip(points, teams, numbers)] for k in track_ids])
        labels = {j: track_ids[i] for i, j in pairs}
        people, evidence = {}, {}
        for j, group in enumerate(groups):
            point = points[j]
            track_id = labels.get(j)
            if track_id is None:
                track_id = f"p-{self.next_id:06d}"
                self.next_id += 1
                active[track_id] = Motion(point, utc_us)
            else:
                active[track_id].update(point, utc_us, 12.)
            track = active[track_id]
            track.team_id = teams[j] or track.team_id
            track.number = numbers[j] if numbers[j] is not None else track.number
            track.quality = sum(foot.quality for foot in group) / len(group)
            self.last_feet[track_id] = group
            evidence[track_id] = [foot.reference for foot in group]
            people[track_id] = {"trackId": track_id, "state": "measured", "positionM": point.tolist(),
                                "views": [f.reference["cameraId"] for f in group], "confidence": track.quality}

        # 人数达协议上限时先保留实测，其余名额按最近实测时刻给待重关联的轨迹。
        missing = sorted(active.keys() - people.keys(), key=lambda k: (-active[k].time, k))
        for key in missing[:PLAYER_LIMIT - len(people)]:
            people[key] = {"trackId": key, **active[key].missing(utc_us)}
        self.tracks = {key: active[key] for key in people}
        self.last_feet = {key: self.last_feet[key] for key in people}

        measurement = measure_ball(ball_views)
        if measurement is not None and self.ball is not None:
            elapsed = (utc_us - self.ball.time) / 1e6
            if elapsed <= PREDICTION_US / 1e6 and math.dist(self.ball.position, measurement[0]) > 1 + elapsed * 60:
                measurement = None
        if measurement is not None:
            point, names, quality = measurement
            if self.ball is None or utc_us - self.ball.time > PREDICTION_US:
                self.ball = Motion(point, utc_us)
            else:
                self.ball.update(point, utc_us, 60.)
            self.ball.quality = quality
            self.last_ball_views = [view for view in ball_views if view[0]["cameraId"] in names]
            ball = {"state": "measured", "positionM": point.tolist(), "views": names, "confidence": quality}
        elif self.ball is not None:
            ball = self.ball.missing(utc_us)
        else:
            ball = {"state": "lost", "positionM": None, "views": []} if ball_views else None
        if ball_views:
            evidence["_ball"] = [reference for reference, _, _ in ball_views]
            if ball["state"] == "lost":
                ball["views"] = [reference["cameraId"] for reference in evidence["_ball"]]
        self.utc = utc_us
        return [people[k] for k in sorted(people)], ball, evidence
