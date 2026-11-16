# -*- coding: utf-8 -*-
"""引擎自检脚本：用本机 data 文件夹里的真实 K 线跑完全流程，检查各模块是否正常。

用法：双击「运行自检.bat」，或命令行执行  python verify_engine.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import quant_core as qc  # noqa: E402

FILES = {"BTC": "btc_usdt.csv", "ETH": "eth_usdt.csv",
         "SOL": "sol_usdt.csv", "BNB": "bnb_usdt.csv"}


def main() -> int:
    print("=" * 78)
    print("  引擎自检：读取本机真实 K 线，跑完 回测 / 组合 / 稳健性检验 全流程")
    print("=" * 78)
    frames = {}
    for name, fname in FILES.items():
        path = BASE / "data" / fname
        if not path.is_file():
            print(f"  [跳过] {fname} 不存在")
            continue
        frame = qc.read_ohlc_csv(path)
        frames[name] = frame
        print(f"  [载入] {name:4s} {len(frame):5d} 根   {frame.index[0].date()} ~ {frame.index[-1].date()}")

    if not frames:
        print("  data 文件夹里没有 CSV，请先双击「下载真实K线.bat」。")
        return 1

    base = qc.StrategyParams(initial_cash=100_000.0)
    print("-" * 78)
    print("  一、单资产回测（默认机构级参数）")
    print("-" * 78)
    print(f"  {'资产':<6}{'期末净值':>13}{'年化':>9}{'波动':>9}{'夏普':>7}"
          f"{'回撤':>9}{'Calmar':>8}{'成交':>6}")
    t0 = time.time()
    for name, frame in frames.items():
        res = qc.run_backtest(frame, base, name)
        m = res["metrics"]
        print(f"  {name:<6}{m['final_equity']:>13,.0f}{m['cagr'] * 100:>8.1f}%{m['ann_vol'] * 100:>8.1f}%"
              f"{m['sharpe']:>7.2f}{m['max_drawdown'] * 100:>8.1f}%{m['calmar']:>8.2f}{m['trades']:>6d}")
    print(f"  （耗时 {time.time() - t0:.1f} 秒）")

    print("-" * 78)
    print("  二、多资产组合（风险平价加权）")
    print("-" * 78)
    t0 = time.time()
    port = qc.run_portfolio(frames, base)
    m = port["metrics"]
    print(f"  初始 {base.initial_cash:,.0f} USDT  ->  期末 {m['final_equity']:,.0f} USDT ({m['total_return'] * 100:+.1f}%)")
    print(f"  年化 {m['cagr'] * 100:+.1f}%   波动 {m['ann_vol'] * 100:.1f}%   夏普 {m['sharpe']:.2f}   "
          f"Sortino {m['sortino']:.2f}   Calmar {m['calmar']:.2f}   Omega {m['omega']:.2f}")
    print(f"  最大回撤 {m['max_drawdown'] * 100:.1f}%（持续 {m['max_dd_days']} 天，"
          f"修复 {m['recovery_days']} 天）  VaR95 {m['var95'] * 100:.2f}%   CVaR95 {m['cvar95'] * 100:.2f}%")
    print(f"  基准(买入持有) {m['benchmark_return'] * 100:+.1f}%   超额 {m['excess_return'] * 100:+.1f}%   "
          f"Beta {m['beta']:.2f}   Alpha {m['alpha'] * 100:+.1f}%   信息比率 {m['information_ratio']:.2f}")
    print(f"  交易 {m['trade_count']} 笔  交易胜率 {m['trade_win_rate'] * 100:.1f}%  "
          f"盈亏比 {m['payoff_ratio']:.2f}  平均持仓 {m['avg_holding_days']:.1f} 天  "
          f"平均仓位 {m['exposure_avg'] * 100:.1f}%  手续费 {m['fees_paid']:,.0f}")
    print("  各资产：")
    for leg in port["legs"]:
        lm = leg["metrics"]
        print(f"    {leg['symbol']:<5} 权重 {leg['alloc_weight'] * 100:>5.1f}%  "
              f"期末 {leg['final_equity']:>12,.0f}  盈亏 {leg['pnl']:>+11,.0f}  "
              f"贡献 {leg['contribution'] * 100:>+5.1f}%  夏普 {lm['sharpe']:>5.2f}  "
              f"回撤 {lm['max_drawdown'] * 100:>6.1f}%  成交 {len(leg['trades']):>3} 笔")
    print(f"  （耗时 {time.time() - t0:.1f} 秒）")

    print("-" * 78)
    print("  三、稳健性检验")
    print("-" * 78)
    target = frames.get("BTC", list(frames.values())[0])
    t0 = time.time()
    grid = qc.parameter_grid(target, base, [10, 15, 20, 25, 30, 40], [50, 60, 80, 100, 120])
    print(f"  参数网格 6x5 完成（{time.time() - t0:.1f} 秒）：最优 fast={grid['best']['fast']} "
          f"slow={grid['best']['slow']} 夏普={grid['best']['value']:.2f}")
    t0 = time.time()
    wf = qc.walk_forward(target, base, [10, 20, 30], [50, 80, 120], n_splits=4)
    print(f"  滚动前推 {len(wf['folds'])} 折（{time.time() - t0:.1f} 秒）：样本外平均收益 "
          f"{wf['oos_avg_return'] * 100:+.1f}%，正收益折数 {wf['oos_win_rate'] * 100:.0f}%，"
          f"平均夏普 {wf['oos_avg_sharpe']:.2f}，跑赢基准 {wf['oos_vs_benchmark'] * 100:+.1f}%")
    for f in wf["folds"]:
        print(f"    第 {f['fold']} 折  训练 {f['train_start']}~{f['train_end']}  ->  "
              f"样本外 {f['test_start']}~{f['test_end']}   选中参数 {f['best_fast']}/{f['best_slow']}   "
              f"收益 {f['test_return'] * 100:>+7.1f}%  (基准 {f['test_benchmark'] * 100:>+7.1f}%)   "
              f"夏普 {f['test_sharpe']:>5.2f}")
    mc = qc.monte_carlo(port["equity"], n_sims=500, horizon=365)
    print(f"  蒙特卡洛 500 条路径：一年后 5% / 50% / 95% 分位 = "
          f"{mc['final']['p05'] * 100:+.1f}% / {mc['final']['p50'] * 100:+.1f}% / {mc['final']['p95'] * 100:+.1f}%，"
          f"亏钱概率 {mc['final']['prob_loss'] * 100:.0f}%，平均最大回撤 {mc['final']['avg_max_dd'] * 100:.1f}%")
    month = qc.monthly_return_table(port["equity"])
    print("  逐年收益：" + "  ".join(f"{a['year']}:{a['ret'] * 100:+.0f}%" for a in month["annual"]))
    corr = port["correlation"]
    print("  相关性矩阵：")
    print("        " + "".join(f"{n:>9}" for n in corr["labels"]))
    for i, name in enumerate(corr["labels"]):
        print(f"    {name:<4}" + "".join(f"{(v if v is not None else float('nan')):>9.2f}"
                                         for v in corr["matrix"][i]))

    print("-" * 78)
    problems = []
    if port["equity"].isna().any():
        problems.append("组合净值出现 NaN")
    if not 0.999 < sum(port["weights"]) < 1.001:
        problems.append("权重之和不是 1")
    if m["max_drawdown"] > 0.001:
        problems.append("最大回撤为正（不可能）")
    if port["equity"].iloc[-1] <= 0:
        problems.append("净值归零")
    print("  自检结果：" + ("发现问题 -> " + "; ".join(problems) if problems else "全部通过 [OK]"))
    print("=" * 78)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())