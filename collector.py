"""Persistent, manual-only public-feed collection. Never feeds the trading engine.

All timestamps are UTC ISO strings. An imported timestamp is not evidence of
historical availability: first-seen time is always the time this desk received it.
"""
from __future__ import annotations

import csv
import hashlib
import http.client
import io
import ipaddress
import json
import os
import queue
import re
import socket
import sqlite3
import ssl
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pandas as pd

from sentiment import sentiment_core as core, sentiment_sources as adapter, weight_config as cfg

MAX_BYTES = 3 * 1024 * 1024
MAX_ROWS = 2000
FETCH_TIMEOUT = 20.0
DNS_TIMEOUT = 5.0
LOCAL_ZONE = datetime.now().astimezone().tzinfo


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_time(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("缺少发布时间")
    try:
        stamp = pd.Timestamp(text)
    except (ValueError, TypeError):
        try:
            stamp = pd.Timestamp(parsedate_to_datetime(text))
        except (ValueError, TypeError) as exc:
            raise ValueError("发布时间无法解析：" + text[:80]) from exc
    if pd.isna(stamp):
        raise ValueError("发布时间无法解析")
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(LOCAL_ZONE)
    return stamp.tz_convert("UTC").isoformat(timespec="seconds")


def infer_asset(text, default="GENERAL"):
    # Token boundaries prevent SOL from matching words such as "resolution".
    aliases = {"BTC": ("bitcoin", "比特币"), "ETH": ("ethereum", "以太坊"),
               "SOL": ("solana",), "BNB": ("binance coin", "币安币"),
               "TSLA": ("tesla", "特斯拉"), "AAPL": ("apple", "苹果公司"),
               "NVDA": ("nvidia", "英伟达"), "SPY": ("spdr s&p 500",)}
    for asset, words in aliases.items():
        for word in (asset,) + words:
            pattern = re.escape(word) if re.search(r"[\u4e00-\u9fff]", word) else r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])"
            if re.search(pattern, str(text), re.IGNORECASE):
                return asset
    return default


def validate_url(url):
    parts = urlsplit(str(url))
    if (parts.scheme != "https" or not parts.hostname or parts.username or
            parts.password or parts.fragment or parts.port not in (None, 443) or len(url) > 2048):
        raise ValueError("订阅地址须为公开 HTTPS 链接，不能包含密码、片段或自定义端口")
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith((".local", ".localhost")):
        raise ValueError("不能采集本机或内网地址")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("不能采集本机或内网地址")
    return parts


def fetch_public_feed(url):
    """Validate every redirect and pin the connection to a public resolved IP.

    No cookies, credentials, proxy environment or arbitrary filesystem access.
    TLS is still verified against the original hostname.
    """
    deadline = time.monotonic() + FETCH_TIMEOUT
    active_socket = [None]

    def remaining():
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise TimeoutError("订阅请求超过总时限，请稍后重试")
        return min(10.0, budget)

    def abort_socket():
        current = active_socket[0]
        if current is not None:
            try:
                current.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                current.close()
            except OSError:
                pass

    # Closing the socket also bounds slow HTTP headers and TLS trickle attacks.
    watchdog = threading.Timer(FETCH_TIMEOUT, abort_socket)
    watchdog.daemon = True
    watchdog.start()
    try:
        for _ in range(4):
            parts = validate_url(url)
            resolved = queue.Queue(maxsize=1)

            def resolve(host=parts.hostname, result=resolved):
                try:
                    result.put((socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM), None))
                except Exception as exc:
                    result.put((None, exc))

            resolver = threading.Thread(target=resolve, daemon=True)
            resolver.start()
            try:
                addresses, error = resolved.get(timeout=min(DNS_TIMEOUT, remaining()))
            except queue.Empty as exc:
                raise TimeoutError("订阅域名解析超时") from exc
            if error:
                raise error
            if not addresses or any(not ipaddress.ip_address(v[4][0]).is_global for v in addresses):
                raise ValueError("订阅域名解析到内网或非公开地址，已拒绝")
            conn = http.client.HTTPSConnection(parts.hostname, timeout=remaining())
            try:
                active_socket[0] = socket.create_connection((addresses[0][4][0], 443), timeout=remaining())
                active_socket[0] = ssl.create_default_context().wrap_socket(active_socket[0], server_hostname=parts.hostname,
                                                                         do_handshake_on_connect=False)
                conn.sock = active_socket[0]
                conn.sock.settimeout(remaining())
                conn.sock.do_handshake()
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                conn.sock.settimeout(remaining())
                conn.request("GET", path, headers={"User-Agent": "HuaiceQuant/3.0 (public RSS research)",
                                                  "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
                                                  "Accept-Encoding": "identity"})
                conn.sock.settimeout(remaining())
                with conn.getresponse() as response:
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.getheader("Location")
                        if not location:
                            raise ValueError("订阅源重定向缺少地址")
                        url = urljoin(url, location)
                        continue
                    if response.status != 200:
                        raise ValueError(f"订阅源返回 HTTP {response.status}")
                    chunks, size = [], 0
                    while True:
                        active_socket[0].settimeout(remaining())
                        chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ValueError("订阅响应超过 3 MB，请使用更小的订阅源")
                        chunks.append(chunk)
                    remaining()
                    return b"".join(chunks)
            finally:
                conn.close()
                abort_socket()
                active_socket[0] = None
        raise ValueError("订阅源重定向过多")
    except (OSError, http.client.HTTPException) as exc:
        if time.monotonic() >= deadline:
            raise TimeoutError("订阅请求超过总时限，请稍后重试") from exc
        raise
    finally:
        watchdog.cancel()


