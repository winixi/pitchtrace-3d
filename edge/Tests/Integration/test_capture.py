"""用固定 H.264 录像检查采集、曝光隔离和多周期重放。"""

import errno
import hashlib
from unittest.mock import patch
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


EDGE = Path(__file__).resolve().parents[2]
SCRIPT = EDGE / "Sources/PitchTraceEdge/Geometry/capture.py"


def execute(*args, success=True):
    result = subprocess.run(args, capture_output=True, text=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"退出码 {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result


class CaptureTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.output = self.base / "record"
        self.source = EDGE / "samples/preview-A.mp4"

    def run_capture(self, cameras, success=True):
        config = self.base / "config.json"
        config.write_text(json.dumps({"matchId": "match-1", "cameras": cameras}))
        return execute(sys.executable, "-B", SCRIPT, config, self.output, success=success)

    def camera(self, epoch):
        return {"type": "file", "source": str(self.source), "videoMode": "copy", "mediaEpoch": epoch}

    def mapping(self, camera, epoch, utc):
        # 生成样例为 30 fps、每段 60 帧，HLS 封装时基 1/15360。
        header = {"matchId": "match-1", "cameraId": camera, "mediaEpoch": epoch,
                  "clockSource": "合成回归时钟", "timeBase": "1/15360"}
        rows = [{"segment": i // 60, "pts": i * 512, "captureUtcUs": str(utc + i * 1_000_000 // 30),
                 "frameNumber": 10 + i} for i in range(120)]
        path = self.base / f"{camera}-{epoch}.ndjson"
        path.write_text("".join(json.dumps(r) + "\n" for r in [header, *rows]))
        return path

    def attach(self, camera, epoch, mapping, success=True):
        return execute(sys.executable, "-B", SCRIPT, "attach", self.output, camera, epoch, mapping, success=success)

    def test_mapped_capture_replay_and_overlap(self):
        for epoch, start in [("first", 0), ("second", 5_000_000)]:
            cameras = {name: {**self.camera(epoch), "exposures": self.mapping(name, epoch, start).name}
                       for name in ("A", "B")}
            self.run_capture(cameras)
        self.attach("A", "first", self.base / "A-first.ndjson")
        altered = self.mapping("A", "first", 1000)
        self.assertIn("不同的曝光映射", self.attach("A", "first", altered, success=False).stderr)
        replay = self.base / "review"
        execute(EDGE / ".build/debug/pitchtrace-edge", "replay", self.output / "recordings", replay)
        frames = [json.loads(line) for line in (replay / "frames.ndjson").read_text().splitlines()]
        self.assertEqual(len(frames), 480)
        for epoch, utc in [("first", "0"), ("second", "5000000")]:
            for camera in ("A", "B"):
                reference = next(row for row in frames if row["mediaEpoch"] == epoch and row["cameraId"] == camera)
                self.assertEqual((reference["pts"], reference["frameNumber"], reference["captureUtcUs"]), (0, 10, utc))
        self.run_capture({"A": self.camera("third")})
        overlapping = self.mapping("A", "third", 5_001_000)
        self.assertIn("范围重叠", self.attach("A", "third", overlapping, success=False).stderr)
        self.assertFalse((self.output / "recordings/A/epochs/third").exists())

    def test_follow_sealed_parts_late_camera_and_restart(self):
        sys.path.insert(0, str(SCRIPT.parent))
        from stream_capture import follow_rows, key
        cameras = {name: self.camera("one") for name in ("A", "B")}
        self.run_capture(cameras)
        for name in cameras:
            source = self.mapping(name, "one", 0)
            target = self.output / "raw" / name / "one/exposures.ndjson"
            text = source.read_text()
            # B 路只写了部分曝光；它的原片已经封口。
            target.write_text(text if name == "A" else text[:text.index("\n") + 8])
        destination = self.base / "media"
        commits = set()

        def committed(name, epoch, number, index_file):
            index = json.loads(index_file.read_text())
            self.assertEqual(index["segmentSha256"], hashlib.sha256(index_file.with_suffix(".m4s").read_bytes()).hexdigest())
            commits.add((name, epoch, number))

        finished = False
        args = (self.base / "config.json", self.output, destination, lambda: finished)
        follower = follow_rows(*args, wait_seconds=0.02, poll_seconds=0.005, on_segment=committed)
        prefix = [next(follower) for _ in range(65)]
        self.assertTrue(all(row["cameraId"] == "A" for row in prefix))
        late_mapping = self.output / "raw/B/one/exposures.ndjson"
        late_mapping.write_text((self.base / "B-one.ndjson").read_text())
        finished = True
        rows = prefix + list(follower)
        self.assertEqual([key(row) for row in rows], sorted({key(row) for row in rows}))
        self.assertEqual(len(commits), 4)
        self.assertEqual(sum(r["cameraId"] == "A" for r in rows), 120)
        self.assertGreater(sum(r["cameraId"] == "B" for r in rows), 0)
        health = json.loads((destination / "stream-health.json").read_text())
        self.assertEqual(len(rows) + health["lateFrames"], 240)
        self.assertEqual(health["issues"], [])
        # 模拟行写到一半被杀；恢复必须沿用最初的直播次序和迟到选择。
        with (destination / "frames.ndjson").open("ab") as output:
            output.write(b'{"camera')
        again = list(follow_rows(*args, wait_seconds=0, poll_seconds=0.005))
        self.assertEqual(again, rows)
        self.assertTrue((destination / "frames.ndjson").read_bytes().endswith(b"\n"))

    def test_crash_after_commit_and_disk_full_retry(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import stream_capture
        self.run_capture({"A": self.camera("one")})
        self.mapping("A", "one", 0).replace(self.output / "raw/A/one/exposures.ndjson")
        config = self.base / "config.json"
        dest = self.base / "stream"
        # 让独立进程在索引已可见、直播日志尚未写入时退出。
        code = ("import os,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                "from stream_capture import follow_rows; "
                "list(follow_rows(*map(Path,sys.argv[2:]),lambda:True,on_segment=lambda *args:os._exit(37)))")
        killed = execute(sys.executable, "-B", "-c", code, SCRIPT.parent, config, self.output, dest, success=False)
        self.assertEqual(killed.returncode, 37)
        self.assertEqual((dest / "frames.ndjson").stat().st_size, 0)
        first = dest / "A/epochs/one/segments/0.json"
        self.assertTrue(first.exists())
        # 原始第 0 段已从工作目录移走。恢复仍需读到它并继续第 1 段。
        (self.output / "raw/A/one/0.m4s").unlink()
        write = stream_capture.write_json
        def disk_full(path, data):
            if path.name == "1.json" and path.parent.name == "segments":
                raise OSError(errno.ENOSPC, "测试写盘空间不足")
            return write(path, data)
        with patch.object(stream_capture, "write_json", disk_full):
            with self.assertRaises(OSError) as failed:
                list(stream_capture.follow_rows(config, self.output, dest, lambda: True))
            self.assertEqual(failed.exception.errno, errno.ENOSPC)
        self.assertFalse((first.parent / "1.json").exists())
        rows = list(stream_capture.follow_rows(config, self.output, dest, lambda: True))
        self.assertEqual([r["frameNumber"] for r in rows], list(range(10, 130)))
        # 只有已发布媒体和日志也能复算同样顺序。
        self.output.rename(self.base / "offline-raw")
        self.assertEqual(list(stream_capture.follow_rows(config, self.output, dest, lambda: True)), rows)

    def test_unknown_clock_isolation_and_partial_input(self):
        self.run_capture({"A": {**self.camera("one"), "videoMode": "encode"},
                          "B": {**self.camera("one"), "source": "missing.mp4"}}, success=False)
        status = json.loads((self.output / "health.json").read_text())["finished"]
        self.assertEqual(status["A"]["status"], "completed")
        self.assertEqual(status["B"]["status"], "failed")
        self.assertEqual(status["A"]["clockSource"], "unknown")
        self.assertGreater(status["A"]["bytesPerVideoSecond"], 0)
        playlist = (self.output / "raw/A/one/preview.m3u8").read_text()
        self.assertIn("#EXT-X-ENDLIST", playlist)
        self.assertFalse((self.output / "recordings").exists())
        mapping = self.mapping("A", "one", 0)
        lines = mapping.read_text().splitlines()
        mapping.write_text("\n".join(lines[:-1]))
        self.attach("A", "one", mapping, success=False)
        self.assertFalse((self.output / "recordings").exists())
        self.run_capture({"A": self.camera("one")}, success=False)
        self.assertEqual((self.output / "raw/A/one/preview.m3u8").read_text(), playlist)


if __name__ == "__main__":
    unittest.main()
