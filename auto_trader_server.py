# -*- coding: utf-8 -*-
"""黄淮学院量化交易研究平台（本地虚拟盘回放引擎 + 本地界面服务）v3.0。

程序名字 / 学校 / 署名都在 branding.py 里改，本文件不用动。

设计原则（三条硬约束，任何版本都不会破坏）：
1. 只在本机运行，只监听 127.0.0.1，绝不连接任何交易所或券商的交易接口；
2. 任何时候都不会发送真实订单、不需要 API Key、不使用真实资金；
3. 行情全部来自本机 CSV（可由公开只读行情接口下载）。

结构：
    quant_core.py          研究内核（指标 / 策略 / 风控 / 成本 / 绩效 / 稳健性）
    auto_trader_server.py  本文件：把回测结果逐根 K 线"回放"出来 + 本地界面服务
    research_report.py     生成 HTML 研究报告
    branding.py            品牌配置（改名只改这一个文件）
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import numpy as np
import pandas as pd

import branding
import quant_core as qc
from collector import CollectionDesk
from research_workflow import WorkflowStore
from research_command import CommandCenter

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
RESULT_DIR = BASE_DIR / "results"
DATA_DIR = BASE_DIR / "data"
APP_VERSION = branding.APP_VERSION      # 改版本号请去 branding.py
DEFAULT_PORT = 8777
DEFAULT_ASSETS = ["BTC", "ETH", "SOL", "BNB"]
DEFAULT_US_ASSETS = ["AAPL", "TSLA", "SPY", "NVDA"]
OHLC_WINDOW = 180          # 界面蜡烛图保留的根数

SENTIMENT_DIR = BASE_DIR / "sentiment"   # 舆情子模块（删掉也不影响主程序）
_SENTIMENT_CACHE: dict[str, Any] = {}
_TRADE_SENTIMENT_ITEMS: dict[str, Any] = {}


def _make_console_safe() -> None:
    """保证中文日志在任意控制台编码下都能正常输出，不因编码中断程序。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# =====================================================================
# 舆情雷达（第二阶段：只显示，不参与任何买卖决策）
# =====================================================================

def _sentiment_daily() -> dict[str, Any]:
    """算一次日频舆情表并缓存（只读本机 CSV，不联网）。"""
    from sentiment import SAMPLE_CSV
    mtime = SAMPLE_CSV.stat().st_mtime_ns if SAMPLE_CSV.exists() else None
    if _SENTIMENT_CACHE.get("mtime") == mtime and ("daily" in _SENTIMENT_CACHE or "error" in _SENTIMENT_CACHE):
        return _SENTIMENT_CACHE
    _SENTIMENT_CACHE.clear()
    _SENTIMENT_CACHE["mtime"] = mtime
    try:
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        from sentiment import daily_scores, SAMPLE_CSV
        table = daily_scores()
        _SENTIMENT_CACHE["daily"] = table
        _SENTIMENT_CACHE["source"] = SAMPLE_CSV.name
        _SENTIMENT_CACHE["source_path"] = str(SAMPLE_CSV)
        _SENTIMENT_CACHE["is_sample"] = True
    except Exception as exc:  # pragma: no cover
        _SENTIMENT_CACHE["error"] = f"{type(exc).__name__}: {exc}"
        logger_line = f"[舆情] 加载失败：{_SENTIMENT_CACHE['error']}"
        print(logger_line)
    return _SENTIMENT_CACHE


def _sentiment_verdict() -> list:
    """读取验证层输出的 IC 汇总（如果跑过）。"""
    path = SENTIMENT_DIR / "output" / "检验1b_IC汇总.csv"
    if not path.exists():
        return []
    try:
        frame = pd.read_csv(path)
        if "RankIC_水平z" not in frame.columns:
            return []      # 旧版结果，让前端提示重跑一次自检
        return json.loads(frame.to_json(orient="records", force_ascii=False))
    except Exception:
        return []


def sentiment_payload(asof: str | None = None, window: int = 240) -> dict[str, Any]:
    """给前端的「舆情雷达」标签页提供数据：回放日快照 + 截止回放日的曲线。

    重要：曲线只取「截止回放日」的 window 天，防止回放时看到未来数据。
    回放日位于数据起点、历史不足 window 天时，前端会明确提示实际可用天数。
    """
    cache = _sentiment_daily()
    if cache.get("error"):
        return {"ok": False, "error": cache["error"],
                "hint": "双击「运行舆情自检.bat」可看到详细错误。"}
    daily: pd.DataFrame = cache["daily"]
    assets = sorted(str(a) for a in daily["asset"].unique())
    start = daily["date"].min()
    end = daily["date"].max()
    try:
        asof_ts = pd.Timestamp(asof).normalize() if asof else end
    except Exception:
        asof_ts = end
    asof_ts = max(start, min(asof_ts, end))

    window = max(20, int(window))
    pivot = daily.pivot_table(index="date", columns="asset", values="z").sort_index()
    available = pivot.index[pivot.index <= asof_ts]
    win = available[-window:]
    history_days = int(len(win))
    insufficient = history_days < window
    asof_pos = history_days - 1 if history_days else None
    series: dict[str, list] = {}
    for asset in pivot.columns:
        col = pivot[asset].reindex(win)
        series[str(asset)] = [
            {"d": str(d.date()), "z": (None if pd.isna(v) else round(float(v), 4))}
            for d, v in col.items()]

    snapshot: list[dict[str, Any]] = []
    for asset in assets:
        sub = daily[daily["asset"] == asset].sort_values("date")
        sub = sub[sub["date"] <= asof_ts]
        if sub.empty:
            continue
        last = sub.iloc[-1]
        try:
            from sentiment import sentiment_core as _core
            mult = float(_core.sentiment_multiplier(float(last["z"]), float(last["coverage"])))
        except Exception:
            mult = 1.0
        snapshot.append({
            "asset": asset,
            "date": str(last["date"].date()),
            "z": round(float(last["z"]), 4),
            "score": round(float(last["score"]), 4),
            "coverage": round(float(last["coverage"]), 4),
            "evidence": int(last["evidence"]),
            "top_event": str(last["top_event"]),
            "top_source": str(last["top_source"]),
            "top_direction": round(float(last["top_direction"]), 3),
            "multiplier": round(mult, 3),
        })

    return {
        "ok": True,
        "is_sample": bool(cache.get("is_sample")),
        "source": cache.get("source", ""),
        "asof": str(asof_ts.date()),
        "start": str(start.date()),
        "end": str(end.date()),
        "window": int(window),
        "window_days": history_days,
        "window_start": str(win[0].date()) if history_days else "",
        "window_end": str(win[-1].date()) if history_days else "",
        "insufficient_history": bool(insufficient),
        "asof_index": asof_pos,
        "assets": assets,
        "series": series,
        "snapshot": snapshot,
        "verdict": _sentiment_verdict(),
        "note": "舆情模块独立于价格引擎：这一页只展示，不会改变任何一笔模拟交易。",
    }

