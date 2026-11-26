"""身份时段、三维关节局部坐标和可溯源的动作候选回归。"""

from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Sources/PitchTraceEdge/Geometry"))
from match_analysis import MatchAnalysis
from match_events import CandidateDetector
from pose import MODEL_TO_WORLD, local_pose


def subject(track="one", x=0):
    return {"trackId": track, "positionM": [x, 0, 0], "state": "measured"}


def cam(cx):
    return {"imageSize": [960, 720], "intrinsics": {"fx": 600, "fy": 600, "cx": 480, "cy": 360},
            "distortion": [0] * 5, "rotationWorldToCamera": [[1, 0, 0], [0, -1, 0], [0, 0, -1]],
            "translationM": [-cx, 1.2, 6.]}


def ref(camera, utc):
    return dict(cameraId=camera, segment=0, pts=utc, captureUtcUs=str(utc))


def observations(cameras, points, utc=0, number=7, team="home"):
    rows = []
    for name, camera in cameras.items():
        pixels = {}
        for joint, xyz in points.items():
            x, y, z = np.asarray(camera["rotationWorldToCamera"]) @ xyz + camera["translationM"]
            pixels[joint] = [float(480 + x / z * 600), float(360 + y / z * 600)]
        rows.append(dict(ref(name, utc), player=dict(joints=pixels, teamId=team, number=number)))
    return rows


