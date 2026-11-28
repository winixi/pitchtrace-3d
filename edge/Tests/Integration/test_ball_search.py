"""验证搜索裁片的像素边界、连续性和已归档曝光的恢复行为。"""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

EDGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EDGE / "Sources/PitchTraceEdge/Geometry"))
from ball_search import BallSearch, cold_region, motion_candidate
from calibrate import calibrate
from match_config import MatchConfig
from observations import validate_observation
from process_match import analyze_rows
from scene_archive import SceneArchive


def row(utc, camera="A", epoch="one", pixel=None):
    return {"cameraId": camera, "mediaEpoch": epoch, "captureUtcUs": str(utc),
            "pts": utc, "segment": 0, "imageSize": [800, 600],
            "players": [], "ball": {"pixel": pixel} if pixel is not None else None}


class BallSearchTests(unittest.TestCase):
    def test_cold_proposals_require_model_confirmation(self):
        image = np.full((540, 960, 3), (40, 95, 50), np.uint8)
        cv2.line(image, (80, 0), (80, 539), (236, 236, 236), 3)
        cv2.circle(image, (750, 145), 5, (248, 248, 248), -1)
        detector = Mock()
        detector.observe.return_value = {"players": [], "imageSize": [960, 540], "ball": None}
        search = BallSearch()
        current = row(100_000)
        result = search.observe(detector, "v1", current, image)
        x, y, w, h = result["ballSearch"]["region"]
        self.assertTrue(x <= 750 < x+w and y <= 145 < y+h)
        self.assertEqual(result["ballSearch"]["mode"], "cold")
        self.assertTrue(result["ballSearch"]["ran"])
        self.assertIsNone(result["ball"])
        detector.observe.assert_called_once()
        detector.observe.return_value = {"players": [], "imageSize": [960, 540], "ball": {"source": "coreML-ROI", "pixel": [750, 145]}}
        found = search.observe(detector, "v1", current, image)
        self.assertEqual(found["ball"]["searchMode"], "cold")
        search.record("v1", {**current, **found})
        detector.observe.return_value = {"players": [], "imageSize": [960, 540], "ball": {"source": "coreML", "pixel": [750, 145]}}
        acquired = search.observe(detector, "v1", row(200_000), image)
        self.assertEqual(acquired["ballSearch"]["mode"], "history")
        self.assertFalse(acquired["ballSearch"]["ran"])

    def test_cold_scan_tiles_cover_frame_without_appearance_cues(self):
        image = np.zeros((193, 500, 3), np.uint8)
        covered = np.zeros(image.shape[:2], np.uint8)
        for slot in range(8):
            x, y, w, h = cold_region([500, 193], slot*100_000, [], None)
            self.assertTrue(0 <= x <= 500-w and 0 <= y <= 193-h)
            covered[y:y+h, x:x+w] = 1
        self.assertTrue(covered.all())
        self.assertIsNone(cold_region([500, 16], 0, [], None))

    def test_motion_requires_unique_consistent_exposures(self):
        def dot(x):
            return [x, 50, 10, .65]

        samples = [(0, [dot(10)]), (100_000, [dot(20)])]
        self.assertEqual(motion_candidate([dot(30)], samples, 200_000)[0], dot(30))
        self.assertIsNone(motion_candidate([dot(20)], samples, 200_000))
        self.assertIsNone(motion_candidate([dot(30)], samples, 800_000))
        self.assertIsNone(motion_candidate([dot(30)], samples[-1:], 200_000))
        ambiguous = [(0, [dot(10), dot(70)]), (100_000, [dot(20), dot(80)])]
        self.assertIsNone(motion_candidate([dot(30), dot(90)], ambiguous, 200_000))
        # 四个不同强度的场地标记共同平移，最亮的一个也不能成为球。
        def pan(delta):
            return [[30 * i + delta, 50, 10, .7 if i == 0 else .2] for i in range(4)]
        self.assertIsNone(motion_candidate(pan(12), [(0, pan(0)), (100_000, pan(6))], 200_000))

    def test_motion_observation_requires_a_current_blob_outside_player_boxes(self):
        detector = Mock()
        size = [320, 240]
        image = np.zeros((240, 320, 3), np.uint8)
        def measure(players):
            search = BallSearch()
            for index in range(3):
                key = row(100_000 * index)
                candidate = [70 + 10 * index, 55, 10, .7]
                detector.observe.return_value = {"imageSize": size, "ball": None, "players": players}
                with patch("ball_search.appearance_candidates", return_value=[candidate]):
                    result = search.observe(detector, "v1", key, image)
                search.record("v1", {**key, **result})
            return result
        ball = measure([])["ball"]
        self.assertEqual((ball["source"], ball["pixel"]), ("motion-appearance", [90, 55]))
        self.assertIsNone(measure([{"box": [50, 20, 65, 35]}])["ball"])

    def test_pixel_motion_result_passes_production_validation_before_json(self):
        search = BallSearch()
        detector = Mock()
        detector.observe.side_effect = lambda image, roi, ocr_skip=(): {"imageSize": [320, 240], "players": [], "ball": None}
        for index in range(3):
            image = np.full((240, 320, 3), (45, 105, 50), np.uint8)
            cv2.circle(image, (90 + 8 * index, 90), 5, (246, 246, 246), -1)
            key = row(index * 100_000)
            evidence = {**key, **search.observe(detector, "v1", key, image)}
            validate_observation(evidence)
            roundtrip = json.loads(json.dumps(evidence, allow_nan=False))
            self.assertEqual(evidence, roundtrip)
            validate_observation(roundtrip)
            search.record("v1", roundtrip)
        self.assertEqual(evidence["ball"]["source"], "motion-appearance")
        self.assertIs(type(evidence["ball"]["confidence"]), float)
        self.assertTrue(all(type(score) is float for score in evidence["ball"]["quality"].values()))

    def test_sparse_motion_state_replays_and_resets_at_camera_boundaries(self):
        size = [320, 240]
        history = []
        for i in range(2):
            key = row(100_000 * i)
            history.append({**key, "imageSize": size, "ballSearch": {"candidates": [[70 + 10 * i, 55, 10, .7]]}})
        def resume(version, current, candidate=True):
            search = BallSearch()
            for evidence in json.loads(json.dumps(history)):
                search.record("v1", evidence)
            detector = Mock()
            detector.observe.return_value = {"imageSize": size, "ball": None, "players": []}
            with patch("ball_search.appearance_candidates", return_value=[[90, 55, 10, .7]] if candidate else []):
                return search.observe(detector, version, current, np.zeros((240, 320, 3), np.uint8))["ball"]
        self.assertEqual(resume("v1", row(200_000))["source"], "motion-appearance")
        self.assertIsNone(resume("v1", row(200_000), candidate=False))
        self.assertIsNone(resume("v1", row(200_000, camera="B")))
        self.assertIsNone(resume("v1", row(200_000, epoch="two")))
        self.assertIsNone(resume("v2", row(200_000)))
        self.assertIsNone(resume("v1", row(600_000)))

    def test_velocity_expansion_and_expiry_use_measurement_time(self):
        search = BallSearch()
        first = row(0, pixel=[300, 250])
        first["ball"]["source"] = "ballHSV"
        search.record("v1", first)
        search.record("v1", row(100_000, pixel=[320, 250]))
        size = first["imageSize"]
        nearby = search.region("v1", row(200_000), size)
        self.assertEqual(nearby[0] + nearby[2] / 2, 340)
        search.record("v1", row(200_000))
        limit = search.region("v1", row(400_000), size)
        self.assertGreater(limit[2], nearby[2])
        self.assertEqual(limit[0] + limit[2] / 2, 380)
        self.assertIsNone(search.region("v1", row(400_001), size))
        search.record("v1", row(500_000, pixel=[450, 300]))
        fresh = search.region("v1", row(600_000), size)
        self.assertEqual(fresh[0] + fresh[2] / 2, 450)

    def test_camera_calibration_period_size_and_boundaries(self):
        search = BallSearch()
        first = row(0, pixel=[799, 0])
        size = first["imageSize"]
        search.record("v1", first)
        self.assertIsNone(search.region("v1", row(1, camera="B"), size))
        left, top, width, height = search.region("v1", row(1), size)
        self.assertEqual((left + width, top, width), (800, 0, height))
        for version, current, current_size in (
            ("v2", row(1), size),
            ("v1", row(1, epoch="two"), size),
            ("v1", row(1), [640, 480]),
        ):
            with self.subTest(version=version, row=current, size=current_size):
                search.record("v1", first)
                self.assertIsNone(search.region(version, current, current_size))

    def test_archive_resume_restores_only_measured_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = calibrate(json.loads((EDGE / "samples/calibration.json").read_text()))
            (root / "calibration.json").write_text(json.dumps(report))
            config = MatchConfig({
                "matchId": "training", "streamEpoch": "one", "sampleSource": "synthetic", "startUtcUs": "0",
                "calibrations": [{"fromUtcUs": "0", "calibrationId": "v1", "report": "calibration.json"}],
            }, root)
            size = report["cameras"]["A"]["imageSize"]
            image = np.zeros((size[1], size[0], 3), np.uint8)
            rows = [{"cameraId": "A", "segment": 0, "pts": i, "captureUtcUs": str(i * 100_000)}
                    for i in range(6)]

            # 虚拟相机只在前两个曝光看到球。场景通过实际的归档、证据回读和重建入口。
            def run(database, count, enabled=True):
                regions = []

                def read(frame):
                    image[0, 0, 0] = frame["pts"]
                    return image

                def observe(image, region, ocr_skip=()):
                    regions.append(region)
                    index = int(image[0, 0, 0])
                    ball = {"pixel": [700 + 50 * index, 400]} if index < 2 else None
                    return {"players": [], "ball": ball, "imageSize": size}

                with patch("observations.Detector") as worker, patch("replay_images.ReplayImages") as reader:
                    worker.return_value.ball_search = enabled
                    worker.return_value.observe.side_effect = observe
                    reader.return_value.read.side_effect = read
                    with SceneArchive(root / database) as archive:
                        list(analyze_rows(config, rows[:count], archive, root, {"ballSearch": enabled},
                                          None, None, resume=True))
                return regions

            uninterrupted = run("full.sqlite", 6)
            self.assertEqual(run("partial.sqlite", 3), uninterrupted[:3])
            self.assertEqual(run("partial.sqlite", 6), uninterrupted[3:])
            self.assertIsNotNone(uninterrupted[4])
            self.assertIsNotNone(uninterrupted[5])  # 历史过期后重新进入冷搜。
            self.assertEqual(run("disabled.sqlite", 6, enabled=False), [None] * 6)


if __name__ == "__main__":
    unittest.main()
