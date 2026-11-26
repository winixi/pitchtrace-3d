"""根据静态场地标记拟合外参，用预留点独立核对三维误差。"""

import json
import math
import re
import sys
from pathlib import Path


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"JSON 字段重复：{key}")
        value[key] = item
    return value


def vector(value, length, label):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"{label} 需要 {length} 个数值")
    if not all(type(number) in (int, float) and math.isfinite(number) for number in value):
        raise ValueError(f"{label} 含无效数值")
    return value


def is_identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is not None


def image_size(camera, name):
    size = vector(camera.get("imageSize"), 2, f"机位 {name} 的图像尺寸")
    if not all(type(n) is int and n > 0 for n in size):
        raise ValueError(f"机位 {name} 的图像宽高必须是正整数")
    return size


def read_lens(camera, name, source, directory):
    path = camera["intrinsicsFile"]
    if not isinstance(path, str) or not path.strip() or not is_identifier(camera.get("intrinsicsId")):
        raise ValueError(f"机位 {name} 需指定内参文件及有效的 intrinsicsId")
    if "intrinsics" in camera or "distortion" in camera:
        raise ValueError(f"机位 {name} 同时指定了内参文件和内联参数")
    try:
        with (directory / path).open(encoding="utf-8") as lens_file:
            lens = json.load(lens_file, object_pairs_hook=unique_object)
    except (OSError, ValueError) as error:
        raise ValueError(f"机位 {name} 无法读取内参文件 {path}：{error}") from error
    if not isinstance(lens, dict) or lens.get("cameraId") != name:
        raise ValueError(f"内参文件 {path} 的 cameraId 与机位 {name} 不符")
    if lens.get("intrinsicsId") != camera["intrinsicsId"] or lens.get("sampleSource") != source:
        raise ValueError(f"机位 {name} 的内参版本或样本来源不符")
    if image_size(lens, name) != camera["imageSize"]:
        raise ValueError(f"机位 {name} 的内参分辨率与场地标注不符")
    return {**camera, "intrinsics": lens.get("intrinsics"), "distortion": lens.get("distortion")}


def load_cameras(cameras, source, directory):
    if not isinstance(cameras, dict) or not 2 <= len(cameras) <= 4:
        raise ValueError("需要 2 至 4 个机位")
    loaded = {}
    for name, camera in cameras.items():
        if not is_identifier(name) or not isinstance(camera, dict):
            raise ValueError(f"机位配置无效：{name}")
        image_size(camera, name)
        if "intrinsicsFile" in camera:
            camera = read_lens(camera, name, source, directory)
        intrinsics = camera.get("intrinsics")
        if not isinstance(intrinsics, dict):
            raise ValueError(f"机位 {name} 缺少相机内参")
        fx, fy, _, _ = vector([intrinsics.get(k) for k in ("fx", "fy", "cx", "cy")], 4, f"机位 {name} 的内参")
        if min(fx, fy) <= 0:
            raise ValueError(f"机位 {name} 的焦距必须为正")
        vector(camera.get("distortion"), 5, f"机位 {name} 的畸变系数")
        loaded[name] = camera
    return loaded


def validate_points(data, cameras):
    seen_ids = set()
    seen_positions = set()
    for section in ("fitPoints", "checkPoints"):
        points = data.get(section)
        if not isinstance(points, list):
            raise ValueError(f"{section} 必须是点列表")
        for point in points:
            if not isinstance(point, dict):
                raise ValueError(f"{section} 中存在无效点")
            name = point.get("id")
            if not isinstance(name, str) or not name.strip() or name in seen_ids:
                raise ValueError(f"{section} 点 ID 为空或重复：{name}")
            seen_ids.add(name)
            world = vector(point.get("worldM"), 3, f"点 {name} 的场地坐标")
            if world[1] < 0:
                raise ValueError(f"点 {name} 位于地面以下")
            if tuple(world) in seen_positions:
                raise ValueError(f"点 {name} 的场地坐标与其他点重复，请独立留出检查点")
            seen_positions.add(tuple(world))
            pixels = point.get("pixels")
            if not isinstance(pixels, dict):
                raise ValueError(f"点 {name} 的 pixels 必须按机位填写")
            for camera_id, pixel in pixels.items():
                if camera_id not in cameras:
                    raise ValueError(f"点 {name} 引用了未知机位 {camera_id}")
                u, v = vector(pixel, 2, f"点 {name} 在机位 {camera_id} 的像素坐标")
                width, height = cameras[camera_id]["imageSize"]
                if not (0 <= u < width and 0 <= v < height):
                    raise ValueError(f"点 {name} 的像素超出机位 {camera_id} 的图像范围")