class AnalysisTests(unittest.TestCase):
    def test_identity_and_substitution(self):
        roster = [
            dict(teamId="home", number=7, rosterId="first", untilUtcUs="400000"),
            dict(teamId="home", number=7, rosterId="second", fromUtcUs="400000"),
            dict(teamId="away", number=7, rosterId="opponent"),
            dict(teamId="home", number=9, rosterId="corrected"),
        ]
        analysis = MatchAnalysis(roster, [dict(trackId="one", fromUtcUs="500000", teamId="home", number=9)])
        cameras = {"A": cam(-2), "B": cam(2)}
        for i, expected in enumerate((None, None, "first", "first", "second", "corrected")):
            utc = i * 100_000
            rows = {track: observations(cameras, {}, utc, 7, team) for track, team in (("one", "home"), ("two", "away"))}
            players, events = analysis.update(utc, [subject(), subject("two")], None, cameras, rows)
            self.assertEqual(players[0]["trackId"], "one")
            self.assertEqual(players[0]["rosterId"], expected)
            self.assertEqual(players[1]["rosterId"], "opponent" if i >= 2 else None)
            self.assertFalse(events)

    def test_ambiguous_roster_and_conflicting_manual_intervals(self):
        roster = [dict(teamId="home", number=7, rosterId=i) for i in ("one", "two")]
        with self.assertRaises(ValueError):
            MatchAnalysis(roster, [dict(trackId="a", number=1, fromUtcUs="0")] * 2)
        analysis = MatchAnalysis(roster)
        cameras = {"A": cam(0)}
        for i in range(3):
            result, _ = analysis.update(i, [subject()], None, cameras, {"one": observations(cameras, {}, i)})
        self.assertEqual(result[0]["number"], 7)
        self.assertIsNone(result[0]["rosterId"])

    def test_triangulation_and_parent_local_rotations(self):
        points = {"leftHip": [.1, .9, 0], "rightHip": [-.1, .9, 0], "leftShoulder": [.2, 1.45, 0],
                  "rightShoulder": [-.2, 1.45, 0], "leftKnee": [.1, .48, 0], "leftAnkle": [.1, .48, .44],
                  "leftElbow": [.2, 1.12, 0], "leftWrist": [.2, 1.12, .3]}
        cameras = {"A": cam(-2), "B": cam(2)}
        rows = observations(cameras, points)
        del rows[1]["player"]["joints"]["leftWrist"]
        analysis = MatchAnalysis([])
        players, _ = analysis.update(0, [subject()], None, cameras, {"one": rows})
        measured = analysis.measurements["one"]["joints"]
        self.assertEqual(measured["leftWrist"]["status"], "insufficientViews")
        for name in points.keys() - {"leftWrist"}:
            np.testing.assert_allclose(measured[name]["reconstructedM"], points[name], atol=1e-8)
        bones = players[0]["bones"]
        np.testing.assert_allclose(bones["body"], [0, 0, 0, 1], atol=1e-8)
        self.assertAlmostEqual(players[0]["yawRad"], 0)
        self.assertNotIn("left_elbow", bones)
        self.assertNotIn("left_ankle", bones)
        np.testing.assert_allclose(bones["left_thigh"], [0, 0, 0, 1], atol=1e-8)
        # 验证把四元数施加到实际绑定骨段偏移后，该膝的踝端朝场地 +Z。
        x, y, z, w = bones["left_knee"]
        vector = np.array([x, y, z])
        rest = np.array([0, -.04, -.44])
        rotated = rest + 2 * np.cross(vector, np.cross(vector, rest) + w * rest)
        np.testing.assert_allclose(MODEL_TO_WORLD @ rotated / np.linalg.norm(rotated), [0, 0, 1], atol=1e-6)
        turn = np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]])
        yaw, turned = local_pose({k: turn @ v for k, v in points.items()})
        self.assertAlmostEqual(yaw, np.pi / 2)
        np.testing.assert_allclose(turned["left_knee"], bones["left_knee"], atol=1e-8)

    def test_kick_shot_pass_and_gaps(self):
        for pitch, expected in ((None, "kick"), (20, "shot")):
            detector = CandidateDetector(pitch, 5 if pitch else None)
            emitted = []
            for i, (foot, ball) in enumerate(((-.4, 0), (.1, .05), (.1, 1.15), (.1, 2.3), (.1, 2.5))):
                utc = i * 100_000
                tracks = {k: actor(k, x, ankle, utc) for k, x, ankle in (("a", 0, foot), ("b", 2.5, 2.5))}
                emitted.extend(feed(detector, utc, tracks, ball))
            self.assertEqual([e["kind"] for e in emitted], [expected, "pass"])
            self.assertEqual(emitted[1]["trackIds"], ["a", "b"])
            self.assertTrue(all(e["status"] == "candidate" and e["evidence"] for e in emitted))
        detector = CandidateDetector()
        for i, (foot, ball) in enumerate(((-.4, 0), (.1, .05), (.1, 1.15))):
            utc = i * 100_000
            events = feed(detector, utc, {"a": actor("a", 0, foot, utc)}, ball,
                          state="predicted" if i == 1 else "measured")
            self.assertFalse(events)

    def test_carry_beat_and_slide(self):
        detector = CandidateDetector()
        emitted = []
        for i in range(16):
            utc, x = i * 100_000, .2 * i
            tracks = {"a": actor("a", x, x + .1, utc), "b": actor("b", 1.3, 1.3, utc, "away", z=1.0)}
            emitted.extend(feed(detector, utc, tracks, x + .1))
        self.assertEqual([e["kind"] for e in emitted], ["dribble", "beat"])
        detector.reset()
        for i, (x, ankle) in enumerate(((1.4, 1.3), (1., .25))):
            utc = i * 100_000
            tackler = actor("b", x, ankle, utc, "away")
            tackler["joints"].update(leftHip=np.array([x, .4, 0]), leftKnee=np.array([(x + ankle) * .5, .2, 0]))
            events = feed(detector, utc, {"a": actor("a", 0, 0, utc), "b": tackler}, .1)
        self.assertEqual([e["kind"] for e in events], ["tackle"])


def actor(track, x, ankle, utc, team="home", z=0):
    return {"player": dict(subject(track, x), teamId=team, positionM=[x, 0, z]),
            "joints": {"leftAnkle": np.array([ankle, .1, z])}, "refs": [ref("A", utc)]}


def feed(detector, utc, tracks, x, state="measured"):
    return detector.update(utc, tracks, {"state": state, "positionM": [x, .11, 0]}, [ref("B", utc)])


if __name__ == "__main__":
    unittest.main()
