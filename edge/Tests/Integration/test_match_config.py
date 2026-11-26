"""从合成标定报告核对比赛配置、标定切换和曝光窗口取样。"""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

EDGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EDGE / "Sources/PitchTraceEdge/Geometry"))
from calibrate import calibrate
from match_config import MatchConfig


def save(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def row(camera, utc):
    return {"cameraId": camera, "captureUtcUs": str(utc), "imageSize": [1920, 1080], "players": [], "ball": None}


class MatchConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = calibrate(json.loads((EDGE / "samples/calibration.json").read_text()))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        save(self.directory / "field.json", self.report)
        self.data = {
            "matchId": "trial", "streamEpoch": "one", "sampleSource": "synthetic",
            "startUtcUs": "100", "frameIntervalUs": 100, "maxSkewUs": 5,
            "calibrations": [{"fromUtcUs": "99", "calibrationId": "first", "report": "field.json"}],
            "roster": [{"rosterId": "p1", "teamId": "home", "number": 14},
                       {"rosterId": "p2", "teamId": "away", "number": 14}],
        }

    def load(self):
        path = self.directory / "match.json"
        save(path, self.data)
        return MatchConfig.load(path)

    def test_camera_isolation_and_recalibration_split_same_slot(self):
        updated = copy.deepcopy(self.report)
        updated["calibrationId"] = "moved-camera"
        updated["cameras"]["A"]["translationM"][0] += 2.0
        save(self.directory / "moved.json", updated)
        self.data["calibrations"].extend([
            {"fromUtcUs": "123", "calibrationId": "a-isolated", "report": "field.json", "disabledCameras": ["A"]},
            {"fromUtcUs": "180", "calibrationId": "restored", "report": "moved.json"},
            {"fromUtcUs": "200", "calibrationId": "paused", "report": "moved.json", "disabledCameras": ["A", "B"]},
        ])
        config = self.load()
        rows = [row("B", 90), row("A", 120), row("B", 121), row("A", 123), row("B", 127),
                row("A", 180), row("B", 181), row("B", 200)]
        groups = list(config.groups(rows))
        self.assertEqual([g[0] for g in groups], [120, 123, 180, 200])
        self.assertEqual([g[1] for g in groups], ["first", "a-isolated", "restored", "paused"])
        self.assertEqual([set(g[2]) for g in groups], [{"A", "B"}, {"B"}, {"A", "B"}, set()])
        self.assertEqual(groups[1][3], rows[3:5])
        self.assertEqual(groups[2][2]["A"]["translationM"], updated["cameras"]["A"]["translationM"])
        self.assertEqual(config.calibration_sources["a-isolated"], self.report["calibrationId"])
        self.assertEqual(config.calibration_sources["restored"], "moved-camera")
        self.assertEqual(groups, list(config.groups(rows)))

    def test_selects_fuller_windows_without_reusing_boundary_exposures(self):
        config = self.load()
        # 第一、第二组的同步对都跨过 100 微秒的取样分界。
        rows = [row("B", 130), row("A", 198), row("B", 201), row("A", 298), row("B", 301), row("A", 450)]
        results = list(config.groups(rows))
        self.assertEqual([group[3] for group in results], [rows[1:3], rows[3:5], rows[5:]])

    def test_zero_skew_and_stable_tie_order(self):
        self.data["maxSkewUs"] = 0
        rows = [row("B", 100), row("A", 100), row("B", 150), row("A", 152)]
        self.assertEqual(list(self.load().groups(rows))[0][3], [rows[1], rows[0]])

    def test_validates_skipped_rows_and_postpones_replay_resolution_check(self):
        config = self.load()
        a = row("A", 100)
        replay = dict(a)
        del replay["imageSize"]
        self.assertEqual(list(config.groups([replay], replay=True))[0][3], [replay])
        for rows in ([replay], [row("X", 100)], [a, a], [a, row("B", 99)],
                     [a, dict(row("B", 110), imageSize=[960, 540])], [dict(a, captureUtcUs="0100")]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                list(config.groups(rows))
        with self.assertRaises(ValueError):
            list(config.groups([dict(a, imageSize=[960, 540])], replay=True))

    def test_rejects_invalid_versions_rotations_and_sources(self):
        original = copy.deepcopy(self.data)
        entry = self.data["calibrations"][0]
        for extra in (dict(entry, fromUtcUs="101"), dict(entry, calibrationId="another"),
                      dict(entry, fromUtcUs="101", calibrationId="ok", disabledCameras=["C"])):
            self.data["calibrations"] = [entry, extra]
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.load()
        self.data["calibrations"] = [dict(entry, fromUtcUs="101")]
        with self.assertRaises(ValueError):
            self.load()
        self.data = original
        for delta in ({"rotationWorldToCamera": [[1, 0, 0], [0, 1, 0], [0, 0, -1]]},
                      {"rotationWorldToCamera": [[1, 0, 0], [0, 2, 0], [0, 0, 1]]},
                      {"translationM": [1, 2]}, {"distortion": []}):
            report = copy.deepcopy(self.report)
            report["cameras"]["A"].update(delta)
            save(self.directory / "field.json", report)
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                self.load()
        save(self.directory / "field.json", dict(self.report, sampleSource="field"))
        with self.assertRaises(ValueError):
            self.load()


if __name__ == "__main__":
    unittest.main()
