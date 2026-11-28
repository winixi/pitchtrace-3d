"""检查 mvs.trigger 配置校验。不需要相机。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Sources/PitchTraceEdge/Geometry"))
import capture  # noqa: E402


def mvs(trigger, mode="ptp-utc", framerate=30):
    return {"type": "mvs", "source": "DA1", "framerate": framerate, "clock": {"mode": mode},
            "mvs": {"trigger": trigger}}


class MvsTriggerTest(unittest.TestCase):
    def test_accepts_valid_triggers(self):
        for trigger in ({}, {"source": "free"}, {"source": "Line0"}, {"source": "Line2", "activation": "FallingEdge"},
                        {"source": "action", "actionDeviceKey": 7, "actionGroupMask": 0xFFFFFFFF}):
            with self.subTest(trigger=trigger):
                capture.validate_native_camera("A", mvs(trigger))
        capture.validate_native_camera("A", mvs({"source": "Line0"}, mode="host-fit"))

    def test_rejects_invalid_triggers(self):
        cases = [({"source": "Line1"}, "ptp-utc"), ({"source": "Line0", "activation": "High"}, "ptp-utc"),
                 ({"selector": "AcquisitionStart"}, "ptp-utc"), ({"actionGroupKey": -1}, "ptp-utc"),
                 ({"actionGroupKey": 2 ** 32}, "ptp-utc"), ({"extra": 1}, "ptp-utc"), ("Line0", "ptp-utc"),
                 ({"source": "action"}, "host-fit")]
        for trigger, mode in cases:
            with self.subTest(trigger=trigger, mode=mode), self.assertRaises(ValueError):
                capture.validate_native_camera("A", mvs(trigger, mode))
        with self.assertRaises(ValueError):
            capture.validate_native_camera("A", mvs({"source": "action"}, framerate=29.97))


if __name__ == "__main__":
    unittest.main()
