"""用已知内参估计世界到相机的位姿，并从多路像素恢复场地坐标。"""

from itertools import combinations

import cv2
import numpy as np


def _optics(camera):
    lens = camera["intrinsics"]
    matrix = np.array(
        [[lens["fx"], 0, lens["cx"]], [0, lens["fy"], lens["cy"]], [0, 0, 1]], dtype=float
    )
    return matrix, np.asarray(camera["distortion"], dtype=float)


def _undistort(pixels, matrix, distortion):
    # OpenCV 4、5 的迭代接口名称不同。归一化像点的第三个坐标为 1。
    undistort = getattr(cv2, "undistortPointsIter", cv2.undistortPoints)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 1e-10)
    normalized = undistort(pixels.reshape(-1, 1, 2), matrix, distortion, R=None, P=None, criteria=criteria)
    normalized = normalized.reshape(-1, 2)
    # 迭代逆畸变可能返回未收敛的结果。重新投影到原像素确认求逆有效。
    rays = np.column_stack((normalized, np.ones(len(normalized))))
    original = _project(rays, np.zeros(3), np.zeros(3), matrix, distortion)
    if not np.allclose(original, pixels.reshape(-1, 2), rtol=0, atol=1e-4):
        raise ValueError("像点的镜头畸变校正未收敛")
    return normalized


def _project(world, rotation, translation, matrix, distortion):
    pixels, _ = cv2.projectPoints(world, rotation, translation, matrix, distortion)
    return pixels.reshape(-1, 2)


def _spread(points):
    return np.linalg.svd(points - np.mean(points, axis=0), compute_uv=False)


def fit_camera(camera, world, pixels):
    world = np.asarray(world, dtype=float)
    pixels = np.asarray(pixels, dtype=float)
    if world.ndim != 2 or world.shape[1] != 3 or len(world) < 4 or pixels.shape != (len(world), 2):
        raise ValueError("每个机位至少需要四组对应的世界点与像点")
    if not np.isfinite(world).all() or not np.isfinite(pixels).all():
        raise ValueError("标定坐标必须为有限数值")
    matrix, distortion = _optics(camera)
    try:
        spread = _spread(world)
        image_spread = _spread(_undistort(pixels, matrix, distortion))
        if spread[1] <= 1e-8 * spread[0] or image_spread[1] <= 1e-8 * image_spread[0]:
            raise ValueError("世界点或像点近乎共线，无法确定机位位姿")
        if spread[2] > 1e-8 * spread[0] and len(world) < 6:
            raise ValueError("非共面标定至少需要六组对应点")

        # SQPnP 给出初始解，再在带畸变的原像素坐标内细化重投影误差。
        _, rotations, translations, _ = cv2.solvePnPGeneric(
            world, pixels, matrix, distortion, flags=cv2.SOLVEPNP_SQPNP
        )
        best = None
        for rvec, tvec in zip(rotations, translations):
            rvec, tvec = cv2.solvePnPRefineLM(
                world, pixels, matrix, distortion, rvec, tvec,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-12),
            )
            rotation, _ = cv2.Rodrigues(rvec)
            translation = tvec.reshape(3)
            depths = world @ rotation[2] + translation[2]
            if not np.all(depths > 0):
                continue
            residual = _project(world, rvec, translation, matrix, distortion) - pixels
            rms = float(np.sqrt(np.mean(np.sum(residual ** 2, axis=1))))
            if np.isfinite(rms) and (best is None or rms < best[2]):
                best = rotation, translation, rms
        if best is None:
            raise ValueError("无法求得所有拟合点均在相机前方的有限位姿")
        return best
    except (cv2.error, np.linalg.LinAlgError) as exc:
        raise ValueError("控制点几何退化或相机位姿求解失败") from exc


def triangulate(views, min_ray_angle_deg):
    if len(views) < 2:
        return {"status": "insufficientViews"}
    degenerate = {"status": "degenerateGeometry"}
    centers = []
    directions = []
    projections = []
    try:
        for camera, rotation, translation, pixel in views:
            matrix, distortion = _optics(camera)
            pixel = np.asarray(pixel, dtype=float)
            normalized = _undistort(pixel, matrix, distortion)[0]
            direction = rotation.T @ np.append(normalized, 1.0)
            directions.append(direction / np.linalg.norm(direction))
            centers.append(-rotation.T @ translation)
            projections.append((matrix, distortion, rotation, translation, pixel))
        if not np.isfinite(directions).all() or not np.isfinite(centers).all():
            return degenerate

        # 取两条直线的锐角，反向共线的射线同样缺少深度约束。
        min_sine = np.sin(np.deg2rad(min_ray_angle_deg))
        if not any(
            np.linalg.norm(centers[i] - centers[j]) > 1e-8
            and np.linalg.norm(np.cross(directions[i], directions[j])) >= min_sine
            for i, j in combinations(range(len(views)), 2)
        ):
            return degenerate

        # 到各视线的垂直位移为 (I - ddᵀ)(X - C)，联合最小化这些位移。
        projectors = np.array([np.eye(3) - np.outer(ray, ray) for ray in directions])
        origin = np.mean(centers, axis=0)
        offsets = np.asarray(centers) - origin
        rhs = np.einsum("nij,nj->ni", projectors, offsets)
        offset, _, rank, _ = np.linalg.lstsq(projectors.reshape(-1, 3), rhs.reshape(-1), rcond=None)
        world = origin + offset
        if rank < 3 or not np.isfinite(world).all():
            return degenerate
        squared_errors = []
        for matrix, distortion, rotation, translation, pixel in projections:
            if rotation[2] @ world + translation[2] <= 0:
                return {"status": "behindCamera"}
            projected = _project(world.reshape(1, 3), rotation, translation, matrix, distortion)[0]
            squared_errors.append(np.sum((projected - pixel) ** 2))
        rms = float(np.sqrt(np.mean(squared_errors)))
        if not np.isfinite(rms):
            return degenerate
        return {"status": "measured", "reconstructedM": world.tolist(), "reprojectionRmsPx": rms}
    except (ValueError, cv2.error, np.linalg.LinAlgError):
        return degenerate
