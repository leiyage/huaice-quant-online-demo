from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from http.cookies import SimpleCookie

import auto_trader_server as app
import quant_core as qc

MAX_SESSIONS = max(1, int(os.environ.get("DEMO_MAX_SESSIONS", "3")))
IDLE_SECONDS = 30 * 60
COOKIE = "huaice_demo"
ALLOWED_POST = {"start", "pause", "step", "reset", "interval", "fastforward", "research"}
STATIC_PREFIXES = ("/static/", "/intelligence", "/desk", "/bank")
SAFE_GET = {"/api/state", "/api/files", "/api/report", "/api/trade-evidence"}
MAX_RESEARCH_SIMULATIONS = 500
MAX_RESEARCH_BLOCK = 60
HEAVY_WORK_LOCK = threading.BoundedSemaphore(1)

@dataclass
class Guest:
    engine: app.PaperEngine
    touched: float
    lock: threading.RLock
    last_action: float = 0.0
    last_reset: float = 0.0
    last_research: float = 0.0
    artifact_dir: str = ""

class GuestStore:
    def __init__(self):
        self.items: dict[str, Guest] = {}
        self.lock = threading.RLock()

    def get(self, token: str | None):
        now = time.monotonic()
        with self.lock:
            for key, guest in list(self.items.items()):
                if now - guest.touched > IDLE_SECONDS:
                    guest.engine.shutdown()
                    if guest.artifact_dir:
                        shutil.rmtree(guest.artifact_dir, ignore_errors=True)
                    del self.items[key]
            if token and token in self.items:
                guest = self.items[token]
                guest.touched = now
                return token, guest, False
            if len(self.items) >= MAX_SESSIONS:
                return None, None, False
            token = secrets.token_urlsafe(32)
            engine = app.PaperEngine(qc.StrategyParams(), interval=0.35, market="crypto")
            engine.start_worker()
            guest = Guest(engine, now, threading.RLock(), artifact_dir=tempfile.mkdtemp(prefix="huaice-guest-"))
            self.items[token] = guest
            return token, guest, True

    def close(self):
        with self.lock:
            for guest in self.items.values():
                guest.engine.shutdown()
                if guest.artifact_dir:
                    shutil.rmtree(guest.artifact_dir, ignore_errors=True)
            self.items.clear()

store = GuestStore()

