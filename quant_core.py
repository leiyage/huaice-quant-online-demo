# -*- coding: utf-8 -*-
"""机构级量化研究核心库（本地虚拟盘专用）。

本模块只做数学计算：指标、仓位管理、风控、撮合成本、绩效归因、稳健性检验。
它**不连接任何交易所、不读取 API Key、不发送任何订单**，全部在内存中完成。

分层结构：

    quant_core.py          研究内核（本文件）：指标 / 策略 / 风控 / 成本 / 绩效 / 稳健性
    auto_trader_server.py  回放引擎 + 本地界面服务（只监听 127.0.0.1）
    research_lab.py        一键生成研究报告（HTML）
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

ANN = 365.0          # 加密资产一年 365 天（美股可改成 252）
EPS = 1e-12


# =====================================================================
# 一、基础指标（全部只用「已经收盘」的数据，天然不含未来信息）
# =====================================================================

def sma(series: pd.Series, window: int) -> pd.Series:
    """简单移动平均。"""
    window = int(window)
    return series.rolling(window, min_periods=window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    """指数移动平均。"""
    window = int(window)
    return series.ewm(span=window, adjust=False, min_periods=window).mean()


def true_range(frame: pd.DataFrame) -> pd.Series:
    """真实波幅：max(高-低, |高-昨收|, |低-昨收|)。"""
    high, low, close = frame["High"], frame["Low"], frame["Close"]
    prev_close = close.shift(1)
    return pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    """平均真实波幅 ATR（Wilder 平滑），用于止损距离与仓位估计。"""
    return true_range(frame).ewm(alpha=1.0 / float(window), adjust=False,
                                min_periods=int(window)).mean()


def realized_vol(close: pd.Series, window: int = 30, ann: float = ANN) -> pd.Series:
    """年化历史波动率（收盘价对数收益的滚动标准差）。"""
    returns = np.log(close / close.shift(1))
    return returns.rolling(int(window), min_periods=int(window)).std(ddof=1) * np.sqrt(ann)


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """相对强弱指标（Wilder 平滑）。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    value = 100.0 - 100.0 / (1.0 + rs)
    value = value.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    return value.fillna(50.0)


def bollinger(close: pd.Series, window: int = 20, k: float = 2.0):
    """布林带，返回 (中轨, 上轨, 下轨)。"""
    mid = sma(close, window)
    std = close.rolling(int(window), min_periods=int(window)).std(ddof=1)
    return mid, mid + k * std, mid - k * std


def zscore(series: pd.Series, window: int = 60) -> pd.Series:
    """滚动 Z 分数（衡量偏离历史均值多少个标准差）。"""
    mean = series.rolling(int(window), min_periods=int(window)).mean()
    std = series.rolling(int(window), min_periods=int(window)).std(ddof=1)
    return (series - mean) / std.replace(0.0, np.nan)


def dynamic_adv(volume: pd.Series, window: int = 20) -> pd.Series:
    """平均日成交量 ADV，用于估算市场冲击成本。"""
    return volume.rolling(int(window), min_periods=3).mean()


def drawdown_series(equity: pd.Series) -> pd.Series:
    """回撤序列（负值，-0.25 表示从最高点回撤 25%）。"""
    return equity / equity.cummax() - 1.0


