"""两路增量封口时检查采集尚在运行即可归档、断网恢复与幂等交付。"""

from contextlib import closing
import fcntl
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest


EDGE = Path(__file__).resolve().parents[2]
EXECUTABLE = EDGE / ".build/debug/pitchtrace-edge"


def wait_for(predicate, message):
    end = time.monotonic() + 20
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError(message)


class StreamTest(unittest.TestCase):
    def test_archive_while_capturing_then_retry_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run([sys.executable, "-B", EDGE / "samples/generate-match.py", root], check=True)
            raw, run = root / "capture", root / "stream"
            raw.mkdir()
            (raw / "match.json").write_text('{"matchId":"training"}')
            with (raw / ".capture.lock").open("wb") as capture_lock:
                fcntl.flock(capture_lock, fcntl.LOCK_EX)
                cameras = {}
                for name in ("A", "B"):
                    record = root / "recordings" / name
                    p = raw / "raw" / name / "period1"
                    p.mkdir(parents=True)
                    shutil.copy(record / "init.mp4", p)
                    shutil.copy(record / "segments/0.m4s", p)
                    (p / "status.json").write_text(json.dumps(dict(matchId="training", cameraId=name, mediaEpoch="period1", startedUnix=1)))
                    mapped = [dict(matchId="training", cameraId=name, mediaEpoch="period1",
                                   clockSource="合成回归", timeBase="1/10240")]
                    for num in range(2):
                        frames = json.loads((record / f"segments/{num}.exposure.json").read_text())["frames"]
                        mapped.extend(dict(f, segment=num) for f in frames)
                    (p / "exposures.ndjson").write_text("".join(json.dumps(x) + "\n" for x in mapped))
                    cameras[name] = dict(type="file", source=str(record / "init.mp4"), mediaEpoch="period1")
                (root / "inputs.json").write_text(json.dumps(dict(matchId="training", cameras=cameras)))
                node = subprocess.Popen(["node", EDGE.parent / "live/server/server.cjs"],
                                        env={**os.environ, "PITCHTRACE_HOST": "127.0.0.1", "PITCHTRACE_PORT": "0",
                                             "PITCHTRACE_DB": str(root / "gateway.sqlite"),
                                             "PITCHTRACE_MEDIA_DIR": str(root / "gateway-media")},
                                        stdout=subprocess.PIPE, text=True)
                def close_gateway():
                    node.terminate()
                    node.wait(timeout=10)
                    node.stdout.close()
                self.addCleanup(close_gateway)
                address = node.stdout.readline().strip().split("http://")[-1]
                delivered = []
                online = threading.Event()
                class Gateway(BaseHTTPRequestHandler):
                    def handle_upload(self):
                        body = self.rfile.read(int(self.headers["Content-Length"]))
                        code, reply = 503, b""
                        if online.is_set():
                            with closing(http.client.HTTPConnection(address, timeout=5)) as client:
                                client.request(self.command, self.path, body, dict(self.headers))
                                response = client.getresponse()
                                code, reply = response.status, response.read()
                            delivered.append((self.command, self.path))
                        self.send_response(code)
                        self.end_headers()
                        self.wfile.write(reply)
                    do_POST = do_PUT = handle_upload
                    def log_message(self, *_):
                        pass
                with ThreadingHTTPServer(("127.0.0.1", 0), Gateway) as server:
                    threading.Thread(target=server.serve_forever, daemon=True).start()
                    config = json.loads((root / "match.json").read_text())
                    config.update(captureConfig="inputs.json", gateway=f"http://127.0.0.1:{server.server_port}")
                    (root / "match.json").write_text(json.dumps(config))
                    cmd = [EXECUTABLE, "stream-match", root / "match.json", raw, run]

                    def count():
                        if not (run / "scenes.sqlite").exists():
                            return 0
                        with closing(sqlite3.connect(run / "scenes.sqlite")) as db:
                            try:
                                return db.execute("SELECT COUNT(*) FROM edge_scenes").fetchone()[0]
                            except sqlite3.OperationalError:
                                return 0

                    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
                        try:
                            try:
                                wait_for(lambda: count() >= 8 or process.poll() is not None, "第一段应已产生场景")
                            except AssertionError:
                                process.kill()
                                _, error = process.communicate()
                                health = run / "media/stream-health.json"
                                self.fail(error + (health.read_text() if health.exists() else "无状态"))
                            if process.poll() is not None:
                                self.fail(process.communicate()[1])
                            self.assertIsNone(process.poll())
                            for name in ("A", "B"):
                                shutil.copy(root / f"recordings/{name}/segments/1.m4s", raw / f"raw/{name}/period1")
                            fcntl.flock(capture_lock, fcntl.LOCK_UN)
                            _, error = process.communicate(timeout=30)
                            self.assertNotEqual(process.returncode, 0)
                            self.assertIn("待提交", error)
                        finally:
                            if process.poll() is None:
                                process.kill()
                    self.assertEqual(count(), 20)
                    online.set()
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)["status"], "completed")
                    self.assertEqual(sum(m == "POST" for m, p in delivered), 20)
                    self.assertEqual(sum(m == "PUT" and p.endswith("/index") for m, p in delivered), 4)
                    with closing(http.client.HTTPConnection(address, timeout=5)) as client:
                        client.request("GET", "/v2/matches/training/frames")
                        response = client.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(len(json.load(response)["frames"]), 20)
                        for camera in ("A", "B"):
                            client.request("GET", f"/v2/matches/training/cameras/{camera}/locate?captureUtcUs={config['startUtcUs']}")
                            response = client.getresponse()
                            self.assertEqual(response.status, 200)
                            self.assertEqual(json.load(response)["mediaEpoch"], "period1")
                    stats = json.loads(result.stdout)
                    self.assertEqual(stats["committedRows"], stats["consumedRows"])
                    self.assertEqual(stats["committedRows"], 40)
                    self.assertEqual(stats["backlogRows"], 0)
                    self.assertGreater(stats["processedFramesPerSecond"], 0)
                    confirmed = list(delivered)
                    subprocess.run(cmd, check=True, capture_output=True, timeout=30)
                    self.assertEqual(delivered, confirmed)
                    server.shutdown()


if __name__ == "__main__":
    unittest.main()
