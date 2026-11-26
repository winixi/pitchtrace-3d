"""从原始投影与独立已知点验证机位巡检和场地覆盖。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from test_tracking import cameras, pixel
from audit_calibration import audit


class AuditCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.work = TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.report = Path(self.work.name) / "poses.json"
        self.inspection = Path(self.work.name) / "survey.json"
        self.points = [
            {"id": f"pt-{i}", "worldM": xyz, "pixels": {}}
            for i, xyz in enumerate([[-2, 0, 0], [0, 0, 0], [1, 0, 0], [1, 2, 1]])
        ]
        self.report.write_text(json.dumps({
            "calibrationId": "pitch-v1", "sampleSource": "synthetic", "cameras": cameras(),
            "checkPoints": self.points,
        }))
        self.grid = {"lengthM": 5, "widthM": 3, "cellSizeM": 2, "heightM": .5}

    def run_audit(self, checks, **options):
        settings = {"grid": self.grid, "checks": checks, **options}
        self.inspection.write_text(json.dumps(settings))
        return audit(self.report, self.inspection)

    def test_move_and_previously_disabled(self):
        checks = []
        for point in self.points:
            mark = {name: pixel(name, point["worldM"]) for name in ("A", "B", "C")}
            mark["A"][0] += 12
            checks.append({"id": point["id"], "pixels": mark})
        result = self.run_audit(checks, captureUtcUs="2030000", calibrationId="isolate-a", disabledCameras=["C"])
        self.assertEqual(result["cameras"]["B"]["status"], "consistent")
        self.assertEqual(result["cameras"]["D"]["status"], "insufficientEvidence")
        self.assertEqual(result["suspectedCameras"], ["A"])
        self.assertAlmostEqual(result["cameras"]["A"]["checks"][0]["errorPx"], 12)
        proposal = result["proposedCalibration"]
        self.assertEqual(proposal["disabledCameras"], ["A", "C"])
        self.assertEqual(proposal["fromUtcUs"], "2030000")
        self.assertEqual(Path(proposal["report"]), self.report.resolve())
        for cell in result["coverage"]["cells"]:
            self.assertTrue(set(cell["views"]).isdisjoint({"A", "C"}))

    def test_geometry_only_coverage_and_target_scale(self):
        result = self.run_audit([])
        grid = result["coverage"]
        self.assertEqual((grid["columnsX"], grid["rowsZ"], grid["dualViewCells"]), (3, 2, 6))
        first, last = grid["cells"][0], grid["cells"][-1]
        self.assertEqual(first["centerM"], [-1.5, .5, -.5])
        self.assertEqual(last["centerM"], [2., .5, 1.])
        self.assertEqual(first["viewCount"], 4)
        self.assertEqual(len(first["pairs"]), 6)
        self.assertAlmostEqual(first["views"]["A"]["expectedDiameterPx"], 600 * .22 / 7.5)
        restricted = self.run_audit([], grid={**self.grid, "minRayAngleDeg": 70})
        self.assertEqual(restricted["coverage"]["dualViewCells"], 0)
        self.assertEqual(restricted["coverage"]["cells"][0]["viewCount"], 4)

    def test_collinear_and_ambiguous_evidence(self):
        checks = [{"id": p["id"], "pixels": {"A": pixel("A", p["worldM"])}} for p in self.points]
        for obj in checks[:3]:
            obj["pixels"]["A"][0] += 20
        result = self.run_audit(checks[:3])
        self.assertEqual(result["cameras"]["A"]["status"], "insufficientEvidence")
        checks[0]["pixels"]["A"][0] -= 20
        result = self.run_audit(checks)
        self.assertEqual(result["cameras"]["A"]["status"], "review")
        self.assertEqual(result["suspectedCameras"], [])

    def test_reject_bad_inputs(self):
        for kwargs in [
            {"checks": [{"id": "unknown", "pixels": {}}]},
            {"checks": [{"id": "pt-0", "pixels": {"B": [1200, 10]}}]},
            {"checks": [], "captureUtcUs": "01"}, {"checks": [], "sampleSource": "field"},
            {"checks": [], "grid": {**self.grid, "cellSizeM": 1e-5}},
        ]:
            with self.assertRaises(ValueError):
                self.run_audit(**kwargs)


if __name__ == "__main__":
    unittest.main()
