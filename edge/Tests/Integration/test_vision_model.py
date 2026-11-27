"""核验 Core ML 清单、工作进程传参和运行记录的可复算性。"""

import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

EDGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EDGE / "Sources/PitchTraceEdge/Geometry"))
from calibrate import calibrate
from observations import Detector
from process_match import process
from scene_archive import SceneArchive
from stream_match import stream_match
from vision_model import load_vision_model


class VisionModelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / "models/fixture.mlpackage"
        self.package.mkdir(parents=True)
        self.weights = self.package / "weights.bin"
        self.weights.write_bytes(b"test weights")
        self.manifest = {"format": "pitchtrace-vision-model-v1", "model": self.package.name,
                         "task": "person-and-ball", "preprocessing": "vision-scale-fit",
                         "version": "test-1", "source": "测试夹具", "license": "MIT"}
        self.manifest_path = self.package.parent / "detector.json"
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.settings = {"modelManifest": "models/detector.json"}

    def test_model_task_and_crop_packet_match_worker_arguments(self):
        image = np.zeros((24, 32, 3), dtype=np.uint8)
        for task, search, roi in (("ball", False, None), ("person-and-ball", True, None),
                                  ("ball", True, [3, 4, 12, 10]), ("person-and-ball", False, None)):
            with self.subTest(task=task, search=search, roi=roi), patch("observations.subprocess") as worker:
                self.manifest_path.write_text(json.dumps(dict(self.manifest, task=task)))
                settings = dict(self.settings, ballSearch=search)
                model = load_vision_model(settings, self.root)
                detector = Detector(settings, model, "edge")
                expected = ["edge", "vision-worker", str(self.package)]
                if task == "person-and-ball":
                    expected.append("--person-and-ball")
                if search:
                    expected.append("--ball-search")
                self.assertEqual(worker.Popen.call_args.args[0], expected)
                detector.worker.stdin = io.BytesIO()
                detector.worker.stdout = io.BytesIO(b'{"imageSize":[32,24],"players":[],"ball":null}\n')
                self.assertIsNone(detector.observe(image, ball_roi=roi)["ball"])
                data = detector.worker.stdin.getvalue()
                if search:
                    self.assertEqual(struct.unpack(">4I", data[:16]), tuple(roi or [0] * 4))
                    data = data[16:]
                self.assertEqual(struct.unpack(">I", data[:4])[0], len(data) - 4)
                self.assertEqual(data[4:6], b"\xff\xd8")
                detector.close()

        legacy = load_vision_model({"model": "models/fixture.mlpackage"}, self.root)
        self.assertEqual(legacy.worker_arguments(), [str(self.package)])
        self.assertIsNone(load_vision_model({}, self.root))

    def test_rejects_invalid_or_ambiguous_model_settings(self):
        for settings in (dict(self.settings, model="other"), dict(self.settings, backend="color-ball"),
                         {"modelManifest": ""}, {"ballSearch": True}, dict(self.settings, ballSearch=1),
                         {"backend": "color-ball", "model": "unused", "ballSearch": True}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                load_vision_model(settings, self.root)
        for key, invalid in (("format", "v2"), ("task", "people"), ("preprocessing", "fill"), ("model", []),
                             ("version", 1), ("source", None), ("license", " ")):
            self.manifest_path.write_text(json.dumps(dict(self.manifest, **{key: invalid})))
            with self.subTest(key=key), self.assertRaises(ValueError):
                load_vision_model(self.settings, self.root)
        self.manifest_path.write_text('{"format":"bad","format":"pitchtrace-vision-model-v1"}')
        with self.assertRaisesRegex(ValueError, "重复"):
            load_vision_model(self.settings, self.root)

    def test_offline_and_stream_bind_manifest_and_weights_to_epoch(self):
        report = calibrate(json.loads((EDGE / "samples/calibration.json").read_text()))
        (self.root / "report.json").write_text(json.dumps(report))
        config = self.root / "match.json"
        config.write_text(json.dumps({
            "matchId": "trial", "streamEpoch": "one", "sampleSource": "synthetic", "startUtcUs": "0",
            "detector": self.settings, "captureConfig": "inputs.json", "calibrations": [
                {"fromUtcUs": "0", "calibrationId": "opening", "report": "report.json"}],
        }))
        capture = self.root / "capture"
        capture.mkdir()
        (capture / "match.json").write_text('{"matchId":"trial"}')
        (self.root / "inputs.json").write_text(json.dumps({"matchId": "trial", "cameras": {
            name: {"type": "file", "source": f"{name}.mp4"} for name in ("A", "B")}}))
        row = {"cameraId": "A", "captureUtcUs": "0", "pts": 0, "segment": 0}

        def captured_rows(_, __, media, *args, **kwargs):
            media.mkdir(exist_ok=True)
            (media / "frames.ndjson").write_text(json.dumps(row) + "\n")
            (media / "stream-health.json").write_text('{"issues":[]}')
            yield row

        stream_dir = self.root / "live"
        executable = Path(sys.executable)
        media, offline_db = stream_dir / "media", self.root / "offline.sqlite"
        with patch("stream_match.follow_rows", captured_rows), patch("observations.subprocess") as worker, \
                patch("replay_images.ReplayImages.read", return_value=np.zeros((1080, 1920, 3), np.uint8)):
            worker.Popen.return_value.stdout.readline.return_value = b'{"imageSize":[1920,1080],"players":[],"ball":null}\n'
            self.assertEqual(stream_match(config, capture, stream_dir, executable)["frames"], 1)
            self.assertEqual(process("process-match", config, media, offline_db, executable)["frames"], 1)
            for db in (stream_dir / "scenes.sqlite", offline_db):
                with SceneArchive(db) as archive:
                    record = archive.run_info("trial", "one")
                self.assertEqual(record["modelManifest"], self.manifest)
                self.assertEqual(record["modelManifestSha256"], hashlib.sha256(self.manifest_path.read_bytes()).hexdigest())
                self.assertEqual(record["modelSha256"], {"weights.bin": hashlib.sha256(self.weights.read_bytes()).hexdigest()})
            for path in (self.manifest_path, self.weights):
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, "streamEpoch"):
                    stream_match(config, capture, stream_dir, executable)
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, "streamEpoch"):
                    process("process-match", config, media, offline_db, executable)
                path.write_bytes(original)

        self.manifest_path.unlink()
        annotations = self.root / "observations.ndjson"
        annotations.write_text(json.dumps(dict(row, imageSize=[1920, 1080], players=[], ball=None)) + "\n")
        result = process("reconstruct-match", config, annotations, self.root / "annotated.sqlite", executable)
        self.assertEqual(result["frames"], 1)


if __name__ == "__main__":
    unittest.main()
