"""用巡检照片的独立检查点复验既有机位，并计算指定高度的几何覆盖。"""

from itertools import combinations
import json
import math
from pathlib import Path
import sys

try:
    import cv2
    import numpy as np
    from calibrate import is_identifier, validate_points
    from camera_geometry import _optics, _project
    from match_config import read_object, utc_us, validate_report
except ImportError as error:
    if __name__ == "__main__":
        sys.exit(f"标定巡检需要当前 python3 能导入 cv2 和 numpy：{error}")
    raise


def number(value, name, zero=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or value < 0 or (not zero and value == 0)):
        raise ValueError(f"{name} 必须是{'非负' if zero else '正'}有限数值")
    return value


class Projection:
    def __init__(self, camera):
        self.size = camera["imageSize"]
        self.matrix, self.distortion = _optics(camera)
        self.rotation = np.array(camera["rotationWorldToCamera"], dtype=float)
        self.translation = np.array(camera["translationM"], dtype=float)
        self.center = -self.rotation.T @ self.translation

    def project(self, points):
        return _project(np.array(points, dtype=float), self.rotation, self.translation, self.matrix, self.distortion)

    def depth(self, points):
        return points @ self.rotation[2] + self.translation[2]


def reprojections(report_checks, inspection, cameras, threshold):
    validate_points({"fitPoints": [], "checkPoints": report_checks}, cameras)
    known = {p["id"]: p["worldM"] for p in report_checks}
    checks = inspection.get("checks")
    if not isinstance(checks, list):
        raise ValueError("checks 必须是巡检标注数组")
    observations = []
    for item in checks:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] not in known:
            raise ValueError("巡检点 ID 须引用外参报告中的独立检查点")
        frames = item.get("frames", {})
        if not isinstance(frames, dict) or not isinstance(item.get("pixels"), dict) or frames.keys() - item["pixels"].keys():
            raise ValueError("巡检原帧引用须对应同一点的机位标注")
        for frame in frames.values():
            if (not isinstance(frame, dict) or type(frame.get("segment")) is not int or frame["segment"] < 0
                    or type(frame.get("pts")) is not int
                    or ("mediaEpoch" in frame and not is_identifier(frame["mediaEpoch"]))):
                raise ValueError("巡检原帧需提供有效的 segment、pts 和可选媒体周期")
            if "captureUtcUs" in frame:
                exposure = utc_us(frame["captureUtcUs"], "原帧 captureUtcUs")
                if "captureUtcUs" in inspection and exposure > utc_us(inspection["captureUtcUs"], "巡检 captureUtcUs"):
                    raise ValueError("巡检时刻早于用于复验的原帧曝光")
        observations.append({"id": item["id"], "worldM": known[item["id"]], "pixels": item["pixels"], "frames": frames})
    validate_points({"fitPoints": [], "checkPoints": observations}, cameras)
    results = {}
    for name, camera in sorted(cameras.items()):
        projection = Projection(camera)
        residuals = []
        coordinates = []
        failures = 0
        for p in observations:
            if name not in p["pixels"]:
                continue
            point = np.array(p["worldM"])
            expected = projection.project([point])[0]
            visible = projection.depth(point) > 0 and np.isfinite(expected).all()
            error = math.dist(expected, p["pixels"][name]) if visible else None
            residuals.append({"id": p["id"], "observedPx": p["pixels"][name],
                              "expectedPx": expected.tolist() if visible else None, "errorPx": error})
            if name in p["frames"]:
                residuals[-1]["frame"] = p["frames"][name]
            coordinates.append(point)
            if error is None or error > threshold:
                failures += 1
        status = "insufficientEvidence"
        if len(coordinates) >= 3:
            centered = np.array(coordinates) - np.mean(coordinates, axis=0)
            singular = np.linalg.svd(centered, compute_uv=False)
            if singular[1] > 1e-6 * singular[0]:
                status = "consistent" if not failures else "review"
                if 2 * failures > len(residuals):
                    status = "suspectedMovement"
        results[name] = {"status": status, "observedCount": len(residuals), "overThresholdCount": failures,
                         "checks": residuals}
    return results


