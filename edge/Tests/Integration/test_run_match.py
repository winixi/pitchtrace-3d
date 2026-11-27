"""核对运行编排的断点、源文件核验、独占锁和不同上传目的地。"""

from contextlib import closing
import fcntl
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

EDGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EDGE / "Sources/PitchTraceEdge/Geometry"))
from calibrate import calibrate
from run_match import Run, run_match


class RunMatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.config_path = self.base / "match.json"
        self.root = self.base / "result"
        self.recordings = self.base / "recordings"
        for camera in ("A", "B"):
            (self.recordings / camera).mkdir(parents=True)
        (self.recordings / "A" / "init.mp4").write_bytes(b"source")
        report = calibrate(json.loads((EDGE / "samples/calibration.json").read_text()))
        (self.base / "report.json").write_text(json.dumps(report))
        self.config = {
            "matchId": "trial", "streamEpoch": "one", "startUtcUs": "0", "sampleSource": "synthetic",
            "recordings": "recordings", "calibrations": [
                {"fromUtcUs": "0", "calibrationId": "opening", "report": "report.json"}],
        }
        self.calls = []
        self.mutex = threading.Lock()
        self.reject = None
        self.binary = self.base / "edge"
        self.binary.write_text("测试替身")
        owner = self

        def command(_, name, *args):
            label = name + ("/" + args[-1].name if name == "publish-media" else "")
            with owner.mutex:
                owner.calls.append(label)
            if name == "capture":
                settings = json.loads(args[0].read_text())
                for camera in settings["cameras"]:
                    if settings["cameras"][camera]["source"] == "missing.mp4":
                        continue
                    period = args[1] / "recordings" / camera / "epochs/opening"
                    period.mkdir(parents=True)
                    (period / "clock.json").write_text("{}")
            if name == "replay":
                shutil.copytree(args[0], args[1])
                (args[1] / "frames.ndjson").write_text("")
            if name == "process-match":
                with closing(sqlite3.connect(args[2])) as db:
                    db.executescript("""
                        CREATE TABLE IF NOT EXISTS edge_runs(match_id,epoch,provenance);
                        CREATE TABLE IF NOT EXISTS edge_scenes(match_id,epoch,seq,payload,evidence);
                        INSERT INTO edge_runs VALUES('trial','one','{}');
                    """)
            if label == owner.reject:
                raise ValueError("模拟传输失败")

        hook = patch.object(Run, "command", command)
        hook.start()
        self.addCleanup(hook.stop)

    def submit(self):
        self.config_path.write_text(json.dumps(self.config))
        return run_match(self.config_path, self.root, self.binary)

    def test_resume_partial_confirmations_and_new_destination(self):
        self.assertEqual(self.submit()["status"], "completed")
        self.assertEqual(self.calls, ["replay", "process-match"])
        self.config["gateway"] = "http://127.0.0.1:8766"
        self.reject = "publish-media/A"
        with self.assertRaisesRegex(ValueError, "media/A"):
            self.submit()
        state = json.loads((self.root / "run-state.json").read_text())
        receipts = state["destinations"][self.config["gateway"]]
        self.assertEqual(receipts["scenes"], "confirmed")
        self.assertEqual(receipts["media/B"], "confirmed")
        self.assertEqual(state["failedStage"], "publishing")
        self.calls.clear()
        self.reject = None
        self.submit()
        self.assertEqual(self.calls, ["publish-media/A"])
        self.calls.clear()
        self.config["gateway"] += "/other"
        self.submit()
        self.assertEqual(sorted(self.calls), ["publish-media/A", "publish-media/B", "publish-scenes"])

    def test_rejects_changed_source_and_output(self):
        self.submit()
        self.calls.clear()
        path = self.recordings / "A/init.mp4"
        path.write_bytes(b"altered")
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.submit()
        path.write_bytes(b"source")
        (self.root / "media/A/init.mp4").write_bytes(b"altered")
        with self.assertRaisesRegex(ValueError, "媒体内容发生变化"):
            self.submit()
        self.assertEqual(self.calls, [])

    def test_manifest_or_weights_change_requires_new_run(self):
        models = self.base / "models"
        models.mkdir()
        weights = models / "detector.mlmodel"
        weights.write_bytes(b"test weights")
        manifest = models / "model.json"
        manifest.write_text(json.dumps({
            "format": "pitchtrace-vision-model-v1", "model": weights.name, "version": "test",
            "task": "ball", "preprocessing": "vision-scale-fit", "source": "测试夹具", "license": "MIT",
        }))
        self.config["detector"] = {"modelManifest": "models/model.json"}
        self.submit()
        self.calls.clear()
        for path in (manifest, weights):
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "已变化"):
                self.submit()
            path.write_bytes(original)
        self.submit()
        self.assertEqual(self.calls, [])

    def test_capture_is_reused_on_restart(self):
        del self.config["recordings"]
        self.config["captureConfig"] = "capture.json"
        settings = {"matchId": "trial", "cameras": {name: {"type": "file", "source": "recordings/A/init.mp4"}
                                                    for name in ("A", "B")}}
        (self.base / "capture.json").write_text(json.dumps(settings))
        self.submit()
        self.submit()
        self.assertEqual(self.calls, ["capture", "replay", "process-match"])

    def test_lock_and_foreign_directory(self):
        self.root.mkdir()
        with (self.root / "run.lock").open("ab") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(ValueError, "正在?被使用"):
                self.submit()
        (self.root / "user.txt").write_text("现有资料")
        with self.assertRaisesRegex(ValueError, "空目录"):
            self.submit()
        self.assertEqual(self.calls, [])

    def test_capture_failure_keeps_other_synchronized_cameras(self):
        del self.config["recordings"]
        self.config["captureConfig"] = "capture.json"
        settings = {"matchId": "trial", "cameras": {
            camera: {"type": "file", "source": "missing.mp4" if camera == "C" else "recordings/A/init.mp4"}
            for camera in ("A", "B", "C")}}
        (self.base / "capture.json").write_text(json.dumps(settings))
        self.reject = "capture"
        state = self.submit()
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["missingCaptureCameras"], ["C"])
        self.assertEqual(state["capturedCameras"], ["A", "B"])
        self.assertIn("失败", state["captureDiagnostic"])
        self.assertEqual(self.calls, ["capture", "replay", "process-match"])


if __name__ == "__main__":
    unittest.main()
