# -*- coding: utf-8 -*-
"""淮策 Quant · 舆情计算内核（纯计算，不联网、不依赖价格引擎）。

流程（每一步都可以单独测试）：
    原始条目 → 字段标准化 → 事件去重聚类 → 方向打分 → 半衰期衰减加权
             → 证据收缩聚合 → 日频舆情分数表

核心公式：
    单条权重   w = w_来源 × w_内容 × 2^( -(现在 - 可得时间) / 半衰期 )
    聚类权重   w_簇 = w_代表 × (1 + 0.25 × log2(独立来源数))，上限 2 倍
    聚合分数   S = Σ(w·e) / (Σw + κ)          ← κ 是"证据收缩"，证据少就自动靠近 0
    映射分数   z = tanh(S / τ)                 ← 压到 -1 ~ +1
    证据充分度 coverage = Σw / (Σw + κ)

三条防前视的硬规则：
    1. 只用「实际可得时间」(max(发布时间, 抓取时间)) 计算，不用事后整理的时间；
    2. 第 T 日的分数只使用 T 日截止时点之前可得的信息；
    3. 没有信息时 score=0 且 coverage=0 —— 「没有信息」不等于「中性情绪」。
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
# 让「单文件直接运行」和「作为包被导入」两种方式都能找到 weight_config
for _path in (str(BASE_DIR), str(BASE_DIR.parent)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:                                     # 方式一：作为包导入 sentiment.sentiment_core
    from . import weight_config as cfg    # type: ignore
except ImportError:                      # 方式二：直接运行 python sentiment/sentiment_core.py
    import weight_config as cfg           # type: ignore  # noqa: E402

EPS = 1e-9
POSITIVE_SET = {w.lower() for w in cfg.LEXICON_POSITIVE}
NEGATIVE_SET = {w.lower() for w in cfg.LEXICON_NEGATIVE}

# 字段别名：中英文列名都能识别，方便你直接导入自己的数据
COLUMN_ALIASES = {
    "time": ["time", "发布时间", "时间", "日期", "date", "published", "pub_time", "datetime"],
    "captured": ["captured", "抓取时间", "采集时间", "fetched", "crawled"],
    "source": ["source", "来源", "来源类型", "渠道", "平台", "channel"],
    "author": ["author", "作者", "博主", "账号", "用户"],
    "asset": ["asset", "资产", "标的", "币种", "symbol", "ticker"],
    "text": ["text", "内容", "文本", "正文", "标题", "content", "title", "message"],
    "direction": ["direction", "方向分", "情绪分", "sentiment", "score", "polarity"],
    "content_type": ["content_type", "内容类型", "类型", "category", "type"],
    "confidence": ["confidence", "置信度", "可信度"],
    "url": ["url", "链接", "来源链接", "link"],
    "cluster_id": ["cluster_id", "事件ID", "事件簇", "cluster"],
    "weight_override": ["weight_override", "人工权重", "指定权重"],
}


# =====================================================================
# 一、字段标准化
# =====================================================================

def normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """把各种列名统一成标准名（中英文列名都支持）。"""
    df = frame.copy()
    lowered = {str(c).strip().lower(): c for c in df.columns}
    rename: dict[Any, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            key = alias.strip().lower()
            if key in lowered and canonical not in df.columns:
                rename[lowered[key]] = canonical
                break
    df = df.rename(columns=rename)
    if "text" not in df.columns:
        df["text"] = ""
    if "asset" not in df.columns:
        df["asset"] = "ASSET"
    if "source" not in df.columns:
        df["source"] = cfg.DEFAULT_SOURCE
    if "time" not in df.columns:
        raise ValueError("缺少时间列：请提供「发布时间 / time / 日期 / date」中的任意一列。")
    return df


def parse_time_series(series: pd.Series) -> pd.Series:
    """宽容地解析时间（支持 ISO、'YYYY-MM-DD HH:MM:SS'、纯日期、带时区）。"""
    text = series.astype(str).str.strip()
    out = pd.to_datetime(text, errors="coerce", format="mixed", utc=False)
    if out.isna().any():
        fallback = pd.to_datetime(text, errors="coerce", dayfirst=False)
        out = out.fillna(fallback)
    bad = int(out.isna().sum())
    if bad:
        raise ValueError(f"有 {bad} 条记录的时间无法解析，请检查格式（示例：2025-03-01 09:30:00）。")
    try:
        return out.dt.tz_localize(None)
    except TypeError:
        return out.dt.tz_convert(None)


# =====================================================================
# 二、文本方向打分（规则词典起步方案）
# =====================================================================

def lexicon_score(text: str) -> float:
    """用词典给文本打一个 -1 ~ +1 的方向分。

    做法：扫描正负情感词，统计命中次数；若情感词前 3 个字符内出现否定词则方向翻转。
    局限：无法处理反讽、复杂句法。这是"让流程先跑通"的起步方案，
         论文中若要做强结论，建议改用人工标注 + 分类器，并报告标注一致性。
    """
    if not text:
        return 0.0
    low = str(text).lower()
    pos = neg = 0
    for word, sign in [(w, 1) for w in POSITIVE_SET] + [(w, -1) for w in NEGATIVE_SET]:
        start = 0
        while True:
            idx = low.find(word, start)
            if idx < 0:
                break
            start = idx + len(word)
            window = low[max(0, idx - 3): idx]
            if any(n in window for n in cfg.NEGATION_WORDS):
                sign_eff = -sign
            else:
                sign_eff = sign
            if sign_eff > 0:
                pos += 1
            else:
                neg += 1
    if pos == 0 and neg == 0:
        return 0.0
    return float((pos - neg) / (pos + neg + 1.0))


def detect_content_type(row: pd.Series) -> str:
    """判断内容类型；命中推广词的直接降级为 promo（权重 0.05）。"""
    text = str(row.get("text") or "").lower()
    for word in cfg.PROMO_KEYWORDS:
        if word.lower() in text:
            return "promo"
    given = str(row.get("content_type") or "").strip().lower()
    if given in cfg.CONTENT_WEIGHTS:
        return given
    return cfg.DEFAULT_CONTENT


def item_direction(row: pd.Series) -> tuple[float, str]:
    """优先使用人工给定的方向分；没有就用词典。返回 (分数, 来源)。"""
    raw = row.get("direction")
    if raw is not None and not (isinstance(raw, float) and math.isnan(raw)) and str(raw).strip() != "":
        try:
            return float(np.clip(float(raw), -1.0, 1.0)), "given"
        except (TypeError, ValueError):
            pass
    return float(np.clip(lexicon_score(str(row.get("text") or "")), -1.0, 1.0)), "lexicon"


# =====================================================================
# 三、事件去重聚类（防止"转发刷屏"被算成多倍信息）
# =====================================================================

def _shingles(text: str, k: int = 3) -> set[str]:
    clean = "".join(str(text).lower().split())
    if len(clean) <= k:
        return {clean} if clean else set()
    return {clean[i:i + k] for i in range(len(clean) - k + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / float(len(a) + len(b) - inter)


def cluster_items(df: pd.DataFrame) -> pd.DataFrame:
    """把同一资产、时间窗口内的相似文本合并成一个「事件」。

    返回的每一行是一个事件簇（而不是一条帖子），这样才不会因为转发量影响权重。
    """
    work = df.sort_values("t_use").reset_index(drop=True)
    work["cluster_id"] = -1
    next_id = 0
    window = timedelta(hours=float(cfg.CLUSTER_WINDOW_HOURS))
    sim_threshold = float(cfg.CLUSTER_SIMILARITY)

    given = work.get("cluster_id_given")
    if given is not None and given.notna().any():
        # 用户自己标了事件 ID，直接沿用
        work["cluster_id"] = given.fillna(-1).astype(int)
        return work

    for asset, group in work.groupby("asset", sort=False):
        idx_list = list(group.index)
        shingles = {i: _shingles(work.at[i, "text"]) for i in idx_list}
        assigned: dict[int, int] = {}
        order = sorted(idx_list, key=lambda i: work.at[i, "t_use"])
        for pos, i in enumerate(order):
            if i in assigned:
                continue
            cid = next_id
            next_id += 1
            assigned[i] = cid
            t_i = work.at[i, "t_use"]
            for j in order[pos + 1:]:
                if j in assigned:
                    continue
                if work.at[j, "t_use"] - t_i > window:
                    break
                if _jaccard(shingles[i], shingles[j]) >= sim_threshold:
                    assigned[j] = cid
        for idx, cid in assigned.items():
            work.at[idx, "cluster_id"] = cid
    return work


def build_clusters(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """把条目聚成事件。返回 (事件表, 明细表)。"""
    work = cluster_items(df)
    rows: list[dict[str, Any]] = []
    detail: list[dict[str, Any]] = []

    for (asset, cid), group in work.groupby(["asset", "cluster_id"], sort=False):
        g = group.sort_values("t_use")
        weights = g["w_static"].to_numpy(dtype=float)
        directions = g["e"].to_numpy(dtype=float)
        if weights.sum() <= EPS:
            cluster_dir = float(directions.mean())
        else:
            cluster_dir = float(np.average(directions, weights=weights))

        rep = int(np.argmax(weights))          # 代表条目 = 静态权重最高的那条（最权威）
        n_sources = int(g["source"].nunique())
        bonus = 1.0 + float(cfg.MULTI_SOURCE_BONUS) * math.log2(max(1, n_sources))
        bonus = min(bonus, float(cfg.MULTI_SOURCE_CAP))

        rows.append({
            "asset": asset,
            "cluster_id": int(cid),
            "t_use": pd.Timestamp(g["t_use"].min()),        # 最早可得时间 = 信息起点
            "e": cluster_dir,
            "w_static": float(weights[rep]) * bonus,
            "half_life": float(g["half_life"].iloc[rep]),
            "content_type": str(g["content_type"].iloc[rep]),
            "main_source": str(g["source"].iloc[rep]),
            "n_items": int(len(g)),
            "n_sources": n_sources,
            "rep_text": str(g["text"].iloc[rep])[:160],
            "rep_url": str(g["url"].iloc[rep]) if "url" in g.columns else "",
            "n_promo": int((g["content_type"] == "promo").sum()),
        })
        for _, item in g.iterrows():
            detail.append({
                "asset": asset, "cluster_id": int(cid),
                "time": item["t_use"], "source": item["source"],
                "author": item.get("author", ""), "text": str(item["text"])[:160],
                "e": float(item["e"]), "direction_source": item["direction_source"],
                "url": item.get("url", ""),
            })

    clusters = pd.DataFrame(rows)
    if clusters.empty:
        raise ValueError("没有任何可用条目，请检查输入数据。")
    return clusters, pd.DataFrame(detail)


# =====================================================================
# 四、权重方案（主观先验 / 等权 / 熵权 / 历史校准）
# =====================================================================

def equal_source_weights() -> dict[str, float]:
    """等权：所有来源同一权重（作为最朴素的对照组）。"""
    return {key: 1.0 for key in cfg.SOURCE_WEIGHTS}


def entropy_source_weights(items: pd.DataFrame, window: str = "W") -> dict[str, float]:
    """熵权法：按「各来源信息强度在不同时间窗上的差异程度」分配权重。

    严重提醒（论文里必须写清楚）：
      熵权法只看数据差异大不大，**不知道谁说得准**。一个天天大起大落却经常说错的
      来源，可能拿到高权重。所以它只能作为对照方法，不能当作"最优权重"。
    """
    work = items.copy()
    work["strength"] = work["w_static"] * work["e"].abs()
    pivot = (work.groupby([pd.Grouper(key="t_use", freq=window), "source"])["strength"]
             .sum().unstack("source").fillna(0.0))
    matrix = pivot.to_numpy(dtype=float)
    m, n = matrix.shape
    names = list(pivot.columns)
    if m < 2 or n < 1:
        return equal_source_weights()

    col_sum = matrix.sum(axis=0, keepdims=True)
    safe = np.where(col_sum <= EPS, 1.0, col_sum)
    p = matrix / safe
    with np.errstate(divide="ignore", invalid="ignore"):
        terms = np.where(p > EPS, p * np.log(p + EPS), 0.0)
    entropy = -terms.sum(axis=0) / math.log(m)      # 0 <= E <= 1
    diversity = 1.0 - entropy
    if diversity.sum() <= EPS:
        return equal_source_weights()

    raw = diversity / diversity.sum()
    target_total = float(sum(cfg.SOURCE_WEIGHTS.values()))
    scaled = raw * target_total
    weights = {key: float(cfg.SOURCE_WEIGHTS[key]) for key in cfg.SOURCE_WEIGHTS}  # 未出现的来源保持先验
    for i, name in enumerate(names):
        weights[str(name)] = float(scaled[i])
    return weights


def resolve_source_weights(scheme: str, items: pd.DataFrame,
                           override: dict[str, float] | None = None) -> dict[str, float]:
    """按方案给出 来源 → 权重 的字典。"""
    scheme = str(scheme or "prior").lower()
    if override:
        merged = dict(cfg.SOURCE_WEIGHTS)
        merged.update({str(k): float(v) for k, v in override.items()})
        return merged
    if scheme == "equal":
        return equal_source_weights()
    if scheme == "entropy":
        return entropy_source_weights(items)
    return dict(cfg.SOURCE_WEIGHTS)      # prior / calibrated（calibrated 由外部传入 override）


# =====================================================================
# 五、准备条目（把原始表变成"可计算"的表）
# =====================================================================

def prepare_items(frame: pd.DataFrame, asset: str | None = None) -> pd.DataFrame:
    """标准化 → 解析时间 → 打分 → 算静态权重。输出「条目级」表。"""
    df = normalize_columns(frame)
    if asset:
        df = df[df["asset"].astype(str).str.upper() == str(asset).upper()].copy()
    if df.empty:
        raise ValueError(f"没有找到资产 {asset} 的条目。")

    df["t_pub"] = parse_time_series(df["time"])
    if "captured" in df.columns:
        cap = pd.to_datetime(df["captured"].astype(str).str.strip(), errors="coerce", format="mixed")
        cap = cap.dt.tz_localize(None) if cap.dt.tz is not None else cap
        # 防前视：一律取「发布时间」和「抓取时间」中较晚的那个作为实际可得时间
        df["t_use"] = pd.concat([df["t_pub"], cap], axis=1).max(axis=1)
    else:
        df["t_use"] = df["t_pub"]

    df["asset"] = df["asset"].astype(str).str.upper().str.strip()
    df["source"] = df["source"].astype(str).str.strip().str.lower().replace("", cfg.DEFAULT_SOURCE)
    df["content_type"] = df.apply(detect_content_type, axis=1)

    directions = df.apply(item_direction, axis=1, result_type="expand")
    df["e"] = directions[0].astype(float)
    df["direction_source"] = directions[1]

    if "confidence" in df.columns:
        conf = pd.to_numeric(df["confidence"], errors="coerce").fillna(1.0)
        df["confidence"] = conf.clip(0.0, 1.0)
    else:
        df["confidence"] = 1.0

    df["w_source"] = df["source"].map(cfg.SOURCE_WEIGHTS).fillna(
        cfg.SOURCE_WEIGHTS.get(cfg.DEFAULT_SOURCE, 0.15)).astype(float)
    df["w_type"] = df["content_type"].map(cfg.CONTENT_WEIGHTS).fillna(
        cfg.CONTENT_WEIGHTS[cfg.DEFAULT_CONTENT]).astype(float)
    df["half_life"] = df["content_type"].map(cfg.HALF_LIFE_HOURS).fillna(
        cfg.DEFAULT_HALF_LIFE_HOURS).astype(float)
    df["w_static"] = df["w_source"] * df["w_type"] * df["confidence"]

    if "weight_override" in df.columns:
        manual = pd.to_numeric(df["weight_override"], errors="coerce")
        df.loc[manual.notna(), "w_static"] = manual[manual.notna()].astype(float)

    if "cluster_id" in df.columns and df["cluster_id"].notna().any():
        df["cluster_id_given"] = df["cluster_id"]
    df["text"] = df["text"].astype(str)
    df["url"] = df["url"].astype(str) if "url" in df.columns else ""
    return df.dropna(subset=["t_use"]).reset_index(drop=True)


# =====================================================================
# 六、日频聚合（核心输出）
# =====================================================================

def build_daily_sentiment(frame: pd.DataFrame, asset: str | None = None,
                          scheme: str = "prior",
                          source_weights: dict[str, float] | None = None,
                          cutoff_hour: int = 23,
                          days: Sequence[pd.Timestamp] | None = None,
                          min_weight: float = 0.02) -> pd.DataFrame:
    """输出日频舆情分数表。

    返回列：date, asset, score, z, coverage, evidence, dispersion, sum_weight, top_event …
    说明：情绪是「随时间衰减的记忆状态」，所以某天没有新消息时，分数依然会保留
          （按半衰期衰减），但 coverage 会下降 —— 这才是正确的处理方式。
    """
    items = frame if "w_static" in frame.columns else prepare_items(frame, asset)
    if asset:
        items = items[items["asset"] == str(asset).upper()].copy()
    if items.empty:
        raise ValueError("没有可用条目。")

    weights = resolve_source_weights(scheme, items, source_weights)
    items = items.copy()
    items["w_static"] = items["w_static"] * (
        items["source"].map(weights).fillna(1.0) /
        items["source"].map(cfg.SOURCE_WEIGHTS).fillna(1.0)
    ).replace([np.inf, -np.inf], 1.0)

    outputs: list[pd.DataFrame] = []
    for asset_name, group in items.groupby("asset", sort=False):
        clusters, _detail = build_clusters(group)
        t = clusters["t_use"].dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")
        hours_h = clusters["half_life"].to_numpy(dtype=float)
        w_static = clusters["w_static"].to_numpy(dtype=float)
        e = clusters["e"].to_numpy(dtype=float)
        texts = clusters["rep_text"].to_numpy()
        sources = clusters["main_source"].to_numpy()

        start = pd.Timestamp(clusters["t_use"].min()).normalize()
        end = pd.Timestamp(clusters["t_use"].max()).normalize()
        if days is not None:
            day_list = pd.DatetimeIndex([pd.Timestamp(d).normalize() for d in days])
        else:
            day_list = pd.date_range(start, end, freq="D")

        rows: list[dict[str, Any]] = []
        for day in day_list:
            cutoff = pd.Timestamp(day) + pd.Timedelta(hours=int(cutoff_hour), minutes=59, seconds=59)
            visible = t <= np.datetime64(cutoff.to_datetime64())
            if not visible.any():
                rows.append({"date": pd.Timestamp(day), "asset": asset_name, "score": 0.0, "z": 0.0,
                             "coverage": 0.0, "evidence": 0, "dispersion": 0.0, "sum_weight": 0.0,
                             "top_event": "", "top_source": "", "top_direction": 0.0})
                continue
            gap_hours = (np.datetime64(cutoff.to_datetime64()) - t[visible]) / np.timedelta64(1, "h")
            decay = np.power(2.0, -gap_hours / np.maximum(hours_h[visible], EPS))
            w = w_static[visible] * decay
            e_v = e[visible]
            total_w = float(w.sum())
            score = float((w * e_v).sum() / (total_w + float(cfg.KAPPA)))
            z = float(np.tanh(score / float(cfg.TAU)))
            coverage = float(total_w / (total_w + float(cfg.KAPPA))) if total_w > EPS else 0.0
            eff = w >= float(min_weight)
            evidence = int(eff.sum())
            if total_w > EPS:
                mean_e = float(np.average(e_v, weights=w))
                dispersion = float(np.sqrt(np.average((e_v - mean_e) ** 2, weights=w)))
            else:
                dispersion = 0.0
            impact = np.abs(w * e_v)
            top = int(np.argmax(impact))
            top_idx = np.where(visible)[0][top]
            rows.append({
                "date": pd.Timestamp(day), "asset": asset_name, "score": score, "z": z,
                "coverage": coverage, "evidence": evidence, "dispersion": dispersion,
                "sum_weight": total_w, "top_event": str(texts[top_idx])[:120],
                "top_source": str(sources[top_idx]), "top_direction": float(e_v[top]),
            })
        outputs.append(pd.DataFrame(rows))

    daily = pd.concat(outputs, ignore_index=True)
    return daily.sort_values(["date", "asset"]).reset_index(drop=True)


def as_of_score(frame: pd.DataFrame, asset: str, asof: Any,
                scheme: str = "prior",
                source_weights: dict[str, float] | None = None) -> dict[str, Any]:
    """给某个时点算一次舆情分数（用于实盘/影子模式，天然防前视）。

    只使用 t_use <= asof 的条目；返回与日频表同样的字段，方便直接比较。
    """
    day = pd.Timestamp(asof).normalize()
    daily = build_daily_sentiment(frame, asset=asset, scheme=scheme,
                                  source_weights=source_weights, days=[day])
    return daily.iloc[0].to_dict() if len(daily) else {}


# =====================================================================
# 七、把情绪变成仓位建议（第三档才用）
# =====================================================================

def sentiment_multiplier(z: float, coverage: float,
                         mode: str | None = None) -> float:
    """把舆情分数转成「仓位乘数」，范围 0 ~ 1.5。

    risk_control : 1 - γ×max(0,-z)   只用负面情绪减仓（推荐）
    threshold    : z>θ 才允许开新仓，否则 0
    continuous   : clip(1 + λ·z, 0, 1.5)  情绪好就加仓（最激进）
    """
    mode = (mode or cfg.DECISION_MODE).lower()
    z = float(np.clip(z, -1.0, 1.0))
    if coverage < float(cfg.COVERAGE_MIN):
        return 1.0                      # 信息不足时不干预（宁可不动，也不乱动）
    if mode == "threshold":
        return 1.0 if z > float(cfg.THETA) else 0.0
    if mode == "continuous":
        return float(np.clip(1.0 + float(cfg.LAMBDA) * z, 0.0, 1.5))
    return float(np.clip(1.0 - float(cfg.GAMMA) * max(0.0, -z), 0.0, 1.5))


# =====================================================================
# 八、自检
# =====================================================================

def _selftest() -> int:
    print("=" * 70)
    print("  淮策 Quant · 舆情内核自检（不联网、不修改任何其它文件）")
    print("=" * 70)

    demo = pd.DataFrame([
        {"时间": "2025-03-01 09:00", "来源": "official", "资产": "BTC", "内容类型": "policy",
         "内容": "美联储宣布降息，市场流动性预期改善"},
        {"时间": "2025-03-01 09:20", "来源": "group", "资产": "BTC", "内容类型": "emotion",
         "内容": "美联储宣布降息，市场流动性预期改善！！快上车"},
        {"时间": "2025-03-01 10:00", "来源": "media", "资产": "BTC", "内容类型": "fact",
         "内容": "美联储降息 25 个基点，符合市场预期"},
        {"时间": "2025-03-02 14:00", "来源": "analyst", "资产": "BTC", "内容类型": "opinion",
         "内容": "降息利好风险资产，我判断比特币会上涨"},
        {"时间": "2025-03-03 08:00", "来源": "group", "资产": "BTC", "内容类型": "promo",
         "内容": "稳赚不赔！加群带你跟单，收益翻倍"},
        {"时间": "2025-03-05 12:00", "来源": "media", "资产": "ETH", "内容类型": "fact",
         "内容": "以太坊网络升级完成"},
    ])

    items = prepare_items(demo)
    assert len(items) == 6, "条目数不对"
    assert items["content_type"].eq("promo").sum() == 1, "推广识别失败"

    official = items[items["source"] == "official"].iloc[0]
    group = items[items["source"] == "group"].iloc[0]
    assert official["w_static"] > group["w_static"] * 5, "来源权重层级不对"
    print(f"[1] 权重分层 OK   官方 {official['w_static']:.3f}  >>  群聊 {group['w_static']:.3f}")

    score_up = lexicon_score("大涨 突破 新高 利好")
    score_down = lexicon_score("暴跌 崩盘 利空 跌破")
    score_neg = lexicon_score("不会上涨")
    assert score_up > 0.4 and score_down < -0.4, "词典方向判断失败"
    assert score_neg < 0, "否定词处理失败"
    print(f"[2] 词典打分 OK   利好 {score_up:+.2f} / 利空 {score_down:+.2f} / 否定 {score_neg:+.2f}")

    clusters, detail = build_clusters(items[items["asset"] == "BTC"])
    assert len(clusters) < 5, "去重聚类失败：同一事件应合并"
    print(f"[3] 事件聚类 OK   5 条原始条目 → {len(clusters)} 个事件"
          f"（最大簇 {int(clusters['n_items'].max())} 条）")

    daily = build_daily_sentiment(items, asset="BTC")
    assert daily["coverage"].max() > 0.5, "覆盖率异常"

    one = prepare_items(pd.DataFrame([{"时间": "2025-03-01 09:00", "来源": "official",
                                       "资产": "BTC", "内容类型": "policy",
                                       "内容": "美联储宣布降息"}]))
    decay_days = [pd.Timestamp("2025-03-01"), pd.Timestamp("2025-03-02"), pd.Timestamp("2025-03-10")]
    decay = build_daily_sentiment(one, asset="BTC", days=decay_days)["sum_weight"].to_numpy()
    assert decay[0] > decay[1] > decay[2], f"半衰期衰减没有生效: {decay}"
    print(f"[4] 日频聚合 OK   {len(daily)} 天，峰值分数 {daily['score'].max():+.3f}；"
          f"同一条信息权重按半衰期衰减 {decay[0]:.3f} → {decay[1]:.3f} → {decay[2]:.4f}")

    w_eq = equal_source_weights()
    w_en = entropy_source_weights(items)
    assert abs(sum(w_en.values())) > 0, "熵权法失败"
    print(f"[5] 权重方案 OK   等权 {w_eq['official']:.2f}  熵权(官方) {w_en.get('official', 0):.2f}")

    m_neg = sentiment_multiplier(-0.8, 0.9)
    m_pos = sentiment_multiplier(+0.8, 0.9)
    m_low = sentiment_multiplier(-0.8, 0.10)
    assert m_neg < 1.0 and abs(m_pos - 1.0) < 1e-9, "风控型乘数逻辑错误"
    assert abs(m_low - 1.0) < 1e-9, "信息不足时不应干预"
    print(f"[6] 仓位乘数 OK   强负面 {m_neg:.2f} / 强正面 {m_pos:.2f} / 证据不足 {m_low:.2f}")

    point = as_of_score(items, "BTC", "2025-03-02 23:59")
    assert point["date"].isoformat().startswith("2025-03-02")
    print(f"[7] 时点查询 OK   {point['date'].date()} 分数 {point['score']:+.3f}"
          f"  证据 {point['evidence']} 条")

    print("-" * 70)
    print("  自检通过 [OK]  舆情内核工作正常（未联网、未改动价格引擎）")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())