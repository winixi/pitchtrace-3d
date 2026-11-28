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
from observations import Detector, OcrSchedule, validate_observation
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

    def test_compute_units_and_ocr_hints_reach_worker(self):
        image = np.zeros((24, 32, 3), dtype=np.uint8)
        settings = dict(self.settings, computeUnits="cpuAndNeuralEngine", ocrRefreshMs=1000, ballSearch=True)
        model = load_vision_model(settings, self.root)
        with patch("observations.subprocess") as worker:
            detector = Detector(settings, model, "edge")
            self.assertEqual(worker.Popen.call_args.args[0][-4:],
                             ["--ball-search", "--ocr-hints", "--compute-units", "cpuAndNeuralEngine"])
            detector.worker.stdin = io.BytesIO()
            detector.worker.stdout = io.BytesIO(b'{"imageSize":[32,24],"players":[],"ball":null}\n' * 2)
            detector.observe(image, [1, 2, 3, 4], [[5, 6.4, 7, 8], [0, 0, 2, 2]])
            detector.observe(image)
            data = detector.worker.stdin.getvalue()
            self.assertEqual(struct.unpack(">4I", data[:16]), (1, 2, 3, 4))
            self.assertEqual(struct.unpack(">9I", data[16:52]), (2, 5, 6, 7, 8, 0, 0, 2, 2))
            length = struct.unpack(">I", data[52:56])[0]
            rest = data[56 + length:]
            self.assertEqual(struct.unpack(">5I", rest[:20]), (0, 0, 0, 0, 0))
            detector.close()
        with patch("observations.subprocess") as worker:
            Detector({"ocrRefreshMs": 500}, None, "edge")
            self.assertEqual(worker.Popen.call_args.args[0], ["edge", "vision-worker", "--ocr-hints"])

    def test_ocr_schedule_skips_confirmed_tracks_until_refresh(self):
        schedule = OcrSchedule(300_000)
        box_a, box_b = [10, 10, 20, 40], [60, 10, 20, 40]
        def rows(box, skipped=False):
            player = {"box": box, **({"ocrSkipped": True} if skipped else {})}
            return [{"cameraId": "A", "player": player}, {"cameraId": "B", "player": dict(player)}]
        self.assertEqual(schedule.hints(0), {})
        schedule.update(0, {"p-1": rows(box_a), "p-2": rows(box_b), "_ball": []}, {"p-1"})
        self.assertEqual(schedule.hints(100_000), {"A": [box_a], "B": [box_a]})
        schedule.update(100_000, {"p-1": rows(box_a, True), "p-2": rows(box_b)}, {"p-1", "p-2"})
        # 跳过的帧不刷新上次读号时刻，到期后恢复读号；新确认的轨迹随即进入免读号区域。
        self.assertEqual(schedule.hints(200_000), {"A": [box_a, box_b], "B": [box_a, box_b]})
        self.assertEqual(schedule.hints(300_000)["A"], [box_b])
        self.assertEqual(schedule.hints(100_000 + OcrSchedule.STALE_US + 1), {})
        schedule.update(200_000, {"p-2": rows(box_b, True)}, set())
        self.assertEqual(schedule.hints(300_000), {})

    def test_pipeline_reads_confirmed_number_again_after_refresh(self):
        from match_config import MatchConfig
        from process_match import analyze_rows

        sample = json.loads((EDGE / "samples/calibration.json").read_text())
        (self.root / "report.json").write_text(json.dumps(calibrate(sample)))
        config = MatchConfig({"matchId": "trial", "streamEpoch": "one", "startUtcUs": "0", "sampleSource": "synthetic",
                              "calibrations": [{"calibrationId": "pitch", "fromUtcUs": "0", "report": "report.json"}]},
                             self.root)
        foot = {name: sample["fitPoints"][0]["pixels"][name] for name in ("A", "B")}
        rows = [{"cameraId": c, "captureUtcUs": str(n * 100_000), "segment": 0, "pts": n}
                for n in range(7) for c in "AB"]
        calls = []

        class Reader:
            def __init__(self, root):
                pass

            def read(self, row):
                image = np.zeros((1080, 1920, 3), np.uint8)
                image[0, 0, :2] = ord(row["cameraId"]), row["pts"]
                return image

            def close(self):
                pass

        class FakeDetector:
            def __init__(self, settings, model, executable):
                pass

            def observe(self, image, region, ocr_skip=()):
                camera, frame = chr(image[0, 0, 0]), int(image[0, 0, 1])
                x, y = foot[camera]
                box = [x - 20, y - 100, 40, 100]
                calls.append((frame, camera, list(ocr_skip)))
                player = {"box": box, "foot": [x, y], "confidence": 0.9}
                # 与工作进程一致：命中免读号区域时不读号，并标记跳过。
                player.update({"ocrSkipped": True} if ocr_skip else {"number": 7})
                return {"imageSize": [1920, 1080], "players": [player], "ball": None}

            def close(self):
                pass

        with SceneArchive(self.root / "ocr.sqlite") as archive, patch("observations.Detector", FakeDetector), \
                patch("replay_images.ReplayImages", Reader):
            frames = list(analyze_rows(config, rows, archive, self.root, {"ocrRefreshMs": 250}, None, None))
        skipped = sorted({frame for frame, _, hints in calls if hints})
        # 第 0–2 帧三票确认号码；第 3、4 帧距上次读号不足 250 ms；第 5 帧重读后第 6 帧再跳过。
        self.assertEqual(skipped, [3, 4, 6])
        self.assertEqual([f["players"][0]["number"] for f in frames], [None, None, 7, 7, 7, 7, 7])

    def test_skipped_ocr_must_not_carry_a_number(self):
        row = {"cameraId": "A", "pts": 0, "segment": 0, "imageSize": [64, 64], "ball": None,
               "players": [{"box": [1, 1, 10, 20], "ocrSkipped": True}]}
        validate_observation(row)
        for player in ({"box": [1, 1, 10, 20], "ocrSkipped": False},
                       {"box": [1, 1, 10, 20], "ocrSkipped": True, "number": 7}):
            with self.subTest(player=player), self.assertRaises(ValueError):
                validate_observation(dict(row, players=[player]))

    def test_rejects_invalid_or_ambiguous_model_settings(self):
        for settings in (dict(self.settings, model="other"), dict(self.settings, backend="color-ball"),
                         {"modelManifest": ""}, {"ballSearch": True}, dict(self.settings, ballSearch=1),
                         {"backend": "color-ball", "model": "unused", "ballSearch": True},
                         {"computeUnits": "all"}, dict(self.settings, computeUnits="gpu"),
                         dict(self.settings, ocrRefreshMs=99), dict(self.settings, ocrRefreshMs=1000.0),
                         dict(self.settings, ocrRefreshMs=True), {"backend": "color-ball", "ocrRefreshMs": 500}):
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
