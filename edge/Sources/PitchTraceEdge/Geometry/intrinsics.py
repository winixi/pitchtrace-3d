"""从同一机位的多姿态棋盘格原图估计针孔内参与五项畸变。"""

import json
import math
from pathlib import Path
import sys

from calibrate import is_identifier, unique_object

try:
    import cv2
    import numpy as np
except ImportError as error:
    if __name__ == "__main__":
        sys.exit(f"镜头内参标定需要当前 python3 能导入 cv2 和 numpy：{error}")
    raise


def validate_manifest(data):
    if not isinstance(data, dict):
        raise ValueError("内参清单必须是 JSON 对象")
    for key in ("cameraId", "intrinsicsId"):
        if not is_identifier(data.get(key)):
            raise ValueError(f"{key} 必须是有效的 v2 标识")
    if data.get("sampleSource") not in ("synthetic", "field"):
        raise ValueError("sampleSource 必须是 synthetic 或 field")
    board = data.get("board")
    if not isinstance(board, dict):
        raise ValueError("缺少 board 棋盘格配置")
    for key in ("columns", "rows"):
        if type(board.get(key)) is not int or board[key] < 3:
            raise ValueError(f"棋盘格 {key} 必须是至少为 3 的内角点数")
    square = board.get("squareSizeM")
    if type(square) not in (int, float) or not math.isfinite(square) or square <= 0:
        raise ValueError("squareSizeM 必须是以米为单位的有限正数")
    images = data.get("images")
    if not isinstance(images, list) or len(images) < 3:
        raise ValueError("内参标定至少需要三张不同姿态的棋盘格照片")
    if any(not isinstance(name, str) or not name.strip() for name in images):
        raise ValueError("images 中的每项必须是照片路径")


def check_pose_diversity(board_axes):
    # 方格两轴在相机内参定义的度量下正交且等长。每个单应矩阵给出两条
    # 线性约束，解对称矩阵（仅确定到尺度）需要五条独立约束。
    constraints = []
    for axes in board_axes:
        x, y = (axes / np.linalg.norm(axes)).T
        constraints.extend([
            (np.outer(x, y) + np.outer(y, x)).ravel(),
            (np.outer(x, x) - np.outer(y, y)).ravel(),
        ])
    singular = np.linalg.svd(constraints, compute_uv=False)
    if singular[4] <= 1e-3 * singular[0]:
        raise ValueError("棋盘格姿态退化，请改变板面倾角并从不同方向拍摄")


def image_board_axes(grid, corners, image_size):
    centered = (corners.reshape(-1, 2) - np.array(image_size) / 2) / max(image_size)
    homography, _ = cv2.findHomography(grid, centered, method=0)
    if homography is None or not np.isfinite(homography).all():
        raise ValueError("无法从棋盘格角点确定平面单应矩阵")
    return homography[:, :2]


def fit_intrinsics(data, directory):
    validate_manifest(data)
    board = data["board"]
    pattern = (board["columns"], board["rows"])
    paths = [(directory / name).resolve() for name in data["images"]]
    if len(set(paths)) != len(paths):
        raise ValueError("images 中有重复的照片路径")

    views = []
    detections = []
    image_size = None
    for name, path in zip(data["images"], paths):
        gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION)
        if gray is None:
            raise ValueError(f"无法读取棋盘格照片：{name}")
        size = gray.shape[::-1]
        if image_size is not None and size != image_size:
            raise ValueError(f"照片 {name} 的分辨率 {size} 与其他照片 {image_size} 不一致")
        image_size = size
        found = False
        if math.prod(pattern) <= gray.size:
            found, corners = cv2.findChessboardCornersSB(gray, pattern)
        views.append({"image": name, "status": "used" if found else "notDetected"})
        if found:
            detections.append(corners)
    if len(detections) < 3:
        raise ValueError(f"仅有 {len(detections)} 张照片识别到完整棋盘格，至少需要三张")

    grid = np.mgrid[:pattern[0], :pattern[1]].T.reshape(-1, 2).astype(np.float32)
    check_pose_diversity(image_board_axes(grid, corners, image_size) for corners in detections)
    world = np.zeros((len(grid), 3), dtype=np.float32)
    world[:, :2] = grid * board["squareSizeM"]
    if not np.isfinite(world).all() or world[1, 0] == 0:
        raise ValueError("棋盘格实际尺寸超出标定数值范围")

    rms, matrix, distortion, rotations, _, _, _, view_errors = cv2.calibrateCameraExtended(
        [world] * len(detections), detections, image_size, None, None
    )
    if not all(np.isfinite(a).all() for a in (rms, matrix, distortion, view_errors)):
        raise ValueError("内参拟合未收敛，请检查角点与棋盘格姿态")
    # 畸变也能改变棋盘边缘的透视外观。用求解的板面姿态复验，
    # 避免一组正对镜头的平移照片得到低像素误差但焦距不确定。
    check_pose_diversity(cv2.Rodrigues(rvec)[0][:, :2] for rvec in rotations)
    fx, fy = float(matrix[0, 0]), float(matrix[1, 1])
    if min(fx, fy) <= 0:
        raise ValueError("无法拟合正焦距")
    used = (view for view in views if view["status"] == "used")
    for view, error in zip(used, view_errors.ravel()):
        view["rmsPx"] = float(error)
    return {
        "cameraId": data["cameraId"], "intrinsicsId": data["intrinsicsId"],
        "sampleSource": data["sampleSource"], "imageSize": list(image_size),
        "intrinsics": {"fx": fx, "fy": fy, "cx": float(matrix[0, 2]), "cy": float(matrix[1, 2])},
        "distortion": distortion.ravel().tolist(), "fitRmsPx": rms, "views": views,
    }


if __name__ == "__main__":
    try:
        manifest = Path(sys.argv[1])
        with manifest.open(encoding="utf-8") as source:
            report = fit_intrinsics(json.load(source, object_pairs_hook=unique_object), manifest.parent)
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, OverflowError, cv2.error, np.linalg.LinAlgError) as error:
        sys.exit(f"镜头内参标定失败：{error}")
