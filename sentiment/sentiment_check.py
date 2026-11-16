# -*- coding: utf-8 -*-
"""淮策 Quant · 舆情模块验证层（论文核心贡献所在）。

这个文件回答论文里必须回答的问题：
    「多源舆情到底有没有用？有多少用？换个参数还成立吗？是不是我自己在骗自己？」

九项检验（每一项对应论文里的一个小节）：
    [1] 信息系数 IC / RankIC —— 舆情与未来 1/5/20 日收益的相关性
    [2] 分层回测         —— 按舆情分分层，看未来收益是否单调
    [3] 事件研究         —— 重大情绪事件前后的累计异常收益 (CAR)
    [4] 增量检验         —— 只用价格 vs 价格 + 舆情，样本外对比
    [5] 权重方案对照     —— 主观先验 / 等权 / 熵权 / 样本内校准
    [6] 敏感性分析       —— 参数上下浮动 33%~50%，结论是否改变
    [7] 经济意义检验     —— 把舆情当风控开关，看夏普与最大回撤变化
    [8] 选择偏差检验     —— 事后挑选出来的「大事件」会把 IC 抬高多少
    [9] 结果落盘         —— 全部中间结果写入 sentiment/output/

【本文件最重要的一条方法论】
舆情分数 z 是一个「慢变量」（因为带半衰期加权，今天的分数里含有过去几十天的信息）。
慢变量和长期上涨/下跌的价格放在一起做相关，极易产生**虚假相关**：
水平 z 的 IC 看着很漂亮，其实只是「分数慢慢漂 + 价格在走趋势」。
本程序因此同时检验三个版本：
    · 水平 z        —— 原始分数（容易被趋势污染）
    · 差分 Δz       —— z 的一阶差分，去掉慢漂移（更干净）
    · 去趋势 z      —— z 减去 120 日均线（中间方案）
如果「水平 z 的 IC 高、差分 Δz 的 IC 却接近 0」，那这个预测力基本是假的。
这一条，是很多本科生论文里被审稿人一枪打中的地方，请务必写进「稳健性」章节。

【重叠收益问题】
持有 5 日意味着相邻两天样本的收益窗口有 4 天重叠，普通 t 值会被严重高估。
所以本程序额外报告「非重叠样本」的 RankIC（每 h 天只取 1 天），并用它算 t 值。

运行方式：
    python sentiment/sentiment_check.py            # 全量
    python sentiment/sentiment_check.py --quick    # 快速（跳过样本内校准，敏感性只跑 BTC）
    python sentiment/sentiment_check.py --csv 你的文件.csv
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
ROOT = BASE_DIR.parent
for _p in (str(BASE_DIR), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from . import weight_config as cfg
    from . import sentiment_core as core
except ImportError:
    import weight_config as cfg
    import sentiment_core as core

DATA_DIR = ROOT / "data"
OUTPUT_DIR = BASE_DIR / "output"
SAMPLE_CSV = BASE_DIR / "sample_data" / "\u793a\u4f8b_\u8206\u60c5\u4e8b\u4ef6.csv"
PERIODS_PER_YEAR = 365
TAG_COLUMN = "\u6570\u636e\u6027\u8d28"
TAG_SYNTHETIC = "\u5408\u6210\u793a\u4f8b"
FACTORS = ("z", "dz", "z_detrend")
FACTOR_LABELS = {"z": "\u6c34\u5e73z", "dz": "\u5dee\u5206\u0394z", "z_detrend": "\u53bb\u8d8b\u52bfz"}


# =====================================================================
# 零、通用小工具
# =====================================================================

def hrule(char: str = "=", width: int = 74) -> str:
    return char * width


def fmt(x, nd: int = 4) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if not math.isfinite(v):
        return "  nan"
    return f"{v:+.{nd}f}"


def pearson(x, y) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 3:
        return float("nan")
    if x.std() <= 1e-12 or y.std() <= 1e-12:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def spearman(x, y) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 3:
        return float("nan")
    rx = pd.Series(x[m]).rank().to_numpy()
    ry = pd.Series(y[m]).rank().to_numpy()
    return pearson(rx, ry)


def t_stat(ic: float, n: int) -> float:
    if not math.isfinite(ic) or n <= 2 or abs(ic) >= 1.0:
        return float("nan")
    return float(ic * math.sqrt((n - 2) / (1.0 - ic * ic)))


def p_value(t: float) -> float:
    """双尾 p 值。样本足够大时用正态近似（不依赖 scipy）。"""
    if not math.isfinite(t):
        return float("nan")
    return float(math.erfc(abs(t) / math.sqrt(2.0)))


def stars(p: float) -> str:
    if not math.isfinite(p):
        return ""
    if p < 0.01:
        return "***"
    if p < 0.05:
        return "**"
    if p < 0.10:
        return "*"
    return ""


def ols_full(y, X):
    """带标准误的 OLS（不依赖 scipy）。"""
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    mask = np.isfinite(y) & np.isfinite(X).all(axis=1)
    y, X = y[mask], X[mask]
    n, k = X.shape
    if n <= k:
        return None
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    dof = n - k
    sigma2 = float(resid @ resid) / dof if dof > 0 else float("nan")
    try:
        xtx_inv = np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        xtx_inv = np.linalg.pinv(X.T @ X)
    se = np.sqrt(np.maximum(np.diag(xtx_inv) * sigma2, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        tvals = np.where(se > 1e-15, coef / se, np.nan)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 1e-18 else float("nan")
    return {"coef": coef, "se": se, "t": tvals, "r2": r2, "n": n, "k": k}


def perf_stats(returns: pd.Series, label: str = "") -> dict:
    r = pd.Series(returns).dropna()
    if r.empty:
        return {"策略": label, "天数": 0, "累计收益": float("nan"), "年化收益": float("nan"),
                "年化波动": float("nan"), "夏普": float("nan"), "最大回撤": float("nan")}
    cum = (1.0 + r).cumprod()
    total = float(cum.iloc[-1] - 1.0)
    years = len(r) / PERIODS_PER_YEAR
    ann = float((1.0 + total) ** (1.0 / years) - 1.0) if years > 0 and total > -1 else float("nan")
    vol = float(r.std(ddof=1) * math.sqrt(PERIODS_PER_YEAR))
    sharpe = float(r.mean() * PERIODS_PER_YEAR / vol) if vol > 1e-12 else float("nan")
    dd = float((cum / cum.cummax() - 1.0).min())
    return {"策略": label, "天数": int(len(r)), "累计收益": total, "年化收益": ann,
            "年化波动": vol, "夏普": sharpe, "最大回撤": dd}


# =====================================================================
# 一、载入数据
# =====================================================================

def discover_price_files() -> dict:
    out = {}
    for p in sorted(DATA_DIR.glob("*_usdt.csv")):
        out[p.stem.split("_")[0].upper()] = p
    return out


def load_price_series(path: Path) -> pd.Series:
    frame = pd.read_csv(path)
    lowered = {str(c).strip().lower(): c for c in frame.columns}
    date_col = next((lowered[k] for k in ("date", "time", "\u65e5\u671f", "timestamp") if k in lowered), None)
    close_col = next((lowered[k] for k in ("close", "\u6536\u76d8", "adj close", "price") if k in lowered), None)
    if date_col is None or close_col is None:
        raise ValueError(f"{path.name} 缺少日期列或收盘价列。")
    idx = pd.to_datetime(frame[date_col], errors="coerce").dt.normalize()
    s = pd.Series(pd.to_numeric(frame[close_col], errors="coerce").to_numpy(dtype=float), index=idx)
    s = s[s.index.notna()].dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


def load_items(csv_path: Path | None = None, asset: str | None = None) -> tuple:
    path = Path(csv_path) if csv_path else SAMPLE_CSV
    if not path.exists():
        raise FileNotFoundError(f"找不到舆情数据文件：{path}")
    frame = pd.read_csv(path)
    is_sample = path.resolve() == SAMPLE_CSV.resolve()
    items = core.prepare_items(frame, asset=asset)
    return items, is_sample


def load_prices(assets=None) -> dict:
    out = {}
    for asset, path in discover_price_files().items():
        if assets and asset not in assets:
            continue
        out[asset] = load_price_series(path)
    return out


def build_daily(items: pd.DataFrame, scheme: str = "prior",
                source_weights: dict | None = None) -> pd.DataFrame:
    return core.build_daily_sentiment(items, scheme=scheme, source_weights=source_weights)


def split_by_tag(items: pd.DataFrame) -> tuple:
    """把示例数据按「数据性质」列拆成（合成示例, 真实事件节选）。"""
    if TAG_COLUMN not in items.columns:
        return items, items.iloc[0:0]
    mask = items[TAG_COLUMN].astype(str).str.strip() == TAG_SYNTHETIC
    return items[mask].copy(), items[~mask].copy()


# =====================================================================
# 二、把舆情分数与未来收益对齐；构造三套因子
# =====================================================================

def add_factor_columns(daily: pd.DataFrame) -> pd.DataFrame:
    """给日频表补上 差分Δz 与 去趋势z 两列。"""
    d = daily.sort_values(["asset", "date"]).copy()
    d["dz"] = d.groupby("asset")["z"].diff()
    ma = d.groupby("asset")["z"].transform(lambda s: s.rolling(120, min_periods=40).mean())
    d["z_detrend"] = d["z"] - ma
    return d


def attach_forward_return(daily: pd.DataFrame, prices: dict, horizon: int) -> pd.DataFrame:
    """补一列 fwd_ret = 第 t 日收盘买入、第 t+h 日收盘卖出的收益。"""
    frames = []
    for asset, group in daily.groupby("asset", sort=False):
        px = prices.get(asset)
        if px is None:
            continue
        fwd = px.shift(-horizon) / px - 1.0
        g = group.copy()
        g["fwd_ret"] = pd.Series(g["date"].to_numpy()).map(fwd).to_numpy(dtype=float)
        g["horizon"] = horizon
        frames.append(g)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def factor_dataset(daily: pd.DataFrame, prices: dict, horizon: int,
                   coverage_min: float | None = None) -> pd.DataFrame:
    coverage_min = cfg.COVERAGE_MIN if coverage_min is None else coverage_min
    d = add_factor_columns(daily)
    table = attach_forward_return(d, prices, horizon)
    if table.empty:
        return table
    table = table.dropna(subset=["fwd_ret", "z"])
    return table[table["coverage"] >= coverage_min].copy()


def nonoverlap(table: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """每 horizon 天只取 1 天，消除重叠收益带来的 t 值高估。"""
    parts = []
    for _, g in table.groupby("asset", sort=False):
        parts.append(g.sort_values("date").iloc[::max(1, int(horizon))])
    return pd.concat(parts, ignore_index=True) if parts else table


# =====================================================================
# 三、[1] 信息系数 IC / RankIC（三套因子 + 非重叠）
# =====================================================================

def information_coefficient(daily: pd.DataFrame, prices: dict, horizons=None,
                            coverage_min: float | None = None) -> pd.DataFrame:
    horizons = horizons or cfg.IC_HORIZONS
    coverage_min = cfg.COVERAGE_MIN if coverage_min is None else coverage_min
    d = add_factor_columns(daily)      # 因子列只算一次，三个持有期复用
    rows = []
    for horizon in horizons:
        table = attach_forward_return(d, prices, horizon)
        if table.empty:
            continue
        table = table.dropna(subset=["fwd_ret", "z"])
        table = table[table["coverage"] >= coverage_min]
        groups = list(table.groupby("asset", sort=False)) + [("ALL", table)]
        for name, sub in groups:
            if len(sub) < 40:
                continue
            non = nonoverlap(sub, horizon)
            row = {"资产": name, "持有期(日)": horizon, "样本数": len(sub),
                   "非重叠样本数": len(non)}
            for f in FACTORS:
                row[f"IC_{FACTOR_LABELS[f]}"] = pearson(sub[f], sub["fwd_ret"])
                row[f"RankIC_{FACTOR_LABELS[f]}"] = spearman(sub[f], sub["fwd_ret"])
            ric_non = spearman(non["z"], non["fwd_ret"])
            t = t_stat(ric_non, len(non))
            row["非重叠RankIC_水平"] = ric_non
            row["t值"] = t
            row["p值"] = p_value(t)
            row["显著性"] = stars(row["p值"])
            rows.append(row)
    return pd.DataFrame(rows)


# =====================================================================
# 四、[2] 分层回测
# =====================================================================

def layered_backtest(daily: pd.DataFrame, prices: dict, horizon: int = 5,
                     n_layers: int | None = None,
                     coverage_min: float | None = None,
                     factor: str = "z") -> pd.DataFrame:
    n_layers = n_layers or cfg.N_LAYERS
    table = factor_dataset(daily, prices, horizon, coverage_min)
    if table.empty:
        return pd.DataFrame()
    table = table.dropna(subset=[factor])
    rows = []
    for name, sub in list(table.groupby("asset", sort=False)) + [("ALL", table)]:
        if len(sub) < n_layers * 20:
            continue
        sub = sub.copy()
        sub["layer"] = pd.qcut(sub[factor].rank(method="first"), n_layers,
                               labels=[f"L{i+1}" for i in range(n_layers)])
        grp = sub.groupby("layer", observed=True).agg(
            平均因子=(factor, "mean"), 平均未来收益=("fwd_ret", "mean"),
            胜率=("fwd_ret", lambda s: float((s > 0).mean())), 样本数=("fwd_ret", "size"))
        grp = grp.reset_index()
        grp.insert(0, "资产", name)
        grp.insert(1, "因子", FACTOR_LABELS.get(factor, factor))
        grp["持有期(日)"] = horizon
        grp["层序"] = range(1, len(grp) + 1)
        grp["多空差(顶-底)"] = float(grp["平均未来收益"].iloc[-1] - grp["平均未来收益"].iloc[0])
        grp["单调性"] = spearman(grp["层序"], grp["平均未来收益"])
        rows.append(grp)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# =====================================================================
# 五、[3] 事件研究
# =====================================================================

def event_study(daily: pd.DataFrame, prices: dict, threshold: float | None = None,
                window: tuple = None, coverage_min: float | None = None,
                event_quantile: float | None = None) -> tuple:
    """重大情绪事件前后的累计异常收益。

    阈值规则：先用固定阈值 EVENT_Z_THRESHOLD；如果样本里事件太少
    （少于 20 个），自动退回到 |z| 的 EVENT_Z_QUANTILE 分位数作为阈值，
    并在结果里明确标注用了哪一种 —— 免得"没有事件"变成"没有结论"。
    """
    window = window or cfg.EVENT_WINDOW
    coverage_min = cfg.COVERAGE_MIN if coverage_min is None else coverage_min
    event_quantile = cfg.EVENT_Z_QUANTILE if event_quantile is None else event_quantile
    fixed = cfg.EVENT_Z_THRESHOLD if threshold is None else threshold
    covered = daily[daily["coverage"] >= coverage_min]
    n_fixed = int((covered["z"].abs() >= fixed).sum()) if not covered.empty else 0
    if n_fixed < 20 and not covered.empty:
        thr_used = float(min(fixed, covered["z"].abs().quantile(event_quantile)))
        thr_mode = f"自适应（|z| 的 {int(event_quantile * 100)}% 分位，固定阈值下只有 {n_fixed} 个事件）"
    else:
        thr_used = float(fixed)
        thr_mode = f"固定阈值 {fixed}"
    lo, hi = int(window[0]), int(window[1])
    offsets = list(range(lo, hi + 1))
    accum, detail = [], []
    for asset, group in daily.groupby("asset", sort=False):
        px = prices.get(asset)
        if px is None or len(px) < 80:
            continue
        ret = px.pct_change()
        drift = ret.rolling(60, min_periods=30).mean()
        abn = (ret - drift)   # 保留与价格同长度的索引，避免位置错位
        positions = {ts: i for i, ts in enumerate(px.index)}
        events = group[(group["z"].abs() >= thr_used) & (group["coverage"] >= coverage_min)]
        for _, ev in events.iterrows():
            pos = positions.get(pd.Timestamp(ev["date"]))
            if pos is None:
                continue
            window_rets, ok = [], True
            for off in offsets:
                p = pos + off
                if p < 0 or p >= len(px):
                    ok = False
                    break
                v = float(abn.iloc[p])
                window_rets.append(0.0 if np.isnan(v) else v)
            if not ok:
                continue
            accum.append({"资产": asset, "日期": ev["date"], "事件z": float(ev["z"]),
                          "方向": "正面" if ev["z"] > 0 else "负面",
                          "CAR": np.cumsum(window_rets)})
            detail.append({"资产": asset, "日期": ev["date"], "z": float(ev["z"]),
                           "score": float(ev["score"]), "coverage": float(ev["coverage"]),
                           "顶事件": str(ev.get("top_event", ""))[:60]})
    info = {"阈值": thr_used, "阈值说明": thr_mode, "事件总数": len(accum)}
    if not accum:
        return pd.DataFrame(), pd.DataFrame(), info
    rows = []
    for sign_label, subset in [("全部", accum),
                               ("正面", [a for a in accum if a["方向"] == "正面"]),
                               ("负面", [a for a in accum if a["方向"] == "负面"])]:
        if not subset:
            continue
        mat = np.vstack([a["CAR"] for a in subset])
        mean_car = mat.mean(axis=0)
        se = (mat.std(axis=0, ddof=1) / math.sqrt(len(subset))
              if len(subset) > 1 else np.zeros(len(offsets)))
        for i, off in enumerate(offsets):
            t = float(mean_car[i] / se[i]) if se[i] > 1e-12 else float("nan")
            rows.append({"分组": sign_label, "相对日": off, "平均CAR": float(mean_car[i]),
                         "t值": t, "p值": p_value(t), "样本数": len(subset)})
    return pd.DataFrame(rows), pd.DataFrame(detail), info

# =====================================================================
# 六、[4] 增量检验：只用价格 vs 价格 + 舆情
# =====================================================================

def _mom20(table: pd.DataFrame, prices: dict) -> pd.DataFrame:
    out = []
    for asset, group in table.groupby("asset", sort=False):
        px = prices[asset]
        mom = (px / px.shift(20) - 1.0)
        g = group.copy()
        g["mom20"] = pd.Series(g["date"].to_numpy()).map(mom).to_numpy(dtype=float)
        out.append(g)
    return pd.concat(out, ignore_index=True) if out else table


def incremental_test(daily: pd.DataFrame, prices: dict, horizons=None,
                     coverage_min: float | None = None,
                     train_ratio: float = 0.7) -> pd.DataFrame:
    """加了舆情以后，样本外 R2 有没有提高？

    模型 A（基准）：未来收益 ~ 常数 + 20 日动量（只用价格）
    模型 B（+舆情）：未来收益 ~ 常数 + 20 日动量 + 水平 z
    模型 C（+差分）：未来收益 ~ 常数 + 20 日动量 + 差分 Δz
    训练集 = 时间前 70%，测试集 = 时间后 30%（严格按时间切，不随机打乱）。
    """
    horizons = horizons or [1, 5, 20]
    rows = []
    for horizon in horizons:
        table = factor_dataset(daily, prices, horizon, coverage_min)
        if table.empty:
            continue
        table = _mom20(table, prices).dropna(subset=["mom20", "dz"])
        if table.empty:
            continue
        split_date = table["date"].quantile(train_ratio)
        for name, sub in list(table.groupby("asset", sort=False)) + [("ALL", table)]:
            if len(sub) < 150:
                continue
            sub = sub.sort_values("date")
            tr = (sub["date"] < split_date).to_numpy()
            te = ~tr
            if tr.sum() < 80 or te.sum() < 40:
                continue
            y_all = sub["fwd_ret"].to_numpy(dtype=float)
            mom = sub["mom20"].to_numpy(dtype=float)
            z = sub["z"].to_numpy(dtype=float)
            dz = sub["dz"].to_numpy(dtype=float)
            one = np.ones(len(sub))
            Xa = np.column_stack([one, mom])
            Xb = np.column_stack([one, mom, z])
            Xc = np.column_stack([one, mom, dz])

            def fit_eval(X):
                f = ols_full(y_all[tr], X[tr])
                if f is None:
                    return None, float("nan"), float("nan")
                y_te = y_all[te]
                sse = float(((y_te - X[te] @ f["coef"]) ** 2).sum())
                sst = float(((y_te - y_te.mean()) ** 2).sum())
                oos = 1.0 - sse / sst if sst > 1e-18 else float("nan")
                return f, f["r2"], oos

            fa, r2a, oosa = fit_eval(Xa)
            fb, r2b, oosb = fit_eval(Xb)
            fc, r2c, oosc = fit_eval(Xc)
            if fa is None or fb is None or fc is None:
                continue
            rows.append({
                "资产": name, "持有期(日)": horizon,
                "样本内R2(仅价格)": r2a,
                "样本内ΔR2(+水平z)": r2b - r2a,
                "样本内ΔR2(+差分Δz)": r2c - r2a,
                "样本外R2(仅价格)": oosa,
                "样本外ΔR2(+水平z)": oosb - oosa,
                "样本外ΔR2(+差分Δz)": oosc - oosa,
                "水平z系数": float(fb["coef"][-1]), "水平z的t值": float(fb["t"][-1]),
                "差分Δz的t值": float(fc["t"][-1]),
            })
    return pd.DataFrame(rows)


def overlay_backtest(daily: pd.DataFrame, prices: dict, cost_bps: float = 10.0) -> tuple:
    """把舆情当风控开关，套在一个最朴素的动量策略上比较绩效（严格无前视）。

    cost_bps：单边交易成本（手续费 + 滑点，默认 10 个基点）。
    本函数只做“有没有经济意义”的快速比较，真正的组合回测请用主程序。
    """
    cost = float(cost_bps) / 10000.0
    frames = {}
    for asset, px in prices.items():
        g = daily[daily["asset"] == asset].set_index("date").sort_index()
        mom = (px / px.shift(20) - 1.0)
        df = pd.DataFrame({"ret": px.pct_change(), "mom": mom})
        df = df.join(g[["z", "coverage"]], how="left")
        df["z"] = df["z"].fillna(0.0)
        df["coverage"] = df["coverage"].fillna(0.0)
        pos = (df["mom"].shift(1) > 0).astype(float)
        mult = [core.sentiment_multiplier(z, c)
                for z, c in zip(df["z"].shift(1), df["coverage"].shift(1))]
        expo_price = pos
        expo_sent = pos * mult
        df["pos"] = expo_price
        df["mult"] = mult
        df["ret_price"] = expo_price * df["ret"] - expo_price.diff().abs().fillna(0.0) * cost
        df["ret_sent"] = expo_sent * df["ret"] - expo_sent.diff().abs().fillna(0.0) * cost
        frames[asset] = df
    idx = None
    for df in frames.values():
        idx = df.index if idx is None else idx.intersection(df.index)
    port = pd.DataFrame({
        "仅价格": np.mean([frames[a].loc[idx, "ret_price"].to_numpy() for a in frames], axis=0),
        "价格+舆情": np.mean([frames[a].loc[idx, "ret_sent"].to_numpy() for a in frames], axis=0),
    }, index=idx).dropna()
    stats = pd.DataFrame([perf_stats(port["仅价格"], "仅价格(动量20)"),
                          perf_stats(port["价格+舆情"], "价格+舆情风控")])
    return stats, port


# =====================================================================
# 七、[5] 权重方案对照
# =====================================================================

def calibrate_source_weights(items: pd.DataFrame, prices: dict, horizon: int = 5) -> dict:
    """样本内校准：用各来源自己的日频分数解释未来收益，取拟合系数当权重。

    这是「样本内」拟合，天然有过拟合风险，只能当对照组，绝不能当「最优权重」。
    """
    table = factor_dataset(build_daily(items), prices, horizon)
    if table.empty:
        return dict(cfg.SOURCE_WEIGHTS)
    base = table[["date", "asset", "fwd_ret"]].copy()
    cols = []
    for source in cfg.SOURCE_WEIGHTS:
        sub = items[items["source"] == source]
        if sub.empty:
            cols.append(np.zeros(len(base)))
            continue
        series = build_daily(sub).set_index(["date", "asset"])["z"]
        key = pd.MultiIndex.from_arrays([base["date"], base["asset"]])
        cols.append(series.reindex(key).fillna(0.0).to_numpy(dtype=float))
    X = np.column_stack([np.ones(len(base))] + cols)
    fit = ols_full(base["fwd_ret"].to_numpy(dtype=float), X)
    if fit is None:
        return dict(cfg.SOURCE_WEIGHTS)
    raw = np.clip(fit["coef"][1:], 0.0, None)
    if raw.sum() <= 1e-12:
        return dict(cfg.SOURCE_WEIGHTS)
    scaled = raw / raw.sum() * float(sum(cfg.SOURCE_WEIGHTS.values()))
    return {source: float(scaled[i]) for i, source in enumerate(cfg.SOURCE_WEIGHTS)}


def weight_scheme_comparison(items: pd.DataFrame, prices: dict, horizons=None,
                             quick: bool = False) -> tuple:
    horizons = horizons or [1, 5, 20]
    schemes = ["prior", "equal", "entropy"]
    calibrated = None
    if not quick:
        calibrated = calibrate_source_weights(items, prices)
        schemes.append("calibrated")
    rows = []
    for scheme in schemes:
        if scheme == "calibrated":
            daily = build_daily(items, scheme="prior", source_weights=calibrated)
        else:
            daily = build_daily(items, scheme=scheme)
        ic = information_coefficient(daily, prices, horizons)
        pooled = ic[ic["资产"] == "ALL"]
        if pooled.empty:
            continue
        rows.append({
            "权重方案": scheme,
            "平均RankIC(水平)": float(pooled["RankIC_水平z"].mean()),
            "平均RankIC(差分)": float(pooled["RankIC_差分Δz"].mean()),
            "非重叠RankIC@5(水平)": float(pooled.loc[pooled["持有期(日)"] == 5, "非重叠RankIC_水平"].mean()),
            "非重叠RankIC@20(水平)": float(pooled.loc[pooled["持有期(日)"] == 20, "非重叠RankIC_水平"].mean()),
        })
    return pd.DataFrame(rows), calibrated


# =====================================================================
# 八、[6] 敏感性分析（一次只动一个参数）
# =====================================================================

def sensitivity_analysis(items: pd.DataFrame, prices: dict, assets=None,
                         horizon: int = 5) -> pd.DataFrame:
    base_kappa, base_tau = float(cfg.KAPPA), float(cfg.TAU)
    base_items = items
    if assets:
        base_items = items[items["asset"].isin(list(assets))].copy()
    variants = [
        ("基准", "none", 1.0, "——"),
        ("证据收缩 κ ×0.67", "kappa", 0.67, "更相信少量证据"),
        ("证据收缩 κ ×1.50", "kappa", 1.50, "更保守，分数更靠近 0"),
        ("映射尺度 τ ×0.67", "tau", 0.67, "z 更极端"),
        ("映射尺度 τ ×1.50", "tau", 1.50, "z 更平缓"),
        ("半衰期 ×0.50", "halflife", 0.50, "信息过期更快"),
        ("半衰期 ×1.50", "halflife", 1.50, "信息过期更慢"),
        ("聚类相似度 0.60", "cluster", 0.60, "事件合并更宽松"),
        ("聚类相似度 0.85", "cluster", 0.85, "事件合并更严格"),
    ]
    base_cluster = float(cfg.CLUSTER_SIMILARITY)
    rows = []
    for label, kind, mult, note in variants:
        cfg.KAPPA, cfg.TAU = base_kappa, base_tau
        cfg.CLUSTER_SIMILARITY = base_cluster
        work = base_items
        if kind == "kappa":
            cfg.KAPPA = base_kappa * mult
        elif kind == "tau":
            cfg.TAU = base_tau * mult
        elif kind == "cluster":
            cfg.CLUSTER_SIMILARITY = float(mult)
        elif kind == "halflife":
            work = base_items.copy()
            work["half_life"] = work["half_life"] * float(mult)
        try:
            daily = build_daily(work)
            ic = information_coefficient(daily, prices)
            pooled = ic[ic["资产"] == "ALL"]
            cov = factor_dataset(daily, prices, horizon)
            lay = layered_backtest(daily, prices, horizon=horizon)
            spread = float("nan")
            if not lay.empty and (lay["资产"] == "ALL").any():
                spread = float(lay.loc[lay["资产"] == "ALL", "多空差(顶-底)"].iloc[0])
            rows.append({
                "参数变化": label, "说明": note,
                "平均RankIC(水平)": float(pooled["RankIC_水平z"].mean()) if not pooled.empty else float("nan"),
                "平均RankIC(差分)": float(pooled["RankIC_差分Δz"].mean()) if not pooled.empty else float("nan"),
                "5日多空差": spread,
                "平均覆盖率": float(cov["coverage"].mean()) if not cov.empty else float("nan"),
            })
        except Exception as exc:  # pragma: no cover
            rows.append({"参数变化": label, "说明": note, "平均RankIC(水平)": float("nan"),
                         "平均RankIC(差分)": float("nan"), "5日多空差": float("nan"),
                         "平均覆盖率": float("nan")})
        finally:
            cfg.KAPPA, cfg.TAU = base_kappa, base_tau
            cfg.CLUSTER_SIMILARITY = base_cluster
    return pd.DataFrame(rows)


# =====================================================================
# 九、[8] 选择偏差检验
# =====================================================================

def selection_bias_demo(items: pd.DataFrame, prices: dict, horizons=None) -> pd.DataFrame:
    """对比：完整示例数据 vs 去掉「事后挑选的真实大事件」。

    如果去掉这些事件后 IC 明显下降，说明原来那点预测力主要来自
    「我先知道后来涨了，才把这条新闻写进数据集」—— 这在论文里叫选择偏差 / 前视偏差。
    """
    horizons = horizons or [1, 5, 20]
    synth, anchor = split_by_tag(items)
    if anchor.empty:
        return pd.DataFrame()
    rows = []
    for label, sub in [("完整示例数据", items), ("去掉事后挑选事件", synth)]:
        daily = build_daily(sub)
        ic = information_coefficient(daily, prices, horizons)
        pooled = ic[ic["资产"] == "ALL"]
        for _, r in pooled.iterrows():
            rows.append({"数据集": label, "持有期(日)": int(r["持有期(日)"]),
                         "RankIC(水平)": r["RankIC_水平z"], "RankIC(差分)": r["RankIC_差分Δz"],
                         "非重叠RankIC(水平)": r["非重叠RankIC_水平"],
                         "非重叠样本数": int(r["非重叠样本数"])})
    return pd.DataFrame(rows)


# =====================================================================
# 十、落盘与报告
# =====================================================================

def save_csv(frame: pd.DataFrame, name: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / name
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def run_analysis(csv_path=None, quick: bool = False) -> dict:
    items, is_sample = load_items(csv_path)
    all_prices = load_prices()
    assets = sorted(set(items["asset"].unique()) & set(all_prices))
    if not assets:
        raise SystemExit("舆情数据里的资产，在 data 目录下找不到对应的价格 CSV。")
    prices = {a: all_prices[a] for a in assets}
    items = items[items["asset"].isin(assets)].copy()

    print(f"  舆情条目 {len(items)} 条 · 资产 {', '.join(assets)} · "
          f"时间 {items['t_use'].min().date()} → {items['t_use'].max().date()}")

    t0 = time.time()
    daily = build_daily(items)
    print(f"  [1/6] 日频舆情表完成（{len(daily)} 行，{time.time()-t0:.1f}s）")
    ic = information_coefficient(daily, prices)
    print(f"  [2/6] 信息系数完成")
    layer = pd.concat([layered_backtest(daily, prices, 5, factor="z"),
                       layered_backtest(daily, prices, 20, factor="z"),
                       layered_backtest(daily, prices, 5, factor="dz")],
                      ignore_index=True)
    event, event_detail, event_info = event_study(daily, prices)
    print(f"  [3/6] 分层回测 / 事件研究完成（事件 {event_info['事件总数']} 个）")
    incremental = incremental_test(daily, prices)
    perf, portfolio = overlay_backtest(daily, prices)
    print(f"  [4/6] 增量检验 / 经济意义完成")
    weights, weight_table = weight_scheme_comparison(items, prices, quick=quick)
    print(f"  [5/6] 权重方案对照完成")
    sens_assets = ["BTC"] if (quick and "BTC" in assets) else assets
    sensitivity = sensitivity_analysis(items, prices, assets=sens_assets)
    bias = selection_bias_demo(items, prices) if (is_sample and not quick) else pd.DataFrame()
    print(f"  [6/6] 敏感性分析完成（总用时 {time.time()-t0:.1f}s）")

    return {"is_sample": is_sample, "assets": assets, "items": items, "prices": prices,
            "daily": daily, "ic": ic, "layer": layer, "event": event,
            "event_detail": event_detail, "event_info": event_info,
            "incremental": incremental, "weights": weights, "weight_table": weight_table,
            "sensitivity": sensitivity, "bias": bias, "perf": perf, "portfolio": portfolio,
            "quick": quick}


def _layer_wide(layer: pd.DataFrame) -> pd.DataFrame:
    if layer.empty:
        return pd.DataFrame()
    wide = layer.pivot_table(index=["资产", "因子"], columns="layer", values="平均未来收益")
    extra = layer.groupby(["资产", "因子"]).agg(**{
        "顶-底": ("多空差(顶-底)", "first"), "单调性": ("单调性", "first")})
    return wide.join(extra)


def print_report(r: dict) -> list:
    lines = []
    add = lines.append
    add(hrule())
    add("  淮策 Quant · 舆情因子验证报告")
    add(hrule())
    tag = "示例数据（教学用，不能当作论文证据）" if r["is_sample"] else "自定义数据"
    add(f"  数据来源   ：{tag}")
    add(f"  舆情条目   ：{len(r['items'])} 条")
    add(f"  资产       ：{', '.join(r['assets'])}")
    add(f"  价格文件   ：data/*_usdt.csv（本地 CSV，未联网）")
    add(f"  覆盖率阈值 ：{cfg.COVERAGE_MIN}")
    add(hrule("-"))
    add("  [1] 信息系数 IC / RankIC（只统计覆盖率达标的日子）")
    add(f"      {'资产':<6}{'期':>4}{'非重叠n':>8}{'RankIC(水平)':>13}{'RankIC(Δz)':>12}"
        f"{'RankIC(去趋势)':>14}{'非重叠RankIC':>13}{'t值':>8}  ")
    for _, row in r["ic"].iterrows():
        add(f"      {str(row['资产']):<6}{int(row['持有期(日)']):>4}{int(row['非重叠样本数']):>8}"
            f"{fmt(row['RankIC_水平z']):>13}{fmt(row['RankIC_差分Δz']):>12}"
            f"{fmt(row['RankIC_去趋势z']):>14}{fmt(row['非重叠RankIC_水平']):>13}"
            f"{fmt(row['t值'], 2):>8}  {row['显著性']}")
    add("      ★ 读法：若「RankIC(水平)」明显为正、而「RankIC(Δz)」接近 0，")
    add("        说明那点预测力来自「分数慢漂移 × 价格趋势」的虚假相关，不是真信息。")
    add("      ★ t 值用「非重叠样本」计算，避免 5/20 日收益窗口重叠把 t 值吹大。")
    add(hrule("-"))
    add("  [2] 分层回测（按因子分 5 等分，看未来收益是否单调）")
    wide = _layer_wide(r["layer"])
    if not wide.empty:
        add(f"      {'资产':<6}{'因子':<8}{'L1':>9}{'L2':>9}{'L3':>9}{'L4':>9}{'L5':>9}"
            f"{'顶-底':>10}{'单调性':>9}")
        for (asset, factor), row in wide.iterrows():
            cells = "".join(f"{float(row.get(f'L{i}', float('nan')))*100:>8.2f}%"
                            for i in range(1, 6))
            add(f"      {str(asset):<6}{str(factor):<8}{cells}"
                f"{float(row['顶-底'])*100:>9.2f}%{fmt(row['单调性'], 2):>9}")
        add("      注：单看一层数字意义不大，要看 L1→L5 是否单调、以及顶-底差是否为正。")
    else:
        add("      样本不足，跳过。")
    add(hrule("-"))
    add("  [3] 事件研究：重大情绪事件前后的累计异常收益 CAR（%）")
    add(f"      事件阈值：{r['event_info']['阈值说明']}，采用 |z| ≥ {r['event_info']['阈值']:.3f}"
        f"，共 {r['event_info']['事件总数']} 个事件")
    if not r["event"].empty:
        pivot = r["event"].pivot_table(index="相对日", columns="分组", values="平均CAR")
        cols = [c for c in ["全部", "正面", "负面"] if c in pivot.columns]
        add("      相对日  " + "".join(f"{c:>12}" for c in cols))
        for offset, row in pivot.iterrows():
            add(f"      {int(offset):>5}  " + "".join(f"{float(row[c])*100:>11.3f}%" for c in cols))
        add("      注：CAR 为「当日收益 - 过去 60 日平均收益」的累计和，已做简单漂移调整。")
        if 0 in pivot.index and 5 in pivot.index:
            parts = []
            for c in ("正面", "负面"):
                if c in pivot.columns:
                    parts.append(f"{c} {float(pivot.loc[5, c] - pivot.loc[0, c]) * 100:+.3f}%")
            if parts:
                add("      ★ 事件后 5 日漂移（相对事件日）：" + "，".join(parts)
                    + "  —— 负值说明事件后价格继续朝同一方向走（漂移/动量）。")
    else:
        add("      没有可用事件，跳过。")
    add(hrule("-"))
    add("  [4] 增量检验：只用价格 vs 价格 + 舆情（按时间 7:3 切分训练/测试）")
    if not r["incremental"].empty:
        add(f"      {'资产':<6}{'期':>4}{'样本内ΔR2(水平)':>17}{'样本内ΔR2(Δz)':>16}"
            f"{'样本外ΔR2(水平)':>17}{'样本外ΔR2(Δz)':>16}{'z的t值':>9}")
        for _, row in r["incremental"].iterrows():
            add(f"      {str(row['资产']):<6}{int(row['持有期(日)']):>4}"
                f"{fmt(row['样本内ΔR2(+水平z)'], 5):>17}{fmt(row['样本内ΔR2(+差分Δz)'], 5):>16}"
                f"{fmt(row['样本外ΔR2(+水平z)'], 5):>17}{fmt(row['样本外ΔR2(+差分Δz)'], 5):>16}"
                f"{fmt(row['水平z的t值'], 2):>9}")
        add("      ★ 只有「样本外 ΔR2 > 0」才叫真的增量信息；样本内正、样本外负 = 过拟合。")
    else:
        add("      样本不足，跳过。")
    add(hrule("-"))
    add("  [5] 权重方案对照")
    if not r["weights"].empty:
        add(f"      {'方案':<12}{'平均RankIC(水平)':>17}{'平均RankIC(差分)':>17}"
            f"{'非重叠@5':>11}{'非重叠@20':>11}")
        for _, row in r["weights"].iterrows():
            add(f"      {str(row['权重方案']):<12}{fmt(row['平均RankIC(水平)']):>17}"
                f"{fmt(row['平均RankIC(差分)']):>17}{fmt(row['非重叠RankIC@5(水平)']):>11}"
                f"{fmt(row['非重叠RankIC@20(水平)']):>11}")
        add("      ★ prior=主观先验，equal=等权，entropy=熵权（只看数据差异、不知道谁说得准），")
        add("        calibrated=样本内校准（天然过拟合，只能当对照组）。")
        add("      ★ 如果校准把最大的权重给了最低可信度的来源（比如群聊），说明它在拟合噪声。")
    if r["weight_table"]:
        add("      校准后来源权重：" + "  ".join(
            f"{cfg.SOURCE_LABELS.get(k, k)}={v:.2f}" for k, v in r["weight_table"].items()))
    add(hrule("-"))
    add("  [6] 敏感性分析（一次只动一个参数，看结论是否稳定）")
    if not r["sensitivity"].empty:
        add(f"      {'参数变化':<18}{'平均RankIC(水平)':>17}{'平均RankIC(差分)':>17}"
            f"{'5日多空差':>11}{'覆盖率':>9}")
        for _, row in r["sensitivity"].iterrows():
            add(f"      {str(row['参数变化']):<18}{fmt(row['平均RankIC(水平)']):>17}"
                f"{fmt(row['平均RankIC(差分)']):>17}{fmt(row['5日多空差']):>11}"
                f"{fmt(row['平均覆盖率'], 3):>9}")
        add("      ★ 结论必须对参数不敏感：上下浮动后 RankIC 的符号和量级应基本不变。")
    add(hrule("-"))
    add("  [7] 经济意义检验：把舆情当风控开关（无前视，仓位用昨日信息）")
    if not r["perf"].empty:
        add(f"      {'策略':<16}{'天数':>6}{'累计收益':>12}{'年化收益':>12}{'年化波动':>12}"
            f"{'夏普':>9}{'最大回撤':>11}")
        for _, row in r["perf"].iterrows():
            add(f"      {str(row['策略']):<16}{int(row['天数']):>6}{float(row['累计收益'])*100:>11.1f}%"
                f"{float(row['年化收益'])*100:>11.1f}%{float(row['年化波动'])*100:>11.1f}%"
                f"{float(row['夏普']):>9.2f}{float(row['最大回撤'])*100:>10.1f}%")
        add("      ★ 风控开关的目标是「降回撤」，不是「提高收益」。收益略降、回撤明显变小，就算成功。")
    if not r["bias"].empty:
        add(hrule("-"))
        add("  [8] 选择偏差检验：事后挑选的「大事件」把 IC 抬高了多少")
        add(f"      {'数据集':<18}{'期':>4}{'RankIC(水平)':>13}{'RankIC(Δz)':>12}"
            f"{'非重叠RankIC':>13}{'非重叠n':>9}")
        for _, row in r["bias"].iterrows():
            add(f"      {str(row['数据集']):<18}{int(row['持有期(日)']):>4}"
                f"{fmt(row['RankIC(水平)']):>13}{fmt(row['RankIC(差分)']):>12}"
                f"{fmt(row['非重叠RankIC(水平)']):>13}{int(row['非重叠样本数']):>9}")
        add("      ★ 两行差得越多，说明「事后才知道该收哪条新闻」的影响越大。")
    add(hrule("-"))
    add("  [9] 小结")
    if not r["ic"].empty:
        pooled = r["ic"][r["ic"]["资产"] == "ALL"]
        for _, row in pooled.iterrows():
            v = row["非重叠RankIC_水平"]
            strength = "几乎无预测力"
            if abs(v) >= 0.05:
                strength = "有弱预测力"
            if abs(v) >= 0.10:
                strength = "有明显预测力（需警惕虚假相关）"
            dv = row["RankIC_差分Δz"]
            verdict = "差分后仍为正 → 可能是真信息" if dv > 0.01 else "差分后接近 0 或为负 → 大概率为虚假相关"
            add(f"      持有 {int(row['持有期(日)'])} 日：非重叠RankIC(水平) = {fmt(v, 4)} → {strength}"
                f"｜RankIC(Δz) = {fmt(dv, 4)} → {verdict}")
    if r["is_sample"]:
        add(hrule("-"))
        add("  【注意】 你现在跑的是「教学示例数据」，不是真实舆情档案：")
        add("    · 合成条目与真实价格走势无关，所以差分后的 IC 接近 0 是正常且诚实的；")
        add("    · 那 60 多条真实事件是「事后挑选」的，本身带前视偏差，不能当证据；")
        add("    · 想得到能写进论文的结论，请把 CSV 换成你自己合规采集的数据。")
    add(hrule())
    return lines


def save_report_txt(lines: list) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "验证结果摘要.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="淮策 Quant · 舆情因子验证（纯离线）")
    parser.add_argument("--csv", type=str, default="", help="自定义舆情 CSV；默认用示例数据")
    parser.add_argument("--quick", action="store_true", help="快速模式：跳过样本内校准，敏感性只跑 BTC")
    args = parser.parse_args()
    csv_path = Path(args.csv) if args.csv else None
    started = datetime.now()
    print(hrule())
    print("  淮策 Quant · 舆情因子验证（不联网、不下单、只做统计）")
    print(hrule())
    result = run_analysis(csv_path, quick=args.quick)
    lines = print_report(result)
    print("\n".join(lines))
    saved = [
        save_csv(result["daily"], "日频舆情分数.csv"),
        save_csv(result["ic"], "检验1_信息系数IC.csv"),
        save_csv(result["ic"][result["ic"]["资产"] == "ALL"].copy(), "检验1b_IC汇总.csv"),
        save_csv(result["layer"], "检验2_分层回测.csv"),
        save_csv(result["event"], "检验3_事件研究CAR.csv"),
        save_csv(result["event_detail"], "检验3b_事件明细.csv"),
        save_csv(result["incremental"], "检验4_增量检验.csv"),
        save_csv(result["weights"], "检验5_权重方案对照.csv"),
        save_csv(result["sensitivity"], "检验6_敏感性分析.csv"),
        save_csv(result["perf"], "检验7_经济意义.csv"),
        save_csv(result["portfolio"], "检验7b_组合日收益.csv"),
    ]
    if not result["bias"].empty:
        saved.append(save_csv(result["bias"], "检验8_选择偏差.csv"))
    txt = save_report_txt(lines)
    print()
    print("  结果已写入：" + str(OUTPUT_DIR))
    for p in saved:
        print("     · " + p.name)
    print("     · " + txt.name)
    print(f"  用时：{(datetime.now() - started).total_seconds():.1f} 秒")
    print(hrule())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())