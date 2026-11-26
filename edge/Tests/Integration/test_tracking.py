"""使用俯视虚拟相机检查人球的几何、关联、缺测及 v2 数量上限。"""

import math
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Sources/PitchTraceEdge/Geometry"))
from tracking import Tracker, assign


CENTERS = {"A": [-2, 8, 0], "B": [2, 8, 0], "C": [0, 8, 2], "D": [0, 8, -2]}


def cameras():
    return {
        key: {"rotationWorldToCamera": [[1, 0, 0], [0, 0, 1], [0, -1, 0]],
              "translationM": [-x, -z, y], "imageSize": [1200, 1000],
              "intrinsics": {"fx": 600, "fy": 600, "cx": 600, "cy": 500}, "distortion": [0.] * 5}
        for key, (x, y, z) in CENTERS.items()
    }


def pixel(cam, xyz):
    x, y, z = xyz
    cx, cy, cz = CENTERS[cam]
    return [600 + 600 * (x - cx) / (cy - y), 500 + 600 * (z - cz) / (cy - y)]


def row(cam, utc, players=(), ball=None):
    return {"cameraId": cam, "captureUtcUs": str(utc), "pts": utc, "segment": 7, "mediaEpoch": "reset",
            "imageSize": [1200, 1000], "players": [{"foot": pixel(cam, (x, 0, z))} for x, z in players],
            "ball": {"pixel": pixel(cam, ball), "confidence": .85} if ball is not None else None}


