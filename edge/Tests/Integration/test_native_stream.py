"""模拟相机直采时边录边分析：camera-capture 写出的片段与曝光清单直接驱动 stream-match。需先构建 Edge。"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

EDGE = Path(__file__).resolve().parents[2]
BINARY = EDGE / ".build/debug/pitchtrace-edge"


class NativeStreamTest(unittest.TestCase):
    def test_scenes_follow_simulated_capture_while_recording(self):
        self.run_stream()

    def test_backlog_degrades_to_primary_camera(self):
        # 样例曝光时刻早于当前时间，积压直接触发 3 级：停读号、无近期球心不算姿态、跳过 B。
        degradation = {"ocrAfterSeconds": 1, "poseAfterSeconds": 2, "secondaryAfterSeconds": 3,
                       "secondaryCameras": ["B"]}
        stats, run = self.run_stream({"backend": "vision", "degradation": degradation})
        self.assertEqual(stats["degradation"]["level"], 3)
        self.assertEqual(stats["degradation"]["framesByLevel"], [0, 0, 0, 60])
        with closing(sqlite3.connect(run / "scenes.sqlite")) as db:
            proofs = [json.loads(row[0]) for row in db.execute("SELECT evidence FROM edge_scenes ORDER BY seq")]
        self.assertEqual(len(proofs), 60)
        self.assertTrue(all(p["degraded"]["skippedCameras"] == ["B"] for p in proofs))
        self.assertTrue(all([r["cameraId"] for r in p["observations"]] == ["A"] for p in proofs))

    def run_stream(self, detector=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        subprocess.run([sys.executable, "-B", EDGE / "samples/generate-match.py", root], check=True,
                       capture_output=True)
        match = json.loads((root / "match.json").read_text())
        start = int(match["startUtcUs"])
        for key in ("recordings", "mediaEpoch"):
            match.pop(key)
        match["calibrations"] = match["calibrations"][:1]
        match.update(captureConfig="cameras.json", streamWaitSeconds=1)
        if detector is not None:
            match["detector"] = detector
        (root / "match.json").write_text(json.dumps(match))
        cameras = {name: {"type": "simulated", "source": "pattern", "width": 320, "height": 192, "framerate": 10,
                          "clock": {"mode": "ptp-utc"}, "reconnectAttempts": 0, "mediaEpoch": "live",
                          "simulate": {"frames": 60, "startUtcUs": start + 1000 * (i + 1)}}
                   for i, name in enumerate("AB")}
        (root / "cameras.json").write_text(json.dumps({"matchId": match["matchId"], "segmentSeconds": 1,
                                                       "cameras": cameras}))
        raw, run = root / "capture", root / "stream"
        capture = subprocess.Popen([BINARY, "capture", root / "cameras.json", raw],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: capture.poll() is None and capture.kill())
        deadline = time.monotonic() + 10
        while not (raw / "match.json").exists():
            self.assertLess(time.monotonic(), deadline, "采集目录未初始化")
            time.sleep(.05)
        stream = subprocess.Popen([BINARY, "stream-match", root / "match.json", raw, run],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: stream.poll() is None and stream.kill())

        def scenes():
            try:
                with closing(sqlite3.connect(run / "scenes.sqlite")) as db:
                    return db.execute("SELECT COUNT(*) FROM edge_scenes").fetchone()[0]
            except sqlite3.Error:
                return 0

        # 采集共 6 秒、每段 1 秒，场景应在采集结束前开始入库。
        while capture.poll() is None and scenes() == 0:
            time.sleep(.1)
        self.assertIsNone(capture.poll(), "采集结束前没有任何场景入库")
        _, capture_error = capture.communicate(timeout=60)
        self.assertEqual(capture.returncode, 0, capture_error)
        output, error = stream.communicate(timeout=120)
        self.assertEqual(stream.returncode, 0, error)
        stats = json.loads(output)
        self.assertEqual(stats["status"], "completed")
        self.assertEqual(stats["committedRows"], 120)
        self.assertEqual(stats["frames"], 60)
        self.assertEqual(json.loads((run / "media/stream-health.json").read_text())["issues"], [])
        with closing(sqlite3.connect(run / "scenes.sqlite")) as db:
            first, last = db.execute("SELECT MIN(seq), MAX(seq) FROM edge_scenes").fetchone()
        self.assertEqual((first, last), (0, 59))
        for name in "AB":
            state = json.loads((raw / f"raw/{name}/live/status.json").read_text())
            self.assertEqual(state["status"], "completed")
            self.assertTrue(state["attached"])
        return stats, run


if __name__ == "__main__":
    unittest.main()
