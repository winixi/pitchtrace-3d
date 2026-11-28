"""用模拟相机检查 camera-capture 的编码分段、曝光清单、自动登记与故障周期。需先构建 Edge。"""
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

EDGE = Path(__file__).resolve().parents[2]
BINARY = EDGE / ".build/debug/pitchtrace-edge"
START_US = 1_795_494_597_700_000


def simulated(**simulate):
    return {"type": "simulated", "source": "pattern", "width": 320, "height": 180, "framerate": 30,
            "clock": {"mode": "ptp-utc"}, "reconnectAttempts": 0,
            "simulate": {"realtime": False, "startUtcUs": START_US, **simulate}}


class NativeCaptureTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "session"

    def capture(self, cameras, success=True, **config):
        path = self.base / "cameras.json"
        path.write_text(json.dumps({"matchId": "trial", "segmentSeconds": 1, "cameras": cameras, **config}))
        result = subprocess.run([BINARY, "capture", path, self.root], capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return json.loads((self.root / "health.json").read_text())["finished"]

    def period(self, camera, epoch):
        return self.root / "raw" / camera / epoch

    def rows(self, camera, epoch):
        lines = (self.period(camera, epoch) / "exposures.ndjson").read_text().splitlines()
        return json.loads(lines[0]), [json.loads(line) for line in lines[1:]]

    def test_ptp_cameras_attach_and_replay_in_exposure_order(self):
        cameras = {"A": {**simulated(frames=75, dropFrames=[10, 11]), "mediaEpoch": "first"},
                   "B": {**simulated(frames=60), "mediaEpoch": "first"}}
        cameras["B"]["simulate"]["startUtcUs"] = START_US + 5_000
        finished = self.capture(cameras)
        for name, frames in (("A", 75), ("B", 60)):
            state = finished[name]
            self.assertEqual(state["status"], "completed")
            self.assertTrue(state["attached"], state)
            self.assertEqual(state["capture"]["framesRecorded"], frames)
            self.assertIn("PTP 设备时间戳", state["clockSource"])
            clock = json.loads((self.root / "recordings" / name / "epochs/first/clock.json").read_text())
            self.assertEqual(clock["clockSource"], state["clockSource"])
        self.assertEqual(finished["A"]["capture"]["deviceFrameGaps"], 2)
        header, rows = self.rows("A", "first")
        self.assertEqual(header["timeBase"], "1/90000")
        # 曝光起点加 1 ms 半曝光；丢失的第 10、11 帧在帧号、UTC 与 PTS 中都留下间隔。
        self.assertEqual(rows[0], {"segment": 0, "pts": 0, "captureUtcUs": str(START_US + 1000), "frameNumber": 0})
        self.assertEqual([r["frameNumber"] for r in rows[9:11]], [9, 12])
        self.assertEqual(rows[10]["pts"] - rows[9]["pts"], 9000)
        self.assertIn("#EXT-X-ENDLIST", (self.period("A", "first") / "preview.m3u8").read_text())
        output = self.base / "replay"
        result = subprocess.run([BINARY, "replay", self.root / "recordings", output], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        merged = [json.loads(line) for line in (output / "frames.ndjson").read_text().splitlines()]
        self.assertEqual(len(merged), 135)
        utc = [int(r["captureUtcUs"]) for r in merged]
        self.assertEqual(utc, sorted(utc))

    def test_eight_cameras_capture_and_replay_nine_are_rejected(self):
        names = "ABCDEFGH"
        cameras = {}
        for i, name in enumerate(names):
            cameras[name] = {**simulated(frames=30), "mediaEpoch": "eight"}
            cameras[name]["simulate"]["startUtcUs"] = START_US + i * 1000
        finished = self.capture(cameras)
        self.assertTrue(all(finished[n]["attached"] for n in names), finished)
        output = self.base / "replay"
        result = subprocess.run([BINARY, "replay", self.root / "recordings", output], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        merged = [json.loads(line) for line in (output / "frames.ndjson").read_text().splitlines()]
        self.assertEqual(len(merged), 240)
        self.assertEqual({r["cameraId"] for r in merged}, set(names))
        path = self.base / "nine.json"
        path.write_text(json.dumps({"matchId": "trial", "cameras": {
            name: {**simulated(frames=30), "mediaEpoch": "nine"} for name in names + "I"}}))
        result = subprocess.run([BINARY, "capture", path, self.base / "nine"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("1 至 8 个机位", result.stderr)

    def test_host_fit_warms_up_and_stays_out_of_recordings(self):
        camera = {**simulated(frames=45), "clock": {"mode": "host-fit"}, "mediaEpoch": "low"}
        state = self.capture({"A": camera})["A"]
        self.assertEqual(state["status"], "completed")
        self.assertNotIn("attached", state)
        self.assertTrue(state["clockSource"].startswith("低精度"))
        self.assertEqual(state["capture"]["warmupFrames"], 15)
        _, rows = self.rows("A", "low")
        self.assertEqual(len(rows), 30)
        # 偏移取 15 个样本中的最小传输延迟：主机收帧 = 起点 + 曝光 + 8 ms，曝光中心 = 起点 + 1 ms。
        self.assertEqual(int(rows[0]["captureUtcUs"]), START_US + 15 * 33_333_333 // 1000 + 1000 + 9000)
        self.assertFalse((self.root / "recordings").exists())

    def test_clock_reset_and_ptp_loss_keep_sealed_frames(self):
        cameras = {"A": {**simulated(clockResetAtFrame=40), "mediaEpoch": "reset"},
                   "B": {**simulated(ptpUnlockAtFrame=0), "mediaEpoch": "unlock"}}
        finished = self.capture(cameras, success=False)
        self.assertEqual(finished["A"]["status"], "failed")
        self.assertIn("时钟重置", finished["A"]["error"])
        self.assertTrue(finished["A"]["attached"])
        self.assertEqual(len(self.rows("A", "reset")[1]), 40)
        self.assertEqual(finished["B"]["status"], "failed")
        self.assertIn("PTP", finished["B"]["error"])

    def test_stop_marker_seals_realtime_capture(self):
        camera = {**simulated(), "mediaEpoch": "live"}
        camera["simulate"] = {"realtime": True}
        timer = threading.Timer(2.5, lambda: (self.root / "STOP").touch())
        timer.start()
        self.addCleanup(timer.cancel)
        state = self.capture({"A": camera})["A"]
        self.assertEqual(state["status"], "stopped")
        self.assertTrue(state["attached"], state)
        self.assertGreaterEqual(state["capture"]["framesRecorded"], 30)

    def test_rejects_invalid_native_settings(self):
        for change in ({"clock": {"mode": "ntp"}}, {"clock": {"mode": "ptp-utc", "taiUtcOffsetSeconds": -1}},
                       {"width": 321}, {"pixelFormat": "RGB8"}, {"mvs": {}}, {"framerate": 0},
                       {"simulate": {"dropFrames": [-1]}}):
            with self.subTest(change=change):
                path = self.base / "bad.json"
                path.write_text(json.dumps({"matchId": "trial", "cameras": {"A": {**simulated(), **change}}}))
                result = subprocess.run([BINARY, "capture", path, self.base / "bad"], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("采集处理失败", result.stderr)

    def test_mvs_reports_missing_sdk_or_camera(self):
        camera = {**simulated(), "type": "mvs", "source": "DA1234567", "mediaEpoch": "mvs"}
        del camera["simulate"]
        state = self.capture({"A": camera}, success=False)["A"]
        # 链接 SDK 的构建会真实枚举设备；未链接时提示重新构建。
        self.assertRegex(state["error"], "未找到序列号为 DA1234567 的相机|MVS SDK")


if __name__ == "__main__":
    unittest.main()
