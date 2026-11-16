"""Matched-period ablations and auditable offline research bundles.

The evaluation uses equal initial allocations and a fresh cash account. It does
not optimize parameters, manufacture holdout data, or infer data provenance.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import platform
import sys
import zipfile

import numpy as np
import pandas as pd

import quant_core as qc

VERSION = "1.0"
LABELS = {"hold": "买入持有", "trend": "趋势 + 固定仓位",
          "vol": "趋势 + 波动率目标", "stop": "趋势 + 波动率目标 + ATR止损"}


def clean_json(value):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    return value


def align_frames(frames, p, start=None, end=None):
    if not frames:
        raise ValueError("实验至少需要一个资产。")
    normalized = {}
    for name, frame in frames.items():
        if frame.empty or not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError(f"{name} 缺少有效日期行情。")
        if frame.index.has_duplicates:
            raise ValueError(f"{name} 日期重复，请先清洗行情。")
        normalized[name] = frame.sort_index().copy()
    index = next(iter(normalized.values())).index
    for frame in normalized.values():
        index = index.intersection(frame.index)
    index = index.sort_values()
    if end:
        index = index[index <= pd.Timestamp(end)]
    warmup = max(p.slow, p.vol_lookback, p.atr_period, p.adv_window) + 2
    if len(index) <= warmup + 10:
        raise ValueError("共同日期不足以预热及评估；请扩大区间或减少资产。")
    offset = warmup
    if start:
        offset = max(offset, int(index.searchsorted(pd.Timestamp(start))))
    if len(index) - offset < 11:
        raise ValueError("评估期至少需要 11 根共同 K 线；开始日期可能晚于结束日期。")
    aligned = {name: frame.reindex(index) for name, frame in normalized.items()}
    for name, frame in aligned.items():
        prices = frame[["Open", "High", "Low", "Close"]].to_numpy(dtype=float)
        if not np.isfinite(prices).all() or (prices <= 0).any():
            raise ValueError(f"{name} 包含无效 OHLC 价格。")
    audit = [{"symbol": name, "original_start": str(frame.index[0].date()),
              "original_end": str(frame.index[-1].date()), "original_rows": len(frame),
              "common_rows": len(index), "excluded_rows": len(frame) - len(index)}
             for name, frame in normalized.items()]
    return aligned, offset, audit


def paired_block_interval(candidate, reference, periods, simulations=1000, block=10, seed=20261012):
    """Circular paired block bootstrap for mean net daily-return differences.

    Same dates/blocks are sampled for both strategies, preserving within-block
    serial dependence. Intervals are exploratory and not multiplicity-adjusted.
    """
    if not 100 <= simulations <= 5000 or not 1 <= block <= 120:
        raise ValueError("重采样次数须在 100—5000，区块长度须在 1—120 根之间。")
    delta = (candidate.pct_change().iloc[1:] - reference.pct_change().iloc[1:]).to_numpy()
    if len(delta) < 10 or not np.isfinite(delta).all():
        raise ValueError("收益差样本不足或包含非有限数值。")
    block = min(block, len(delta))
    rng = np.random.default_rng(seed)
    estimates = np.empty(simulations)
    # Batches bound memory use for long histories.
    for first in range(0, simulations, 100):
        count = min(100, simulations - first)
        starts = rng.integers(0, len(delta), size=(count, int(np.ceil(len(delta) / block))))
        indices = ((starts[:, :, None] + np.arange(block)) % len(delta)).reshape(count, -1)[:, :len(delta)]
        estimates[first:first + count] = delta[indices].mean(axis=1) * periods
    low, high = np.quantile(estimates, [0.025, 0.975])
    return {"annual_mean_return_difference": float(delta.mean() * periods),
            "ci95_low": float(low), "ci95_high": float(high),
            "contains_zero": bool(low <= 0 <= high), "block_bars": block,
            "simulations": simulations, "seed": seed, "samples": len(delta),
            "interpretation": "年化算术平均净日收益差；不是 CAGR 差，也未做多重比较校正。"}


def run_experiment(frames, params, start=None, end=None, simulations=1000, block=10, seed=20261012):
    params.validate()
    if not isinstance(seed, int) or seed < 0:
        raise ValueError("随机种子必须为非负整数。")
    # Fixed allocation and shared controls isolate the studied modules.
    base = replace(params, alloc_method="equal", weights="", use_sentiment=False,
                   regime_filter=False, risk_limit_pct=100, daily_loss_limit_pct=0,
                   time_stop_days=0, use_stop=False)
    aligned, offset, audit = align_frames(frames, base, start, end)
    index = next(iter(aligned.values())).index
    trade_start = index[offset]
    evaluation_index = index[offset - 1:]
    variants = {"trend": replace(base, use_vol_target=False),
                "vol": replace(base, use_vol_target=True),
                "stop": replace(base, use_vol_target=True, use_stop=True, stop_mode="trailing")}
    results = {key: qc.run_portfolio(aligned, p, trade_start=trade_start) for key, p in variants.items()}
    curves = pd.DataFrame({key: result["equity"].reindex(evaluation_index) for key, result in results.items()})
    curves.insert(0, "hold", results["trend"]["benchmark"].reindex(evaluation_index))
    if not np.isfinite(curves.to_numpy()).all():
        raise ValueError("实验净值包含非有限数值。")
    metrics = {}
    for key in LABELS:
        if key == "hold":
            metrics[key] = qc.performance_stats(curves[key], initial_cash=base.initial_cash,
                                                periods=base.annualization)
            sleeve = base.initial_cash / len(aligned)
            fee_rate = base.fee_bps / 1e4
            metrics[key]["fees_paid"] = len(aligned) * sleeve / (1 + fee_rate) * fee_rate
            metrics[key]["trades"] = len(aligned)
            metrics[key]["turnover"] = base.initial_cash / (1 + fee_rate)
            metrics[key]["exposure_avg"] = 1.0
        else:
            result = results[key]
            bars = result["bars"].reindex(evaluation_index)
            metrics[key] = qc.performance_stats(curves[key], result["trades"], base.initial_cash,
                                                bars, result["turnover"], base.annualization)
            qc._merge_trade_metrics(metrics[key], result["round_trips"])
    differences = [{"candidate": candidate, "reference": reference,
                    **paired_block_interval(curves[candidate], curves[reference], base.annualization,
                                            simulations, block, seed)}
                   for candidate, reference in (("trend", "hold"), ("vol", "trend"), ("stop", "vol"))]
    fingerprints = {}
    for name, frame in aligned.items():
        data = frame.to_csv(date_format="%Y-%m-%dT%H:%M:%S", float_format="%.17g").encode("utf-8")
        fingerprints[name] = hashlib.sha256(data).hexdigest()
    manifest = {"schema_version": VERSION, "input_params": asdict(params),
                "variant_params": {key: asdict(p) for key, p in variants.items()},
                "requested_start": start, "requested_end": end,
                "effective_start": str(trade_start.date()), "effective_end": str(index[-1].date()),
                "baseline_date": str(evaluation_index[0].date()), "evaluation_bars": len(evaluation_index) - 1,
                "warmup_rows": offset, "annualization": base.annualization, "assets": list(aligned),
                "data_audit": audit, "snapshot_sha256": fingerprints,
                "random_seed": seed, "bootstrap_simulations": simulations, "block_bars": block,
                "python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
                "data_provenance": "本地行情快照；下列来源记录按原 CSV 哈希校验。复权历史价格与当日原始报价可能不同。",
                "source_records": {name: frame.attrs.get("provenance", {}) for name, frame in frames.items()},
                "protocol": "同一日期交集、同一本金、固定等权子账户、无舆情/方向过滤/熔断/时间止损；首日前保留本金基线；期末按市价估值，不强制平仓。",
                "limitations": ["历史资产选择未校正幸存者偏差。", "日期交集会排除缺失日期。",
                                "重采样区间仅描述历史样本不确定性，未校正多重比较。",
                                "指定日期区间不自动构成独立样本外；已用于调参的数据应标为探索样本。"]}
    source_dir = Path(__file__).resolve().parent
    manifest["source_sha256"] = {name: hashlib.sha256((source_dir / name).read_bytes()).hexdigest()
                                  for name in ("quant_core.py", "research_experiments.py")}
    identity = json.dumps(manifest, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    manifest["experiment_id"] = hashlib.sha256(identity).hexdigest()[:16]
    return {"manifest": manifest, "metrics": metrics, "comparisons": differences,
            "curves": curves, "frames": aligned, "results": results}


def experiment_payload(experiment):
    return clean_json({"manifest": experiment["manifest"], "metrics": experiment["metrics"],
                       "labels": LABELS, "comparisons": experiment["comparisons"]})


def report_html(experiment):
    payload = experiment_payload(experiment)
    m = payload["manifest"]
    def percent(value):
        return "—" if value is None else f"{value * 100:.2f}%"
    rows = "".join(f"<tr><td>{html.escape(LABELS[key])}</td><td>{percent(value['total_return'])}</td>"
                   f"<td>{percent(value['cagr'])}</td><td>{value['sharpe']:.3f}</td>"
                   f"<td>{percent(value['max_drawdown'])}</td><td>{value['fees_paid']:.2f}</td></tr>"
                   for key, value in payload["metrics"].items())
    pairs = "".join(f"<tr><td>{html.escape(LABELS[c['candidate']] + ' − ' + LABELS[c['reference']])}</td>"
                    f"<td>{percent(c['annual_mean_return_difference'])}</td><td>{percent(c['ci95_low'])} ~ {percent(c['ci95_high'])}</td>"
                    f"<td>{'是' if c['contains_zero'] else '否'}</td></tr>" for c in payload["comparisons"])
    xs = experiment["curves"]
    step = max(1, len(xs) // 500)
    sampled = xs.iloc[::step]
    if sampled.index[-1] != xs.index[-1]:
        sampled = pd.concat([sampled, xs.iloc[[-1]]])
    values = sampled.to_numpy()
    low, high = float(values.min()), float(values.max())
    colors = ["#64748b", "#2563eb", "#059669", "#d97706"]
    lines = []
    for j, key in enumerate(sampled.columns):
        points = " ".join(f"{30 + i * 900 / max(1, len(values)-1):.2f},{280 - (v-low) * 250 / max(high-low,1):.2f}"
                          for i, v in enumerate(values[:, j]))
        lines.append(f'<polyline fill="none" stroke="{colors[j]}" stroke-width="2" points="{points}"/>')
    legend = " · ".join(f'<span style="color:{color}">{html.escape(LABELS[key])}</span>'
                         for key, color in zip(sampled.columns, colors))
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>论文研究实验 {m['experiment_id']}</title>
<style>body{{max-width:1050px;margin:30px auto;font:16px/1.7 'Microsoft YaHei',sans-serif;color:#1e293b;padding:20px}}
table{{border-collapse:collapse;width:100%;margin:20px 0}}td,th{{padding:10px;border:1px solid #cbd5e1;text-align:right}}td:first-child,th:first-child{{text-align:left}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f9;padding:20px;font-size:13px}}svg{{width:100%}}</style>
<h1>策略对照与消融实验</h1><p>实验编号：{m['experiment_id']}<br>评估：{m['effective_start']} — {m['effective_end']}（{m['evaluation_bars']} 根日线）<br>
{html.escape(m['protocol'])}</p><p>{html.escape(m['data_provenance'])}</p>
<h2>同区间净值</h2><p>{legend}</p><svg viewBox="0 0 960 310" role="img" aria-label="四种策略净值曲线">{''.join(lines)}</svg>
<p>横轴：{sampled.index[0].date()} — {sampled.index[-1].date()}；纵轴净值范围：{low:.2f} — {high:.2f}</p>
<table><tr><th>方案</th><th>累计收益</th><th>年化收益</th><th>夏普</th><th>最大回撤</th><th>手续费</th></tr>{rows}</table>
<h2>收益差的区块重采样</h2><p>区块 {m['block_bars']} 根，重采样 {m['bootstrap_simulations']} 次。下面为年化算术平均净日收益差，不是 CAGR 差。
未校正多重比较，不据此自动判断“策略有效”。</p><table><tr><th>比较</th><th>均值差</th><th>95% 区间</th><th>包含零</th></tr>{pairs}</table>
<h2>实验配置与数据审计</h2><pre>{html.escape(json.dumps(m, ensure_ascii=False, indent=2))}</pre></html>"""


