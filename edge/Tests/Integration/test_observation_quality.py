"""核对原图上的模型框清晰度、重叠面积及小球裁片证据。"""

from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Sources/PitchTraceEdge/Geometry"))
from observation_quality import add_model_quality
from observations import validate_observation


class ObservationQualityTests(unittest.TestCase):
    def test_optional_ball_and_search_boxes_stay_within_original_image(self):
        row = {"imageSize": [80, 60], "cameraId": "A", "pts": 0, "segment": 0,
               "players": [], "ball": {"pixel": [29, 10]}}
        validate_observation(row)
        for field in ("box", "searchBox"):
            row["ball"][field] = [20, 0, 60, 60]
            validate_observation(row)
            row["ball"][field] = [20, 0, 61, 60]
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_observation(row)
            del row["ball"][field]

    def test_clipped_overlap_uses_union_and_excludes_own_box(self):
        image = np.zeros((10, 16, 3), np.uint8)
        result = {"players": [{"box": b} for b in [[-2, 0, 12, 10], [2, 0, 4, 10], [4, 0, 4, 10]]],
                  "ball": {"box": [0, 0, 12, 10], "source": "coreML"}}
        add_model_quality(image, result, model_people=True, search_box=[0, 0, 16, 10])
        self.assertEqual(result["players"][0]["quality"], {"personOverlapFraction": 0.6, "laplacianVariance": 0})
        self.assertEqual(result["ball"]["quality"]["personOverlapFraction"], 10 / 12)
        self.assertNotIn("searchBox", result["ball"])

    def test_laplacian_uses_original_pixels_and_excludes_crop_edges(self):
        ramp = np.arange(64, dtype=np.uint8).reshape(8, 8)
        image = np.full((16, 16, 3), 255, np.uint8)
        result = {"players": [], "ball": {"box": [4, 4, 8, 8], "source": "coreML-ROI"}}
        image[4:12, 4:12] = cv2.cvtColor(ramp, cv2.COLOR_GRAY2BGR)
        add_model_quality(image, result, model_people=False, search_box=[2, 3, 12, 10])
        self.assertEqual(result["ball"]["quality"]["laplacianVariance"], 0)
        self.assertEqual(result["ball"]["searchBox"], [2, 3, 12, 10])
        image[8, 8] = 255
        add_model_quality(image, result, model_people=False, search_box=[2, 3, 12, 10])
        self.assertGreater(result["ball"]["quality"]["laplacianVariance"], 0)

    def test_tiny_missing_and_nonmodel_boxes(self):
        image = np.zeros((10, 10, 3), np.uint8)
        result = {"players": [{"box": [0, 0, 10, 10]}], "ball": {"box": [2, 2, 2, 2], "source": "coreML"}}
        add_model_quality(image, result, model_people=False, search_box=None)
        self.assertNotIn("quality", result["players"][0])
        self.assertEqual(result["ball"]["quality"], {"personOverlapFraction": 1})
        for ball in (None, {"source": "coreML"}, {"box": [3, 3, 4, 4], "source": "ballHSV"}):
            result["ball"] = ball
            add_model_quality(image, result, model_people=False, search_box=None)
            self.assertNotIn("quality", result["ball"] or {})


if __name__ == "__main__":
    unittest.main()
