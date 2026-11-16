from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from http.cookies import SimpleCookie

import auto_trader_server as app
import quant_core as qc

MAX_SESSIONS = 3
IDLE_SECONDS = 30 * 60
COOKIE = "huaice_demo"
ALLOWED_POST = {"start", "pause", "step", "reset", "interval", "fastforward"}
STATIC_PREFIXES = ("/static/", "/intelligence", "/desk", "/bank")
SAFE_GET = {"/api/state", "/api/files", "/api/report", "/api/trade-evidence"}

@dataclass
class Guest:
    engine: app.PaperEngine
    touched: float
    lock: threading.RLock
    last_action: float = 0.0
    last_reset: float = 0.0

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
            guest = Guest(engine, now, threading.RLock())
            self.items[token] = guest
            return token, guest, True

    def close(self):
        with self.lock:
            for guest in self.items.values():
                guest.engine.shutdown()
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
        if path not in SAFE_GET:
            self._send_json({"error": "此功能未在在线体验版开放。"}, 403)
            return
        if path == "/api/report" and "heavy=1" in self.path:
            self._send_json({"error": "在线体验版不开放重型报告计算。"}, 403)
            return
        if not self._session(): return
        with self.guest.lock:
            super().do_GET()

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
        try:
            with self.guest.lock:
                now = time.monotonic()
                if now - self.guest.last_action < 0.25:
                    self._send_json({"error": "???????????"}, 429)
                    return
                if payload["command"] == "reset" and now - self.guest.last_reset < 3.0:
                    self._send_json({"error": "??????? 3 ????"}, 429)
                    return
                result = self.engine.control(str(payload["command"]), payload)
                self.guest.last_action = now
                if payload["command"] == "reset":
                    self.guest.last_reset = now
            self._send_json(result)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, 409)
        except Exception as exc:
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

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

