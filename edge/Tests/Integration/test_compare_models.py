"""验证转换对照的配对、缺测与球召回统计。"""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[2] / "scripts/benchmarks/compare-models.py"
spec = importlib.util.spec_from_file_location("compare_models", path)
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


class CompareTests(unittest.TestCase):
    def test_matching_is_class_aware_and_one_to_one(self):
        a = [{"class": "person", "box": [0, 0, 10, 10], "confidence": .8},
             {"class": "person", "box": [0, 0, 10, 10], "confidence": .7}]
        b = [{"class": "person", "box": [0, 0, 10, 10], "confidence": .9},
             {"class": "ball", "box": [0, 0, 10, 10], "confidence": .9}]
        result = compare.agreement(a, b, .5)
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(len(result["beforeOnly"]), 1)
        self.assertEqual(result["afterOnly"], [1])

    def test_scores_use_highest_confidence_and_keep_unscored(self):
        balls = [{"class": "ball", "box": [0, 0, 2, 2], "confidence": .9},
                 {"class": "ball", "box": [9, 9, 2, 2], "confidence": .8}]
        label = {"status": "visible", "box": [9, 9, 2, 2], "center": None, "far": True}
        self.assertEqual(compare.score(balls, label, 8), "wrongLocation")
        self.assertEqual(compare.score([], label, 8), "miss")
        self.assertEqual(compare.score(balls, {"status": "absent"}, 8), "falsePositive")
        self.assertEqual(compare.score(balls, None, 8), "unscored")
        rows = [{"label": label, "beforeScore": "miss", "afterScore": "hit"}]
        self.assertEqual(compare.summarize(rows, "after", "farSmall")["recall"], 1)
        label["far"] = None
        self.assertIsNone(compare.summarize(rows, "after", "far")["recall"])


if __name__ == "__main__":
    unittest.main()