def summarize(points):
    errors = sorted(p["errorM"] for p in points if p["status"] == "measured")
    summary = {"sampleCount": len(points), "measuredCount": len(errors)}
    if errors:
        # 最近秩，与曝光审计采用同一 P95 口径。
        summary.update(p95ErrorM=errors[len(errors) - len(errors) // 20 - 1], maxErrorM=errors[-1])
    return summary


def calibrate(data, directory=Path(".")):
    from camera_geometry import fit_camera, triangulate

    if not isinstance(data, dict) or not is_identifier(data.get("calibrationId")):
        raise ValueError("calibrationId 必须是有效的 v2 标识")
    if data.get("sampleSource") not in ("synthetic", "field"):
        raise ValueError("sampleSource 必须是 synthetic 或 field")
    cameras = load_cameras(data.get("cameras"), data["sampleSource"], directory)
    validate_points(data, cameras)

    poses = {}
    calibrated = {}
    for name, camera in sorted(cameras.items()):
        points = [p for p in data["fitPoints"] if name in p["pixels"]]
        try:
            rotation, translation, rms = fit_camera(
                camera, [p["worldM"] for p in points], [p["pixels"][name] for p in points]
            )
        except ValueError as error:
            raise ValueError(f"机位 {name} 标定失败：{error}") from error
        poses[name] = (rotation, translation)
        calibrated[name] = {
            "imageSize": camera["imageSize"], "intrinsics": camera["intrinsics"],
            "distortion": camera["distortion"], "rotationWorldToCamera": rotation.tolist(),
            "translationM": translation.tolist(), "fitCount": len(points), "fitRmsPx": rms,
        }
        if "intrinsicsFile" in camera:
            calibrated[name]["intrinsicsId"] = camera["intrinsicsId"]

    min_ray_angle_deg = 1.0
    checks = []
    for point in data["checkPoints"]:
        views = [(cameras[name], *poses[name], pixel) for name, pixel in sorted(point["pixels"].items())]
        measurement = triangulate(views, min_ray_angle_deg)
        if measurement["status"] == "measured":
            measurement["errorM"] = math.dist(measurement["reconstructedM"], point["worldM"])
        checks.append({"id": point["id"], "worldM": point["worldM"], "pixels": point["pixels"], **measurement})

    ground = summarize([p for p in checks if p["worldM"][1] == 0])
    ground["thresholdM"] = 0.30
    if ground["sampleCount"] > 0 and ground["sampleCount"] == ground["measuredCount"]:
        ground["withinThreshold"] = ground["p95ErrorM"] <= ground["thresholdM"]
    return {
        "calibrationId": data["calibrationId"], "sampleSource": data["sampleSource"],
        "cameras": calibrated, "minRayAngleDeg": min_ray_angle_deg, "checkPoints": checks,
        "ground": ground, "elevated": summarize([p for p in checks if p["worldM"][1] > 0]),
    }


if __name__ == "__main__":
    try:
        with open(sys.argv[1], encoding="utf-8") as source:
            report = calibrate(json.load(source, object_pairs_hook=unique_object), Path(source.name).parent)
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    except ModuleNotFoundError as error:
        sys.exit(f"离线标定缺少 Python 模块 {error.name}，请确认当前 python3 可以导入 cv2 和 numpy")
    except (OSError, ValueError, OverflowError) as error:
        sys.exit(f"离线标定失败：{error}")
