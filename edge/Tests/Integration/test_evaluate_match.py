import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Sources/PitchTraceEdge/Geometry"))
from evaluate_match import Scores, assign, evaluate, event_scores
from scene_archive import SceneArchive


class EvaluationTests(unittest.TestCase):
    def test_matching_maximizes_count_before_distance(self):
        self.assertEqual(assign([[.1, 1.9], [.2, 5]], 2), {0: 1, 1: 0})
        self.assertFalse(assign([[3, 4]], 2))
        self.assertEqual(len(assign([[0], [0]], 2)), 1)

    def test_missing_samples_switches_manual_numbers_duplicate_events(self):
        with tempfile.TemporaryDirectory() as work:
            db = Path(work) / "archive.sqlite"
            path = Path(work) / "refs.ndjson"
            header = dict(type="evaluation", sampleSource="synthetic", matchId="M", sampleDurationUs=100000,
                          eventWindow=dict(startUtcUs="0", endUtcUs="200000"))
            refs = [header]
            with SceneArchive(db) as archive:
                archive.register_run("M", "E", {"sampleSource": "synthetic"})
                for i, track in enumerate(("track-a", None, "track-b")):
                    stamp = str(i * 100000)
                    refs.append(dict(captureUtcUs=stamp, players=[dict(
                        personId="person", positionM=[0, 0, 0], number=7, numberSegmentId="segment",
                        joints={"nose": [0, 1, 0], "leftHip": None})],
                        ball=dict(kind="ground", positionM=[0, .11, 0])))
                    if track is None:
                        continue
                    person = dict(state="measured", trackId=track, positionM=[.1, 0, 0], number=9 if i else 7)
                    event = dict(kind="shot", trackIds=[track], startUtcUs=stamp, endUtcUs=stamp)
                    archive.append(dict(v=2, seq=i, matchTimeMs=i * 100, matchId="M", streamEpoch="E",
                                        captureUtcUs=stamp, calibrationId="rig", players=[person],
                                        ball=dict(state="predicted", positionM=[0, .11, 0]),
                                        events=[] if i else [event, event]),
                                   {"measurements": {track: {"identitySource": "manual" if not i else "vote",
                                     "joints": {"nose": dict(status="measured", reconstructedM=[0, 1.2, 0])}}}})
            refs.append(dict(type="event", kind="kick", personIds=["person"], startUtcUs="0", endUtcUs="0"))
            path.write_text("\n".join(map(json.dumps, refs)))
            result = evaluate(db, "M", "E", path)
            self.assertEqual(result["matchedFrames"], 2)
            self.assertEqual(result["players"]["measured"], 2)
            self.assertAlmostEqual(result["players"]["coverage"], 2 / 3)
            self.assertEqual(result["players"]["idSwitches"], 1)
            self.assertAlmostEqual(result["players"]["playerMinutes"], .3 / 60)
            self.assertEqual(result["joints"]["samples"], 6)
            self.assertEqual(result["joints"]["errorSamples"], 2)
            self.assertAlmostEqual(result["joints"]["meanErrorM"], .2)
            self.assertEqual(result["ball"]["ground"]["measured"], 0)
            self.assertEqual(result["numbers"]["accuracy"], 0)
            self.assertEqual(result["numbers"]["confirmed"], 1)
            kicks = result["events"]["classes"]["kick"]
            self.assertEqual((kicks["truePositive"], kicks["falsePositive"], kicks["falseNegative"]), (1, 1, 0))
            self.assertAlmostEqual(result["events"]["macroF1"], 2 / 3)
            self.assertEqual(result["events"]["macroClasses"], 1)
            refs.insert(2, refs[1])
            path.write_text("\n".join(map(json.dumps, refs)))
            with self.assertRaisesRegex(ValueError, "曝光"):
                evaluate(db, "M", "E", path)

    def test_nonexistent_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "refs"
            header = dict(type="evaluation", matchId="m", sampleSource="field")
            path.write_text(json.dumps(header) + "\n" + json.dumps(dict(captureUtcUs="0", players=[], ball=None)))
            import sqlite3
            db = Path(work) / "absent.db"
            with self.assertRaises(sqlite3.OperationalError):
                evaluate(db, "m", "e", path)
            self.assertFalse(db.exists())

    def test_event_start_tolerance_and_duration_iou(self):
        scores = Scores()
        scores.identities["a"] = [(0, "ref"), (2_000_000, "ref"), (3_000_000, "ref")]
        refs = [("shot", (0, 0), ("ref",)), ("dribble", (0, 2_000_000), ("ref",))]
        detected = [("kick", (1_000_000, 1_000_000), ["a"]), ("kick", (1_000_001, 1_000_001), ["a"]),
                    ("dribble", (1_000_000, 3_000_000), ["a"])]
        report = event_scores(refs, detected, scores)["classes"]
        self.assertEqual(report["kick"]["truePositive"], 1)
        self.assertEqual(report["kick"]["falsePositive"], 1)
        self.assertIsNone(report["kick"]["meanIntervalIoU"])
        self.assertEqual(report["dribble"]["truePositive"], 1)
        self.assertAlmostEqual(report["dribble"]["meanIntervalIoU"], 1 / 3)
        late = event_scores(refs, [detected[1]], scores)["classes"]["kick"]
        self.assertEqual((late["truePositive"], late["falseNegative"]), (0, 1))


if __name__ == "__main__":
    unittest.main()