class CollectionDesk:
    def __init__(self, root, fetcher=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "collection.sqlite3"
        self.lock = threading.RLock()
        self.fetcher = fetcher or fetch_public_feed
        self.worker = None
        self.stopping = threading.Event()
        self.owner_file = (self.root / "owner.lock").open("a+b")
        self.owner_file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.owner_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.owner_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.owner_file.close()
            raise ValueError("另一个淮策程序正在使用采集库。请关闭旧程序后再启动；不会重置其运行任务。") from exc
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, config TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, started TEXT NOT NULL,
                    status TEXT NOT NULL, info TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, first_seen TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sightings(task_id TEXT NOT NULL, message_id TEXT NOT NULL,
                    source_id TEXT NOT NULL, captured TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS exports(id TEXT PRIMARY KEY, created TEXT NOT NULL,
                    manifest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS rejected(id INTEGER PRIMARY KEY, task_id TEXT NOT NULL,
                    source_id TEXT NOT NULL, data TEXT NOT NULL);
            """)
            for task in db.execute("SELECT id,info FROM tasks WHERE status='running'").fetchall():
                info = json.loads(task[1])
                info["error"] = "程序关闭时任务未结束，请重新采集；已入库数据保留"
                info["finished"] = now_iso()
                db.execute("UPDATE tasks SET status='interrupted',info=? WHERE id=?", (json.dumps(info, ensure_ascii=False), task[0]))

    def close(self):
        self.stopping.set()
        if self.worker and self.worker.is_alive():
            self.worker.join(1)
        if not self.worker or not self.worker.is_alive():
            self.owner_file.close()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_source(self, payload):
        name = str(payload.get("name", "")).strip()
        url = str(payload.get("url", "")).strip()
        category = str(payload.get("category", "media"))
        content_type = str(payload.get("content_type", "opinion"))
        asset = str(payload.get("asset", "GENERAL")).strip().upper()
        if not name or len(name) > 80 or category not in cfg.SOURCE_WEIGHTS or content_type not in cfg.CONTENT_WEIGHTS:
            raise ValueError("请填写名称，并选择有效的来源分级和内容类型")
        if not re.fullmatch(r"[A-Z0-9.^_-]{1,20}", asset):
            raise ValueError("资产代码格式不正确；未确定资产请填 GENERAL")
        validate_url(url)
        source_id = str(payload.get("id") or uuid.uuid4().hex)
        if not re.fullmatch(r"[a-f0-9]{32}", source_id):
            raise ValueError("订阅源编号无效")
        with self.lock, self.connect() as db:
            existing = db.execute("SELECT config FROM sources WHERE id=?", (source_id,)).fetchone()
            config = dict(id=source_id, name=name, url=url, category=category,
                          content_type=content_type, asset=asset, enabled=json.loads(existing[0])["enabled"] if existing else True)
            if db.execute("SELECT count(*) FROM sources").fetchone()[0] >= 30 and not db.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone():
                raise ValueError("最多保存 30 个订阅源")
            db.execute("INSERT OR REPLACE INTO sources VALUES(?,?)", (source_id, json.dumps(config, ensure_ascii=False)))
        return config

    def toggle_source(self, source_id):
        with self.lock, self.connect() as db:
            found = db.execute("SELECT config FROM sources WHERE id=?", (source_id,)).fetchone()
            if not found:
                raise ValueError("订阅源不存在")
            config = json.loads(found[0])
            config["enabled"] = not config["enabled"]
            db.execute("UPDATE sources SET config=? WHERE id=?", (json.dumps(config, ensure_ascii=False), source_id))

    def _new_task(self, kind, configs):
        if self.stopping.is_set():
            raise ValueError("采集控制台已关闭，请重新启动程序")
        with self.lock, self.connect() as db:
            if db.execute("SELECT 1 FROM tasks WHERE status='running'").fetchone():
                raise ValueError("已有采集任务运行，请等待完成")
            task_id = uuid.uuid4().hex
            info = dict(id=task_id, kind=kind, started=now_iso(), finished=None, sources=configs,
                        counts=dict(received=0, cleaned=0, duplicate=0, classified=0, scored=0, stored=0, rejected=0),
                        logs=[], errors=0, stage="采集")
            db.execute("INSERT INTO tasks VALUES(?,?,?,?)", (task_id, info["started"], "running", json.dumps(info, ensure_ascii=False)))
        return info

    def _save_task(self, info, status="running"):
        info["logs"] = info["logs"][-60:]
        with self.lock, self.connect() as db:
            db.execute("UPDATE tasks SET status=?,info=? WHERE id=?", (status, json.dumps(info, ensure_ascii=False), info["id"]))

    def _log(self, info, message):
        info["logs"].append(dict(time=now_iso(), text=str(message)[:500]))
        self._save_task(info)

    def _ingest(self, info, rows, config):
        received = now_iso()
        counts = info["counts"]
        counts["received"] += len(rows)
        info["stage"] = "清洗 / 去重 / 分级 / 评分 / 入库"
        # One transaction per source. Rejected rows never become valid evidence.
        previous_counts = counts.copy()
        try:
            with self.lock, self.connect() as db:
                for position, row in enumerate(rows, 1):
                    try:
                        text = adapter.clean_text(row.get("内容", ""))
                        if not text:
                            raise ValueError("内容为空")
                        if len(text) > 8000:
                            raise ValueError("正文超过 8000 字，未截断入库；请拆分或人工整理")
                        published = iso_time(row.get("发布时间"))
                        asset = (infer_asset(text, config["asset"]) if config["kind"] == "rss" else str(row.get("资产") or config["asset"]).upper().strip())
                        if not re.fullmatch(r"[A-Z0-9.^_-]{1,20}", asset):
                            raise ValueError("资产代码无效")
                    except (ValueError, TypeError, OverflowError) as exc:
                        counts["rejected"] += 1
                        rejection = dict(row_number=position, source_name=config["name"], time=received,
                                         reason=str(exc)[:300], text=str(row.get("内容", ""))[:1000],
                                         published=str(row.get("发布时间", ""))[:100])
                        db.execute("INSERT INTO rejected(task_id,source_id,data) VALUES(?,?,?)", (info["id"], config["id"], json.dumps(rejection, ensure_ascii=False)))
                        if counts["rejected"] <= 5:
                            info["logs"].append(dict(time=received, text=f"{config['name']} 第 {position} 条隔离：{rejection['reason']}"))
                        continue
                    counts["cleaned"] += 1
                    # Same text can legitimately recur on a different publication date.
                    fingerprint = hashlib.sha256((asset + "\n" + published + "\n" + re.sub(r"\s+", "", text).casefold()).encode()).hexdigest()
                    existing = db.execute("SELECT id FROM messages WHERE fingerprint=?", (fingerprint,)).fetchone()
                    if existing:
                        counts["duplicate"] += 1
                        message_id = existing[0]
                    else:
                        category = config["category"] if config["kind"] == "rss" else adapter.normalize_source(row.get("来源", config["category"]))
                        given_type = config["content_type"] if config["kind"] == "rss" else adapter.normalize_content(row.get("内容类型", ""))
                        content_type = core.detect_content_type(pd.Series(dict(text=text, content_type=given_type)))
                        direction = core.lexicon_score(text)
                        message_id = uuid.uuid4().hex
                        counts["classified"] += 1
                        counts["scored"] += 1
                        counts["stored"] += 1
                        data = dict(id=message_id, asset=asset, text=text, url=str(row.get("链接") or "")[:2048],
                                    author=str(row.get("作者") or "")[:200], source_name=config["name"], source_id=config["id"],
                                    category=category, content_type=content_type, published=published, captured=received,
                                    available=max(published, received), direction=direction,
                                    source_time_kind=str(row.get("时间字段") or "发布时间"),
                                    source_weight=cfg.SOURCE_WEIGHTS[category], content_weight=cfg.CONTENT_WEIGHTS[content_type],
                                    half_life_hours=cfg.HALF_LIFE_HOURS[content_type],
                                    provenance="公开 RSS/Atom" if config["kind"] == "rss" else "本地导入（真实性待核验）",
                                    original_capture=str(row.get("抓取时间") or "")[:100] if config["kind"] == "local" else "",
                                    task_id=info["id"], fingerprint=fingerprint)
                        db.execute("INSERT INTO messages VALUES(?,?,?,?)", (message_id, received, fingerprint, json.dumps(data, ensure_ascii=False)))
                    db.execute("INSERT INTO sightings VALUES(?,?,?,?)", (info["id"], message_id, config["id"], received))
                # Counters and evidence commit together even if power is lost.
                db.execute("UPDATE tasks SET info=? WHERE id=?", (json.dumps(info, ensure_ascii=False), info["id"]))
        except BaseException:
            counts.update(previous_counts)
            raise
        self._log(info, f"{config['name']}：读取 {len(rows)} 条，累计新增 {counts['stored']}、重复 {counts['duplicate']}、隔离 {counts['rejected']}")

    def start(self, source_id=""):
        with self.lock, self.connect() as db:
            configs = [json.loads(r[0]) for r in db.execute("SELECT config FROM sources")]
            configs = [c | {"kind": "rss"} for c in configs if c["enabled"] and (not source_id or c["id"] == source_id)]
            if not configs:
                raise ValueError("没有启用的订阅源，请先添加或启用一个来源")
            info = self._new_task("rss", configs)
        self.worker = threading.Thread(target=self._run, args=(info,), daemon=True)
        self.worker.start()
        return dict(task_id=info["id"])

    def _run(self, info):
        try:
            for config in info["sources"]:
                if self.stopping.is_set():
                    raise RuntimeError("程序正在关闭；未开始的来源已停止")
                rows = None
                for attempt in range(2):
                    if self.stopping.is_set():
                        raise RuntimeError("程序正在关闭；不再重试")
                    try:
                        self._log(info, f"正在采集 {config['name']}（尝试 {attempt + 1}/2）")
                        payload = self.fetcher(config["url"])
                        if len(payload) > MAX_BYTES or b"<!doctype" in payload.lower() or b"<!entity" in payload.lower():
                            raise ValueError("订阅 XML 过大或包含不支持的声明")
                        parsed = adapter.parse_feed(payload, source=config["category"], default_asset=config["asset"],
                                                    max_text_chars=None, require_published=True)
                        if len(parsed) > MAX_ROWS:
                            raise ValueError("单次超过 2000 条，请缩小订阅范围")
                        if not parsed:
                            raise ValueError("订阅中没有可解析的消息（可能不是 RSS/Atom）")
                        rows = parsed
                        break
                    except Exception as exc:
                        self._log(info, f"{config['name']} 失败：{type(exc).__name__}: {exc}")
                if rows is None:
                    info["errors"] += 1
                    continue
                self._ingest(info, rows, config)
            info["finished"] = now_iso()
            info["stage"] = "完成"
            self._save_task(info, "partial" if info["errors"] else "done")
        except Exception as exc:
            info["finished"] = now_iso()
            info["error"] = f"{type(exc).__name__}: {exc}"
            self._save_task(info, "failed")
        finally:
            if self.stopping.is_set():
                self.owner_file.close()

    def import_csv(self, text, filename):
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
            raise ValueError("CSV 文件应小于 3 MB")
        raw = pd.read_csv(io.StringIO(text.lstrip("\ufeff")), dtype=str, keep_default_na=False, nrows=MAX_ROWS + 1)
        if len(raw) > MAX_ROWS or raw.empty:
            raise ValueError("每次导入需包含 1—2000 行")
        # standardize supplies legacy capture times; _ingest never trusts those.
        frame = adapter.standardize(raw)
        if not any(str(col).strip().lower() in adapter.FRAME_ALIASES["抓取时间"] for col in raw.columns):
            frame["抓取时间"] = ""
        # Missing asset is GENERAL, never silently label an unrelated news item BTC.
        if not any(str(c).strip().lower() in adapter.FRAME_ALIASES["资产"] for c in raw.columns):
            frame["资产"] = frame["内容"].map(infer_asset)
        config = dict(id="local", kind="local", name=str(filename or "本地 CSV")[:80],
                      category="social", content_type="opinion", asset="GENERAL")
        info = self._new_task("local", [config])
        try:
            self._ingest(info, frame.to_dict("records"), config)
            info["finished"] = now_iso()
            info["stage"] = "完成"
            self._save_task(info, "done")
        except Exception as exc:
            info["finished"] = now_iso()
            info["error"] = str(exc)
            self._save_task(info, "failed")
            raise
        return dict(task_id=info["id"])

    def snapshot(self):
        with self.lock, self.connect() as db:
            messages = [json.loads(r[0]) for r in db.execute("SELECT data FROM messages ORDER BY first_seen DESC,rowid DESC LIMIT 300")]
            tasks = [json.loads(r["info"]) | {"status": r["status"]} for r in db.execute("SELECT * FROM tasks ORDER BY started DESC,rowid DESC LIMIT 15")]
            sources = [json.loads(r[0]) for r in db.execute("SELECT config FROM sources ORDER BY rowid")]
            exports = [json.loads(r[0]) for r in db.execute("SELECT manifest FROM exports ORDER BY created DESC,rowid DESC LIMIT 5")]
            total = db.execute("SELECT count(*) FROM messages").fetchone()[0]
            rejected_total = db.execute("SELECT count(*) FROM rejected").fetchone()[0]
            captured_now = now_iso()
            for item in messages:
                age = max(0, (datetime.fromisoformat(captured_now) - datetime.fromisoformat(item["published"])).total_seconds() / 3600)
                item["decay"] = 2 ** (-age / item["half_life_hours"])
                item["available_now"] = item["available"] <= captured_now
                item["effective_weight"] = item["source_weight"] * item["content_weight"] * item["decay"] if item["available_now"] else 0
                item["observations"] = db.execute("SELECT count(*) FROM sightings WHERE message_id=?", (item["id"],)).fetchone()[0]
        return dict(sources=sources, tasks=tasks, messages=messages, total=total, rejected_total=rejected_total,
                    running=any(t["status"] == "running" for t in tasks), exports=exports, time=captured_now)

    def radar(self, asof=None, window=240):
        from shared_sentiment import radar_payload
        # Read the full committed database, not the desk's 300-row display limit.
        with self.lock, self.connect() as db:
            records = [json.loads(r[0]) for r in db.execute("SELECT data FROM messages ORDER BY first_seen,rowid")]
        return radar_payload(records, now_iso(), asof, window)

    def message(self, message_id):
        if not re.fullmatch(r"[a-f0-9]{32}", message_id):
            raise ValueError("消息编号无效")
        with self.lock, self.connect() as db:
            found = db.execute("SELECT data FROM messages WHERE id=?", (message_id,)).fetchone()
            if not found:
                raise ValueError("消息不存在")
            item = json.loads(found[0])
            item["observations"] = db.execute("SELECT count(*) FROM sightings WHERE message_id=?", (message_id,)).fetchone()[0]
        current = now_iso()
        age = max(0, (datetime.fromisoformat(current) - datetime.fromisoformat(item["published"])).total_seconds() / 3600)
        item["decay"] = 2 ** (-age / item["half_life_hours"])
        item["available_now"] = item["available"] <= current
        item["effective_weight"] = item["source_weight"] * item["content_weight"] * item["decay"] if item["available_now"] else 0
        return item

    def export(self):
        with self.lock, self.connect() as db:
            messages = [json.loads(r[0]) for r in db.execute("SELECT data FROM messages ORDER BY first_seen,rowid")]
            rejections = [dict(r) | {"data": json.loads(r["data"])} for r in db.execute("SELECT * FROM rejected ORDER BY id")]
            if not messages and not rejections:
                raise ValueError("没有已入库消息，无法导出")
            sightings = [dict(r) for r in db.execute("SELECT * FROM sightings ORDER BY rowid")]
            tasks = [dict(r) for r in db.execute("SELECT * FROM tasks ORDER BY rowid")]
            sources = [json.loads(r[0]) for r in db.execute("SELECT config FROM sources ORDER BY rowid")]
            export_id = "collection_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8]
            stream = io.StringIO(newline="")
            columns = ["发布时间", "抓取时间", "来源", "作者", "资产", "内容类型", "内容", "链接", "数据性质", "方向分", "消息ID"]
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for m in messages:
                # Prefix spreadsheet formula characters only in CSV; JSON retains original evidence.
                values = [m[k] for k in ("published", "captured", "category", "author", "asset", "content_type", "text", "url", "provenance", "direction", "id")]
                writer.writerow(dict(zip(columns, [("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v) for v in values])))
            files = {"messages.csv": stream.getvalue().encode("utf-8-sig"),
                     "evidence.json": json.dumps(messages, ensure_ascii=False, indent=2).encode("utf-8"),
                     "observations.json": json.dumps(sightings, ensure_ascii=False, indent=2).encode("utf-8"),
                     "tasks.json": json.dumps(tasks, ensure_ascii=False, indent=2).encode("utf-8"),
                     "sources.json": json.dumps(sources, ensure_ascii=False, indent=2).encode("utf-8")}
            files["rejections.json"] = json.dumps(rejections, ensure_ascii=False, indent=2).encode("utf-8")
            manifest = dict(id=export_id, created=now_iso(), count=len(messages), timezone="UTC",
                            rejected_count=len(rejections), schema_version=1,
                            note="单条规则词典分数；按资产+发布时间+精确文本去重，非语义事件聚类。来源权重/半衰期为人工先验；未接入交易。导入时间为首次可得时间，原始字段仅作线索。",
                            parameters=dict(source_weights=cfg.SOURCE_WEIGHTS, content_weights=cfg.CONTENT_WEIGHTS, half_life_hours=cfg.HALF_LIFE_HOURS,
                                            lexicon_positive=cfg.LEXICON_POSITIVE, lexicon_negative=cfg.LEXICON_NEGATIVE, negation_words=cfg.NEGATION_WORDS),
                            hashes={name: hashlib.sha256(body).hexdigest() for name, body in files.items()},
                            download_url="/api/collection-export?id=" + export_id)
            folder = self.root / "exports"
            folder.mkdir(exist_ok=True)
            temporary = folder / (export_id + ".tmp")
            final = folder / (export_id + ".zip")
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, body in files.items():
                    archive.writestr(name, body)
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
            temporary.replace(final)
            db.execute("INSERT INTO exports VALUES(?,?,?)", (export_id, manifest["created"], json.dumps(manifest, ensure_ascii=False)))
        return manifest

    def export_path(self, export_id):
        if not re.fullmatch(r"collection_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{8}", export_id):
            raise ValueError("导出编号无效")
        target = self.root / "exports" / (export_id + ".zip")
        if not target.is_file():
            raise ValueError("导出文件不存在")
        return target
