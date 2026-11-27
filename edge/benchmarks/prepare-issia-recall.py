"""把 ISSIA-3D 的人工球心、三角化和优化球径转换为精搜对照的逐帧标注。"""

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path

CAMERAS = (3, 4, 5, 6)
WIDTH = 1920
FPS = 25


def video_path(folder, camera):
    # 接受发布 ZIP 原文件名或本地简名，两者的解码方向相同。
    for name in (f"cam{camera}.avi", f"Film Role-0 ID-{camera} T-2 m00s00-000-m00s00-185.avi"):
        path = folder / name
        if path.is_file():
            return path
    raise ValueError(f"缺少机位 {camera} 原片")


def convert(row, camera, position):
    name = f"cam{camera}"
    if not row[f"x_{name}"] or not row[f"y_{name}"]:
        return None  # 发布标注缺失，无法断言画面无球。
    x, y = float(row[f"x_{name}"]), float(row[f"y_{name}"])
    if camera in (2, 6):
        x = WIDTH - 1 - x
    label = dict(clip=name, frame=int(row['frame']), status="visible", cx=x, cy=y)
    if not row['ball_3D'] or not row[f'opt_d_{name}'] or not row[f'err_{name}']:
        label['reason'] = 'noViewTriangulation'
        return label
    ball = json.loads(row['ball_3D'])
    diameter, error = float(row[f'opt_d_{name}']), float(row[f'err_{name}'])
    if diameter <= 0 or not all(math.isfinite(v) for v in [*ball, diameter, error]):
        raise ValueError(f"无效三角化：{name} 第 {label['frame']} 帧")
    far = int(position[1] * ball[1] < 0)
    label.update(rawFar=far, optimizedDiameterPx=diameter, reprojectionErrorPx=error)
    if error > 2 * diameter:
        # 保留原始二维人工标注，仅剔除由该组几何估出的远近和球径。
        label['reason'] = 'reprojectionAboveTwoDiameters'
    else:
        label.update(x=x - diameter / 2, y=y - diameter / 2, w=diameter, h=diameter,
                     far=far, cameraDistanceM=math.dist(position, ball))
    return label


def prepare(args):
    if not 0 <= args.first < args.stop <= 3002:
        raise ValueError('比赛区间须在原片 0–3001 帧内，右端不含')
    cameras = json.loads(args.calibration.read_bytes())
    # macOS 的临时目录可经 /var 或 /private/var 访问，先统一真实路径再生成相对引用。
    clips = [dict(id=f'cam{i}', video=os.path.relpath(video_path(args.videos, i).resolve(), args.output.resolve()),
                  fps=FPS, firstFrame=args.first, stopFrame=args.stop) for i in CAMERAS]
    labels = []
    with args.annotations.open(newline='', encoding='utf-8') as source:
        for row in csv.DictReader(source):
            if args.first <= int(row['frame']) < args.stop:
                for i in CAMERAS:
                    label = convert(row, i, cameras[f'cam{i}']['position_meters'])
                    if label is not None:
                        labels.append(label)
    if not labels:
        raise ValueError('时段中无球心标注')
    args.output.mkdir(parents=True, exist_ok=False)
    with (args.output / 'labels.csv').open('w', encoding='utf-8', newline='') as out:
        writer = csv.DictWriter(out, ('clip', 'frame', 'status', 'x', 'y', 'w', 'h', 'cx', 'cy', 'far',
                                      'cameraDistanceM', 'optimizedDiameterPx', 'reprojectionErrorPx', 'rawFar', 'reason'))
        writer.writeheader()
        writer.writerows(labels)
    dataset = {
        'clips': clips, 'labels': 'labels.csv', 'name': 'ISSIA / ISSIA-3D v1.0.0',
        'frameRule': 'CSV.frame = 原 AVI 从零起算解码帧；机位 6 的原 AVI 水平镜像，x = 1919 − CSV.x_cam6。',
        'farRule': '球三角化和相机坐标的横向 Y 异号；优化直径和远近要求单视角重投影偏差 ≤ 2 倍优化直径。',
        'sources': ['https://pspagnolo.jimdofree.com/download/', 'https://github.com/mguti97/SoccerNet-v3D/releases/tag/v1.0.0'],
        'calibrationSha256': hashlib.sha256(args.calibration.read_bytes()).hexdigest(),
        'annotationsSha256': hashlib.sha256(args.annotations.read_bytes()).hexdigest(),
    }
    (args.output / 'dataset.json').write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('videos', type=Path, help='解压 Sequences.zip 后的目录')
    parser.add_argument('annotations', type=Path, help='上游 ISSIA-3D.csv')
    parser.add_argument('calibration', type=Path, help='上游 issia_calibration.json')
    parser.add_argument('output', type=Path, help='新建的转换输出目录')
    parser.add_argument('--first', type=int, default=400)
    parser.add_argument('--stop', type=int, default=1900)
    prepare(parser.parse_args())
