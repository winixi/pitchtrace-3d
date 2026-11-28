"""生成和检验原始图像坐标系内的单视角观测。"""

import json
import math
import struct
import subprocess

import cv2
import numpy as np

from calibrate import is_identifier, vector
from observation_quality import add_model_quality
from vision_model import COMPUTE_UNITS, ocr_refresh_us


def colour(value, maxima, name):
    vector(value, 3, name)
    if any(type(v) is not int or not 0 <= v <= limit for v, limit in zip(value, maxima)):
        raise ValueError(f"{name} 的颜色分量超出范围")
    return np.array(value, dtype=np.uint8)


class Detector:
    def __init__(self, settings, model, executable):
        if not isinstance(settings, dict):
            raise ValueError("detector 必须是对象")
        self.backend = settings.get("backend", "vision")
        self.model_task = model.task if model is not None else None
        ocr_mode = settings.get("ocrMode", "batch")
        if ocr_mode not in ("batch", "individual"):
            raise ValueError("detector.ocrMode 需要 batch 或 individual")
        self.ocr_hints = ocr_refresh_us(settings) is not None
        units = settings.get("computeUnits")
        if units is not None and (units not in COMPUTE_UNITS or model is None):
            raise ValueError("detector.computeUnits 需要 Core ML 模型和 " + "、".join(COMPUTE_UNITS) + " 之一")
        self.ball_search = settings.get("ballSearch", False)
        if self.backend not in ("vision", "color-ball"):
            raise ValueError("detector.backend 需要 vision 或 color-ball")
        self.ball_range = None
        if "ballHSV" in settings:
            bounds = settings["ballHSV"]
            if not isinstance(bounds, dict):
                raise ValueError("ballHSV 需要 lower 和 upper")
            self.ball_range = tuple(colour(bounds.get(key), [179, 255, 255], key) for key in ("lower", "upper"))
            if np.any(self.ball_range[0] > self.ball_range[1]):
                raise ValueError("ballHSV 下限大于上限")
        if self.backend == "color-ball" and not self.ball_range:
            raise ValueError("按球色检测需要 ballHSV")
        self.team_colors = {}
        teams = settings.get("teamColors", {})
        if not isinstance(teams, dict):
            raise ValueError("teamColors 必须按球队 ID 填写")
        for name, value in teams.items():
            if not is_identifier(name):
                raise ValueError("teamColors 球队 ID 无效")
            self.team_colors[name] = colour(value, [255, 255, 255], name).astype(float)
        self.worker = None
        if self.backend == "vision":
            command = [executable, "vision-worker"]
            if model is not None:
                command.extend(model.worker_arguments())
            if self.ball_search:
                command.append("--ball-search")
            if ocr_mode == "individual":
                command.append("--individual-ocr")
            if self.ocr_hints:
                command.append("--ocr-hints")
            if units is not None:
                command.extend(["--compute-units", units])
            self.worker = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def ball_by_colour(self, image):
        mask = cv2.inRange(cv2.cvtColor(image, cv2.COLOR_BGR2HSV), *self.ball_range)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for contour in contours:
            area = cv2.contourArea(contour)
            x, y, w, h = cv2.boundingRect(contour)
            if area < 5 or max(w, h) > min(image.shape[:2]) / 8 or min(w, h) < 0.65 * max(w, h):
                continue
            if x == 0 or y == 0 or x + w >= image.shape[1] or y + h >= image.shape[0]:
                continue
            perimeter = cv2.arcLength(contour, True)
            roundness = 4 * math.pi * area / perimeter ** 2
            if roundness >= 0.6:
                m = cv2.moments(contour)
                candidates.append({"pixel": [m["m10"] / m["m00"], m["m01"] / m["m00"]],
                                   "confidence": min(roundness, 1), "source": "ballHSV"})
        return candidates[0] if len(candidates) == 1 else None

    def observe(self, image, ball_roi=None, ocr_skip=()):
        h, w = image.shape[:2]
        result = {"imageSize": [w, h], "players": [], "ball": None}
        if self.worker:
            success, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if not success:
                raise ValueError("无法编码选中的原帧")
            if self.ball_search:
                self.worker.stdin.write(struct.pack(">4I", *(ball_roi or (0, 0, 0, 0))))
            if self.ocr_hints:
                boxes = [[round(v) for v in box] for box in ocr_skip][:64]
                self.worker.stdin.write(struct.pack(">I", len(boxes)))
                for box in boxes:
                    self.worker.stdin.write(struct.pack(">4I", *box))
            self.worker.stdin.write(struct.pack(">I", jpeg.size))
            self.worker.stdin.write(jpeg.tobytes())
            self.worker.stdin.flush()
            line = self.worker.stdout.readline()
            if not line:
                raise ValueError(f"Vision 工作进程中断，退出码 {self.worker.wait()}")
            result = json.loads(line)
            if result["imageSize"] != [w, h]:
                raise ValueError("Vision 输出尺寸与原帧不符")
            for player in result["players"]:
                if "shirtRGB" in player and self.team_colors:
                    rgb = colour(player["shirtRGB"], [255, 255, 255], "shirtRGB")
                    distances = sorted((np.linalg.norm(rgb - ref), team) for team, ref in self.team_colors.items())
                    if distances[0][0] <= 60 and (len(distances) == 1 or distances[1][0] - distances[0][0] >= 20):
                        player["teamId"] = distances[0][1]
        if result.get("ball") is None and self.ball_range:
            result["ball"] = self.ball_by_colour(image)
        if self.model_task is not None:
            add_model_quality(image, result, model_people=self.model_task == "person-and-ball",
                              search_box=ball_roi)
        return result

    def close(self):
        if self.worker:
            try:
                self.worker.stdin.close()
            except BrokenPipeError:
                pass
            self.worker.stdout.close()
            self.worker.wait()


