"""设备凭据的读取、权限检查与传输限制。"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

EDGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EDGE / "Sources/PitchTraceEdge/Geometry"))

from gateway_auth import allows_http, auth_headers, child_environment, load_device_token

TOKEN = "pt1.edge-a." + "Q" * 43


class GatewayAuthTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("PITCHTRACE_DEVICE_TOKEN", None)
        os.environ.pop("PITCHTRACE_GATEWAY_ALLOW_HTTP", None)

    def token_file(self, text, mode=0o600):
        path = self.folder / "edge.token"
        path.write_text(text)
        path.chmod(mode)
        return {"deviceTokenFile": "edge.token"}

    def test_reads_environment_before_private_file(self):
        self.assertIsNone(load_device_token({}, self.folder))
        self.assertEqual(load_device_token(self.token_file(TOKEN + "\n"), self.folder), TOKEN)
        other = "pt1.edge-b." + "R" * 43
        os.environ["PITCHTRACE_DEVICE_TOKEN"] = other
        self.assertEqual(load_device_token(self.token_file(TOKEN), self.folder), other)

    def test_rejects_shared_files_and_malformed_tokens_without_echoing(self):
        with self.assertRaisesRegex(ValueError, "chmod 600"):
            load_device_token(self.token_file(TOKEN, 0o644), self.folder)
        secret = "pt1.edge-a.not-long-enough"
        with self.assertRaises(ValueError) as caught:
            load_device_token(self.token_file(secret), self.folder)
        self.assertNotIn(secret, str(caught.exception))
        with self.assertRaises(FileNotFoundError):
            load_device_token({"deviceTokenFile": "missing.token"}, self.folder)
        with self.assertRaises(ValueError):
            load_device_token({"deviceTokenFile": 3}, self.folder)

    def test_credentials_only_travel_over_https_or_loopback(self):
        self.assertEqual(auth_headers("http://127.0.0.1:8766", TOKEN), {"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(auth_headers("http://localhost:8766", TOKEN)["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(auth_headers("https://live.example/base", TOKEN)["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(auth_headers("http://192.168.1.20:8766", None), {})
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            auth_headers("http://192.168.1.20:8766", TOKEN)
        self.assertTrue(auth_headers("http://192.168.1.20:8766", TOKEN, allows_http({"gatewayAllowHttp": True})))
        os.environ["PITCHTRACE_GATEWAY_ALLOW_HTTP"] = "1"
        self.assertTrue(allows_http({}))
        with self.assertRaises(ValueError):
            allows_http({"gatewayAllowHttp": "yes"})

    def test_child_environment_carries_token_without_arguments(self):
        env = child_environment(TOKEN, True)
        self.assertEqual(env["PITCHTRACE_DEVICE_TOKEN"], TOKEN)
        self.assertEqual(env["PITCHTRACE_GATEWAY_ALLOW_HTTP"], "1")
        self.assertNotIn("PITCHTRACE_DEVICE_TOKEN", child_environment(None, False))


if __name__ == "__main__":
    unittest.main()
