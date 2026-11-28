"""原片待办的发送顺序、init.mp4 去重与限速。"""
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

EDGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EDGE / "Sources/PitchTraceEdge/Geometry"))

from stream_match import MediaQueue


def sha(data):
    return hashlib.sha256(data).hexdigest()


class MediaQueueTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.media = Path(temp.name) / "media"
        self.db = sqlite3.connect(Path(temp.name) / "queue.sqlite")
        self.addCleanup(self.db.close)
        self.received = []
        received = self.received

        class Gateway(BaseHTTPRequestHandler):
            def do_PUT(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((self.path, len(body), time.monotonic()))
                self.send_response(202)
                self.end_headers()

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.gateway = f"http://127.0.0.1:{server.server_port}"

    def segment(self, camera, number, init=b"init", size=1000):
        period = self.media / camera / "epochs/live"
        (period / "segments").mkdir(parents=True, exist_ok=True)
        (period / "init.mp4").write_bytes(init)
        data = bytes([number]) * size
        (period / f"segments/{number}.m4s").write_bytes(data)
        index = period / f"segments/{number}.json"
        index.write_text(json.dumps({"initSha256": sha(init), "segmentSha256": sha(data)}))
        return str(index.relative_to(self.media))

    def drain(self, queue):
        while queue.send_one(self.gateway, "trial", self.media):
            pass

    def test_registration_order_and_single_init_per_period(self):
        queue = MediaQueue(self.db)
        queue.register(self.gateway)
        # 字典序会让 A/10 排在 A/2 之前、B 排在 A 全部之后；待办应按登记先后交替发送。
        for camera, number in (("A", 2), ("B", 2), ("A", 10), ("B", 10)):
            queue.add(self.segment(camera, number), self.media / self.segment(camera, number))
        self.drain(queue)
        paths = [p.split("/cameras/")[1] for p, _, _ in self.received]
        self.assertEqual(paths, [
            "A/epochs/live/init.mp4", "A/epochs/live/segments/2.m4s", "A/epochs/live/segments/2/index",
            "B/epochs/live/init.mp4", "B/epochs/live/segments/2.m4s", "B/epochs/live/segments/2/index",
            "A/epochs/live/segments/10.m4s", "A/epochs/live/segments/10/index",
            "B/epochs/live/segments/10.m4s", "B/epochs/live/segments/10/index",
        ])
        # 重启后的新队列沿用已确认的 init.mp4。
        restarted = MediaQueue(self.db)
        restarted.add(self.segment("A", 11), self.media / self.segment("A", 11))
        self.drain(restarted)
        self.assertEqual([p.rsplit("/", 2)[-2:] for p, _, _ in self.received[10:]],
                         [["segments", "11.m4s"], ["11", "index"]])
        # 同一周期的初始化文件被替换时拒绝上传。
        key = self.segment("A", 12, init=b"other")
        restarted.add(key, self.media / key)
        with self.assertRaisesRegex(ValueError, "init.mp4"):
            restarted.send_one(self.gateway, "trial", self.media)

    def test_rate_limit_spaces_requests_by_bytes(self):
        queue = MediaQueue(self.db, bytes_per_second=20_000)
        queue.register(self.gateway)
        for number in range(3):
            key = self.segment("A", number, size=4000)
            queue.add(key, self.media / key)
        start = time.monotonic()
        self.drain(queue)
        elapsed = time.monotonic() - start
        sizes = [size for _, size, _ in self.received]
        # 最后一个请求发出前须等完此前全部字节的额度。
        self.assertGreaterEqual(elapsed, sum(sizes[:-1]) / 20_000 - 0.02)
        self.assertLess(elapsed, sum(sizes) / 20_000 + 1)
        with closing(self.db.cursor()) as cursor:
            self.assertEqual(cursor.execute("SELECT COUNT(*) FROM stream_pending").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