class DemoHandler(app.Handler):
    session_token: str | None = None
    new_session = False
    guest: Guest | None = None

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self' https: data:; style-src 'self' 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'")
        if self.session_token:
            secure = "; Secure" if os.environ.get("DEMO_HTTPS", "0") == "1" else ""
            self.send_header("Set-Cookie", f"{COOKIE}={self.session_token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={IDLE_SECONDS}{secure}")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _session(self) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie[COOKIE].value if COOKIE in cookie else None
        except Exception:
            token = None
        token, guest, fresh = store.get(token)
        if guest is None:
            self._send_json({"error": "当前体验人数已满，请稍后再试。"}, 429)
            return False
        self.session_token, self.guest, self.new_session = token, guest, fresh
        self.engine = guest.engine
        return True

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send_json({"ok": True, "mode": "isolated-paper-demo"})
            return
        if path.startswith(STATIC_PREFIXES) or path in ("/", "/index.html", "/favicon.ico"):
            if not self._session(): return
            with self.guest.lock:
                return super().do_GET()
        if path == "/api/sentiment":
            if not self._session(): return
            query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            if (query.get("source") or [""])[0] != "sample":
                self._send_json({"error": "\u5728\u7ebf\u4f53\u9a8c\u53ea\u63d0\u4f9b\u660e\u786e\u6807\u6ce8\u7684\u6559\u5b66\u793a\u4f8b\u8206\u60c5\uff1b\u771f\u5b9e\u91c7\u96c6\u5e93\u4ec5\u672c\u5730\u53ef\u7528\u3002"}, 403)
                return
            try:
                window = int((query.get("window") or ["240"])[0])
                if window < 20 or window > 365:
                    raise ValueError("\u8206\u60c5\u5c55\u793a\u7a97\u53e3\u9700\u5728 20-365 \u5929\u4e4b\u95f4\u3002")
                asof = (query.get("date") or [None])[0]
                with self.guest.lock:
                    result = app.sentiment_payload(asof=asof, window=window) | {"data_mode": "sample"}
                self._send_json(result)
            except (ValueError, TypeError) as exc:
                self._send_json({"error": str(exc), "ok": False}, 400)
            return
        if path == "/api/demo-experiment-file":
            if not self._session(): return
            query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            run = (query.get("run") or [""])[0]
            kind = (query.get("file") or [""])[0]
            if not run or Path(run).name != run or kind not in ("report", "archive"):
                self._send_json({"error": "\u65e0\u6548\u7684\u5b9e\u9a8c\u6587\u4ef6\u8bf7\u6c42\u3002"}, 400)
                return
            root = (Path(self.guest.artifact_dir) / "experiments").resolve()
            filename = "研究对照报告.html" if kind == "report" else None
            target = (root / run / filename if filename else root / (run + ".zip")).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                self._send_json({"error": "\u5b9e\u9a8c\u6587\u4ef6\u4e0d\u5b58\u5728\u6216\u4f1a\u8bdd\u5df2\u8fc7\u671f\u3002"}, 404)
                return
            self._send(200, target.read_bytes(), "application/zip" if kind == "archive" else "text/html; charset=utf-8")
            return
        if path not in SAFE_GET:
            self._send_json({"error": "\u6b64\u529f\u80fd\u672a\u5728\u5728\u7ebf\u4f53\u9a8c\u7248\u5f00\u653e\u3002"}, 403)
            return
        if not self._session(): return
        heavy = path == "/api/report" and ("heavy=1" in self.path or "heavy=true" in self.path)
        if heavy and not HEAVY_WORK_LOCK.acquire(blocking=False):
            self._send_json({"error": "\u5f53\u524d\u6709\u8bbf\u5ba2\u6b63\u5728\u8fd0\u884c\u7a33\u5065\u6027\u5206\u6790\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002"}, 429)
            return
        try:
            with self.guest.lock:
                super().do_GET()
        finally:
            if heavy:
                HEAVY_WORK_LOCK.release()

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/api/control":
            self._send_json({"error": "此功能未在在线体验版开放。"}, 403)
            return
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if origin and origin not in ("https://" + host, "http://" + host):
            self._send_json({"error": "请求来源校验失败。"}, 403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 32 * 1024:
                raise ValueError("请求体无效或过大。")
            payload = json.loads(self.rfile.read(size))
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self._send_json({"error": "??? JSON ???"}, 415)
                return
            if not isinstance(payload, dict) or payload.get("command") not in ALLOWED_POST:
                self._send_json({"error": "该操作未在在线体验版开放。"}, 403)
                return
            if payload.get("command") == "research":
                simulations = int(payload.get("simulations", 300))
                block = int(payload.get("block", 10))
                if simulations < 100 or simulations > MAX_RESEARCH_SIMULATIONS:
                    raise ValueError(f"在线实验重采样次数需为 100-{MAX_RESEARCH_SIMULATIONS}。")
                if block < 1 or block > MAX_RESEARCH_BLOCK:
                    raise ValueError(f"在线实验区块长度需为 1-{MAX_RESEARCH_BLOCK}。")
                payload["simulations"] = simulations
                payload["block"] = block
            if payload.get("command") == "reset" and (payload.get("use_sentiment") or payload.get("sentiment_source") in ("collection", "event")):
                self._send_json({"error": "在线体验版仅使用内置历史行情，不开放采集舆情数据。"}, 403)
                return
            if payload.get("command") == "reset":
                market = str(payload.get("market") or "crypto").lower()
                assets = payload.get("assets")
                allowed = {"BTC", "ETH", "SOL", "BNB", "AAPL", "TSLA", "SPY", "NVDA"}
                if market not in ("crypto", "us") or (assets is not None and (not isinstance(assets, (str, list)) or len(assets) > 4)):
                    self._send_json({"error": "????????????????"}, 400)
                    return
                if assets is not None:
                    names = [x.strip().upper() for x in assets.split(",")] if isinstance(assets, str) else [str(x).upper() for x in assets]
                    if not names or any(x not in allowed for x in names):
                        self._send_json({"error": "???????????????"}, 400)
                        return
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        if not self._session(): return
        command = payload["command"]
        expensive = command == "research"
        if expensive and not HEAVY_WORK_LOCK.acquire(blocking=False):
            self._send_json({"error": "\u5f53\u524d\u6709\u8bbf\u5ba2\u6b63\u5728\u8fd0\u884c\u7814\u7a76\u5b9e\u9a8c\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002"}, 429)
            return
        try:
            with self.guest.lock:
                now = time.monotonic()
                if now - self.guest.last_action < 0.25:
                    self._send_json({"error": "\u64cd\u4f5c\u8fc7\u4e8e\u9891\u7e41\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002"}, 429)
                    return
                if command == "reset" and now - self.guest.last_reset < 3.0:
                    self._send_json({"error": "\u91cd\u7f6e\u95f4\u9694\u9700\u81f3\u5c11 3 \u79d2\u3002"}, 429)
                    return
                if command == "research":
                    if now - self.guest.last_research < 20.0:
                        self._send_json({"error": "\u7814\u7a76\u5b9e\u9a8c\u6bcf\u4e2a\u8bbf\u5ba2\u9700\u95f4\u9694 20 \u79d2\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002"}, 429)
                        return
                    if len(self.engine.state().get("assets", [])) > 4:
                        self._send_json({"error": "\u5728\u7ebf\u5b9e\u9a8c\u6700\u591a\u652f\u6301 4 \u4e2a\u8d44\u4ea7\u3002"}, 400)
                        return
                    result = self.engine.research_experiment(payload, output_dir=Path(self.guest.artifact_dir))
                    self.guest.last_research = now
                else:
                    result = self.engine.control(str(command), payload)
                self.guest.last_action = now
                if command == "reset":
                    self.guest.last_reset = now
            self._send_json(result)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, 409)
        except Exception as exc:
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        finally:
            if expensive:
                HEAVY_WORK_LOCK.release()

class DemoServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

def main():
    host = os.environ.get("DEMO_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", os.environ.get("DEMO_PORT", "8777")))
    if host not in ("127.0.0.1", "0.0.0.0"):
        raise SystemExit("DEMO_HOST 仅允许 127.0.0.1 或 0.0.0.0")
    server = DemoServer((host, port), DemoHandler)
    print(f"淮策 Quant 隔离体验开发版已启动：{host}:{port}（每访客独立模拟账户；最多 {MAX_SESSIONS} 个会话）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()

if __name__ == "__main__":
    main()

