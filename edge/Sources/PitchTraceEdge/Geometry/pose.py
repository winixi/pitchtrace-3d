"""在统一曝光窗口内测量人体关节，转换到 clips.glb 的父骨骼局部坐标。"""

from collections import defaultdict
import math

import cv2
import numpy as np

from camera_geometry import triangulate


JOINTS = {"nose", "neck", "root", *(side + part for side in ("left", "right")
          for part in ("Eye", "Ear", "Shoulder", "Elbow", "Wrist", "Hip", "Knee", "Ankle", "Toe"))}
# 与 clips.glb 的 grf_zup_to_yup 节点一致。模型局部 +X 向左、+Z 向上。
MODEL_TO_WORLD = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=float)


def frame_ref(row):
    return {k: row[k] for k in ("cameraId", "captureUtcUs", "mediaEpoch", "segment", "pts") if k in row}


def measure_joints(rows, cameras, position):
    inputs = defaultdict(dict)
    for row in rows:
        camera_id = row["cameraId"]
        if camera_id not in cameras:
            continue
        camera = cameras[camera_id]
        width, height = camera["imageSize"]
        for name, pixel in (row["player"].get("joints") or {}).items():
            if name not in JOINTS:
                continue
            if not isinstance(pixel, list) or len(pixel) != 2 or not all(
                type(value) in (int, float) and math.isfinite(value) for value in pixel
            ):
                raise ValueError(f"机位 {camera_id} 的 {name} 关节像素无效")
            if 0 <= pixel[0] < width and 0 <= pixel[1] < height:
                inputs[name][camera_id] = (camera, np.asarray(camera["rotationWorldToCamera"], dtype=float),
                                            np.asarray(camera["translationM"], dtype=float), pixel)
    joints, audit = {}, {}
    for name, views in sorted(inputs.items()):
        result = triangulate(list(views.values()), 1.0)
        if result["status"] == "measured":
            point = np.asarray(result["reconstructedM"])
            if result["reprojectionRmsPx"] > 4 or np.linalg.norm(point - position) > 3:
                result["status"] = "rejectedGeometry"
            else:
                joints[name] = point
        audit[name] = dict(result, views=sorted(views))
    return joints, audit


def unit(vector):
    length = np.linalg.norm(vector)
    return vector / length if length > 1e-6 else None


def body_frame(left, up):
    # 返回模型骨骼坐标系在场地中的三个正交轴。
    z = unit(up)
    if z is None:
        return None
    y = unit(np.cross(z, left))
    if y is None:
        return None
    return np.column_stack((np.cross(y, z), y, z))


def swing(rest, target):
    a, b = unit(np.asarray(rest, dtype=float)), unit(target)
    if b is None:
        return None
    cross = np.cross(a, b)
    length = np.linalg.norm(cross)
    cosine = float(np.clip(a @ b, -1, 1))
    if length <= 1e-8:
        if cosine >= 0:
            return np.eye(3)
        # 反向共线不能确定关节的转动平面。
        return None
    return cv2.Rodrigues(cross * (math.atan2(length, cosine) / length))[0]


def quaternion(rotation):
    vector = cv2.Rodrigues(rotation)[0].ravel()
    angle = np.linalg.norm(vector)
    if angle < 1e-10:
        return [0., 0., 0., 1.]
    return [*(vector * (math.sin(angle / 2) / angle)).tolist(), math.cos(angle / 2)]


def local_pose(joints):
    """返回身体朝向和可观测的局部旋转。父节点不足时子链也缺测。"""
    trunk = ("leftHip", "rightHip", "leftShoulder", "rightShoulder")
    if any(key not in joints for key in trunk):
        return None, {}
    lh, rh, ls, rs = (joints[key] for key in trunk)
    up = (ls + rs - lh - rh) * .5
    pelvis = body_frame(lh - rh, up)
    chest = body_frame(ls - rs, up)
    if pelvis is None or chest is None:
        return None, {}
    front = -pelvis[:, 1]
    if math.hypot(front[0], front[2]) < .1:
        return None, {}
    yaw = math.atan2(front[0], front[2])
    yaw_rotation = cv2.Rodrigues(np.array([0., yaw, 0.]))[0]
    bones = {"body": quaternion((yaw_rotation @ MODEL_TO_WORLD).T @ pelvis),
             "middle": quaternion(pelvis.T @ chest)}
    if {"leftEar", "rightEar", "nose"} <= joints.keys():
        ears = joints["leftEar"] - joints["rightEar"]
        face = joints["nose"] - (joints["leftEar"] + joints["rightEar"]) / 2
        head = body_frame(ears, np.cross(face, ears))
        if head is not None:
            bones["neck"] = quaternion(chest.T @ head)
    for side, sign in (("left", 1), ("right", -1)):
        # 原模型中大腿→膝与膝→踝、肩→肘的节点偏移来自 clips.glb。
        for parent, chain in (
            (pelvis, [("thigh", "Hip", "Knee", [0, 0, -.42]),
                      ("knee", "Knee", "Ankle", [0, -.04, -.44]),
                      ("ankle", "Ankle", "Toe", [0, -1, 0])]),
            (chest, [("shoulder", "Shoulder", "Elbow", [-.01 * sign, 0, -.33]),
                     ("elbow", "Elbow", "Wrist", [0, 0, -1])]),
        ):
            for bone, start, end, rest in chain:
                if side + start not in joints or side + end not in joints:
                    break
                direction = joints[side + end] - joints[side + start]
                local = swing(rest, parent.T @ direction)
                if local is None:
                    break
                bones[side + "_" + bone] = quaternion(local)
                parent = parent @ local
    return yaw, bones
