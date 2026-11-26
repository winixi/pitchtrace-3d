"""检查本地不可变归档、断线续传及本地 v2 网关的直播游标。"""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "edge/Sources/PitchTraceEdge/Geometry"))

from scene_archive import SceneArchive
from scene_compare import compare


def scene(seq, match="match", epoch="first"):
    return {
        "v": 2, "matchId": match, "streamEpoch": epoch, "seq": seq, "captureUtcUs": str(123000 + seq * 1000),
        "matchTimeMs": seq, "calibrationId": "pitch", "players": [], "ball": None,
    }


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "edge.sqlite"

    def test_resume_and_conflicts(self):
        evidence = {"A": {"pts": 0, "segment": 1, "mediaEpoch": "reconnect"}}
        with SceneArchive(self.path) as archive:
            archive.register_run("match", "first", {"version": "one"})
            archive.append(scene(2), evidence)
            archive.append(scene(0))
            archive.append(scene(0, match="other"))
            archive.append(scene(1, epoch="next"))
        with SceneArchive(self.path) as archive:
            self.assertEqual(archive.run_info("match", "first"), {"version": "one"})
            self.assertFalse(archive.register_run("match", "first", {"version": "one"}))
            with self.assertRaisesRegex(ValueError, "新 streamEpoch"):
                archive.register_run("match", "first", {"version": "two"})
            self.assertEqual(list(archive.frames("match", "first")), [scene(0), scene(2)])
            self.assertFalse(archive.append(dict(reversed(scene(2).items())), evidence))
            self.assertFalse(archive.append(scene(0), evidence))
            with self.assertRaisesRegex(ValueError, "场景键冲突"):
                archive.append({**scene(0), "events": [{}]}, {"A": None})
            with self.assertRaisesRegex(ValueError, "证据冲突"):
                archive.append(scene(0), {})
            self.assertEqual(archive.evidence("match", "first", 0), evidence)
            with self.assertRaisesRegex(ValueError, "找不到"):
                archive.evidence("match", "first", 1)
            for invalid in [{**scene(0), "seq": True}, {**scene(0), "players": [{"trackId": "p"}] * 2}]:
                with self.assertRaises(ValueError):
                    archive.append(invalid)

    def test_concurrent_append(self):
        barrier = Barrier(4)

        def writer(_):
            barrier.wait(timeout=3)
            with SceneArchive(self.path) as archive:
                return archive.append(scene(0))

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(writer, range(12))), 1)

    def test_export_window_track_event_version(self):
        play = {**scene(100), "players": [{"trackId": "p1", "state": "lost"}],
                "events": [{"label": "传球候选"}]}
        with SceneArchive(self.path) as archive:
            archive.append(scene(0))
            archive.append(play)
            filters = dict(from_ms=100, to_ms=100, track_id="p1", event_label="传球候选", calibration_id="pitch")
            self.assertEqual(list(archive.frames("match", "first", **filters)), [play])
            for override in ({"to_ms": 99, "from_ms": 0}, {"track_id": "p2"}, {"event_label": "射门候选"},
                             {"calibration_id": "moved"}):
                self.assertEqual(list(archive.frames("match", "first", **(filters | override))), [])

    def test_latest_resume_and_gateway_isolation(self):
        attempts = []
        statuses = iter([202, 409, 422, 202, 202, 202, 202, 202, 202])
        current = self
        class Connection:
            def __init__(self, *_args, **_kwargs):
                pass

            def request(self, method, path, body, _headers):
                current.assertEqual((method, path), ("POST", "/v2/matches/match/frames"))
                attempts.append(json.loads(body)["seq"])

            def getresponse(self):
                self.status = next(statuses)
                return self

            def read(self, _limit):
                return b""

            def close(self):
                pass

        with patch("scene_archive.http.client.HTTPConnection", Connection):
            with SceneArchive(self.path) as archive:
                for seq in range(3):
                    archive.append(scene(seq))
                with self.assertRaisesRegex(ValueError, "409"):
                    archive.publish("http://one", "match", "first")
            with SceneArchive(self.path) as archive:
                with self.assertRaisesRegex(ValueError, "422"):
                    archive.publish("http://one", "match", "first")
                archive.append(scene(3))
                self.assertEqual(archive.publish("http://one", "match", "first"), 3)
                self.assertEqual(archive.publish("http://one/", "match", "first"), 0)
                self.assertEqual(attempts, [2, 0, 0, 3, 0, 1])
                # 第二个归档目标另存回执，独立重发。
                statuses = iter([202] * 4)
                self.assertEqual(archive.publish("http://two", "match", "first"), 4)
                self.assertEqual(attempts[-4:], [3, 0, 1, 2])

    def test_compare_realigns_seq_to_exposure(self):
        p = {"trackId": "p", "state": "measured", "positionM": [0, 0, 0], "number": 10}
        event = {"label": "传球候选"}
        utc = "9500000000000000000"
        with SceneArchive(self.path) as archive:
            archive.append(scene(0))
            archive.append({**scene(1), "captureUtcUs": utc, "players": [p], "events": [event]})
            archive.append({**scene(0, epoch="revised"), "captureUtcUs": utc,
                            "players": [{**p, "positionM": [3, 0, 4], "number": 9, "state": "predicted"}],
                            "calibrationId": "revised-pitch"})
            archive.append({**scene(1, epoch="revised"), "captureUtcUs": "9500000000000000001"})
            diff = compare(archive, "match", "first", "revised")
            self.assertEqual([diff[k] for k in ("pairedFrames", "aOnlyFrames", "bOnlyFrames", "calibrationChanges")],
                             [1, 1, 1, 1])
            self.assertEqual(diff["events"]["byLabel"]["传球候选"], {"onlyA": 1, "onlyB": 0})
            self.assertEqual(diff["events"]["examples"][0]["captureUtcUs"], utc)
            self.assertEqual([diff["tracks"]["p"][k] for k in ("meanDeltaM", "stateChanges", "identityChanges")],
                             [5.0, 1, 1])

    def test_real_gateway(self):
        env = {**os.environ, "PITCHTRACE_HOST": "127.0.0.1", "PITCHTRACE_PORT": "0",
               "PITCHTRACE_DB": str(self.path.with_name("live.sqlite")),
               "PITCHTRACE_MEDIA_DIR": str(self.path.with_name("media"))}
        with subprocess.Popen(["node", str(ROOT / "live/server/server.cjs")], env=env,
                              stdout=subprocess.PIPE, text=True) as gateway:
            try:
                banner = gateway.stdout.readline().strip()
                self.assertIn("http://127.0.0.1:", banner)
                base = banner[banner.index("http://"):]
                with SceneArchive(self.path) as archive:
                    for seq in range(4):
                        archive.append(scene(seq))
                    self.assertEqual(archive.publish(base, "match", "first"), 4)
                    self.assertEqual(archive.publish(base, "match", "first"), 0)
                with urlopen(base + "/v2/matches/match/frames", timeout=5) as history:
                    self.assertEqual(json.load(history)["frames"], [scene(i) for i in range(4)])
                ws = base.replace("http:", "ws:") + "/v2/matches/match/live"
                subscription = """
                    const ws = new WebSocket(process.argv[1]);
                    const timer = setTimeout(() => process.exit(2), 3000);
                    ws.onmessage = event => {
                      clearTimeout(timer);
                      console.log(JSON.parse(event.data).seq);
                      ws.close();
                    };
                """
                self.assertEqual(subprocess.check_output(["node", "-e", subscription, ws], timeout=5).strip(), b"3")
            finally:
                gateway.terminate()


if __name__ == "__main__":
    unittest.main()
