import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class OnlineDemoSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); cls.port = sock.getsockname()[1]; sock.close()
        env = os.environ.copy(); env.update(DEMO_PORT=str(cls.port), DEMO_HTTPS="0", DEMO_MAX_SESSIONS="12", PYTHONDONTWRITEBYTECODE="1")
        cls.proc = subprocess.Popen([sys.executable, "demo_server.py"], cwd=ROOT, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if cls.proc.poll() is not None:
                raise RuntimeError(f"demo_server exited with {cls.proc.returncode}")
            try:
                status, _, _ = cls.request("/healthz")
                if status == 200: break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError("demo_server did not become ready")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try: cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired: cls.proc.kill(); cls.proc.wait()

    @classmethod
    def request(cls, path, cookie=None, payload=None, origin=None, host=None):
        headers = {"Host": host or f"127.0.0.1:{cls.port}"}
        if cookie: headers["Cookie"] = cookie
        if payload is not None:
            headers["Content-Type"] = "application/json"
            headers["Origin"] = origin or f"http://127.0.0.1:{cls.port}"
            payload = json.dumps(payload).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{cls.port}{path}", data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as res: return res.status, res.headers, res.read()
        except urllib.error.HTTPError as exc: return exc.code, exc.headers, exc.read()

    def new_guest(self):
        status, headers, _ = self.request("/")
        self.assertEqual(status, 200)
        return headers.get("Set-Cookie").split(";", 1)[0], headers.get("Set-Cookie", "")

    def test_separate_guest_state_and_api_allowlist(self):
        cookie_a, set_cookie_a = self.new_guest()
        cookie_b, _ = self.new_guest()
        self.assertNotEqual(cookie_a, cookie_b)
        _, headers, body = self.request("/api/state", cookie_a); state_a = json.loads(body)
        _, _, body = self.request("/api/state", cookie_b); state_b = json.loads(body)
        self.assertEqual(self.request("/api/collection", cookie_a)[0], 403)
        status, _, body = self.request("/api/control", cookie_a, {"command": "research", "simulations": 100, "block": 10})
        self.assertEqual(status, 200, body.decode("utf-8", errors="replace"))
        experiment = json.loads(body)
        self.assertIn("experiment_id", experiment["manifest"])
        self.assertIn("/api/demo-experiment-file", experiment["archive_url"])
        self.assertEqual(self.request(experiment["report_url"], cookie_a)[0], 200)
        self.assertEqual(self.request(experiment["report_url"], cookie_b)[0], 404)
        self.assertEqual(self.request("/api/control", cookie_a, {"command": "step"})[0], 200)
        self.assertEqual(self.request("/api/control", cookie_a, {"command": "step"})[0], 429)
        time.sleep(.3)
        _, _, body = self.request("/api/state", cookie_a); changed_a = json.loads(body)
        _, _, body = self.request("/api/state", cookie_b); unchanged_b = json.loads(body)
        self.assertNotEqual((state_a.get("logs"), state_a.get("cursor")),
                            (changed_a.get("logs"), changed_a.get("cursor")))
        self.assertEqual((state_b.get("logs"), state_b.get("cursor")),
                         (unchanged_b.get("logs"), unchanged_b.get("cursor")))
        self.assertIn("HttpOnly", set_cookie_a)
        self.assertIn("SameSite=Strict", set_cookie_a)

    def test_historical_market_data_and_sample_sentiment_are_explicit(self):
        cookie, _ = self.new_guest()
        status, _, body = self.request("/api/state", cookie)
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertIn("BTC", state.get("data_source", ""))
        status, _, body = self.request("/", cookie)
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("\u5386\u53f2\u884c\u60c5\u865a\u62df\u76d8", html)
        self.assertIn("\u4e0d\u4ee3\u8868\u672a\u6765\u6536\u76ca", html)
        self.assertIn("\u5404\u5e02\u573a\u6765\u6e90", html)
        status, _, body = self.request("/api/sentiment?source=sample&window=240", cookie, host="huaice-demo.example")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body).get("is_sample"))
        self.assertEqual(self.request("/api/sentiment?source=collection", cookie, host="huaice-demo.example")[0], 403)

    def test_full_robustness_report_and_research_limits(self):
        cookie, _ = self.new_guest()
        status, _, body = self.request("/api/report?heavy=1", cookie)
        self.assertEqual(status, 200)
        report = json.loads(body)
        self.assertTrue(report.get("heavy"))
        self.assertTrue(report.get("grid"))
        self.assertTrue(report.get("walk_forward"))
        self.assertTrue(report.get("monte_carlo"))
        status, _, body = self.request("/api/control", cookie,
                                       {"command": "research", "simulations": 600, "block": 10})
        self.assertEqual(status, 400)

    def test_origin_and_health(self):
        self.assertEqual(self.request("/healthz")[0], 200)
        self.assertEqual(self.request("/api/control", payload={"command": "step"},
                                      origin="https://attacker.invalid")[0], 403)

if __name__ == "__main__": unittest.main(verbosity=2)