class OcrSchedule:
    """号码经多帧投票确认后，按重读间隔跳过该轨迹在各机位的读号。

    状态只由已选原帧观测和投票结果推进，恢复时从归档观测重放可得到相同的免读号区域。
    """

    STALE_US = 250_000

    def __init__(self, refresh_us):
        self.refresh_us = refresh_us
        self.last_read = {}
        self.boxes = {}
        self.utc = None

    def hints(self, utc):
        """返回本曝光组各机位的免读号人框；上一组过旧时全部重读。"""
        if self.utc is None or utc - self.utc > self.STALE_US:
            return {}
        return {camera: [box for track, box in rows if utc - self.last_read[track] < self.refresh_us]
                for camera, rows in self.boxes.items()}

    def update(self, utc, sources, confirmed):
        self.boxes = {}
        for track, rows in sources.items():
            if track == "_ball":
                continue
            if any(not row["player"].get("ocrSkipped") for row in rows):
                self.last_read[track] = utc
            if track in confirmed and track in self.last_read:
                for row in rows:
                    self.boxes.setdefault(row["cameraId"], []).append((track, row["player"]["box"]))
        self.last_read = {t: v for t, v in self.last_read.items() if t in sources}
        self.utc = utc


def validate_observation(row):
    size = vector(row.get("imageSize"), 2, "imageSize")
    if any(type(n) is not int or n <= 0 for n in size):
        raise ValueError("imageSize 需要两个正整数")

    def pixel(value):
        p = vector(value, 2, "像点")
        if not all(0 <= v < limit for v, limit in zip(p, size)):
            raise ValueError(f"像点超出原图：{p}")

    def box(value, name):
        x, y, w, h = vector(value, 4, name)
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > size[0] + 1e-5 or y + h > size[1] + 1e-5:
            raise ValueError(f"无效{name}")

    def confidence(item):
        if "confidence" in item:
            number = item["confidence"]
            if type(number) not in (int, float) or not 0 <= number <= 1:
                raise ValueError("观测 confidence 必须在 0–1 内")

    if not is_identifier(row.get("cameraId")) or ("mediaEpoch" in row and not is_identifier(row["mediaEpoch"])):
        raise ValueError("观测原帧的机位或媒体周期无效")
    if type(row.get("pts")) is not int or type(row.get("segment")) is not int or row["segment"] < 0:
        raise ValueError("观测缺少有效的原帧 PTS 或段号")
    if not isinstance(row.get("players"), list) or "ball" not in row:
        raise ValueError("每个原帧必须有 players 和 ball")
    for player in row["players"]:
        if not isinstance(player, dict):
            raise ValueError("球员观测必须是对象")
        box(player.get("box"), "球员框")
        if player.get("foot") is not None:
            pixel(player["foot"])
        joints = player.get("joints", {})
        if not isinstance(joints, dict):
            raise ValueError("joints 必须是按关节名索引的对象")
        for point in joints.values():
            pixel(point)
        if "ocrSkipped" in player and (player["ocrSkipped"] is not True or player.get("number") is not None):
            raise ValueError("ocrSkipped 只能为 true，且免读号的观测不带号码")
        number = player.get("number")
        if number is not None and (type(number) is not int or not 0 <= number <= 99):
            raise ValueError("号码需为 0–99 或缺测")
        if player.get("teamId") is not None and not is_identifier(player["teamId"]):
            raise ValueError("球队标识无效")
        confidence(player)
    if row["ball"] is not None:
        if not isinstance(row["ball"], dict):
            raise ValueError("球观测必须是像点对象或 null")
        pixel(row["ball"].get("pixel"))
        for field, name in (("box", "球框"), ("searchBox", "足球搜索框")):
            if field in row["ball"]:
                box(row["ball"][field], name)
        confidence(row["ball"])
