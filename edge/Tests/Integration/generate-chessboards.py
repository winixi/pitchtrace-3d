"""生成已知镜头下的棋盘格照片，供离线内参回归使用。"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np


SIZE = (960, 540)
BOARD = {"columns": 8, "rows": 5, "squareSizeM": 0.04}
MATRIX = np.array([[620., 0., 451.], [0., 660., 293.], [0., 0., 1.]])
DISTORTION = np.array([-0.075, 0.012, 0.0003, -0.0005, 0.])


def render(path, rotation, center, distortion):
    # 先细分方格边缘以保留曲线畸变，再超采样取得亚像素边界。
    scale = 3
    canvas = np.full((SIZE[1] * scale, SIZE[0] * scale), 238, dtype=np.uint8)
    columns = BOARD["columns"] + 1
    rows = BOARD["rows"] + 1
    fraction = np.arange(8) / 8
    zeros = np.zeros_like(fraction)
    ones = np.ones_like(fraction)
    outline = np.vstack([
        np.column_stack((fraction, zeros)), np.column_stack((ones, fraction)),
        np.column_stack((1 - fraction, ones)), np.column_stack((zeros, 1 - fraction)),
    ])
    for row in range(rows):
        for col in range(columns):
            if (row + col) % 2:
                continue
            board_xy = (outline + [col - columns / 2, row - rows / 2]) * BOARD["squareSizeM"]
            world = np.column_stack((board_xy, np.zeros(len(outline))))
            pixels, _ = cv2.projectPoints(world, rotation, center, MATRIX, distortion)
            vertices = np.rint(pixels.reshape(-1, 2) * scale).astype(np.int32)
            cv2.fillPoly(canvas, [vertices], 22)
    image = cv2.resize(canvas, SIZE, interpolation=cv2.INTER_AREA)
    if not cv2.imwrite(str(path), image):
        raise OSError(f"无法写入样例图片 {path}")


def generate(directory):
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError("请指定一个空的样例目录")
    # 棋盘中心平移以米为单位；各方向的倾斜提供独立的内参约束。
    poses = [
        ([-0.4, 0.2, 0.05], [-0.13, -0.06, 0.60]),
        ([0.2, -0.4, -0.1], [0.19, 0.04, 0.72]),
        ([0.5, 0.1, 0.12], [-0.05, 0.08, 0.66]),
        ([-0.05, 0.6, -0.1], [0.24, -0.11, 0.75]),
        ([0.38, 0.38, 0.0], [-0.20, 0.06, 0.69]),
        ([-0.5, -0.25, 0.1], [0.08, -0.07, 0.55]),
        ([0.25, -0.48, -0.2], [-0.22, -0.13, 0.78]),
        ([-0.25, -0.35, -0.05], [0.27, 0.1, 0.72]),
    ]
    images = []
    for i, (rotation, center) in enumerate(poses):
        name = f"tilted-{i}.png"
        render(directory / name, np.array(rotation), np.array(center), DISTORTION)
        images.append(name)
        # 平面内转动、变距和镜头畸变都可能改变图案，却未增加板法线方向。
        render(directory / f"front-{i}.png", np.array([0., 0., 0.07 * i]), np.array(center), DISTORTION)
    cv2.imwrite(str(directory / "blank.png"), np.full((SIZE[1], SIZE[0]), 200, np.uint8))
    cv2.imwrite(str(directory / "small.png"), np.full((240, 320), 200, np.uint8))
    manifest = {
        "cameraId": "A", "intrinsicsId": "lens-A-v1", "sampleSource": "synthetic",
        "board": BOARD, "images": images + ["blank.png"],
    }
    (directory / "lens.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    generate(Path(sys.argv[1]))
