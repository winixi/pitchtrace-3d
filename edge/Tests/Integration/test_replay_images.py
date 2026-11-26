"""实际编码 B 帧并重置媒体周期，按展示 PTS 核对选中帧的像素。"""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Sources/PitchTraceEdge/Geometry"))
from replay_images import ReplayImages


def make_period(root, epoch, utc, reverse):
    base = root / "A/epochs" / epoch
    segments = base / "segments"
    segments.mkdir(parents=True)
    values = [20 + (i * 53) % 203 for i in range(30)]
    if reverse:
        values.reverse()
    raw = b"".join(np.full((64, 96, 3), value, np.uint8).tobytes() for value in values)
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "rawvideo", "-s", "96x64", "-r", "15",
        "-pix_fmt", "bgr24", "-i", "pipe:0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-g", "15", "-bf", "3", "-x264-params", "b-adapt=0:scenecut=0", "source.mp4",
    ], cwd=base, input=raw, check=True)
    # 第二周期附 90 度显示矩阵。检测使用编码坐标系，图像宽高必须保持 96×64。
    angle = ["-display_rotation", "90"] if reverse else []
    subprocess.run([
        "ffmpeg", "-v", "error", *angle, "-i", "source.mp4", "-c:v", "copy", "-hls_time", "1",
        "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4", "-hls_segment_filename",
        "segments/%d.m4s", "preview.m3u8",
    ], cwd=base, check=True)
    init = (base / "init.mp4").read_bytes()
    rows, kinds = [], set()
    for n, path in enumerate(sorted(segments.glob("*.m4s"))):
        data = path.read_bytes()
        video = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=time_base:frame=pts,pict_type", "-of", "json", "pipe:0",
        ], input=init + data))
        num, den = map(int, video["streams"][0]["time_base"].split("/"))
        time_base = {"numerator": num, "denominator": den}
        index = {"timeBase": time_base, "segmentBytes": len(data), "initSha256": hashlib.sha256(init).hexdigest(),
                 "segmentSha256": hashlib.sha256(data).hexdigest(), "frames": []}
        for frame in video["frames"]:
            i = len(rows)
            stamp = {"pts": frame["pts"], "captureUtcUs": str(utc + i * 66667), "durationPts": den // (num * 15)}
            index["frames"].append(stamp)
            rows.append({"cameraId": "A", "mediaEpoch": epoch, "segment": n, "timeBase": time_base, **stamp})
            kinds.add(frame["pict_type"])
        path.with_suffix(".json").write_text(json.dumps(index))
    return rows, values, kinds


class ReplayImageTests(unittest.TestCase):
    def test_b_frames_skip_and_period_reset(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            reader = ReplayImages(root)
            try:
                first_pts = None
                for epoch, utc, reverse in (("opening", 1000, False), ("reconnect", 8000000, True)):
                    rows, values, kinds = make_period(root, epoch, utc, reverse)
                    self.assertIn("B", kinds)
                    self.assertEqual(len(rows), 30)
                    if first_pts is not None:
                        self.assertEqual(rows[0]["pts"], first_pts)
                    first_pts = rows[0]["pts"]
                    # 以不等步长跨段取帧。第二周期重用段号和 PTS，但有不同像素和 UTC。
                    for i in [0, 2, 5, 12, 16, 18, 26, 29]:
                        image = reader.read(rows[i])
                        self.assertEqual(image.shape, (64, 96, 3))
                        self.assertAlmostEqual(float(image.mean()), values[i], delta=4)
                broken = {**rows[0], "captureUtcUs": "0"}
                with self.assertRaisesRegex(ValueError, "曝光或展示时序"):
                    reader.read(broken)
            finally:
                reader.close()

    def test_requires_ffmpeg_backend_and_checks_packet_pts(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            rows, _, _ = make_period(root, "p", 0, False)
            reader = ReplayImages(root)
            try:
                with patch.object(cv2.videoio_registry, "hasBackend", return_value=False):
                    with self.assertRaisesRegex(ValueError, "FFmpeg 视频后端"):
                        reader.read(rows[0])
                index = root / "A/epochs/p/segments/0.json"
                data = json.loads(index.read_text())
                data["frames"][3]["pts"] += 1
                index.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, "PTS"):
                    reader.read(rows[0])
            finally:
                reader.close()


if __name__ == "__main__":
    unittest.main()
