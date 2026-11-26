"""生成两路带真值的短录像、曝光索引与对应二维观测，供离线流程回归。"""

import json
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np


START = 1795494600000000
FRAMES = 20


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fixture(directory):
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError("请指定新的空目录")
    observations = []
    cameras = {}
    for camera, center in (("A", -3), ("B", 3)):
        root = directory / "recordings" / camera
        segments = root / "segments"
        segments.mkdir(parents=True)
        cameras[camera] = {
            "imageSize": [320, 192], "intrinsics": {"fx": 175, "fy": 175, "cx": 160, "cy": 96},
            "distortion": [0] * 5, "rotationWorldToCamera": [[1, 0, 0], [0, 0, 1], [0, -1, 0]],
            "translationM": [-center, 0, 10],
        }
        def pixel(x, y, z):
            return [160 + 175 * (x - center) / (10 - y), 96 + 175 * z / (10 - y)]

        images = []
        annotations = []
        for i in range(FRAMES):
            image = np.full((192, 320, 3), (46, 110, 42), np.uint8)
            ball = pixel(-1 + i * 0.12, 0.8, 1.5)
            foot = pixel(-2 + i * 0.08, 0, 0)
            u, v = (int(round(n)) for n in foot)
            cv2.rectangle(image, (u - 4, v - 14), (u + 4, v), (235, 70, 20), -1)
            hidden = 12 <= i <= 16
            if not hidden:
                cv2.circle(image, tuple(round(n) for n in ball), 5, (0, 110, 255), -1)
            images.append(image.tobytes())
            annotations.append({"imageSize": [320, 192], "players": [
                {"foot": foot, "box": [u - 4, v - 14, 9, 15], "teamId": "blue", "number": 7}
            ], "ball": None if hidden else {"pixel": ball, "confidence": 1}})
        subprocess.run([
            "ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "320x192",
            "-r", "10", "-i", "pipe:0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-g", "10", "-sc_threshold", "0", "-bf", "0", "-hls_time", "1", "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", "init.mp4", "-hls_segment_filename", "segments/%d.m4s",
            "-hls_playlist_type", "vod", "playlist.m3u8",
        ], cwd=root, input=b"".join(images), check=True)
        i = 0
        for part in sorted(segments.glob("*.m4s")):
            probe = json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "frame=pts", "-of", "json", "pipe:0",
            ], input=(root / "init.mp4").read_bytes() + part.read_bytes()))
            mapping = []
            for frame in probe["frames"]:
                stamp = {"pts": frame["pts"], "captureUtcUs": str(START + i * 100000)}
                mapping.append({**stamp, "frameNumber": i})
                observations.append({**stamp, **annotations[i], "cameraId": camera, "segment": int(part.stem)})
                i += 1
            write_json(part.with_suffix(".exposure.json"), {"frames": mapping})
        if i != FRAMES:
            raise ValueError("样例未保留全部曝光帧")
    write_json(directory / "calibration.json", {"calibrationId": "rig-v1", "sampleSource": "synthetic",
                                                "cameras": cameras})
    timeline = [{"fromUtcUs": str(START + offset), "calibrationId": version, "report": "calibration.json",
                 "disabledCameras": disabled} for offset, version, disabled in (
                     (0, "both", []), (500000, "only-b", ["A"]), (900000, "verified", []))]
    config = {"matchId": "training", "streamEpoch": "video", "startUtcUs": str(START),
              "sampleSource": "synthetic", "calibrations": timeline, "recordings": "recordings",
              "mediaEpoch": "sample", "roster": [{"rosterId": "p07", "teamId": "blue", "number": 7}],
              "detector": {"backend": "color-ball", "ballHSV": {"lower": [4, 160, 140], "upper": [24, 255, 255]}}}
    write_json(directory / "match.json", config)
    write_json(directory / "annotated.json", {**config, "streamEpoch": "annotated"})
    with (directory / "observations.ndjson").open("w", encoding="utf-8") as stream:
        for row in sorted(observations, key=lambda r: (int(r["captureUtcUs"]), r["cameraId"])):
            stream.write(json.dumps(row) + "\n")
    # 从投影前的虚拟场地坐标生成人工可核对的真值；两种场景周期均可复用。
    with (directory / "truth.ndjson").open("w", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "evaluation", "matchId": "training", "sampleSource": "synthetic",
                                 "sampleDurationUs": 100000}) + "\n")
        for i in range(FRAMES):
            sample = {"captureUtcUs": str(START + i * 100000),
                      "players": [{"personId": "p07", "positionM": [-2 + i * 0.08, 0, 0], "number": 7,
                                   "numberSegmentId": "visible-7"}],
                      "ball": None if 12 <= i <= 16 else {"kind": "high", "positionM": [-1 + i * 0.12, 0.8, 1.5]}}
            stream.write(json.dumps(sample) + "\n")


if __name__ == "__main__":
    fixture(Path(sys.argv[1]))
