"""Radar projection of collection evidence; no sample fallback or trading writes.

Uses stored per-message weights and directions, not whole-batch clustering.
Each historical point filters availability BEFORE aggregation. Exact dedup only;
message count must never be described as independent-source count.
"""
from datetime import datetime

import numpy as np
import pandas as pd

from sentiment import sentiment_core as core, weight_config as cfg


def radar_payload(records, now, asof=None, window=240):
    current = pd.Timestamp(now)
    if asof:
        stamp = pd.Timestamp(asof)
        if pd.isna(stamp):
            raise ValueError("查询时间无效")
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(datetime.now().astimezone().tzinfo)
        # Date-only replay is the end of that local calendar day, not UTC day.
        if len(str(asof).strip()) == 10:
            stamp += pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        cutoff = min(stamp.tz_convert("UTC"), current)
    else:
        cutoff = current
    window = max(1, min(365, int(window)))
    visible = [m for m in records if pd.Timestamp(m["available"]) <= cutoff]
    assets = sorted({m["asset"] for m in visible})
    first = min((pd.Timestamp(m["available"]) for m in visible), default=cutoff).normalize()
    days = pd.date_range(max(first, cutoff.normalize() - pd.Timedelta(days=window - 1)), cutoff.normalize(), freq="D") if visible else []
    series, snapshot = {}, []

    def aggregate(prepared, at):
        group, availability, publication, static, half_life, direction = prepared
        mask = availability <= at.timestamp()
        if not mask.any():
            return dict(z=0., score=0., coverage=0., evidence=0, top_event="", top_source="", top_direction=0., top_message_id="", multiplier=1.)
        age = np.maximum(0., (at.timestamp() - publication[mask]) / 3600.)
        weights = static[mask] * np.power(2., -age / half_life[mask])
        directions = direction[mask]
        total = float(weights.sum())
        score = float(np.dot(weights, directions) / (total + cfg.KAPPA))
        z = float(np.tanh(score / cfg.TAU))
        coverage = total / (total + cfg.KAPPA)
        top = group[np.flatnonzero(mask)[int(np.argmax(np.abs(weights * directions)))]]
        return dict(z=round(z, 6), score=round(score, 6), coverage=round(coverage, 6),
                    evidence=int((weights >= .02).sum()), top_event=top["text"][:120],
                    top_source=top["source_name"], top_direction=top["direction"], top_message_id=top["id"],
                    multiplier=round(float(core.sentiment_multiplier(z, coverage)), 3))

    for asset in assets:
        group = [m for m in visible if m["asset"] == asset]
        prepared = (group, np.array([pd.Timestamp(m["available"]).timestamp() for m in group]),
                    np.array([pd.Timestamp(m["published"]).timestamp() for m in group]),
                    np.array([m["source_weight"] * m["content_weight"] for m in group]),
                    np.array([m["half_life_hours"] for m in group]), np.array([m["direction"] for m in group]))
        series[asset] = [{"d": str(day.date()), "z": aggregate(prepared, min(day + pd.Timedelta(days=1) - pd.Timedelta(seconds=1), cutoff))["z"]} for day in days]
        snapshot.append(dict(asset=asset, date=str(cutoff.date()), **aggregate(prepared, cutoff)))
    return dict(ok=True, is_sample=False, data_mode="collection", source="采集控制台共享库 · collection.sqlite3",
                asof=cutoff.isoformat(), start=str(first.date()) if visible else "—", end=str(cutoff.date()),
                window=window, window_days=len(days), window_start=str(days[0].date()) if len(days) else "",
                window_end=str(days[-1].date()) if len(days) else "", insufficient_history=len(days) < window,
                asof_index=len(days)-1 if len(days) else None, assets=assets, series=series, snapshot=snapshot,
                verdict=[], visible_messages=len(visible), total_messages=len(records), empty=not visible,
                note="与采集控制台共享消息库；曲线按 UTC 日展示，最后一点截止查询时刻。只做精确文本去重，消息数不等于独立证据数；本地导入真实性待核验。未接入回测或交易。")