def coverage_grid(settings, cameras, excluded):
    if not isinstance(settings, dict):
        raise ValueError("需要 grid 场地网格参数")
    length = number(settings.get("lengthM"), "lengthM")
    width = number(settings.get("widthM"), "widthM")
    step = number(settings.get("cellSizeM"), "cellSizeM")
    height = number(settings.get("heightM"), "heightM", zero=True)
    diameter = number(settings.get("ballDiameterM", .22), "ballDiameterM")
    min_angle = number(settings.get("minRayAngleDeg", 1.), "minRayAngleDeg")
    if min_angle > 90:
        raise ValueError("minRayAngleDeg 必须在 (0, 90] 范围内")
    nx, nz = math.ceil(length / step), math.ceil(width / step)
    if nx * nz > 10000:
        raise ValueError("覆盖报告最多支持 10000 格，请加大 cellSizeM")
    # 最外一格按场地边缘截断，采样实际格子的中心。
    points = np.array([[.5 * (ix * step + min((ix + 1) * step, length) - length), height,
                        .5 * (iz * step + min((iz + 1) * step, width) - width)]
                       for iz in range(nz) for ix in range(nx)])
    views = [{} for p in points]
    centers = {}
    for name, camera in sorted(cameras.items()):
        if name in excluded:
            continue
        projection = Projection(camera)
        centers[name] = projection.center
        pixels = projection.project(points)
        depth = projection.depth(points)
        visible = ((depth > 0) & np.isfinite(pixels).all(axis=1)
                   & np.all(pixels >= 0, axis=1) & np.all(pixels < projection.size, axis=1))
        # 针孔近似下，相机横向的目标直径在图上的宽度约为 fx * d / Z。
        for index in np.flatnonzero(visible):
            views[index][name] = {"expectedDiameterPx": float(projection.matrix[0, 0] * diameter / depth[index])}
    cells = []
    count = 0
    for point, visible in zip(points, views):
        pairs = []
        for a, b in combinations(visible, 2):
            va, vb = centers[a] - point, centers[b] - point
            angle = math.degrees(math.atan2(np.linalg.norm(np.cross(va, vb)), abs(va @ vb)))
            if angle >= min_angle:
                pairs.append({"cameras": [a, b], "angleDeg": angle})
        cells.append({"centerM": point.tolist(), "views": visible, "viewCount": len(visible), "pairs": pairs})
        count += bool(pairs)
    return {"lengthM": length, "widthM": width, "heightM": height, "cellSizeM": step, "columnsX": nx,
            "rowsZ": nz, "ballDiameterM": diameter, "minRayAngleDeg": min_angle, "dualViewCells": count, "cells": cells}


def audit(report_path, inspection_path):
    report = read_object(report_path)
    inspection = read_object(inspection_path)
    source = inspection.get("sampleSource", report.get("sampleSource"))
    if source not in ("synthetic", "field"):
        raise ValueError("sampleSource 必须是 synthetic 或 field")
    version, cameras = validate_report(report, report_path, source)
    threshold = number(inspection.get("maxReprojectionPx", 3.), "maxReprojectionPx")
    checked = reprojections(report.get("checkPoints"), inspection, cameras, threshold)
    suspected = sorted(name for name, result in checked.items() if result["status"] == "suspectedMovement")
    disabled = inspection.get("disabledCameras", [])
    if (not isinstance(disabled, list) or any(not isinstance(n, str) or n not in cameras for n in disabled)
            or len(set(disabled)) != len(disabled)):
        raise ValueError("disabledCameras 含未知或重复机位")
    excluded = sorted(set(disabled) | set(suspected))
    result = {"reportCalibrationId": version, "sampleSource": source, "maxReprojectionPx": threshold,
              "cameras": checked, "suspectedCameras": suspected, "disabledForCoverage": excluded,
              "coverage": coverage_grid(inspection.get("grid"), cameras, excluded)}
    if "captureUtcUs" in inspection:
        utc_us(inspection["captureUtcUs"], "captureUtcUs")
        result["captureUtcUs"] = inspection["captureUtcUs"]
    if "calibrationId" in inspection:
        new_id = inspection["calibrationId"]
        if not is_identifier(new_id) or new_id == version:
            raise ValueError("提议的 calibrationId 需要一个新的有效版本标识")
        if "captureUtcUs" not in inspection:
            raise ValueError("生成标定变更项需指定 captureUtcUs")
        if set(suspected) - set(disabled):
            result["proposedCalibration"] = {"calibrationId": new_id, "fromUtcUs": inspection["captureUtcUs"],
                                             "report": str(report_path.resolve()), "disabledCameras": excluded}
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(audit(Path(sys.argv[1]), Path(sys.argv[2])), allow_nan=False, ensure_ascii=False))
    except (OSError, ValueError, OverflowError, cv2.error, np.linalg.LinAlgError) as error:
        sys.exit(f"标定巡检失败：{error}")
