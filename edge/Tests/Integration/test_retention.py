"""在临时目录核对预演、到期删除、运行锁及外部原片保护。"""

from datetime import datetime, timedelta, timezone
import fcntl
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "edge/Sources/PitchTraceEdge/Geometry"))

from retention import record_owned_outputs, retention
from run_match import archive_signature
from scene_archive import SceneArchive


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "run"
        self.root.mkdir()
        self.start = datetime.now(timezone.utc) - timedelta(days=7)
        self.state = {"format": "pitchtrace-run-v1", "status": "completed", "createdAtUtc": self.start.isoformat(),
                      "retentionDays": 2, "inputSha256": "a" * 64, "matchId": "m", "streamEpoch": "e"}
        self.write_state()
        (self.root / "run.lock").touch()
        self.media = self.root / "media"
        self.media.mkdir()
        (self.media / "frames.ndjson").write_text("{}\n")
        (self.media / "gaps.txt").write_text("")
        self.db = self.root / "scenes.sqlite"
        with SceneArchive(self.db) as archive:
            archive.register_run("m", "e", {"configuration": "example"})
        self.state["sceneSha256"] = archive_signature(self.db, "m", "e")
        self.write_state()
        record_owned_outputs(self.root)

    def write_state(self):
        (self.root / "run-state.json").write_text(json.dumps(self.state))

    def test_due_only_generated_unchanged_files(self):
        outside = self.root.parent / "original"
        outside.write_text("原片")
        raw = self.root / "capture-001"
        raw.mkdir()
        (raw / "original").write_text("采集输入")
        (self.media / "operator-note").write_text("个人记录")
        (self.media / "gaps.txt").unlink()
        (self.media / "gaps.txt").symlink_to(outside)
        preview = retention(self.root)
        self.assertEqual(preview["planned"], ["media/frames.ndjson", "scenes.sqlite"])
        self.assertFalse((self.root / "run-retention.jsonl").exists())
        self.assertTrue(self.db.exists())
        actual = retention(self.root, True)
        self.assertEqual(actual["removed"], preview["planned"])
        self.assertFalse(self.db.exists())
        self.assertEqual((self.media / "operator-note").read_text(), "个人记录")
        self.assertEqual((raw / "original").read_text(), "采集输入")
        self.assertEqual((self.media / "gaps.txt").read_text(), "原片")
        self.assertTrue((self.root / "run-state.json").exists())
        self.assertEqual(len((self.root / "run-retention.jsonl").read_text().splitlines()), 2)
        self.assertEqual(retention(self.root, True)["removed"], [])
        with self.assertRaisesRegex(ValueError, "已按留存策略清理"):
            SceneArchive(self.db)

    def test_locks_and_policy(self):
        with (self.root / "run.lock").open() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(ValueError, "正在使用"):
                retention(self.root, True)
        with SceneArchive(self.db):
            with self.assertRaisesRegex(ValueError, "正在使用"):
                retention(self.root, True)
        self.assertEqual(retention(self.root, True, now=self.start)["status"], "notDue")
        self.state["retentionDays"] = None
        self.write_state()
        self.assertEqual(retention(self.root, True)["status"], "noPolicy")
        self.state.update(retentionDays=1, status="failed")
        self.write_state()
        self.assertEqual(retention(self.root, True)["status"], "incomplete")
        self.assertTrue(self.db.exists())

    def test_foreign_run_and_modified_media_survive(self):
        with SceneArchive(self.db) as archive:
            archive.register_run("other", "e", {"user": "data"})
        (self.media / "frames.ndjson").write_text("新增内容\n")
        result = retention(self.root, True)
        self.assertEqual(set(result["retained"]), {"scenes.sqlite", "media/frames.ndjson"})
        self.assertTrue(self.db.exists())
        self.assertEqual((self.media / "frames.ndjson").read_text(), "新增内容\n")


if __name__ == "__main__":
    unittest.main()
