"""用四路独立的假解码器和检测器验证曝光组的并发、失败清理和归档恢复。"""

from collections import defaultdict
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

EDGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EDGE / "Sources/PitchTraceEdge/Geometry"))
from camera_workers import CameraWorkers
from match_config import MatchConfig
from process_match import analyze_rows
from scene_archive import SceneArchive
from vision_model import load_vision_model


class ParallelInferenceTest(unittest.TestCase):
    def test_configuration_and_fixed_camera_assignment(self):
        for value in (0, 9, True, "4", 2.5):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "workers"):
                load_vision_model({"workers": value}, Path("."))
        with CameraWorkers("DCBA", 2) as workers:
            rows = [{"cameraId": camera} for camera in "DCBA"]
            self.assertEqual(workers.map_group(rows, lambda lane, row: (row["cameraId"], lane)),
                             [("D", 1), ("C", 0), ("B", 1), ("A", 0)])
        with CameraWorkers(["B"], 8) as workers:
            self.assertEqual(workers.worker_count, 1)
        with CameraWorkers([], 4) as workers:
            self.assertEqual(workers.map_group([], None), [])

    def test_four_cameras_commit_in_order_and_retry_complete_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.make_config(root)
            rows = [{"cameraId": camera, "captureUtcUs": str(frame * 100_000), "segment": 0, "pts": frame}
                    for frame in range(3) for camera in "DCBA"]

            def run(archive, workers, fail_on=None):
                opened, closed = [], []
                assignments = defaultdict(set)
                samples = []
                lock = threading.Lock()
                # 栅栏只在检测器内部等待。串行或意外共享一个 lane 都会超时失败。
                barrier = threading.Barrier(4, timeout=5) if workers == 4 else None

                class Reader:
                    def __init__(self, root):
                        opened.append(self)

                    def read(self, row):
                        image = np.zeros((48, 64, 3), np.uint8)
                        image[0, 0, :2] = ord(row["cameraId"]), row["pts"]
                        return image

                    def close(self):
                        closed.append(self)

                class Detector:
                    ball_search = True

                    def __init__(self, settings, model, executable):
                        opened.append(self)

                    def observe(self, image, region, ocr_skip=(), control=None):
                        camera, frame = chr(image[0, 0, 0]), int(image[0, 0, 1])
                        with lock:
                            assignments[camera].add((self, threading.get_ident()))
                            samples.append((camera, frame))
                        if barrier:
                            barrier.wait()
                        if (camera, frame) == fail_on:
                            raise RuntimeError("模拟机位错误")
                        ball = {"source": "coreML", "pixel": [24, 20]} if (camera, frame) == ("A", 0) else None
                        return {"players": [], "ball": ball, "imageSize": [64, 48],
                                "timing": {"detectionNs": threading.get_ident()}}

                    def close(self):
                        closed.append(self)

                try:
                    with patch("observations.Detector", Detector), patch("replay_images.ReplayImages", Reader):
                        list(analyze_rows(config, rows, archive, root, {"workers": workers, "ballSearch": True},
                                          None, None, resume=True))
                finally:
                    self.assertCountEqual(opened, closed)
                    self.assertTrue(all(len(bindings) == 1 for bindings in assignments.values()))
                    if not samples:
                        self.assertEqual(opened, [])
                return samples

            with SceneArchive(root / "serial.sqlite") as serial, SceneArchive(root / "parallel.sqlite") as parallel:
                run(serial, 1)
                with self.assertRaisesRegex(RuntimeError, "模拟机位错误"):
                    run(parallel, 4, fail_on=("C", 1))
                self.assertEqual(parallel.last_seq("match", "one"), 0)
                self.assertCountEqual(run(parallel, 4), [(c, n) for n in (1, 2) for c in "ABCD"])
                for seq in range(3):
                    self.assertEqual(serial.frame("match", "one", seq), parallel.frame("match", "one", seq))
                    before = serial.evidence("match", "one", seq)
                    after = parallel.evidence("match", "one", seq)
                    self.assertEqual(before, after)
                    self.assertEqual([r["cameraId"] for r in after["observations"]], list("ABCD"))
                    self.assertTrue(all("timing" not in r for r in after["observations"]))
                self.assertEqual(parallel.evidence("match", "one", 1)["observations"][0]["ballSearch"]["mode"],
                                 "history")
                self.assertEqual(run(parallel, 4), [])

    @staticmethod
    def make_config(root):
        cameras = {name: {"imageSize": [64, 48], "distortion": [0] * 5,
                          "intrinsics": {"fx": 50, "fy": 50, "cx": 32, "cy": 24},
                          "rotationWorldToCamera": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                          "translationM": [index, 0, 10]} for index, name in enumerate("ABCD")}
        (root / "report.json").write_text(json.dumps({"calibrationId": "pitch", "sampleSource": "synthetic",
                                                    "cameras": cameras}))
        return MatchConfig({"matchId": "match", "streamEpoch": "one", "startUtcUs": "0", "sampleSource": "synthetic",
                            "calibrations": [{"calibrationId": "pitch", "fromUtcUs": "0", "report": "report.json"}]},
                           root)


if __name__ == "__main__":
    unittest.main()