class TrackingTests(unittest.TestCase):
    def test_foot_association_and_evidence(self):
        tracker = Tracker()
        observations = [row("A", 0, [(-.5, 1), (.5, 1)]), row("B", 0, [(.5, 1), (-.5, 1)])]
        people, ball, evidence = tracker.update(0, cameras(), observations[::-1])
        self.assertEqual(len(people), 2)
        self.assertIsNone(ball)
        np.testing.assert_allclose(people[0]["positionM"], [-.5, 0, 1], atol=1e-7)
        ids = [p["trackId"] for p in people]
        for key in ids:
            self.assertEqual([r["cameraId"] for r in evidence[key]], ["A", "B"])
            self.assertEqual(evidence[key][0]["mediaEpoch"], "reset")
            self.assertEqual(evidence[key][0]["segment"], 7)
        # 两人交叉，足点顺序翻转。利用上一段速度仍可分别连接。
        for t, positions in [(100000, [(-.1, 1), (.1, 1)]), (200000, [(.3, 1), (-.3, 1)])]:
            values = [row("A", t, positions), row("B", t, positions[::-1])]
            people, _, _ = tracker.update(t, cameras(), values)
        self.assertEqual([p["trackId"] for p in people], ids)
        self.assertAlmostEqual(people[0]["positionM"][0], .3)

    def test_single_view_ball_short_predictions_and_loss(self):
        tracker = Tracker()
        pos = (.1, 1.5, .2)
        rows = [row(c, 0, [(1, 0)], pos) for c in ("A", "B")]
        _, lost, evidence = Tracker().update(0, cameras(), rows[:1])
        self.assertEqual(lost["state"], "lost")
        self.assertIsNone(lost["positionM"])
        self.assertEqual(evidence["_ball"][0]["cameraId"], "A")
        people, ball, _ = tracker.update(0, cameras(), rows)
        np.testing.assert_allclose(ball["positionM"], pos, atol=1e-6)
        self.assertEqual(ball["state"], "measured")
        self.assertEqual(ball["views"], ["A", "B"])
        people, ball, evidence = tracker.update(300_000, cameras(), [row("B", 300_000, [], (.2, 1, 0))])
        self.assertEqual((people[0]["state"], ball["state"]), ("predicted", "predicted"))
        self.assertEqual(list(evidence), ["_ball"])
        people, ball, _ = tracker.update(300_001, cameras(), [])
        self.assertEqual((people[0]["positionM"], ball["positionM"]), (None, None))
        self.assertEqual((people[0]["state"], ball["state"]), ("lost", "lost"))
        people, _, _ = tracker.update(1_000_001, cameras(), [])
        self.assertEqual(people, [])

    def test_outlier_and_disabled_views(self):
        rows = [row(c, 10, [(1, 0)], (.2, 1.5, -.5)) for c in CENTERS]
        rows[-1]["ball"]["pixel"] = [800, 450]
        people, ball, evidence = Tracker().update(10, cameras(), rows)
        self.assertEqual(ball["views"], ["A", "B", "C"])
        self.assertEqual(len(evidence["_ball"]), 4)
        np.testing.assert_allclose(ball["positionM"], [.2, 1.5, -.5], atol=1e-6)
        enabled = {"A": cameras()["A"]}
        rows[0]["players"] = [{"box": [20, 30, 20, 60]}]
        people, ball, _ = Tracker().update(10, enabled, rows)
        self.assertEqual(people, [])
        self.assertEqual(ball["views"], ["A"])

    def test_reject_bad_depth_pixel_and_time(self):
        tracker = Tracker()
        reports = cameras()
        reports["B"] = reports["A"]
        rows = [row(cam, 0, [], (.2, 2, .1)) for cam in ("A", "B")]
        people, ball, _ = tracker.update(0, reports, rows)
        self.assertEqual(ball["state"], "lost")
        with self.assertRaises(ValueError):
            tracker.update(0, reports, [])
        rows[0]["ball"]["pixel"] = [100, 1000]
        with self.assertRaises(ValueError):
            tracker.update(1, reports, rows)

    def test_calibration_switch_uses_remaining_measurements_and_new_ids(self):
        tracker = Tracker()
        reports = cameras()
        a = row("A", 0, [(-.2, 0)], (0, 1, 0))
        b = row("B", 0, [(.2, 0), (2, 0)], (0, 1, 0))
        people, _, _ = tracker.update(0, reports, [a, b])
        common, isolated = [p["trackId"] for p in people]
        tracker.reset_geometry({"B"})
        people, ball, _ = tracker.update(10_000, reports, [])
        self.assertEqual([p["trackId"] for p in people], [common])
        self.assertAlmostEqual(people[0]["positionM"][0], -.2)
        self.assertIsNone(ball)
        people, _, _ = tracker.update(20_000, reports, [row("A", 20_000, [(0, 0), (2, 0)])])
        self.assertEqual(people[0]["trackId"], common)
        self.assertNotEqual(people[1]["trackId"], isolated)
        previous_ids = [p["trackId"] for p in people]
        tracker.reset_geometry()
        people, _, _ = tracker.update(30_000, reports, [row("A", 30_000, [(0, 0)])])
        self.assertNotIn(people[0]["trackId"], previous_ids)

    def test_calibration_switch_rechecks_ball_from_two_unmoved_cameras(self):
        tracker = Tracker()
        reports = cameras()
        pos = [1, 1.6, -.5]
        tracker.update(0, reports, [row(c, 0, [], pos) for c in ("A", "B", "C")])
        tracker.reset_geometry({"A"})
        _, ball, _ = tracker.update(10_000, reports, [])
        self.assertEqual(ball["state"], "predicted")
        np.testing.assert_allclose(ball["positionM"], pos, atol=1e-6)
        tracker.reset_geometry({"C"})
        _, ball, _ = tracker.update(20_000, reports, [])
        self.assertIsNone(ball)

    def test_maximum_cardinality_and_protocol_limit(self):
        self.assertEqual(sorted(assign([[1, 3], [2, math.inf]])), [(0, 1), (1, 0)])
        self.assertEqual(assign([[math.inf, math.inf]]), [])
        coords = [(i / 12, 0) for i in range(40)]
        people, _, _ = Tracker().update(0, cameras(), [row("B", 0, coords)])
        self.assertEqual(len(people), 32)
        self.assertEqual(len({p["trackId"] for p in people}), 32)


if __name__ == "__main__":
    unittest.main()