def save_experiment(experiment, directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    # Every click gets an isolated bundle, even when configuration is unchanged.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = root / f"{stamp}_{experiment['manifest']['experiment_id']}"
    folder.mkdir()
    (folder / "数据快照").mkdir()
    for name, frame in experiment["frames"].items():
        safe = hashlib.sha256(name.encode()).hexdigest()[:12]
        filename = f"{safe}.csv"
        frame.to_csv(folder / "数据快照" / filename, date_format="%Y-%m-%dT%H:%M:%S", float_format="%.17g")
    payload = experiment_payload(experiment)
    payload["snapshot_files"] = {name: f"数据快照/{hashlib.sha256(name.encode()).hexdigest()[:12]}.csv"
                                 for name in experiment["frames"]}
    (folder / "实验清单.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    experiment["curves"].to_csv(folder / "对照净值.csv", encoding="utf-8-sig")
    pd.DataFrame([{"scheme": k, "label": LABELS[k], **v} for k, v in payload["metrics"].items()]).to_csv(
        folder / "绩效对照.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(payload["comparisons"]).to_csv(folder / "区块重采样.csv", index=False, encoding="utf-8-sig")
    for key, result in experiment["results"].items():
        pd.DataFrame(result["trades"]).to_csv(folder / f"成交_{key}.csv", index=False, encoding="utf-8-sig")
    (folder / "研究对照报告.html").write_text(report_html(experiment), encoding="utf-8")
    source_dir = Path(__file__).resolve().parent
    (folder / "源码快照").mkdir()
    for filename in ("quant_core.py", "research_experiments.py"):
        data = (source_dir / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != experiment["manifest"]["source_sha256"][filename]:
            raise ValueError("实验计算期间源码发生变化，请重新生成实验。")
        (folder / "源码快照" / filename).write_bytes(data)
    with zipfile.ZipFile(folder.with_suffix(".zip"), "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(folder))
    return folder


def reproduce(bundle, output):
    folder = Path(bundle).resolve()
    saved = json.loads((folder / "实验清单.json").read_text(encoding="utf-8"))
    manifest = saved["manifest"]
    frames = {}
    for name, relative in saved["snapshot_files"].items():
        path = (folder / relative).resolve()
        if not path.is_relative_to(folder):
            raise ValueError("快照路径超出实验包。")
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["snapshot_sha256"][name]:
            raise ValueError(f"{name} 数据快照校验失败。")
        frame = pd.read_csv(path, index_col=0, parse_dates=True, float_precision="round_trip")
        frame.attrs["provenance"] = manifest.get("source_records", {}).get(name, {})
        frames[name] = frame
    experiment = run_experiment(frames, qc.StrategyParams(**manifest["input_params"]),
                                start=manifest["requested_start"], end=manifest["requested_end"],
                                simulations=manifest["bootstrap_simulations"], block=manifest["block_bars"],
                                seed=manifest["random_seed"])
    old = saved["metrics"]
    new = experiment_payload(experiment)["metrics"]
    for key in LABELS:
        for metric in ("final_equity", "total_return", "sharpe", "max_drawdown"):
            if not np.isclose(old[key][metric], new[key][metric], rtol=1e-9, atol=1e-9):
                raise ValueError(f"复现结果不一致：{key}/{metric}，请检查源码和依赖版本。")
    return save_experiment(experiment, output)


def main():
    parser = argparse.ArgumentParser(description="策略消融、统一日期与可复现实验包")
    parser.add_argument("--assets", default="BTC,ETH,SOL,BNB")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "results" / "experiments"))
    parser.add_argument("--reproduce", help="已解压的实验包目录，校验数据并核对绩效")
    args = parser.parse_args()
    if args.reproduce:
        folder = reproduce(args.reproduce, args.out)
    else:
        from auto_trader_server import resolve_assets
        names = [n.strip().upper() for n in args.assets.split(",") if n.strip()]
        known = {item["name"]: item for item in qc.list_csv_files(Path(__file__).resolve().parent / "data")}
        if any(name not in known for name in names):
            raise ValueError("资产不存在；研究实验不自动回退为其他资产。")
        markets = {known[name]["market"] for name in names}
        if len(markets) != 1 or not markets <= {"crypto", "us"}:
            raise ValueError("一次实验须使用同一市场的日线资产。")
        market = next(iter(markets))
        frames, _ = resolve_assets(names, market)
        experiment = run_experiment(frames, qc.StrategyParams(annualization=252 if market == "us" else 365),
                                    start=args.start, end=args.end)
        folder = save_experiment(experiment, args.out)
    print(f"实验已生成：{folder}")
    print(f"可分享实验包：{folder.with_suffix('.zip')}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print(f"实验失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