def rolling_sharpe(returns: pd.Series, window: int = 90, ann: float = ANN) -> pd.Series:
    """滚动夏普比率。"""
    minp = max(10, int(window) // 3)
    mean = returns.rolling(int(window), min_periods=minp).mean()
    std = returns.rolling(int(window), min_periods=minp).std(ddof=1)
    return mean / std.replace(0.0, np.nan) * np.sqrt(ann)


# =====================================================================
# 二、策略与风控参数
# =====================================================================

@dataclass
class StrategyParams:
    """机构级策略参数。界面上都能改，改完点「重置」生效。"""

    # ---- 资金 ----
    initial_cash: float = 10_000.0
    annualization: int = 365       # 日线：加密资产 365，美股 252

    # ---- 信号（趋势层）----
    ma_type: str = "sma"            # sma / ema
    fast: int = 20                  # 快均线
    slow: int = 60                  # 慢均线
    use_sentiment: bool = False     # Optional paper-trading experiment; off by default
    sentiment_source: str = "sample"  # sample / collection; no implicit mixing
    sentiment_include_general: bool = False  # Explicit opt-in for macro evidence
    regime_filter: bool = True      # 只在价格站上慢均线时才做多（大方向过滤）

    # ---- 仓位（波动率目标层）----
    vol_lookback: int = 30          # 估算波动的回看天数
    vol_target: float = 0.45        # 年化目标波动率，0.45 = 45%
    use_vol_target: bool = True
    fixed_weight: float = 0.85     # 关闭波动率目标时使用固定仓位
    max_weight: float = 0.85        # 单一资产最大仓位上限
    min_weight: float = 0.02        # 目标仓位低于该值直接空仓

    # ---- 止损（ATR 层）----
    atr_period: int = 14
    use_stop: bool = True
    stop_mode: str = "trailing"     # trailing=移动止损（跟着最高价走）/ fixed=只设灾难止损
    stop_atr: float = 6.0           # 止损距离 = N x ATR（加密资产波动大，6 倍 ATR 是常见 CTA 设置）
    stop_reentry_days: int = 5      # 被止损后冷静 N 天才能再进场
    time_stop_days: int = 0         # 时间止损（0 = 关闭）：持仓超过 N 天强制离场

    # ---- 交易（成本层）----
    rebalance_band: float = 0.05    # 偏离超过净值的 5% 才调仓，省手续费
    min_trade_notional: float = 50.0
    fee_bps: float = 6.0            # 手续费（bps），6 = 0.06%
    slippage_bps: float = 4.0       # 固定滑点
    impact_coef_bps: float = 15.0   # 冲击成本系数：冲击 = 系数 x sqrt(下单量/ADV)
    adv_window: int = 20

    # ---- 组合风控（熔断层）----
    risk_limit_pct: float = 35.0        # 组合回撤触及该值 → 熔断清仓并暂停
    risk_pause_days: int = 90           # 熔断后暂停交易的天数（到期自动复工）
    daily_loss_limit_pct: float = 15.0  # 单日亏损超过该值 → 冷静 N 天
    cooldown_days: int = 2

    # ---- 组合层 ----
    alloc_method: str = "risk_parity"   # equal / risk_parity / manual
    weights: str = ""                   # manual 时用，例如 "0.4,0.3,0.2,0.1"

    def validate(self) -> None:
        if self.sentiment_source not in ("sample", "collection", "event"):
            raise ValueError("舆情来源必须为 sample、collection 或 event。")
        for name, value in asdict(self).items():
            if isinstance(value, (int, float)) and not np.isfinite(value):
                raise ValueError(f"参数 {name} 必须是有限数值。")
        for name in ("fast", "slow", "vol_lookback", "atr_period", "adv_window",
                     "annualization", "risk_pause_days", "cooldown_days",
                     "stop_reentry_days", "time_stop_days"):
            value = getattr(self, name)
            if value != int(value) or value < 0:
                raise ValueError(f"参数 {name} 必须是非负整数。")
        if self.annualization <= 0 or self.adv_window < 3:
            raise ValueError("年化周期必须大于 0，成交量回看周期至少为 3。")
        if self.min_trade_notional < 0 or self.daily_loss_limit_pct < 0:
            raise ValueError("最小成交金额和单日亏损阈值不能为负数。")
        if not 0 <= self.min_weight <= self.max_weight:
            raise ValueError("最小仓位必须在 0 和最大仓位之间。")
        if not 0 <= self.fixed_weight <= 1:
            raise ValueError("固定仓位必须在 0 到 1 之间。")
        if self.ma_type not in ("sma", "ema"):
            raise ValueError("均线类型只能是 sma 或 ema。")
        if not 2 <= self.fast < self.slow:
            raise ValueError("快均线必须 >= 2，并且小于慢均线。")
        if self.initial_cash <= 0:
            raise ValueError("初始虚拟资金必须大于 0。")
        if not 0 < self.max_weight <= 1.0:
            raise ValueError("本引擎为无借贷现金账户，最大仓位必须在 0 到 1 之间。")
        if self.vol_target <= 0:
            raise ValueError("目标波动率必须大于 0。")
        if self.vol_lookback < 5:
            raise ValueError("波动率回看天数至少 5 天。")
        if self.atr_period < 2:
            raise ValueError("ATR 周期至少 2 天。")
        if self.stop_mode not in ("trailing", "fixed"):
            raise ValueError("止损方式只能是 trailing（移动）或 fixed（固定）。")
        if self.use_stop and self.stop_atr <= 0:
            raise ValueError("止损 ATR 倍数必须大于 0。")
        if self.risk_pause_days < 0:
            raise ValueError("熔断暂停天数不能为负数。")
        if self.fee_bps < 0 or self.slippage_bps < 0 or self.impact_coef_bps < 0:
            raise ValueError("手续费/滑点/冲击成本不能为负数。")
        if not 0 <= self.rebalance_band < 1:
            raise ValueError("调仓阈值必须在 0 到 1 之间。")
        if self.risk_limit_pct <= 0:
            raise ValueError("回撤熔断阈值必须大于 0。")
        if self.alloc_method not in ("equal", "risk_parity", "manual"):
            raise ValueError("组合加权方式只能是 equal / risk_parity / manual。")

    def describe(self) -> str:
        """把参数翻译成一句人话，方便课堂讲解。"""
        ma = "简单" if self.ma_type == "sma" else "指数"
        parts = [
            f"{ma}均线 {self.fast}/{self.slow} 趋势跟随",
            (f"目标波动率 {self.vol_target * 100:.0f}%（仓位上限 {self.max_weight * 100:.0f}%）"
             if self.use_vol_target else f"固定目标仓位 {min(self.fixed_weight, self.max_weight):.0%}"),
        ]
        parts.append(f"ATR({self.atr_period})x{self.stop_atr:g} 移动止损" if self.use_stop else "不设止损")
        parts.append(f"回撤熔断 {self.risk_limit_pct:.0f}%")
        if self.daily_loss_limit_pct > 0:
            parts.append(f"单日亏损 {self.daily_loss_limit_pct:.0f}% 冷静 {self.cooldown_days} 天")
        parts.append(f"成本：手续费 {self.fee_bps:g}bps + 滑点 {self.slippage_bps:g}bps + 冲击成本")
        parts.append(("TSLA公司事件模拟实验" if self.sentiment_source == "event" else "共享库负面减仓实验" if self.sentiment_source == "collection" else "合成舆情负面减仓实验") if self.use_sentiment else "舆情不参与交易")
        return "｜".join(parts)


# =====================================================================
# 三、撮合成本模型（滑点 = 固定滑点 + 冲击成本）
# =====================================================================

def estimate_slippage(qty: float, price: float, adv: float, params: StrategyParams) -> float:
    """返回滑点比例（例如 0.0006 表示成交价比理想价差 0.06%）。

    冲击成本用平方根模型：impact = 系数 x sqrt(下单量 / 平均日成交量)。
    规模越大越吃亏 —— 这正是真实机构最在意的成本项。
    """
    notional = abs(float(qty)) * float(price)
    if adv is None or not np.isfinite(adv) or adv <= 0 or price <= 0:
        participation = 0.0
    else:
        participation = notional / (adv * price)
        participation = float(np.clip(participation, 0.0, 5.0))
    base = params.slippage_bps / 1e4
    impact = params.impact_coef_bps / 1e4 * float(np.sqrt(participation))
    return float(base + impact)


# =====================================================================
# 四、策略信号与目标仓位（全部用「上一根已收盘 K 线」的信息）
# =====================================================================

def prepare_indicators(frame: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
    """给原始 OHLCV 加上全部指标列。"""
    df = frame.copy().sort_index()
    if "Volume" not in df.columns:
        df["Volume"] = np.nan
    close = df["Close"].astype(float)
    if params.ma_type == "ema":
        df["fast_ma"] = ema(close, params.fast)
        df["slow_ma"] = ema(close, params.slow)
    else:
        df["fast_ma"] = sma(close, params.fast)
        df["slow_ma"] = sma(close, params.slow)
    df["atr"] = atr(df, params.atr_period)
    df["vol"] = realized_vol(close, params.vol_lookback, params.annualization)
    df["adv"] = dynamic_adv(df["Volume"].astype(float), params.adv_window)
    return df


def target_weight_series(df: pd.DataFrame, params: StrategyParams) -> pd.Series:
    """昨日收盘决定今日目标仓位：趋势信号 x 波动率目标仓位。"""
    close = df["Close"].astype(float)
    long_ok = df["fast_ma"].shift(1) > df["slow_ma"].shift(1)
    if params.regime_filter:
        long_ok = long_ok & (close.shift(1) > df["slow_ma"].shift(1))
    vol_prev = df["vol"].shift(1)
    safe_vol = vol_prev.where(vol_prev > 1e-6)
    scalar = ((params.vol_target / safe_vol).clip(upper=params.max_weight, lower=0.0)
              if params.use_vol_target else pd.Series(min(params.fixed_weight, params.max_weight), index=df.index))
    weight = long_ok.astype(float) * scalar
    weight = weight.fillna(0.0).clip(lower=0.0, upper=params.max_weight)
    weight[weight < params.min_weight] = 0.0
    return weight

# =====================================================================
# 五、回测引擎（逐根 K 线撮合，绝不使用未来数据）
# =====================================================================

def run_backtest(frame: pd.DataFrame, params: StrategyParams, symbol: str = "ASSET",
                 sentiment: pd.DataFrame | None = None,
                 trade_start: pd.Timestamp | None = None) -> dict[str, Any]:
    """对单一资产跑一遍完整回测，返回逐根 K 线轨迹 + 成交明细 + 绩效。

    撮合规则（贴近真实）：
      1. 昨天收盘算出的目标仓位，在**今天开盘**成交；
      2. 止损用今天的**最低价**判断，成交价取 min(开盘价, 止损价)，跳空也认；
      3. 手续费按成交额收，滑点 = 固定滑点 + 冲击成本；
      4. 收盘后更新权益、回撤、移动止损位与熔断状态。
    """
    params.validate()
    df = prepare_indicators(frame, params)
    n = len(df)
    warmup = max(int(params.slow), int(params.vol_lookback), int(params.atr_period),
                 int(params.adv_window)) + 2
    if n <= warmup + 10:
        raise ValueError(f"{symbol} 的 K 线只有 {n} 根，至少需要 {warmup + 11} 根才能回测。")

    weight_target = target_weight_series(df, params).to_numpy(dtype=float)
    # The input is calculated as-of the PREVIOUS calendar day; missing evidence is neutral.
    if params.use_sentiment and sentiment is None:
        raise ValueError("Sentiment mode requires point-in-time sentiment data")
    aligned = sentiment.reindex(df.index) if params.use_sentiment else None
    event_mode = params.use_sentiment and params.sentiment_source == "event"
    if event_mode and (aligned is None or "entry_allowed" not in aligned):
        raise ValueError("公司事件模式需要逐时点 entry_allowed，不能回退为价格策略")
    entry_allowed = aligned["entry_allowed"].fillna(False).astype(bool).to_numpy() if event_mode else np.ones(n,dtype=bool)
    event_reasons = aligned["event_reason"].fillna("").astype(str).to_numpy() if event_mode else [""]*n
    multipliers = (aligned["multiplier"].fillna(1.0).to_numpy(dtype=float)
                   if aligned is not None else np.ones(n))
    z_values = (aligned["z"].fillna(0.0).to_numpy(dtype=float)
                if aligned is not None else np.zeros(n))
    coverages = (aligned["coverage"].fillna(0.0).to_numpy(dtype=float)
                 if aligned is not None else np.zeros(n))
    multipliers = np.clip(multipliers, 0.0, 1.0)
    O = df["Open"].to_numpy(dtype=float)
    H = df["High"].to_numpy(dtype=float)
    L = df["Low"].to_numpy(dtype=float)
    C = df["Close"].to_numpy(dtype=float)
    FM = df["fast_ma"].to_numpy(dtype=float)
    SM = df["slow_ma"].to_numpy(dtype=float)
    ATRp = df["atr"].shift(1).to_numpy(dtype=float)
    VOLp = df["vol"].shift(1).to_numpy(dtype=float)
    ADVp = df["adv"].shift(1).to_numpy(dtype=float)
    dates = df.index

    fee_rate = params.fee_bps / 1e4
    start = warmup
    if trade_start is not None:
        start = max(start, int(df.index.searchsorted(pd.Timestamp(trade_start))))
        if start >= n:
            raise ValueError("交易起点必须位于行情区间内。")
    cash = float(params.initial_cash)
    initial = cash
    qty = 0.0
    trail_peak = None
    stop_level = None
    entry_price = None
    bars_in_pos = 0
    cooldown = 0
    halt_date = None
    risk_events: list[dict[str, Any]] = []
    fees_paid = 0.0
    turnover = 0.0
    trades: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    peak_equity = initial
    equity_prev = initial
    force_liquidation = False

    def close_position(i: int, fill: float, action: str, reason: str) -> None:
        nonlocal cash, qty, fees_paid, turnover, trail_peak, stop_level, entry_price, bars_in_pos
        slip = estimate_slippage(qty, fill, ADVp[i], params)
        px = fill * (1.0 - slip)
        proceeds = qty * px
        fee = proceeds * fee_rate
        cash += proceeds - fee
        fees_paid += fee
        turnover += proceeds
        new_trades.append({"date": dates[i].date().isoformat(), "action": action,
                           "price": px, "quantity": qty, "amount": proceeds,
                           "fee": fee, "note": reason})
        qty = 0.0
        trail_peak = stop_level = entry_price = None
        bars_in_pos = 0

    for i in range(0, start):
        rows.append({
            "date": dates[i], "open": O[i], "high": H[i], "low": L[i], "close": C[i],
            "fast_ma": FM[i], "slow_ma": SM[i], "atr": ATRp[i], "vol": VOLp[i],
            "target_weight": 0.0, "base_target_weight": 0.0,
            "sentiment_multiplier": 1.0, "sentiment_z": 0.0, "sentiment_coverage": 0.0, "weight": 0.0, "qty": 0.0, "cash": initial,
            "equity": initial, "drawdown": 0.0, "daily_ret": 0.0, "stop_level": np.nan,
            "event": "", "note": "指标预热期（空仓观察）", "fees": 0.0,
            "benchmark": initial, "halted": False,
        })

    for i in range(start, n):
        open_px = O[i]
        base_target = float(weight_target[i])
        factor = float(multipliers[i])
        # 消耗已有暂停日；当天新触发的暂停从下一根 K 线开始计数。
        paused_today = cooldown > 0 or force_liquidation
        target_w = 0.0 if paused_today else base_target * factor
        cooldown = max(0, cooldown - 1)
        force_liquidation = False
        sentiment_note = (f" | 舆情 z={z_values[i]:+.3f}，覆盖率={coverages[i]:.2f}，"
                          f"目标仓位 {base_target:.1%}→{target_w:.1%}"
                          if params.use_sentiment and factor < 0.999999 and base_target > 0 else "")
        if event_mode:
            sentiment_note = " | 公司事件：" + event_reasons[i]
        event = ""
        note = ""
        new_trades: list[dict[str, Any]] = []
        stopped = False

        # ---------- 1) 开盘：先判断是否触发移动止损 ----------
        if qty > 0 and params.use_stop and stop_level is not None and np.isfinite(stop_level) and open_px <= stop_level:
            fill = min(open_px, float(stop_level))
            close_position(i, fill, "STOP", f"跳空止损（止损位 {stop_level:,.2f}）")
            cooldown = max(cooldown, int(params.stop_reentry_days))
            target_w = 0.0
            stopped = True
            event = "STOP"
            note = f"{dates[i].date().isoformat()} 触发移动止损，离场冷静 {params.stop_reentry_days} 天"

        # ---------- 2) 开盘：按目标仓位调仓 / 减仓 / 清仓 ----------
        if not stopped:
            equity_open = cash + qty * open_px
            target_val = equity_open * target_w
            cur_val = qty * open_px
            gap = target_val - cur_val
            band = 0.0 if target_w <= 0.0 else max(params.rebalance_band * equity_open, 0.0)
            if abs(gap) > band and (abs(gap) >= params.min_trade_notional or (target_w <= 0 and qty > 0)):
                if gap > 0 and entry_allowed[i]:  # 新闻模式仅在有效利好时允许买入/加仓
                    spend = min(gap, max(0.0, cash) / (1.0 + fee_rate))
                    if spend >= params.min_trade_notional:
                        slip = estimate_slippage(spend / open_px, open_px, ADVp[i], params)
                        px = open_px * (1.0 + slip)
                        q = spend / px
                        fee = spend * fee_rate
                        cash -= spend + fee
                        qty += q
                        fees_paid += fee
                        turnover += spend
                        old_cost = (entry_price or 0.0) * (qty - q)
                        entry_price = (old_cost + px * q) / qty if qty > 1e-12 else px
                        if trail_peak is None:
                            trail_peak = px
                            bars_in_pos = 0
                            if params.use_stop and np.isfinite(ATRp[i]):
                                stop_level = px - params.stop_atr * ATRp[i]
                        new_trades.append({
                            "date": dates[i].date().isoformat(), "action": "BUY", "price": px,
                            "quantity": q, "amount": spend, "fee": fee,
                            "note": f"目标仓位 {target_w * 100:.1f}%",
                        })
                        event = "BUY"
                        note = f"{dates[i].date().isoformat()} 买入，目标仓位 {target_w * 100:.1f}%"
                elif gap < 0:  # 卖出 / 减仓 / 清仓
                    q = min(-gap / open_px, qty)
                    if q * open_px >= params.min_trade_notional or target_w <= 0.0:
                        q = min(q, qty)
                        slip = estimate_slippage(q, open_px, ADVp[i], params)
                        px = open_px * (1.0 - slip)
                        proceeds = q * px
                        fee = proceeds * fee_rate
                        cash += proceeds - fee
                        qty -= q
                        fees_paid += fee
                        turnover += proceeds
                        why = "目标仓位归零，清仓" if target_w <= 0.0 else f"目标仓位降至 {target_w * 100:.1f}%"
                        if target_w <= 0.0 and paused_today:
                            why = "风控冷静期，清仓"
                        new_trades.append({
                            "date": dates[i].date().isoformat(), "action": "SELL", "price": px,
                            "quantity": q, "amount": proceeds, "fee": fee, "note": why,
                        })
                        event = "SELL"
                        note = f"{dates[i].date().isoformat()} 卖出，{why}"
                        if qty <= 1e-12:
                            qty = 0.0
                            trail_peak = None
                            stop_level = None
                            entry_price = None
                            bars_in_pos = 0

        # 开盘调仓完成后，才用当日最低价检查当时已生效的止损。
        # 当日最高价抬高的移动止损只在下一根 K 线生效。
        if qty > 0 and params.use_stop and stop_level is not None and L[i] <= stop_level:
            close_position(i, min(open_px, float(stop_level)), "STOP",
                           f"盘中止损（止损位 {stop_level:,.2f}）")
            cooldown = max(cooldown, int(params.stop_reentry_days))
            event = "STOP"
            note = f"{dates[i].date().isoformat()} 止损离场，后续暂停 {params.stop_reentry_days} 根 K 线"

        # ---------- 3) 收盘：更新下一根 K 线的移动止损与持仓天数 ----------
        if qty > 0:
            bars_in_pos += 1
            if trail_peak is None or H[i] > trail_peak:
                trail_peak = H[i]
            if params.use_stop and np.isfinite(ATRp[i]):
                if params.stop_mode == "fixed" and entry_price is not None:
                    ref = entry_price
                else:
                    ref = trail_peak
                if ref is not None:
                    candidate = ref - params.stop_atr * ATRp[i]
                    if params.stop_mode == "fixed":
                        stop_level = candidate if stop_level is None else stop_level
                    else:
                        stop_level = candidate if stop_level is None else max(stop_level, candidate)
            if params.time_stop_days > 0 and bars_in_pos >= params.time_stop_days:
                slip = estimate_slippage(qty, C[i], ADVp[i], params)
                px = C[i] * (1.0 - slip)
                proceeds = qty * px
                fee = proceeds * fee_rate
                cash += proceeds - fee
                fees_paid += fee
                turnover += proceeds
                new_trades.append({
                    "date": dates[i].date().isoformat(), "action": "SELL", "price": px,
                    "quantity": qty, "amount": proceeds, "fee": fee,
                    "note": f"时间止损（持仓满 {bars_in_pos} 根 K 线）",
                    "exit_reason": "时间止损",
                })
                qty = 0.0
                trail_peak = None
                stop_level = None
                entry_price = None
                bars_in_pos = 0
                event = "TIME"
                note = f"{dates[i].date().isoformat()} 时间止损离场"

        # ---------- 4) 收盘结算 ----------
        equity_close = cash + qty * C[i]
        peak_equity = max(peak_equity, equity_close)
        dd = equity_close / peak_equity - 1.0 if peak_equity > 0 else 0.0
        daily_ret = equity_close / equity_prev - 1.0 if equity_prev > 0 else 0.0

        # 熔断 1：单日亏损超过阈值 → 冷静 N 天
        if params.daily_loss_limit_pct > 0 and daily_ret * 100.0 <= -params.daily_loss_limit_pct:
            cooldown = max(cooldown, int(params.cooldown_days))
            force_liquidation = True
            note = f"{dates[i].date().isoformat()} 单日亏损 {daily_ret * 100:.1f}% 触发熔断，冷静 {params.cooldown_days} 天"
            if not event:
                event = "CB"

        # 熔断 2：组合回撤触及风控线 → 清仓 + 把回撤基准重置到当前净值 + 暂停一段时间
        if dd * 100.0 <= -params.risk_limit_pct:
            force_liquidation = True
            halt_date = dates[i].date().isoformat()
            cooldown = max(cooldown, int(params.risk_pause_days))
            risk_events.append({
                "date": halt_date, "drawdown": round(dd * 100, 2),
                "pause_days": int(params.risk_pause_days), "equity": round(equity_close, 2),
            })
            note = (f"{halt_date} 回撤 {dd * 100:.1f}% 触及 {params.risk_limit_pct:.0f}% 风控线："
                    f"清仓并把回撤基准重置到当前净值，暂停交易 {params.risk_pause_days} 天")
            event = "HALT"
            peak_equity = equity_close

        for t in new_trades:
            t["equity"] = equity_close
            if sentiment_note and t["action"] != "STOP":
                t["note"] += sentiment_note
            trades.append(t)

        rows.append({
            "date": dates[i], "open": open_px, "high": H[i], "low": L[i], "close": C[i],
            "fast_ma": FM[i], "slow_ma": SM[i], "atr": ATRp[i], "vol": VOLp[i],
            "target_weight": target_w,
            "base_target_weight": base_target,
            "sentiment_multiplier": factor, "sentiment_z": z_values[i],
            "sentiment_coverage": coverages[i],
            "weight": (qty * C[i] / equity_close) if equity_close > 0 else 0.0,
            "qty": qty, "cash": cash, "equity": equity_close, "drawdown": dd,
            "daily_ret": daily_ret,
            "stop_level": stop_level if stop_level is not None else np.nan,
            "event": event, "note": note, "fees": fees_paid,
            "benchmark": np.nan, "halted": bool(cooldown > 0 or force_liquidation),
        })
        equity_prev = equity_close

    # ---------- 基准：同期「买入并持有」，同样扣手续费与滑点 ----------
    bm_slip = estimate_slippage(initial / O[start], O[start], ADVp[start], params)
    bm_px = O[start] * (1.0 + bm_slip)
    bm_qty = (initial / (1.0 + fee_rate)) / bm_px
    bm_cash = 0.0  # 余下资金已作为入场手续费支出，不能继续计入现金。
    bm_equity = np.full(n, np.nan)
    for i in range(0, start):
        bm_equity[i] = initial
    for i in range(start, n):
        bm_equity[i] = bm_cash + bm_qty * C[i]

    bars = pd.DataFrame(rows).set_index("date")
    bars["benchmark"] = bm_equity
    bars["drawdown"] = drawdown_series(bars["equity"])
    bars.index.name = "date"

    result = {
        "symbol": symbol,
        "params": params,
        "bars": bars,
        "equity": bars["equity"],
        "benchmark": bars["benchmark"],
        "trades": trades,
        "round_trips": build_round_trips(trades),
        "fees_paid": fees_paid,
        "turnover": turnover,
        "halted": bool(cooldown > 0 or force_liquidation),
        "halt_date": halt_date,
        "risk_events": risk_events,
        "start": dates[start],
        "end": dates[-1],
    }
    result["metrics"] = performance_stats(bars["equity"], trades, initial, bars, turnover, params.annualization)
    return result


# =====================================================================
# 六、成交配对（FIFO）与交易级统计
# =====================================================================

def build_round_trips(trades: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """把「买进 + 卖出」的成交流水配对成一笔笔完整交易（先进先出）。"""
    if any("symbol" in t for t in trades):
        groups: dict[str, list[dict[str, Any]]] = {}
        for t in trades:
            symbol = str(t.get("symbol", "ASSET"))
            groups.setdefault(symbol, []).append({k: v for k, v in t.items() if k != "symbol"})
        combined = [dict(t, symbol=symbol) for symbol, group in groups.items()
                    for t in build_round_trips(group)]
        return sorted(combined, key=lambda t: str(t["exit_date"]))
    lots: list[dict[str, Any]] = []
    trips: list[dict[str, Any]] = []
    for t in trades:
        action = str(t.get("action") or "")
        qty = float(t.get("quantity") or 0.0)
        px = float(t.get("price") or 0.0)
        fee = float(t.get("fee") or 0.0)
        date = t.get("date")
        if qty <= 0 or px <= 0:
            continue
        if action == "BUY":
            lots.append({"qty": qty, "price": px, "fee": fee, "date": date})
            continue
        remaining = qty
        matched = 0.0
        cost = 0.0
        entry_fee = 0.0
        entry_dates: list[str] = []
        while remaining > 1e-12 and lots:
            lot = lots[0]
            take = min(remaining, lot["qty"])
            frac = take / lot["qty"] if lot["qty"] > 1e-12 else 0.0
            cost += take * lot["price"]
            entry_fee += lot["fee"] * frac
            lot["fee"] -= lot["fee"] * frac
            entry_dates.append(lot["date"])
            lot["qty"] -= take
            remaining -= take
            matched += take
            if lot["qty"] <= 1e-12:
                lots.pop(0)
        if matched <= 1e-12:
            continue
        exit_fee = fee * (matched / qty) if qty > 0 else 0.0
        pnl = px * matched - cost - entry_fee - exit_fee
        entry_date = entry_dates[0] if entry_dates else date
        holding = (pd.Timestamp(date) - pd.Timestamp(entry_date)).days
        trips.append({
            "entry_date": entry_date,
            "exit_date": date,
            "quantity": matched,
            "entry_price": cost / matched,
            "exit_price": px,
            "notional": cost,
            "pnl": pnl,
            "ret": pnl / cost if cost > 0 else 0.0,
            "fee": entry_fee + exit_fee,
            "holding_days": int(holding),
            "exit_reason": t.get("exit_reason") or ("止损" if action == "STOP" else
                           ("时间止损" if action == "TIME" else
                            ("风控离场" if "风控" in str(t.get("note", "")) else "信号离场"))),
            "win": bool(pnl > 0),
        })
    return trips


def trade_stats(trips: Sequence[dict[str, Any]]) -> dict[str, float]:
    """交易级统计：胜率、盈亏比、期望值、平均持仓天数等。"""
    if not trips:
        return {"trade_count": 0, "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "payoff_ratio": 0.0, "profit_factor": 0.0, "expectancy": 0.0,
                "avg_holding_days": 0.0, "best_trade": 0.0, "worst_trade": 0.0,
                "avg_return_per_trade": 0.0, "max_consecutive_losses": 0}
    pnls = np.array([float(t["pnl"]) for t in trips], dtype=float)
    rets = np.array([float(t["ret"]) for t in trips], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    gross_win = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(-losses.sum()) if losses.size else 0.0
    streak = 0
    worst_streak = 0
    for p in pnls:
        streak = streak + 1 if p <= 0 else 0
        worst_streak = max(worst_streak, streak)
    return {
        "trade_count": len(trips),
        "win_rate": float(wins.size) / len(trips),
        "avg_win": float(wins.mean()) if wins.size else 0.0,
        "avg_loss": float(losses.mean()) if losses.size else 0.0,
        "payoff_ratio": (float(wins.mean()) / abs(float(losses.mean())))
                        if wins.size and losses.size and losses.mean() != 0 else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0),
        "expectancy": float(pnls.mean()),
        "avg_holding_days": float(np.mean([t["holding_days"] for t in trips])),
        "best_trade": float(pnls.max()),
        "worst_trade": float(pnls.min()),
        "avg_return_per_trade": float(rets.mean()),
        "max_consecutive_losses": int(worst_streak),
    }

# =====================================================================
# 七、绩效指标（机构常用的全套风险调整后指标）
# =====================================================================

def performance_stats(equity: pd.Series, trades: Sequence[dict[str, Any]] | None = None,
                      initial_cash: float | None = None, bars: pd.DataFrame | None = None,
                      turnover: float | None = None, periods: float = ANN) -> dict[str, Any]:
    """计算全套绩效指标。equity 为净值序列（索引为 K 线日期）。"""
    eq = pd.Series(equity, dtype=float).dropna()
    base = float(initial_cash if initial_cash is not None else (eq.iloc[0] if len(eq) else 0.0))
    out: dict[str, Any] = {
        "days": int(len(eq)), "years": 0.0, "final_equity": base, "total_return": 0.0,
        "cagr": 0.0, "ann_vol": 0.0, "sharpe": 0.0, "sortino": 0.0, "calmar": 0.0,
        "max_drawdown": 0.0, "max_dd_days": 0, "dd_start": "", "dd_end": "",
        "var95": 0.0, "var99": 0.0, "cvar95": 0.0, "cvar99": 0.0, "omega": 0.0,
        "skew": 0.0, "kurt": 0.0, "best_day": 0.0, "worst_day": 0.0,
        "win_rate": 0.0, "profit_factor": 0.0, "gain_loss_ratio": 0.0,
        "avg_win": 0.0, "avg_loss": 0.0, "exposure_avg": 0.0, "turnover": 0.0,
        "fees_paid": 0.0, "position_days": 0, "trades": 0, "buys": 0, "sells": 0,
        "stops": 0, "benchmark_return": 0.0, "excess_return": 0.0, "beta": 0.0,
        "alpha": 0.0, "information_ratio": 0.0, "correlation": 0.0,
        "recovery_days": 0, "risk_free": 0.0,
    }
    if len(eq) < 3:
        return out

    rets = eq.pct_change().dropna()
    years = (len(eq) - 1) / periods
    out["years"] = float(years)
    out["final_equity"] = float(eq.iloc[-1])
    out["total_return"] = float(eq.iloc[-1] / base - 1.0) if base > 0 else 0.0
    growth = eq.iloc[-1] / base if base > 0 else 1.0
    out["cagr"] = float(growth ** (1.0 / years) - 1.0) if years > 0 and growth > 0 else 0.0

    std = float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
    out["ann_vol"] = std * float(np.sqrt(periods))
    out["sharpe"] = (float(rets.mean()) / std * float(np.sqrt(periods))) if std > EPS else 0.0
    downside = rets[rets < 0]
    dstd = float(np.sqrt(np.mean(np.minimum(rets.to_numpy(), 0.0) ** 2)))
    out["sortino"] = (float(rets.mean()) / dstd * float(np.sqrt(periods))) if dstd > EPS else 0.0

    dd = eq / eq.cummax() - 1.0
    out["max_drawdown"] = float(dd.min())
    out["calmar"] = (out["cagr"] / abs(out["max_drawdown"])) if out["max_drawdown"] < -EPS else 0.0
    try:
        trough = dd.idxmin()
        peak_date = eq.loc[:trough].idxmax()
        out["dd_start"] = pd.Timestamp(peak_date).date().isoformat()
        out["dd_end"] = pd.Timestamp(trough).date().isoformat()
        after = eq.loc[trough:]
        recovered = after[after >= eq.loc[peak_date]]
        out["recovery_days"] = int((recovered.index[0] - trough).days) if len(recovered) else int((eq.index[-1] - trough).days)
    except Exception:
        pass
    longest = 0
    run = 0
    for flag in (dd < -1e-9).to_numpy():
        run = run + 1 if flag else 0
        longest = max(longest, run)
    out["max_dd_days"] = int(longest)

    arr = rets.to_numpy(dtype=float)
    q05 = float(np.percentile(arr, 5))
    q01 = float(np.percentile(arr, 1))
    out["var95"] = -q05
    out["var99"] = -q01
    tail95 = arr[arr <= q05]
    tail99 = arr[arr <= q01]
    out["cvar95"] = float(-tail95.mean()) if tail95.size else 0.0
    out["cvar99"] = float(-tail99.mean()) if tail99.size else 0.0
    gains = float(arr[arr > 0].sum())
    losses = float(-arr[arr < 0].sum())
    out["omega"] = gains / losses if losses > EPS else 0.0
    out["profit_factor"] = gains / losses if losses > EPS else 0.0
    out["skew"] = float(pd.Series(arr).skew()) if arr.size > 2 else 0.0
    out["kurt"] = float(pd.Series(arr).kurt()) if arr.size > 3 else 0.0
    out["best_day"] = float(arr.max())
    out["worst_day"] = float(arr.min())
    out["win_rate"] = float((arr > 0).mean())
    ups = arr[arr > 0]
    downs = arr[arr < 0]
    out["avg_win"] = float(ups.mean()) if ups.size else 0.0
    out["avg_loss"] = float(downs.mean()) if downs.size else 0.0
    out["gain_loss_ratio"] = (out["avg_win"] / abs(out["avg_loss"])) if abs(out["avg_loss"]) > EPS else 0.0

    if bars is not None and "weight" in bars.columns:
        w = bars["weight"].reindex(eq.index).fillna(0.0)
        out["exposure_avg"] = float(w.mean())
        out["position_days"] = int((w.abs() > 1e-6).sum())
    if turnover is not None:
        out["turnover"] = float(turnover)

    if trades is not None:
        tlist = list(trades)
        out["trades"] = len(tlist)
        out["buys"] = sum(1 for t in tlist if t.get("action") == "BUY")
        out["sells"] = sum(1 for t in tlist if t.get("action") == "SELL")
        out["stops"] = sum(1 for t in tlist if t.get("action") == "STOP")
        out["fees_paid"] = float(sum(float(t.get("fee") or 0.0) for t in tlist))

    if bars is not None and "benchmark" in bars.columns:
        bm = pd.Series(bars["benchmark"], dtype=float).reindex(eq.index).ffill().dropna()
        if len(bm) > 3 and bm.iloc[0] > 0:
            out["benchmark_return"] = float(bm.iloc[-1] / bm.iloc[0] - 1.0)
            out["excess_return"] = out["total_return"] - out["benchmark_return"]
            bm_ret = bm.pct_change().dropna()
            common = rets.index.intersection(bm_ret.index)
            if len(common) > 3:
                r1 = rets.loc[common].to_numpy(dtype=float)
                r2 = bm_ret.loc[common].to_numpy(dtype=float)
                var2 = float(np.var(r2, ddof=1))
                if var2 > EPS:
                    beta = float(np.cov(r1, r2, ddof=1)[0, 1] / var2)
                    out["beta"] = beta
                    out["alpha"] = float((r1.mean() - beta * r2.mean()) * periods)
                    out["correlation"] = float(np.corrcoef(r1, r2)[0, 1])
                diff = r1 - r2
                dstd2 = float(np.std(diff, ddof=1))
                if dstd2 > EPS:
                    out["information_ratio"] = float(np.mean(diff) / dstd2 * np.sqrt(periods))
    return out


def monthly_return_table(equity: pd.Series) -> dict[str, Any]:
    """把净值序列转成「年 x 月」的收益率矩阵，供热力图展示。"""
    eq = pd.Series(equity, dtype=float).dropna()
    if len(eq) < 2:
        return {"years": [], "matrix": [], "annual": []}
    monthly = eq.resample("ME").last()
    ret = monthly.pct_change()
    if len(ret):
        ret.iloc[0] = monthly.iloc[0] / eq.iloc[0] - 1.0
    years = sorted({int(d.year) for d in ret.index})
    matrix = [[None] * 12 for _ in years]
    for d, v in ret.items():
        if pd.isna(v):
            continue
        matrix[years.index(int(d.year))][int(d.month) - 1] = float(v)
    annual = []
    for y in years:
        sub = eq[eq.index.year == y]
        annual.append({"year": y, "ret": float(sub.iloc[-1] / sub.iloc[0] - 1.0) if len(sub) > 1 else 0.0})
    return {"years": years, "matrix": matrix, "annual": annual}


def rolling_sharpe_series(equity: pd.Series, window: int = 90, periods: float = ANN,
                          limit: int = 400) -> dict[str, Any]:
    """滚动夏普比率序列（看策略是否稳定，而不是靠某一段行情）。"""
    eq = pd.Series(equity, dtype=float).dropna()
    rets = eq.pct_change().dropna()
    rs = rolling_sharpe(rets, window, periods).dropna()
    if len(rs) > limit:
        step = int(np.ceil(len(rs) / limit))
        rs = rs.iloc[::step]
    return {"dates": [d.date().isoformat() for d in rs.index],
            "values": [round(float(v), 3) for v in rs.to_numpy()],
            "window": int(window)}


# =====================================================================
# 八、稳健性检验：参数网格 / 滚动前推 / 蒙特卡洛
# =====================================================================

def parameter_grid(frame: pd.DataFrame, params: StrategyParams, fast_list: Sequence[int],
                   slow_list: Sequence[int], metric: str = "sharpe",
                   sentiment: pd.DataFrame | None = None) -> dict[str, Any]:
    """参数敏感性：不同均线组合下的表现。用于识别「参数孤岛」与过拟合。"""
    rows: list[list[float | None]] = []
    best = {"fast": None, "slow": None, "value": -np.inf}
    for slow in slow_list:
        row: list[float | None] = []
        for fast in fast_list:
            if fast >= slow:
                row.append(None)
                continue
            try:
                res = run_backtest(frame, replace(params, fast=int(fast), slow=int(slow)), "GRID", sentiment=sentiment)
                value: float | None = float(res["metrics"].get(metric, 0.0))
            except Exception:
                value = None
            row.append(value)
            if value is not None and np.isfinite(value) and value > best["value"]:
                best = {"fast": int(fast), "slow": int(slow), "value": float(value)}
        rows.append(row)
    if best["fast"] is None:
        raise ValueError("没有可用的参数组合；请检查数据长度、均线范围和舆情输入。")
    return {"fast": [int(f) for f in fast_list], "slow": [int(s) for s in slow_list],
            "matrix": rows, "metric": metric, "best": best}


def walk_forward(frame: pd.DataFrame, params: StrategyParams, fast_list: Sequence[int],
                 slow_list: Sequence[int], n_splits: int = 4, first_train_frac: float = 0.5,
                 metric: str = "sharpe", sentiment: pd.DataFrame | None = None) -> dict[str, Any]:
    """锚定式滚动前推（Walk-Forward）：每折在训练集上重新选参数，
    再到「策略从没见过」的样本外区间检验。这是判断策略是否过拟合的关键实验。"""
    n = len(frame)
    if n_splits < 1 or not 0 < first_train_frac < 1:
        raise ValueError("滚动前推折数必须为正数，训练比例必须在 0 和 1 之间。")
    frame = frame.sort_index()
    first_train = int(n * first_train_frac)
    test_len = max(30, (n - first_train) // max(1, n_splits))
    candidates = [(f, s) for s in slow_list for f in fast_list if f < s]
    folds: list[dict[str, Any]] = []
    for k in range(n_splits):
        tr_end = first_train + k * test_len
        te_end = n if k == n_splits - 1 else min(tr_end + test_len, n)
        if te_end - tr_end < 20 or tr_end <= 0:
            break
        train_frame = frame.iloc[:tr_end]
        best = {"fast": params.fast, "slow": params.slow, "value": -np.inf, "found": False}
        for fast, slow in candidates:
            try:
                res = run_backtest(train_frame, replace(params, fast=int(fast), slow=int(slow)), "WF", sentiment=sentiment)
            except Exception:
                continue
            value = float(res["metrics"].get(metric, 0.0))
            if np.isfinite(value) and value > best["value"]:
                best = {"fast": int(fast), "slow": int(slow), "value": value, "found": True}
        if not best["found"]:
            continue
        chosen = replace(params, fast=best["fast"], slow=best["slow"])
        try:
            # 训练数据只预热指标；样本外从现金账户开始，不能继承训练期仓位和熔断状态。
            res = run_backtest(frame.iloc[:te_end], chosen, "WF", sentiment=sentiment,
                               trade_start=frame.index[tr_end])
        except Exception:
            continue
        bars = res["bars"]
        # 保留样本外前一根的本金基线，使首日盈亏与费用也计入统计。
        test_bars = bars.loc[bars.index >= frame.index[tr_end - 1]]
        if len(test_bars) < 5:
            continue
        eq_test = test_bars["equity"]
        base_test = float(eq_test.iloc[0])
        m_test = performance_stats(eq_test, [], base_test, test_bars, None, params.annualization)
        folds.append({
            "fold": k + 1,
            "train_start": frame.index[0].date().isoformat(),
            "train_end": frame.index[max(0, tr_end - 1)].date().isoformat(),
            "test_start": frame.index[tr_end].date().isoformat(),
            "test_end": frame.index[te_end - 1].date().isoformat(),
            "best_fast": best["fast"], "best_slow": best["slow"],
            "train_score": round(float(best["value"]), 3),
            "test_return": round(float(m_test["total_return"]), 4),
            "test_benchmark": round(float(m_test["benchmark_return"]), 4),
            "test_sharpe": round(float(m_test["sharpe"]), 3),
            "test_max_dd": round(float(m_test["max_drawdown"]), 4),
            "test_vol": round(float(m_test["ann_vol"]), 4),
            "test_days": int(len(eq_test) - 1),
        })
    oos = [f["test_return"] for f in folds]
    return {
        "folds": folds,
        "oos_avg_return": float(np.mean(oos)) if oos else 0.0,
        "oos_win_rate": float(np.mean([1.0 if x > 0 else 0.0 for x in oos])) if oos else 0.0,
        "oos_avg_sharpe": float(np.mean([f["test_sharpe"] for f in folds])) if folds else 0.0,
        "oos_vs_benchmark": float(np.mean([f["test_return"] - f["test_benchmark"] for f in folds])) if folds else 0.0,
    }


def monte_carlo(equity: pd.Series, n_sims: int = 400, horizon: int | None = None,
                seed: int = 20260924) -> dict[str, Any]:
    """蒙特卡洛检验：对历史日收益做有放回抽样，看未来可能出现的分布。

    它回答的是「同样的运气水平，换个顺序最坏会怎样」，不是预测未来。
    """
    eq = pd.Series(equity, dtype=float).dropna()
    rets = eq.pct_change().dropna().to_numpy(dtype=float)
    rets = rets[np.isfinite(rets)]
    if rets.size < 30:
        return {"percentiles": [], "paths": [], "final": {}, "horizon": 0}
    horizon = int(horizon or min(len(rets), 365))
    rng = np.random.default_rng(seed)
    draws = rng.choice(rets, size=(int(n_sims), horizon), replace=True)
    paths = np.cumprod(1.0 + draws, axis=1)
    levels = [5, 25, 50, 75, 95]
    fan = [[round(float(np.percentile(paths[:, t], p)), 4) for p in levels] for t in range(horizon)]
    finals = paths[:, -1] - 1.0
    dd = []
    for row in paths[:: max(1, int(n_sims) // 200)]:
        curve = np.r_[1.0, np.asarray(row, dtype=float)]
        dd.append(float((curve / np.maximum.accumulate(curve) - 1.0).min()))
    final_stats = {
        "p05": round(float(np.percentile(finals, 5)), 4),
        "p25": round(float(np.percentile(finals, 25)), 4),
        "p50": round(float(np.percentile(finals, 50)), 4),
        "p75": round(float(np.percentile(finals, 75)), 4),
        "p95": round(float(np.percentile(finals, 95)), 4),
        "prob_loss": round(float((finals < 0).mean()), 4),
        "avg_max_dd": round(float(np.mean(dd)) if dd else 0.0, 4),
        "worst_max_dd": round(float(np.min(dd)) if dd else 0.0, 4),
    }
    return {"percentiles": levels, "paths": fan, "final": final_stats, "horizon": horizon}


def correlation_matrix(named_series: dict[str, pd.Series]) -> dict[str, Any]:
    """资产日收益相关性矩阵，用于判断组合是否真的分散了风险。"""
    names = list(named_series.keys())
    if len(names) < 2:
        return {"labels": names, "matrix": [[1.0]]}
    frame = pd.DataFrame({k: pd.Series(v, dtype=float) for k, v in named_series.items()})
    rets = frame.pct_change().dropna(how="all")
    corr = rets.corr(min_periods=20)
    labels = list(corr.columns)
    matrix = [[None if pd.isna(corr.iloc[i, j]) else round(float(corr.iloc[i, j]), 3)
               for j in range(len(labels))] for i in range(len(labels))]
    return {"labels": labels, "matrix": matrix}

# =====================================================================
# 九、组合层：多资产配置（等权 / 风险平价 / 手动权重）
# =====================================================================

def asset_volatility(frame: pd.DataFrame, window: int = 90, periods: float = ANN) -> float:
    """单资产年化波动率估计，用于风险平价加权。"""
    close = frame["Close"].astype(float)
    rets = np.log(close / close.shift(1)).dropna()
    tail = rets.iloc[-int(window):] if len(rets) > int(window) else rets
    if len(tail) < 5:
        return 0.8
    return float(tail.std(ddof=1) * np.sqrt(periods))


def resolve_weights(names: Sequence[str], frames: Sequence[pd.DataFrame],
                    params: StrategyParams) -> list[float]:
    """决定各资产在组合里的资金权重。"""
    n = len(names)
    if n == 1:
        return [1.0]
    method = params.alloc_method
    if method == "equal":
        return [1.0 / n] * n
    if method == "manual":
        raw = str(params.weights or "").replace("，", ",").replace(" ", "")
        parsed = [float(x) for x in raw.split(",") if x != ""]
        if len(parsed) != n:
            raise ValueError(f"手动权重需要 {n} 个数字（用英文逗号分隔），现在给了 {len(parsed)} 个。")
        if any(not np.isfinite(x) or x < 0 for x in parsed):
            raise ValueError("手动权重不能是负数。")
        total = sum(parsed)
        if total <= 0:
            raise ValueError("手动权重之和必须大于 0。")
        return [x / total for x in parsed]
    # 固定初始逆波动权重只使用组合第一笔可交易日前已知的数据。
    # 尚未上市或历史不足的资产使用固定 80% 波动先验，不能偷看其未来数据。
    warmup = max(params.slow, params.vol_lookback, params.atr_period, params.adv_window) + 2
    starts = [f.sort_index().index[warmup] for f in frames if len(f) > warmup]
    if not starts:
        raise ValueError("行情长度不足以完成预热。")
    cutoff = min(starts)
    vols = [max(asset_volatility(f.loc[f.index < cutoff].sort_index(),
                                periods=params.annualization), 1e-6) for f in frames]
    inv = [1.0 / v for v in vols]
    total = sum(inv)
    return [x / total for x in inv]


def _merge_trade_metrics(metrics: dict[str, Any], trips: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """把交易级统计并入指标字典（用 trade_ 前缀与日频指标区分开）。"""
    ts = trade_stats(trips)
    metrics["trade_count"] = ts["trade_count"]
    metrics["trade_win_rate"] = ts["win_rate"]
    metrics["payoff_ratio"] = ts["payoff_ratio"]
    metrics["trade_profit_factor"] = ts["profit_factor"]
    metrics["avg_holding_days"] = ts["avg_holding_days"]
    metrics["expectancy"] = ts["expectancy"]
    metrics["max_consecutive_losses"] = ts["max_consecutive_losses"]
    metrics["best_trade"] = ts["best_trade"]
    metrics["worst_trade"] = ts["worst_trade"]
    return metrics


def run_portfolio(named_frames: dict[str, pd.DataFrame], params: StrategyParams,
                  sentiment: dict[str, pd.DataFrame] | None = None,
                  trade_start: pd.Timestamp | None = None) -> dict[str, Any]:
    """把多个资产的独立子账户合并成一个组合，返回组合净值与各资产贡献。"""
    params.validate()
    names = list(named_frames.keys())
    if not names:
        raise ValueError("组合里至少需要一个资产。")
    frames = [named_frames[k] for k in names]
    weights = resolve_weights(names, frames, params)
    active = [(name, weight) for name, weight in zip(names, weights) if weight > 0]
    # 主图资产必须覆盖组合起点；按日期排序仍保留各资产已解析的手动权重。
    active.sort(key=lambda item: named_frames[item[0]].index.min())
    names, weights = map(list, zip(*active))
    total_cash = float(params.initial_cash)

    legs: list[dict[str, Any]] = []
    for name, weight in zip(names, weights):
        sleeve = total_cash * weight
        leg = run_backtest(named_frames[name], replace(params, initial_cash=sleeve), name,
                           sentiment=(sentiment or {}).get(name), trade_start=trade_start)
        leg["alloc_weight"] = weight
        leg["sleeve_cash"] = sleeve
        legs.append(leg)

    index = pd.DatetimeIndex(sorted(set().union(*[set(leg["equity"].index) for leg in legs])))
    equity_map: dict[str, pd.Series] = {}
    bm_map: dict[str, pd.Series] = {}
    weight_map: dict[str, pd.Series] = {}
    for leg in legs:
        sleeve = leg["sleeve_cash"]
        symbol = leg["symbol"]
        equity_map[symbol] = leg["equity"].reindex(index).ffill().fillna(sleeve)
        bm_map[symbol] = leg["benchmark"].reindex(index).ffill().fillna(sleeve)
        weight_map[symbol] = leg["bars"]["weight"].reindex(index).ffill().fillna(0.0)

    total_equity = sum(equity_map.values())
    total_benchmark = sum(bm_map.values())
    exposure = (sum(weight_map[k] * equity_map[k] for k in equity_map)
                / total_equity.replace(0.0, np.nan)).fillna(0.0)

    primary = legs[0]["bars"].reindex(index).ffill()
    combined = primary.copy()
    combined["equity"] = total_equity
    combined["benchmark"] = total_benchmark
    combined["weight"] = exposure
    combined["drawdown"] = total_equity / total_equity.cummax() - 1.0
    combined["cash"] = sum(leg["bars"]["cash"].reindex(index).ffill().fillna(leg["sleeve_cash"])
                            for leg in legs)
    combined["fees"] = sum(leg["bars"]["fees"].reindex(index).ffill().fillna(0.0) for leg in legs)
    combined["halted"] = pd.concat(
        [leg["bars"]["halted"].reindex(index).ffill().fillna(False) for leg in legs], axis=1).any(axis=1)

    all_trades: list[dict[str, Any]] = []
    all_trips: list[dict[str, Any]] = []
    for leg in legs:
        for t in leg["trades"]:
            row = dict(t)
            row["symbol"] = leg["symbol"]
            all_trades.append(row)
        for t in leg["round_trips"]:
            row = dict(t)
            row["symbol"] = leg["symbol"]
            all_trips.append(row)
    all_trades.sort(key=lambda x: str(x.get("date") or ""))
    all_trips.sort(key=lambda x: str(x.get("exit_date") or ""))

    turnover = float(sum(leg["turnover"] for leg in legs))
    metrics = performance_stats(total_equity, all_trades, total_cash, combined, turnover, params.annualization)
    _merge_trade_metrics(metrics, all_trips)

    leg_rows = []
    for leg in legs:
        sleeve = leg["sleeve_cash"]
        symbol = leg["symbol"]
        aligned = equity_map[symbol]
        leg_metrics = performance_stats(aligned, leg["trades"], sleeve, None, leg["turnover"], params.annualization)
        _merge_trade_metrics(leg_metrics, leg["round_trips"])
        leg_rows.append({
            "symbol": symbol,
            "alloc_weight": leg["alloc_weight"],
            "sleeve_cash": sleeve,
            "final_equity": float(aligned.iloc[-1]),
            "pnl": float(aligned.iloc[-1] - sleeve),
            "contribution": (float(aligned.iloc[-1] - sleeve) / total_cash) if total_cash > 0 else 0.0,
            "equity": aligned,
            "metrics": leg_metrics,
            "trades": leg["trades"],
            "round_trips": leg["round_trips"],
            "bars": leg["bars"],
            "halted": leg["halted"],
        })

    return {
        "params": params,
        "names": names,
        "weights": weights,
        "equity": total_equity,
        "benchmark": total_benchmark,
        "bars": combined,
        "primary_bars": primary,
        "metrics": metrics,
        "trades": all_trades,
        "round_trips": all_trips,
        "legs": leg_rows,
        "correlation": correlation_matrix({k: equity_map[k] for k in equity_map}),
        "risk_events": [dict(ev, symbol=leg["symbol"]) for leg in legs for ev in leg.get("risk_events", [])],
        "fees_paid": float(sum(float(t.get("fee") or 0.0) for t in all_trades)),
        "halted": any(leg["halted"] for leg in legs),
        "turnover": turnover,
    }


# =====================================================================
# 十、内置自检：直接运行本文件即可检查引擎是否正常
# =====================================================================

def _synthetic(name: str = "SYN", days: int = 1500, drift: float = 0.0008,
               vol: float = 0.03, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2019-01-01", periods=days, freq="D")
    ret = drift + rng.normal(0.0, vol, days)
    close = 100.0 * np.exp(np.cumsum(ret))
    open_px = close * np.exp(rng.normal(0.0, 0.004, days))
    high = np.maximum.reduce([close, open_px]) * (1.0 + np.abs(rng.normal(0.0, 0.008, days)))
    low = np.minimum.reduce([close, open_px]) * (1.0 - np.abs(rng.normal(0.0, 0.008, days)))
    return pd.DataFrame({
        "Open": open_px, "High": high, "Low": low, "Close": close,
        "Volume": rng.uniform(5_000, 80_000, days),
    }, index=dates)


def _selftest() -> int:
    print("=" * 66)
    print("  quant_core 自检（使用合成数据，不联网、不下单）")
    print("=" * 66)
    a = _synthetic("AAA", drift=0.0010, vol=0.030, seed=1)
    b = _synthetic("BBB", drift=0.0006, vol=0.018, seed=2)

    single = run_backtest(a, StrategyParams(initial_cash=10_000.0), "AAA")
    m = single["metrics"]
    print(f"[单资产] 净值 {m['final_equity']:,.0f} | 收益 {m['total_return'] * 100:6.2f}% | "
          f"夏普 {m['sharpe']:5.2f} | 回撤 {m['max_drawdown'] * 100:6.2f}% | 成交 {m['trades']} 笔")
    assert m["final_equity"] > 0, "净值必须为正"
    assert single["bars"]["equity"].notna().all(), "净值序列不能有 NaN"
    assert len(single["trades"]) == m["trades"], "成交笔数不一致"

    port = run_portfolio({"AAA": a, "BBB": b}, StrategyParams(initial_cash=100_000.0))
    pm = port["metrics"]
    print(f"[组合]   净值 {pm['final_equity']:,.0f} | 收益 {pm['total_return'] * 100:6.2f}% | "
          f"夏普 {pm['sharpe']:5.2f} | 回撤 {pm['max_drawdown'] * 100:6.2f}% | "
          f"基准 {pm['benchmark_return'] * 100:6.2f}%")
    print(f"[组合]   权重 {[round(w, 3) for w in port['weights']]} | 交易胜率 "
          f"{pm.get('trade_win_rate', 0) * 100:.1f}% | 盈亏比 {pm.get('payoff_ratio', 0):.2f}")
    assert abs(sum(port["weights"]) - 1.0) < 1e-9, "权重之和必须为 1"

    month = monthly_return_table(port["equity"])
    filled = sum(1 for row in month["matrix"] for v in row if v is not None)
    print(f"[月度]   年份 {month['years']} | 月度格子 {filled} 个")
    mc = monte_carlo(port["equity"], n_sims=100, horizon=90)
    print(f"[蒙特卡洛] 一年后中位数 {mc['final']['p50'] * 100:+.1f}% | "
          f"5% 分位 {mc['final']['p05'] * 100:+.1f}% | 亏钱概率 {mc['final']['prob_loss'] * 100:.0f}%")
    grid = parameter_grid(a, StrategyParams(initial_cash=10_000.0), [10, 20, 30], [50, 80])
    print(f"[参数网格] 最优 fast={grid['best']['fast']} slow={grid['best']['slow']} "
          f"夏普={grid['best']['value']:.2f}")
    print("-" * 66)
    print("  自检通过 [OK]  引擎工作正常（本程序不连接任何交易所）")
    print("=" * 66)
    return 0


# =====================================================================
# 十一、本地行情文件读取（兼容多种 CSV 格式）
# =====================================================================

def _pick_column(columns, *names):
    """在 CSV 表头里找列名，忽略大小写和空格。"""
    normalized = {}
    for column in columns:
        normalized.setdefault(str(column).strip().lower().replace(" ", "_"), column)
    for name in names:
        key = name.strip().lower().replace(" ", "_")
        if key in normalized:
            return normalized[key]
    return None


def read_ohlc_csv(path, min_rows: int = 60) -> pd.DataFrame:
    """读取本地 K 线 CSV，兼容以下格式：

    * 本程序自己下载的：Date, Open, High, Low, Close, Volume
    * Binance 官方导出：open_time, open, high, low, close, volume, ...
    * 只有 Date, Close 的简单格式（自动补齐 OHLC）
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"找不到这个 CSV 文件：{path}")
    raw = pd.read_csv(path)
    if raw.empty:
        raise ValueError(f"{path.name} 是空文件。")

    date_col = _pick_column(raw.columns, "Date", "open_time", "opentime", "timestamp", "time", "时间")
    if date_col is None:
        raise ValueError(f"{path.name} 里找不到日期列（Date / open_time / timestamp）。")
    series = raw[date_col]
    if pd.api.types.is_numeric_dtype(series):
        sample = series.dropna()
        magnitude = abs(float(sample.iloc[0])) if len(sample) else 0
        unit = "ns" if magnitude >= 1e17 else "us" if magnitude >= 1e14 else "ms" if magnitude >= 1e11 else "s"
        dates = pd.to_datetime(series, unit=unit, errors="coerce", utc=True)
    else:
        dates = pd.to_datetime(series, errors="coerce", utc=True)
    dates = pd.Series(dates).dt.tz_localize(None)

    close_col = _pick_column(raw.columns, "Close", "收盘", "收盘价", "Price", "price")
    if close_col is None:
        raise ValueError(f"{path.name} 里找不到收盘价列（Close）。")

    frame = pd.DataFrame({"Date": dates})
    for target, aliases in (
        ("Open", ("Open", "开盘", "开盘价")),
        ("High", ("High", "最高", "最高价")),
        ("Low", ("Low", "最低", "最低价")),
        ("Close", ("Close", "收盘", "收盘价")),
        ("Volume", ("Volume", "成交量")),
    ):
        column = _pick_column(raw.columns, *aliases)
        frame[target] = (pd.to_numeric(raw[column], errors="coerce") if column is not None
                         else np.nan if target == "Volume" else pd.to_numeric(raw[close_col], errors="coerce"))

    frame = frame.dropna(subset=["Date", "Close"]).set_index("Date").sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    frame = frame[np.isfinite(frame["Close"]) & (frame["Close"] > 0)]
    for column in ("Open", "High", "Low"):
        frame[column] = frame[column].fillna(frame["Close"])
    prices = frame[["Open", "High", "Low", "Close"]]
    if not (np.isfinite(prices.to_numpy()).all() and (prices > 0).all().all()):
        raise ValueError(f"{path.name} 包含非正数或非有限 OHLC 价格。")
    frame["Volume"] = frame["Volume"].where(np.isfinite(frame["Volume"]) & (frame["Volume"] >= 0))
    frame["High"] = frame[["High", "Open", "Close"]].max(axis=1)
    frame["Low"] = frame[["Low", "Open", "Close"]].min(axis=1)
    if len(frame) < int(min_rows):
        raise ValueError(f"{path.name} 有效 K 线只有 {len(frame)} 根，至少需要 {min_rows} 根。")
    result = frame[["Open", "High", "Low", "Close", "Volume"]]
    result.attrs["provenance"] = read_data_provenance(path)
    return result


def read_data_provenance(path) -> dict[str, Any]:
    """Only label a local file verified when its recorded content hash matches."""
    path = Path(path)
    metadata_path = path.with_suffix(".metadata.json")
    unknown = {"data_kind": "unverified", "source": "本地 CSV，来源未验证", "verified": False}
    if not metadata_path.is_file():
        return unknown
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or metadata.get("data_kind") != "real":
            return unknown
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if metadata.get("sha256") != actual:
            return unknown | {"warning": "CSV 已改变，与来源记录哈希不匹配。"}
        return metadata | {"verified": True}
    except (OSError, ValueError, TypeError):
        return unknown


def list_csv_files(data_dir) -> list[dict[str, Any]]:
    """列出 data 文件夹里的所有 CSV（名称 / 路径 / K 线根数 / 市场类型）。
    支持加密货币（xxx_usdt.csv）和股票（aapl.csv / spy.csv）两种命名风格。
    """
    _KNOWN_US_STOCKS = {"AAPL", "TSLA", "SPY", "NVDA", "QQQ", "MSFT", "AMZN", "GOOG",
                        "GOOGL", "META", "AMD", "BA", "RKLB", "NFLX", "DIS", "JPM",
                        "V", "MA", "WMT", "JNJ", "PG", "KO", "PEP", "UNH", "HD",
                        "INTC", "COST", "ADBE", "CRM", "PYPL", "BABA", "TSM"}
    directory = Path(data_dir)
    if not directory.is_dir():
        return []
    files: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.csv")):
        try:
            with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
                rows = sum(1 for _ in handle) - 1
        except OSError:
            rows = -1
        stem = path.stem
        name = stem.replace("_usdt", "").upper()
        # 判断市场类型
        if "_usdt" in stem.lower():
            market = "crypto"
        elif name in _KNOWN_US_STOCKS:
            market = "us"
        else:
            market = "other"
        provenance = read_data_provenance(path) if market == "us" else {}
        files.append({"name": name, "file": path.name,
                      "path": str(path), "rows": max(rows, 0), "market": market,
                      "provenance": provenance})
    return files

if __name__ == "__main__":
    raise SystemExit(_selftest())