def point_in_time_sentiment(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Daily pre-open risk multiplier, computed only from earlier captured raw events.

    The display radar clusters events; its precomputed daily score is deliberately NOT
    used here because a cluster may acquire corroborating messages later in the day.
    No same-day message can affect an opening fill. Raw duplicates are not deduplicated.
    """
    from sentiment import SAMPLE_CSV, sentiment_core as core, weight_config as cfg
    if not SAMPLE_CSV.is_file():
        raise ValueError("????????????????????????????")
    mtime = SAMPLE_CSV.stat().st_mtime_ns
    if _TRADE_SENTIMENT_ITEMS.get("mtime") != mtime:
        items = core.prepare_items(pd.read_csv(SAMPLE_CSV))
        _TRADE_SENTIMENT_ITEMS.clear()
        _TRADE_SENTIMENT_ITEMS.update(mtime=mtime, items=items)
    items = _TRADE_SENTIMENT_ITEMS["items"]
    output = {}
    for symbol, frame in frames.items():
        sub = items[items["asset"] == symbol].sort_values("t_use")
        times = sub["t_use"].to_numpy(dtype="datetime64[ns]")
        weights = sub["w_static"].to_numpy(dtype=float)
        half_lives = sub["half_life"].to_numpy(dtype=float)
        directions = sub["e"].to_numpy(dtype=float)
        rows = []
        for day in frame.index:
            # Conservative cutoff: strictly before the fill day's midnight.
            cutoff = pd.Timestamp(day).normalize()
            end = int(np.searchsorted(times, np.datetime64(cutoff), side="left"))
            if end == 0:
                rows.append((0.0, 0.0, 1.0))
                continue
            age = (np.datetime64(cutoff) - times[:end]) / np.timedelta64(1, "h")
            decayed = weights[:end] * np.exp2(-age / half_lives[:end])
            total = float(decayed.sum())
            coverage = total / (total + cfg.KAPPA)
            z = float(np.tanh(float(np.dot(decayed, directions[:end])) /
                              (total + cfg.KAPPA) / cfg.TAU))
            multiplier = float(core.sentiment_multiplier(z, coverage, mode="risk_control"))
            rows.append((z, coverage, multiplier))
        output[symbol] = pd.DataFrame(rows, index=frame.index,
                                      columns=["z", "coverage", "multiplier"])
    return output


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on", "是")


def _as_float(payload: dict[str, Any], key: str, default: float) -> float:
    try:
        raw = payload.get(key)
        return default if raw in (None, "") else float(raw)
    except (TypeError, ValueError):
        return default


def _as_int(payload: dict[str, Any], key: str, default: int) -> int:
    try:
        raw = payload.get(key)
        return default if raw in (None, "") else int(float(raw))
    except (TypeError, ValueError):
        return default


def parse_params(payload: dict[str, Any], base: qc.StrategyParams) -> qc.StrategyParams:
    """把界面提交的 JSON 变成策略参数对象（字段缺失就沿用原值）。"""
    params = replace(
        base,
        initial_cash=_as_float(payload, "initial_cash", base.initial_cash),
        ma_type=str(payload.get("ma_type") or base.ma_type).lower(),
        fast=_as_int(payload, "fast", base.fast),
        slow=_as_int(payload, "slow", base.slow),
        regime_filter=_as_bool(payload.get("regime_filter"), base.regime_filter),
        use_sentiment=_as_bool(payload.get("use_sentiment"), base.use_sentiment),
        sentiment_source=str(payload.get("sentiment_source") or base.sentiment_source),
        sentiment_include_general=_as_bool(payload.get("sentiment_include_general"), base.sentiment_include_general),
        vol_lookback=_as_int(payload, "vol_lookback", base.vol_lookback),
        vol_target=_as_float(payload, "vol_target", base.vol_target),
        use_vol_target=_as_bool(payload.get("use_vol_target"), base.use_vol_target),
        fixed_weight=_as_float(payload, "fixed_weight", base.fixed_weight),
        max_weight=_as_float(payload, "max_weight", base.max_weight),
        use_stop=_as_bool(payload.get("use_stop"), base.use_stop),
        stop_mode=str(payload.get("stop_mode") or base.stop_mode).lower(),
        stop_atr=_as_float(payload, "stop_atr", base.stop_atr),
        stop_reentry_days=_as_int(payload, "stop_reentry_days", base.stop_reentry_days),
        time_stop_days=_as_int(payload, "time_stop_days", base.time_stop_days),
        rebalance_band=_as_float(payload, "rebalance_band", base.rebalance_band),
        fee_bps=_as_float(payload, "fee_bps", base.fee_bps),
        slippage_bps=_as_float(payload, "slippage_bps", base.slippage_bps),
        impact_coef_bps=_as_float(payload, "impact_coef_bps", base.impact_coef_bps),
        risk_limit_pct=_as_float(payload, "risk_limit_pct", base.risk_limit_pct),
        risk_pause_days=_as_int(payload, "risk_pause_days", base.risk_pause_days),
        daily_loss_limit_pct=_as_float(payload, "daily_loss_limit_pct", base.daily_loss_limit_pct),
        cooldown_days=_as_int(payload, "cooldown_days", base.cooldown_days),
        alloc_method=str(payload.get("alloc_method") or base.alloc_method).lower(),
        weights=str(payload.get("weights") or base.weights),
    )
    params.validate()
    return params


def resolve_assets(selected: Any, market: str = "crypto") -> tuple[dict[str, pd.DataFrame], str]:
    """按用户勾选的资产读取本地 CSV。勾选为空则按市场类型选默认标的。"""
    files = qc.list_csv_files(DATA_DIR)
    if not files:
        frame = qc._synthetic("SYN", days=1500, drift=0.0008, vol=0.03)
        return {"SYN": frame}, "合成演示数据（data 文件夹里还没有 CSV）"

    by_name = {item["name"]: item for item in files}
    wanted: list[str] = []
    if isinstance(selected, (list, tuple)):
        wanted = [str(x).upper() for x in selected if str(x).strip()]
    elif isinstance(selected, str) and selected.strip():
        wanted = [x.strip().upper() for x in selected.split(",") if x.strip()]
    if not wanted:
        defaults = DEFAULT_US_ASSETS if market == "us" else DEFAULT_ASSETS
        wanted = [n for n in defaults if n in by_name] or [files[0]["name"]]

    frames: dict[str, pd.DataFrame] = {}
    labels: list[str] = []
    for name in wanted:
        item = by_name.get(name)
        if item is None:
            # 允许传文件名（例如 btc_usdt.csv）
            hit = next((f for f in files if f["file"].lower() == name.lower()), None)
            item = hit
        if item is None:
            continue
        frame = qc.read_ohlc_csv(item["path"])
        frames[item["name"]] = frame
        labels.append(f"{item['name']} {len(frame)}根")
    if not frames:
        frame = qc.read_ohlc_csv(files[0]["path"])
        frames[files[0]["name"]] = frame
        labels.append(f"{files[0]['name']} {len(frame)}根")

    span_start = min(f.index[0] for f in frames.values())
    span_end = max(f.index[-1] for f in frames.values())
    market_label = "美股" if market == "us" else "加密货币"
    currency = "USD" if market == "us" else "USDT"
    source = f"{market_label}K线 {'+'.join(frames.keys())}（{len(frames)} 个资产，{span_start.date()} ~ {span_end.date()}，{currency}）"
    if market == "us":
        verified = all(frame.attrs.get("provenance", {}).get("verified") for frame in frames.values())
        source += (" · Yahoo Finance 真实日线 · 拆股/分红复权" if verified
                   else " · 本地行情来源未验证，请检查数据记录")
    return frames, source


class PaperEngine:
    """把组合回测结果逐根 K 线回放出来的引擎。"""

    def __init__(self, params: qc.StrategyParams, assets: Any = None, interval: float = 0.2, market: str = "crypto") -> None:
        self.lock = threading.RLock()
        self._params = params
        self._assets_selection = assets
        self._market = market
        self._interval = max(0.02, float(interval))
        self._running = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._logs: list[dict[str, str]] = []
        self._report_lock = threading.RLock()
        self._research_lock = threading.Lock()
        self._report_cache: dict[str, Any] = {}
        self._generation = 0
        self._metrics_cache: tuple[int, dict[str, Any]] | None = None
        self.rebuild(params=params, assets=assets, interval=interval)

    # ---------------- 生命周期 ----------------

    def start_worker(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="paper-engine", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            do_step = False
            with self.lock:
                if self._running and not self._finished:
                    now = time.monotonic()
                    if now >= self._next_step_at:
                        do_step = True
                        self._next_step_at = now + self._interval
            if do_step:
                self.step()
            time.sleep(0.08)

    # ---------------- 重新构建 ----------------

    def rebuild(self, params: qc.StrategyParams, assets: Any = None, interval: float | None = None,
                market: str | None = None) -> None:
        """按新参数重跑组合回测（毫秒级），并把回放游标归零。"""
        next_market = self._market if market is None else market
        params = replace(params, annualization=252 if next_market == "us" else 365)
        params.validate()
        next_assets = self._assets_selection if assets is None else assets
        frames, source = resolve_assets(next_assets, next_market)
        from collection_risk import load_records, risk_frames
        records = load_records(DATA_DIR / "collection" / "collection.sqlite3") if params.use_sentiment and params.sentiment_source in ("collection", "event") else []
        if params.use_sentiment and params.sentiment_source == "event":
            if set(frames) != {"TSLA"}:
                raise ValueError("公司事件实验第一版仅支持TSLA：请选择美股并仅勾选TSLA")
            from company_events import event_frames
            sentiment = event_frames(frames, records)
        else:
            sentiment = (risk_frames(frames, records, params.sentiment_include_general) if params.sentiment_source == "collection" else point_in_time_sentiment(frames)) if params.use_sentiment else None
        result = qc.run_portfolio(frames, params, sentiment=sentiment)
        with self.lock:
            self._params = params
            self._market = next_market
            self._assets_selection = next_assets
            self._sentiment = sentiment
            self._collection_records = records
            self._result = result
            self._frames = frames
            self._data_source = source
            if interval is not None:
                self._interval = max(0.02, float(interval))
            self._bars = result["bars"]
            self._equity = result["equity"]
            self._benchmark = result["benchmark"]
            self._trades = result["trades"]
            self._risk_events = result.get("risk_events", [])
            self._dates = [d.date().isoformat() for d in self._bars.index]
            self._cursor = 1
            self._running = False
            self._warning = None
            self._error = ""
            self._generation += 1
            self._report_cache = {}
            self._metrics_cache = None
            self._next_step_at = time.monotonic() + self._interval
            self._logs = []
            span = f"{self._dates[0]} ~ {self._dates[-1]}"
            self._log(f"已就绪：{len(self._dates)} 根 K 线（{span}），"
                      f"{len(result['names'])} 个资产，本地虚拟盘未连接任何交易所。")
            if self._risk_events:
                events = self._risk_events or []
                if events:
                    last = events[-1]
                    self._log(f"提示：历史回放中曾触发风控熔断（{last['date']}，"
                              f"回撤 {last['drawdown']}%），已按规则暂停 {last['pause_days']} 天。")

    def _log(self, text: str) -> None:
        self._logs.append({"time": time.strftime("%H:%M:%S"), "text": text})
        if len(self._logs) > 200:
            self._logs = self._logs[-200:]

    # ---------------- 回放 ----------------

    @property
    def _finished(self) -> bool:
        return self._cursor >= len(self._dates)

    def step(self) -> bool:
        with self.lock:
            if self._finished:
                if self._running:
                    self._running = False
                    self._log("行情数据已走完，自动暂停。")
                return False
            self._cursor += 1
            self._metrics_cache = None
            row = self._bars.iloc[self._cursor - 1]
            event = str(row.get("event") or "")
            note = str(row.get("note") or "")
            if event in ("BUY", "SELL", "STOP", "TIME") or note:
                if note:
                    self._log(note)
            if event == "HALT":
                self._log("⚠ 风控熔断事件已记录到「风控事件」面板。")
            return True

    def control(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        command = (command or "").lower()
        if command == "start":
            with self.lock:
                if self._finished:
                    raise ValueError("行情已走完，请先点「重置」重新开始。")
                self._running = True
                self._next_step_at = time.monotonic() + 0.05
                self._log(f"回放已启动，每 {self._interval:.2f} 秒推进 1 根 K 线。")
        elif command == "pause":
            with self.lock:
                self._running = False
                self._log("回放已暂停。")
        elif command == "step":
            self.step()
        elif command == "reset":
            new_params = parse_params(payload, self._params)
            assets = payload.get("assets", self._assets_selection)
            market = str(payload.get("market") or self._market).lower()
            if market not in ("us", "crypto"):
                raise ValueError("市场类型只能是 crypto 或 us。")
            self.rebuild(new_params, assets=assets,
                         interval=_as_float(payload, "interval", self._interval), market=market)
            self._log("已按新参数重新计算，回放游标归零。")
        elif command == "interval":
            with self.lock:
                self._interval = max(0.02, _as_float(payload, "interval", self._interval))
                self._next_step_at = time.monotonic() + self._interval
                self._log(f"回放节奏已改为每 {self._interval:.2f} 秒 1 根 K 线。")
        elif command == "fastforward":
            with self.lock:
                self._cursor = len(self._dates)
                self._metrics_cache = None
                self._running = False
                self._log("已直接跳到最后一根 K 线。")
        else:
            raise ValueError(f"未知指令：{command}")
        return self.state()

    # ---------------- 状态输出 ----------------

    def _cursor_index(self) -> int:
        return max(1, min(self._cursor, len(self._dates)))

    def _sliced_metrics(self) -> dict[str, Any]:
        """只统计「已经回放到」的那一段，保证界面上看到的都是当时可得的真实结果。"""
        idx = self._cursor_index()
        if self._metrics_cache and self._metrics_cache[0] == idx:
            return self._metrics_cache[1]
        bars = self._bars.iloc[:idx]
        trades = [t for t in self._trades if str(t.get("date") or "") <= self._dates[idx - 1]]
        metrics = qc.performance_stats(bars["equity"], trades, self._params.initial_cash, bars, None,
                                       self._params.annualization)
        qc._merge_trade_metrics(metrics, qc.build_round_trips(trades))
        self._metrics_cache = (idx, metrics)
        return metrics

    def _ohlc_window(self) -> list[dict[str, Any]]:
        primary = self._result["primary_bars"]
        end = self._cursor_index()
        start = max(0, end - OHLC_WINDOW)

        def num(value: Any):
            try:
                value = float(value)
            except (TypeError, ValueError):
                return None
            return None if not np.isfinite(value) else round(value, 2)

        bars: list[dict[str, Any]] = []
        for i in range(start, end):
            row = primary.iloc[i]
            bars.append({
                "d": self._dates[i],
                "o": num(row["open"]), "h": num(row["high"]),
                "l": num(row["low"]), "c": num(row["close"]),
                "f": num(row["fast_ma"]), "s": num(row["slow_ma"]),
                "stop": num(row["stop_level"]),
                "tgt": round(float(row["target_weight"]), 4),
                "pos": round(float(row["weight"]), 4),
                "ev": str(row["event"] or ""),
            })
        return bars

    def _positions(self, last_date: str) -> list[dict[str, Any]]:
        """各资产在"当前回放日期"的持仓明细（组合不能简单相加不同币种的数量）。"""
        stamp = pd.Timestamp(last_date)
        rows: list[dict[str, Any]] = []
        for leg in self._result["legs"]:
            bars = leg["bars"]
            sub = bars.loc[bars.index <= stamp]
            if not len(sub):
                continue
            row = sub.iloc[-1]
            price = float(row["close"])
            qty = float(row["qty"])
            stop = row["stop_level"]
            rows.append({
                "symbol": leg["symbol"],
                "price": round(price, 2),
                "qty": round(qty, 6),
                "value": round(qty * price, 2),
                "weight": round(float(row["weight"]), 4),
                "target": round(float(row["target_weight"]), 4),
                "stop": None if pd.isna(stop) else round(float(stop), 2),
                "cash": round(float(row["cash"]), 2),
            })
        return rows

    def _series(self, limit: int = 520) -> dict[str, list[Any]]:
        idx = self._cursor_index()
        eq = self._equity.iloc[:idx]
        bm = self._benchmark.iloc[:idx]
        dd = (eq / eq.cummax() - 1.0) * 100.0
        if len(eq) > limit:
            step = int(np.ceil(len(eq) / limit))
            pick = list(range(0, len(eq), step))
            if pick[-1] != len(eq) - 1:
                pick.append(len(eq) - 1)
        else:
            pick = list(range(len(eq)))
        return {
            "dates": [self._dates[i] for i in pick],
            "equity": [round(float(eq.iloc[i]), 2) for i in pick],
            "benchmark": [round(float(bm.iloc[i]), 2) for i in pick],
            "drawdown": [round(float(dd.iloc[i]), 2) for i in pick],
        }

    def state(self) -> dict[str, Any]:
        with self.lock:
            idx = self._cursor_index()
            row = self._bars.iloc[idx - 1]
            total = len(self._dates)
            price = float(row["close"])
            equity = float(row["equity"])
            qty = float(row["qty"])
            initial = float(self._params.initial_cash)
            last_date = self._dates[idx - 1]
            trades = [t for t in self._trades if str(t.get("date") or "") <= last_date]
            stop_level = row["stop_level"]
            stop_value = None if pd.isna(stop_level) else round(float(stop_level), 2)
            paused = bool(row["halted"])
            warning = None
            if paused:
                warning = ("部分资产子账户处于风控冷静期；收盘触发的清仓在下一根开盘执行。"
                           "暂停期间不再开新仓，其他子账户独立运行。")
            return {
                "version": APP_VERSION,
                "generation": self._generation,
                "market_type": self._market,
                "sentiment_sample_mode": bool(self._params.use_sentiment and self._params.sentiment_source == "sample"),
                "sentiment_collection_mode": bool(self._params.use_sentiment and self._params.sentiment_source == "collection"),
                "sentiment_event_mode": bool(self._params.use_sentiment and self._params.sentiment_source == "event"),
                "sentiment_collection_count": len(self._collection_records),
                "currency": "USD" if self._market == "us" else "USDT",
                "data_provenance": {name: frame.attrs.get("provenance", {}) for name, frame in self._frames.items()},
                "running": bool(self._running),
                "finished": bool(self._finished),
                "interval": round(self._interval, 3),
                "warning": warning,
                "data_source": self._data_source,
                "safety": "本地虚拟盘 · 未连接交易所 · 不使用真实资金",
                "scenario": " + ".join(self._result["names"]),
                "assets": list(self._result["names"]),
                "strategy": self._params.describe(),
                "params": asdict(self._params),
                "params_view": {
                    "initial_cash": self._params.initial_cash,
                    "ma_type": self._params.ma_type,
                    "fast": self._params.fast,
                    "slow": self._params.slow,
                    "regime_filter": self._params.regime_filter,
                    "use_sentiment": self._params.use_sentiment,
                    "sentiment_source": self._params.sentiment_source,
                    "sentiment_include_general": self._params.sentiment_include_general,
                    "vol_lookback": self._params.vol_lookback,
                    "vol_target": self._params.vol_target,
                    "use_vol_target": self._params.use_vol_target,
                    "fixed_weight": self._params.fixed_weight,
                    "max_weight": self._params.max_weight,
                    "use_stop": self._params.use_stop,
                    "stop_mode": self._params.stop_mode,
                    "stop_atr": self._params.stop_atr,
                    "stop_reentry_days": self._params.stop_reentry_days,
                    "time_stop_days": self._params.time_stop_days,
                    "rebalance_band": self._params.rebalance_band,
                    "fee_bps": self._params.fee_bps,
                    "slippage_bps": self._params.slippage_bps,
                    "impact_coef_bps": self._params.impact_coef_bps,
                    "risk_limit_pct": self._params.risk_limit_pct,
                    "risk_pause_days": self._params.risk_pause_days,
                    "daily_loss_limit_pct": self._params.daily_loss_limit_pct,
                    "cooldown_days": self._params.cooldown_days,
                    "alloc_method": self._params.alloc_method,
                    "weights": self._params.weights,
                },
                "market": {
                    "date": last_date,
                    "price": round(price, 2),
                    "fast_ma": None if pd.isna(row["fast_ma"]) else round(float(row["fast_ma"]), 2),
                    "slow_ma": None if pd.isna(row["slow_ma"]) else round(float(row["slow_ma"]), 2),
                    "atr": None if pd.isna(row["atr"]) else round(float(row["atr"]), 2),
                    "vol": None if pd.isna(row["vol"]) else round(float(row["vol"]), 4),
                    "stop_level": stop_value,
                    "target_weight": round(float(row["target_weight"]), 4),
                    "weight": round(float(row["weight"]), 4),
                    "signal": "做多" if float(row["target_weight"]) > 1e-6 else "空仓",
                    "event": str(row["event"] or ""),
                    "note": str(row["note"] or ""),
                    "day": idx,
                    "total_days": total,
                    "progress": round(idx / total * 100, 2) if total else 0.0,
                },
                "account": {
                    "cash": round(float(row["cash"]), 2),
                    "qty": round(qty, 8),
                    "btc": round(qty, 8),
                    "position_value": round(equity - float(row["cash"]), 2),
                    "btc_value": round(qty * price, 2),
                    "equity": round(equity, 2),
                    "initial_cash": round(initial, 2),
                    "drawdown_pct": round(float(row["drawdown"]) * 100, 2),
                    "exposure_pct": round(float(row["weight"]) * 100, 2),
                    "fees_paid": round(float(row["fees"]), 2),
                    "paused": paused,
                },
                "metrics": metrics_json(self._sliced_metrics()),
                "full_metrics": metrics_json(self._result["metrics"]),
                "series": self._series(),
                "ohlc": self._ohlc_window(),
                "trades": [dict(t, evidence_index=i) for i, t in reversed(list(enumerate(self._trades)))
                           if str(t.get("date") or "") <= last_date][:60],
                "round_trips": [t for t in self._result["round_trips"]
                                if str(t.get("exit_date") or "") <= last_date],
                "legs": [
                    {"symbol": leg["symbol"], "weight": round(leg["alloc_weight"], 4),
                     "final_equity": round(leg["final_equity"], 2), "pnl": round(leg["pnl"], 2),
                     "contribution": round(leg["contribution"], 4),
                     "sharpe": round(leg["metrics"]["sharpe"], 3),
                     "max_drawdown": round(leg["metrics"]["max_drawdown"], 4),
                     "trades": len(leg["trades"])}
                    for leg in self._result["legs"]
                ],
                "risk_events": [ev for ev in self._risk_events if ev["date"] <= last_date],
                "positions": self._positions(last_date),
                "logs": list(reversed(self._logs[-80:])),
            }

    # ---------------- 研究报告数据 ----------------

    def trade_evidence(self, index: int) -> dict[str, Any]:
        """Explain a replayed fill using prior-close signals and pre-open context only."""
        with self.lock:
            if index < 0 or index >= len(self._trades):
                raise ValueError("交易编号无效或尚未回放")
            trade = dict(self._trades[index])
            evidence_params = replace(self._params)
            collection_records = list(self._collection_records)
            day = str(trade["date"])
            if day > self._dates[self._cursor_index() - 1]:
                raise ValueError("交易编号无效或尚未回放")
            symbol = str(trade["symbol"])
            leg = next(x for x in self._result["legs"] if x["symbol"] == symbol)
            bars = leg["bars"]
            idx = bars.index.get_loc(pd.Timestamp(day))
            row = bars.iloc[idx]
            prev = bars.iloc[idx - 1] if idx else None
            def safe(v: Any) -> float | None:
                try:
                    v = float(v)
                    return round(v, 6) if np.isfinite(v) else None
                except (ValueError, TypeError):
                    return None
            result = {"trade": trade, "sentiment_active": bool(self._params.use_sentiment), "currency": "USD" if self._market == "us" else "USDT",
                      "data_source": self._data_source,
                      "bar": {k: safe(row[k]) for k in ("open", "high", "low", "close", "target_weight", "base_target_weight", "sentiment_multiplier", "sentiment_z", "sentiment_coverage", "weight", "stop_level")},
                      "prior": None if prev is None else {"date": str(bars.index[idx-1].date()),
                          **{k: safe(prev[k]) for k in ("close", "fast_ma", "slow_ma", "vol")}},
                      "cost": {"fee": safe(trade.get("fee")), "fee_bps": self._params.fee_bps,
                               "slippage_bps": self._params.slippage_bps,
                               "impact_coef_bps": self._params.impact_coef_bps}}
        # Fills may occur at the open: never use any message captured during the fill day.
        if evidence_params.sentiment_source in ("collection", "event"):
            from collection_risk import visible_records, preopen_time
            visible = visible_records(collection_records, symbol, day, evidence_params.sentiment_include_general)
            if evidence_params.sentiment_source == "event":
                from company_events import classify, TTL_HOURS
                cutoff = preopen_time(day)
                visible = [m for m in visible if m["asset"] == "TSLA" and cutoff-pd.Timestamp(m["published"]) <= pd.Timedelta(hours=TTL_HOURS)]
                result["company_events"] = [classify(m) for m in visible]
            result["sentiment"] = {"cutoff": preopen_time(day).isoformat(), "sample": False,
                "count": len(visible), "events": [{"available_at":m["available"],"source":m["source_name"],"text":m["text"][:160],"direction":m["direction"],"message_id":m["id"]} for m in visible[-8:]],
                "note":"Frozen collection evidence; strictly before the day's UTC midnight. GENERAL is used only when explicitly enabled. No positive leverage."}
            return result
        cutoff = pd.Timestamp(day).normalize() - pd.Timedelta(nanoseconds=1)
        result["sentiment"] = {"cutoff": str(cutoff), "events": [], "sample": True,
                               "note": "Synthetic example messages; raw event context only, not a validated prediction."}
        try:
            from sentiment import SAMPLE_CSV
            from sentiment import sentiment_core as core
            items = core.prepare_items(pd.read_csv(SAMPLE_CSV), asset=symbol)
            visible = items[items["t_use"] <= cutoff].sort_values("t_use")
            result["sentiment"]["count"] = int(len(visible))
            result["sentiment"]["events"] = [
                {"available_at": str(r["t_use"]), "source": str(r["source"]),
                 "text": str(r["text"])[:160], "direction": safe(r["e"])}
                for _, r in visible.tail(8).iterrows()]
        except ValueError:
            result["sentiment"]["note"] += " No messages for this asset."
        except Exception as exc:
            result["sentiment"]["error"] = f"{type(exc).__name__}: {exc}"
        return result

    def collection_risk_experiment(self, payload: dict[str, Any]) -> dict[str, Any]:
        from collection_risk import load_records, risk_frames
        if not self._research_lock.acquire(blocking=False):
            raise ValueError("已有实验运行，请稍后重试")
        try:
            with self.lock:
                frames = {name: frame.copy(deep=True) for name,frame in self._frames.items()}
                base = replace(self._params, use_sentiment=False)
                generation = self._generation
            include_general = _as_bool(payload.get("include_general"), False)
            records = load_records(DATA_DIR / "collection" / "collection.sqlite3")
            mode = str(payload.get("mode") or "collection")
            if mode not in ("collection", "event"):
                raise ValueError("无效的实验模式")
            if mode == "event":
                if set(frames) != {"TSLA"}:
                    raise ValueError("公司事件A/B实验仅支持TSLA；请先切换美股、仅选择TSLA并应用参数")
                from company_events import event_frames
                include_general = False
                risk = event_frames(frames, records)
            else:
                risk = risk_frames(frames, records, include_general)
            baseline = qc.run_portfolio(frames, base)
            experiment_params = replace(base,use_sentiment=True,sentiment_source=mode,sentiment_include_general=include_general)
            experiment = qc.run_portfolio(frames, experiment_params, sentiment=risk)
            import hashlib
            import datetime
            evidence = json.dumps(records,ensure_ascii=False,sort_keys=True).encode("utf-8")
            eligible = sum(int((r.event_message_ids!='').sum()) if mode == "event" else int((r.coverage>0).sum()) for r in risk.values())
            reduced = sum(int((r.multiplier<.999999).sum()) for r in risk.values())
            result = {"generation":generation,"messages":len(records),"eligible_asset_days":eligible,"reduced_asset_days":reduced,
                "include_general":include_general,"baseline":metrics_json(baseline["metrics"]),"experiment":metrics_json(experiment["metrics"]),
                "evidence_sha256":hashlib.sha256(evidence).hexdigest(),"params":asdict(experiment_params),
                "note":"没有开盘前可得消息；两组可能完全相同。不能回填历史抓取时间。" if not eligible else "严格按 UTC 日零点前已可得消息减仓；未假定独立来源，不能据此证明预测力。"}
            from sentiment import weight_config as cfg
            result["risk_parameters"] = {"kappa":cfg.KAPPA,"tau":cfg.TAU,"gamma":cfg.GAMMA,"coverage_min":cfg.COVERAGE_MIN,"cutoff":"strictly before UTC day midnight"}
            result["price_inputs"] = {name:{"first":str(frame.index.min()),"last":str(frame.index.max()),"sha256":hashlib.sha256(frame.to_csv().encode()).hexdigest()} for name,frame in frames.items()}
            result["code_hashes"] = {name:hashlib.sha256((BASE_DIR/name).read_bytes()).hexdigest() for name in ("collection_risk.py","quant_core.py")}
            result["mode"] = mode
            if mode == "event":
                from company_events import VERSION, TTL_HOURS
                result["risk_parameters"] = {"version":VERSION,"ttl_hours":TTL_HOURS,"negative_multiplier":.5,"exit_multiplier":0.,"no_event_entry":False,"general_mapping":False,"cutoff":"strictly before UTC day midnight"}
                result["code_hashes"]["company_events.py"] = hashlib.sha256((BASE_DIR/"company_events.py").read_bytes()).hexdigest()
                result["note"] = "历史区间无可得公司事件，事件策略不新增入场；两组差异不能证明新闻有效。" if not eligible else "事件有效期72小时；利好加价格确认入场，利空减仓/退出，冲突不新增入场；规则结果未经预测力验证。"
            folder = RESULT_DIR / "collection_risk" / datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            folder.mkdir(parents=True)
            (folder/"evidence.json").write_bytes(evidence)
            for name,run in (("baseline",baseline),("experiment",experiment)):
                run["equity"].to_csv(folder/(name+"_equity.csv"))
            for name,r in risk.items():
                safe_name = "asset_" + hashlib.sha256(name.encode()).hexdigest()[:12]
                r.to_csv(folder/(safe_name+"_risk.csv"))
            result["saved_to"] = str(folder)
            (folder/"result.json").write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2),encoding="utf-8")
            return result
        finally:
            self._research_lock.release()

    def research_experiment(self, payload: dict[str, Any]) -> dict[str, Any]:
        import research_experiments as research
        if not self._research_lock.acquire(blocking=False):
            raise ValueError("已有研究实验正在生成，请稍后重试。")
        try:
            with self.lock:
                frames = {name: frame.copy(deep=True) for name, frame in self._frames.items()}
                params = replace(self._params)
                generation = self._generation
            experiment = research.run_experiment(frames, params,
                start=payload.get("start") or None, end=payload.get("end") or None,
                simulations=_as_int(payload, "simulations", 1000),
                block=_as_int(payload, "block", 10), seed=20261012)
            folder = research.save_experiment(experiment, RESULT_DIR / "experiments")
            return research.experiment_payload(experiment) | {"saved_to": str(folder),
                "generation": generation, "report_url": f"/api/experiment-file?run={folder.name}&file=report",
                "archive_url": f"/api/experiment-file?run={folder.name}&file=archive"}
        finally:
            self._research_lock.release()

    def report(self, heavy: bool = True) -> dict[str, Any]:
        with self._report_lock:
            key = "heavy" if heavy else "light"
            if key in self._report_cache:
                return self._report_cache[key]
            generation = self._generation
            result = self._result
            equity = result["equity"]
            benchmark = result["benchmark"]
            bars = result["bars"]
            initial = float(self._params.initial_cash)
            metrics = metrics_json(result["metrics"])
            bench_metrics = metrics_json(qc.performance_stats(benchmark, None, initial, None, None,
                                                              self._params.annualization))
            payload: dict[str, Any] = {
                "version": APP_VERSION,
                "generation": generation,
                "strategy": self._params.describe(),
                "scenario": " + ".join(result["names"]),
                "weights": [round(w, 4) for w in result["weights"]],
                "period": f"{self._dates[0]} ~ {self._dates[-1]}",
                "days": len(self._dates),
                "metrics": metrics,
                "benchmark_metrics": bench_metrics,
                "legs": [
                    {"symbol": leg["symbol"], "weight": round(leg["alloc_weight"], 4),
                     "final_equity": round(leg["final_equity"], 2), "pnl": round(leg["pnl"], 2),
                     "contribution": round(leg["contribution"], 4),
                     "metrics": metrics_json(leg["metrics"]),
                     "round_trips": len(leg["round_trips"])}
                    for leg in result["legs"]
                ],
                "monthly": qc.monthly_return_table(equity),
                "benchmark_monthly": qc.monthly_return_table(benchmark),
                "rolling_sharpe": qc.rolling_sharpe_series(equity, 90, self._params.annualization),
                "rolling_sharpe_benchmark": qc.rolling_sharpe_series(benchmark, 90, self._params.annualization),
                "correlation": result["correlation"],
                "risk_events": list(self._risk_events),
                "round_trips": sorted(result["round_trips"], key=lambda t: str(t.get("exit_date") or "")),
                "curve": self._full_curve(),
                "equity_curve": {
                    "dates": [d.date().isoformat() for d in equity.index[::max(1, len(equity) // 600)]],
                    "equity": [round(float(v), 2) for v in equity.iloc[::max(1, len(equity) // 600)]],
                    "benchmark": [round(float(v), 2) for v in benchmark.iloc[::max(1, len(benchmark) // 600)]],
                },
                "heavy": False,
            }
            if heavy:
                primary_name = result["names"][0]
                primary_frame = self._frames[primary_name]
                payload["grid"] = qc.parameter_grid(
                    primary_frame, self._params,
                    [8, 10, 15, 20, 25, 30, 40, 50, 60], [40, 50, 60, 80, 100, 120, 150],
                    sentiment=(self._sentiment or {}).get(primary_name))
                payload["walk_forward"] = qc.walk_forward(
                    primary_frame, self._params, [10, 20, 30, 40], [50, 80, 120, 150], n_splits=5,
                    sentiment=(self._sentiment or {}).get(primary_name))
                payload["monte_carlo"] = qc.monte_carlo(equity, n_sims=600, horizon=self._params.annualization)
                payload["grid_symbol"] = primary_name
                payload["heavy"] = True
            if payload["generation"] == self._generation:
                self._report_cache[key] = payload
            return payload

    def _full_curve(self) -> dict[str, Any]:
        eq = self._equity
        bm = self._benchmark
        step = max(1, len(eq) // 700)
        return {
            "dates": [d.date().isoformat() for d in eq.index[::step]],
            "equity": [round(float(v), 2) for v in eq.iloc[::step]],
            "benchmark": [round(float(v), 2) for v in bm.iloc[::step]],
            "drawdown": [round(float(v), 4) for v in (eq / eq.cummax() - 1.0).iloc[::step]],
        }

    # ---------------- 导出结果 ----------------

    def save_reports(self) -> Path:
        with self.lock:
            RESULT_DIR.mkdir(exist_ok=True)
            result = self._result
            equity = result["equity"]
            benchmark = result["benchmark"]
            pd.DataFrame({"日期": equity.index, "组合净值": equity.to_numpy(),
                          "买入持有基准": benchmark.to_numpy(),
                          "回撤": (equity / equity.cummax() - 1.0).to_numpy()
                          }).to_csv(RESULT_DIR / "组合净值曲线.csv", index=False, encoding="utf-8-sig")
            pd.DataFrame(result["trades"]).to_csv(
                RESULT_DIR / "成交明细.csv", index=False, encoding="utf-8-sig")
            pd.DataFrame(result["round_trips"]).to_csv(
                RESULT_DIR / "完整交易流水.csv", index=False, encoding="utf-8-sig")
            monthly = qc.monthly_return_table(equity)
            pd.DataFrame(monthly["matrix"], index=monthly["years"],
                         columns=[f"{m}月" for m in range(1, 13)]
                         ).to_csv(RESULT_DIR / "月度收益矩阵.csv", encoding="utf-8-sig")
            currency = "USD" if self._market == "us" else "USDT"
            labels = {
                "final_equity": f"期末净值({currency})", "total_return": "累计收益率", "cagr": "年化收益率",
                "ann_vol": "年化波动率", "sharpe": "夏普比率", "sortino": "索提诺比率",
                "calmar": "卡玛比率", "omega": "Omega 比率", "max_drawdown": "最大回撤",
                "max_dd_days": "最长回撤天数", "recovery_days": "回撤修复天数",
                "var95": "VaR 95%", "var99": "VaR 99%", "cvar95": "CVaR 95%", "cvar99": "CVaR 99%",
                "win_rate": "日胜率", "trade_win_rate": "交易胜率", "payoff_ratio": "盈亏比",
                "trade_profit_factor": "总盈亏比", "expectancy": "单笔期望盈亏",
                "avg_holding_days": "平均持仓天数", "exposure_avg": "平均仓位",
                "turnover": "累计成交额", "fees_paid": "累计手续费", "trades": "成交笔数",
                "trade_count": "完整交易笔数", "buys": "买入次数", "sells": "卖出次数",
                "stops": "止损次数", "max_consecutive_losses": "最大连续亏损次数",
                "benchmark_return": "基准收益(买入持有)", "excess_return": "相对基准超额",
                "beta": "Beta", "alpha": "Alpha(年化)", "information_ratio": "信息比率",
                "correlation": "与基准相关性", "best_day": "单日最佳", "worst_day": "单日最差",
                "skew": "偏度", "kurt": "峰度", "days": "回放天数", "years": "年数",
                "dd_start": "最大回撤起点", "dd_end": "最大回撤最低点",
                "position_days": "有持仓的天数", "profit_factor": "总盈亏比",
            }
            m = result["metrics"]
            year_keys = {"years"}
            money_keys = {"final_equity", "fees_paid", "turnover", "expectancy",
                          "best_trade", "worst_trade"}
            int_keys = {"days", "trades", "buys", "sells", "stops", "max_dd_days", "recovery_days",
                        "position_days", "trade_count", "max_consecutive_losses"}
            plain_keys = {"beta", "sharpe", "sortino", "calmar", "omega", "skew", "kurt",
                          "payoff_ratio", "trade_profit_factor", "information_ratio", "correlation",
                          "gain_loss_ratio", "avg_holding_days", "profit_factor", "risk_free"}
            rows_out = []
            for key, value in m.items():
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    shown = "" if value is None else str(value)
                elif key in year_keys:
                    shown = f"{value:.2f} 年"
                elif key in int_keys:
                    shown = f"{int(value)}"
                elif key in money_keys:
                    shown = f"{value:,.2f} {currency}"
                elif key in plain_keys:
                    shown = f"{value:.3f}"
                else:                     # 其余按百分比展示
                    shown = f"{value * 100:,.2f}%"
                rows_out.append({"指标": labels.get(key, key), "数值": value, "显示值": shown})
            pd.DataFrame(rows_out).to_csv(
                RESULT_DIR / "绩效指标汇总.csv", index=False, encoding="utf-8-sig")
            pd.DataFrame([{"资产": leg["symbol"], "资金权重": leg["alloc_weight"],
                           "期末净值": leg["final_equity"], "盈亏": leg["pnl"],
                           "贡献": leg["contribution"],
                           "夏普": leg["metrics"]["sharpe"],
                           "最大回撤": leg["metrics"]["max_drawdown"],
                           "成交笔数": len(leg["trades"])}
                          for leg in result["legs"]]).to_csv(
                RESULT_DIR / "各资产贡献.csv", index=False, encoding="utf-8-sig")
            if self._risk_events:
                pd.DataFrame(self._risk_events).to_csv(
                    RESULT_DIR / "风控事件.csv", index=False, encoding="utf-8-sig")
            (RESULT_DIR / "说明.txt").write_text(
                "本程序为本地虚拟盘（paper trading）教学演示：\n"
                "  * 未连接任何交易所或券商，未使用真实资金，未发送任何真实订单；\n"
                "  * 行情来自本机 CSV 文件（可由公开只读行情接口下载）；\n"
                f"  * 策略：{self._params.describe()}\n"
                "  * 回测结果包含手续费、滑点与冲击成本；历史表现不代表未来收益，不构成投资建议。\n",
                encoding="utf-8")
            return RESULT_DIR

# =====================================================================
# HTTP 接口层
# =====================================================================

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def metrics_json(metrics: dict[str, Any]) -> dict[str, Any]:
    """把指标字典转成 JSON 安全格式（NaN / inf 一律变成 None）。"""
    out: dict[str, Any] = {}
    for key, value in metrics.items():
        if isinstance(value, bool) or value is None:
            out[key] = value
        elif isinstance(value, (int, np.integer)):
            out[key] = int(value)
        elif isinstance(value, (float, np.floating)):
            number = float(value)
            out[key] = None if not np.isfinite(number) else round(number, 6)
        else:
            out[key] = value if isinstance(value, str) else str(value)
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalPaperTrader/3.0"
    engine: PaperEngine
    collector: CollectionDesk
    workflow: WorkflowStore

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    # ---------- 基础输出 ----------

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _send_json(self, payload: Any, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _send_static(self, relative: str) -> bool:
        target = (STATIC_DIR / relative).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._send(403, b"forbidden", "text/plain; charset=utf-8")
            return True
        if not target.is_file():
            return False
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        body = target.read_bytes()
        if target.suffix.lower() in (".html", ".js", ".css"):
            # 页面里的 {{占位符}} 由 branding.py 提供；改名后界面自动跟着变
            try:
                body = branding.render(body.decode("utf-8")).encode("utf-8")
            except UnicodeDecodeError:
                pass
        self._send(200, body, ctype)
        return True

    # ---------- 路由 ----------

    def _collection_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return (host in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}") and
                (origin is None or origin == "http://" + host) and
                self.headers.get("Sec-Fetch-Site") != "cross-site")

    def do_GET(self) -> None:  # noqa: N802
        raw_path = self.path.split("?", 1)[0]
        if raw_path in ("/api/collection", "/api/collection-export", "/api/collection-message", "/api/sentiment", "/api/company-events", "/api/workflow", "/api/command") and not self._collection_allowed():
            self._send_json({"error": "采集数据只允许本机同源页面读取"}, code=403)
            return
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        try:
            if raw_path in ("/", "/index.html"):
                if not self._send_static("index.html"):
                    self._send(500, "缺少 static/index.html".encode("utf-8"),
                               "text/plain; charset=utf-8")
            elif raw_path in ("/intelligence", "/intelligence/", "/intelligence.html"):
                if not self._send_static("intelligence/index.html"):
                    self._send(404, b"not found", "text/plain; charset=utf-8")
            elif raw_path in ("/desk", "/desk.html"):
                if not self._send_static("desk.html"):
                    self._send(404, b"not found", "text/plain; charset=utf-8")
            elif raw_path in ("/bank", "/bank/"):
                if not self._send_static("bank.html"):
                    self._send(404, b"not found", "text/plain; charset=utf-8")
            elif raw_path.startswith("/static/"):
                if not self._send_static(raw_path[len("/static/"):]):
                    self._send(404, b"not found", "text/plain; charset=utf-8")
            elif raw_path == "/api/state":
                self._send_json(self.engine.state())
            elif raw_path == "/api/collection":
                self._send_json(self.collector.snapshot())
            elif raw_path == "/api/company-events":
                from collection_risk import load_records
                from company_events import current_events
                from collector import now_iso
                frame = qc.read_ohlc_csv(DATA_DIR / "tsla.csv")
                close = frame["Close"]
                confirmed = bool(len(close)>=60 and close.iloc[-1]>close.tail(20).mean()>close.tail(60).mean())
                self._send_json(current_events(load_records(DATA_DIR / "collection" / "collection.sqlite3"),now_iso(),str(frame.index[-1].date()),confirmed))
            elif raw_path == "/api/command":
                task_id = (parse_qs(query).get("id") or [""])[0]
                self._send_json(self.command_center.get(task_id) if task_id else {"tasks": self.command_center.list()})
            elif raw_path == "/api/workflow":
                params = parse_qs(query)
                task_id = (params.get("id") or [""])[0]
                if task_id:
                    try:
                        self._send_json(self.workflow.get(task_id))
                    except KeyError as exc:
                        self._send_json({"error": str(exc)}, code=404)
                else:
                    self._send_json({"tasks": self.workflow.list()})
            elif raw_path == "/api/collection-message":
                try:
                    self._send_json(self.collector.message((parse_qs(query).get("id") or [""])[0]))
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, code=400)
            elif raw_path == "/api/collection-export":
                export_id = (parse_qs(query).get("id") or [""])[0]
                try:
                    target = self.collector.export_path(export_id)
                    self._send(200, target.read_bytes(), "application/zip")
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, code=400)
            elif raw_path == "/api/files":
                self._send_json({"files": qc.list_csv_files(DATA_DIR),
                                 "default": DEFAULT_ASSETS,
                                 "default_us": DEFAULT_US_ASSETS})
            elif raw_path == "/api/trade-evidence":
                try:
                    index = int((parse_qs(query).get("index") or [""])[0])
                    self._send_json(self.engine.trade_evidence(index))
                except (ValueError, TypeError) as exc:
                    self._send_json({"error": str(exc)}, code=400)
            elif raw_path == "/api/report":
                heavy = "heavy=1" in query or "heavy=true" in query
                self._send_json(self.engine.report(heavy=heavy))
            elif raw_path == "/api/experiment-file":
                params = parse_qs(query)
                run = (params.get("run") or [""])[0]
                kind = (params.get("file") or [""])[0]
                root = (RESULT_DIR / "experiments").resolve()
                if not run or Path(run).name != run or kind not in ("report", "archive"):
                    raise ValueError("无效的实验文件请求。")
                target = (root / (run + ".zip") if kind == "archive" else root / run / "研究对照报告.html").resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    self._send(404, b"not found", "text/plain; charset=utf-8")
                    return
                self._send(200, target.read_bytes(), "application/zip" if kind == "archive" else "text/html; charset=utf-8")
            elif raw_path == "/api/sentiment":
                params = parse_qs(query)
                asof = (params.get("date") or [""])[0] or None
                try:
                    window = int((params.get("window") or ["240"])[0])
                except (TypeError, ValueError):
                    window = 240
                mode = (params.get("source") or ["collection"])[0]
                try:
                    if mode == "collection":
                        self._send_json(self.collector.radar(asof=asof, window=window))
                    elif mode == "sample":
                        self._send_json(sentiment_payload(asof=asof, window=window) | {"data_mode": "sample"})
                    else:
                        raise ValueError("无效的舆情来源")
                except ValueError as exc:
                    self._send_json({"error": str(exc), "ok": False}, code=400)
            elif raw_path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception as exc:  # pragma: no cover
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, code=500)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/api/workflow", "/api/command"):
                if not self._collection_allowed():
                    self._send_json({"error": "研究任务只允许本机同源页面操作"}, code=403)
                    return
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise ValueError("研究任务请求需要 JSON")
            if path == "/api/collection":
                # Stop cross-origin pages from submitting to the localhost collector.
                if not self._collection_allowed():
                    self._send_json({"error": "采集操作只允许本机同源页面发起"}, code=403)
                    return
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise ValueError("采集操作需要 JSON 请求")
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > 6 * 1024 * 1024:
                raise ValueError("请求过大，请缩小文件（上限 3 MB）")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("请求应为一个对象")
            if path == "/api/command":
                command = payload.get("command")
                if command == "plan":
                    result = self.command_center.plan(payload.get("description"))
                elif command == "submit":
                    result = self.command_center.submit(payload.get("description"), payload.get("request_key"), payload.get("generation"))
                elif command == "cancel":
                    result = self.command_center.cancel(str(payload.get("task_id") or ""))
                else:
                    raise ValueError("未知中央研究命令")
                self._send_json(result, code=201 if command == "submit" else 200)
                return
            if path == "/api/workflow":
                command = payload.get("command")
                if command == "create":
                    task = self.workflow.create(str(payload.get("workflow_type") or ""), payload.get("input"))
                elif command == "advance":
                    task = self.workflow.advance(str(payload.get("task_id") or ""),
                                                 str(payload.get("stage") or ""),
                                                 str(payload.get("status") or ""),
                                                 str(payload.get("message") or ""),
                                                 payload.get("result") if isinstance(payload.get("result"), dict) else {})
                else:
                    raise ValueError("未知研究任务命令")
                self._send_json(task, code=201 if command == "create" else 200)
                return
            if path == "/api/collection":
                command = payload.get("command")
                if command == "source":
                    result = self.collector.save_source(payload)
                elif command == "toggle":
                    self.collector.toggle_source(str(payload.get("id", "")))
                    result = {"ok": True}
                elif command == "start":
                    result = self.collector.start(str(payload.get("id", "")))
                elif command == "import":
                    result = self.collector.import_csv(payload.get("text", ""), payload.get("filename", ""))
                elif command == "export":
                    result = self.collector.export()
                elif command == "company_news":
                    from company_news import download
                    result = download(self.collector)
                else:
                    raise ValueError("未知采集命令")
                self._send_json(result)
                return
            if path != "/api/control":
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            command = str(payload.get("command") or "")
            if command == "collection_risk_experiment":
                if not self._collection_allowed():
                    self._send_json({"error":"实验仅允许本机同源发起"},code=403)
                    return
                self._send_json(self.engine.collection_risk_experiment(payload))
                return
            if command == "research":
                self._send_json(self.engine.research_experiment(payload))
                return
            if command == "save":
                folder = self.engine.save_reports()
                self._send_json(self.engine.state() | {"saved_to": str(folder)})
                return
            self._send_json(self.engine.control(command, payload))
        except ValueError as exc:
            self._send_json({"error": str(exc)}, code=400)
        except KeyError as exc:
            self._send_json({"error": str(exc)}, code=404)
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, code=409)
        except Exception as exc:  # pragma: no cover
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, code=500)


# =====================================================================
# 启动
# =====================================================================

def serve(port: int = DEFAULT_PORT, open_browser: bool = True,
          assets: Any = None, interval: float = 0.2, market: str = "crypto", start_tab: str = "replay", runtime_dir: Path | None = None) -> ThreadingHTTPServer:
    params = qc.StrategyParams()
    runtime_root = Path(runtime_dir) if runtime_dir is not None else DATA_DIR
    runtime_root.mkdir(parents=True, exist_ok=True)
    collector = CollectionDesk(runtime_root / "collection")
    workflow = WorkflowStore(runtime_root / "research_workflow.sqlite3")
    try:
        engine = PaperEngine(params, assets=assets, interval=interval, market=market)
    except Exception:
        collector.close()
        raise
    engine.start_worker()
    Handler.engine = engine
    Handler.collector = collector
    Handler.workflow = workflow
    Handler.command_center = CommandCenter(runtime_root / "research_command.sqlite3", engine, RESULT_DIR)

    # 后台预热舆情缓存（不阻塞界面启动，失败也不影响主程序）
    def _warm_sentiment() -> None:
        try:
            _sentiment_daily()
        except Exception:
            pass

    threading.Thread(target=_warm_sentiment, daemon=True).start()

    server: ThreadingHTTPServer | None = None
    for candidate in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            break
        except OSError:
            continue
    if server is None:
        collector.close()
        engine.shutdown()
        raise RuntimeError("没有可用端口，请关闭其它程序后重试。")

    url = f"http://127.0.0.1:{server.server_address[1]}/" + ("desk" if start_tab == "desk" else "#collection" if start_tab == "collection" else "")
    state = engine.state()
    print("=" * 70)
    print(f"  {branding.APP_NAME}  v{APP_VERSION}")
    print(f"  {branding.APP_EN}")
    print(f"  {branding.APP_TAGLINE}{branding.author_suffix()}")
    print("  " + state["safety"])
    print(f"  标的：{state['scenario']}   K线：{state['data_source']}")
    print(f"  策略：{state['strategy']}")
    print(f"  界面地址：{url}")
    print("  关闭这个黑色窗口即可结束程序。")
    print("=" * 70)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    return server


def main() -> None:
    _make_console_safe()
    parser = argparse.ArgumentParser(
        description=f"{branding.APP_NAME}（本地虚拟盘，只在本机运行）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器（调试用）")
    parser.add_argument("--assets", type=str, default="", help="逗号分隔，例如 BTC,ETH,SOL,BNB")
    parser.add_argument("--interval", type=float, default=0.2, help="每根 K 线的回放间隔（秒）")
    parser.add_argument("--market", choices=["crypto", "us"], default="crypto", help="?????crypto=?????us=??")
    parser.add_argument("--runtime-dir", type=Path, default=None, help="独立服务的采集与任务状态目录；行情仍使用原数据目录")
    parser.add_argument("--verify", action="store_true", help="只跑一次组合回测并打印结果，不启动界面")
    parser.add_argument("--start-tab", choices=["replay", "collection", "desk"], default="replay", help="启动时打开的页面")
    args = parser.parse_args()
    assets = [x.strip().upper() for x in args.assets.split(",") if x.strip()] or None

    if args.verify:
        frames, source = resolve_assets(assets, args.market)
        result = qc.run_portfolio(frames, qc.StrategyParams(annualization=252 if args.market == "us" else 365))
        m = result["metrics"]
        print(f"标的 {source}")
        print(f"期末净值 {m['final_equity']:,.0f} | 累计 {m['total_return'] * 100:+.1f}% | "
              f"年化 {m['cagr'] * 100:+.1f}% | 夏普 {m['sharpe']:.2f} | "
              f"最大回撤 {m['max_drawdown'] * 100:.1f}%")
        return

    server = serve(port=args.port, open_browser=not args.no_browser,
                   assets=assets, interval=args.interval, market=args.market, start_tab=args.start_tab, runtime_dir=args.runtime_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        Handler.engine.shutdown()
        Handler.collector.close()
        server.server_close()


if __name__ == "__main__":
    main()
