"""模拟相机直采时边录边分析：camera-capture 写出的片段与曝光清单直接驱动 stream-match。需先构建 Edge。"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

EDGE = Path(__file__).resolve().parents[2]
BINARY = EDGE / ".build/debug/pitchtrace-edge"
LIVE = EDGE.parent / "live/server"


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

    def test_pause_holds_analysis_while_recording_continues(self):
        def pause_then_resume(run, scenes, capture):
            (run / "PAUSE").touch()
            time.sleep(1)
            held = scenes()
            state = json.loads((run / "stream-state.json").read_text())
            self.assertTrue(state["paused"])
            time.sleep(1.5)
            # 暂停期间不再入库，采集仍在运行、跟随线程照常登记新行。
            self.assertEqual(scenes(), held)
            self.assertIsNone(capture.poll())
            later = json.loads((run / "stream-state.json").read_text())
            self.assertGreater(later["committedRows"], state["committedRows"])
            self.assertIsNotNone(later["pausedSinceUnix"])
            (run / "PAUSE").unlink()

        stats, _ = self.run_stream(during=pause_then_resume)
        self.assertFalse(stats["paused"])

    def gateway(self, root):
        """以 required 模式启动本地网关，签发只能写 training 的设备凭据。"""
        env = {**os.environ, "PITCHTRACE_HOST": "127.0.0.1", "PITCHTRACE_PORT": "0",
               "PITCHTRACE_DB": str(root / "gateway.sqlite"), "PITCHTRACE_MEDIA_DIR": str(root / "gateway-media"),
               "PITCHTRACE_DEVICE_AUTH": "required"}
        node = subprocess.Popen(["node", LIVE / "server.cjs"], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(lambda: (node.terminate(), node.wait(timeout=10), node.stdout.close()))
        address = node.stdout.readline().strip().split(" ")[-1]
        token = root / "edge.token"
        subprocess.run(["node", LIVE / "tools/devices.mjs", "issue", "edge-a", "--match", "training", "--out", token],
                       env=env, check=True, capture_output=True)
        return address, token

    def test_authenticated_stream_uploads_scenes_and_media(self):
        def configure(root, match):
            address, token = self.gateway(root)
            match.update(gateway=address, deviceTokenFile=token.name)
            return address

        stats, run = self.run_stream(configure=configure)
        self.assertEqual(stats["pendingUploads"], {"scenes": 0, "mediaSegments": 0})
        with closing(sqlite3.connect(run / "scenes.sqlite")) as db:
            provenance = db.execute("SELECT provenance FROM edge_runs").fetchone()[0]
        self.assertNotIn("deviceTokenFile", provenance)
        self.assertNotIn("pt1.", provenance)

    def test_gateway_rejects_stream_without_credential(self):
        def configure(root, match):
            address, _ = self.gateway(root)
            match.update(gateway=address)

        error = self.run_stream(configure=configure, expect_failure=True)
        self.assertIn("401", error)

    def run_stream(self, detector=None, during=None, configure=None, expect_failure=False):
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
        if configure is not None:
            configure(root, match)
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
        if during:
            during(run, scenes, capture)
        _, capture_error = capture.communicate(timeout=60)
        self.assertEqual(capture.returncode, 0, capture_error)
        output, error = stream.communicate(timeout=120)
        if expect_failure:
            self.assertNotEqual(stream.returncode, 0)
            return error
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
