# -*- coding: utf-8 -*-
"""淮策 Quant · 舆情模块（独立子模块）。

本模块与价格引擎（quant_core.py）完全解耦：
  - 删掉整个 sentiment 文件夹，主系统照样正常运行；
  - 本模块可以单独运行、单独自检、单独出报告；
  - 主系统只在需要时读取本模块输出的日频舆情分数表。

三层结构：
  weight_config.py      参数与权重（唯一调参入口）
  sentiment_core.py     纯计算内核（不联网）：权重 / 半衰期 / 聚类 / 聚合 / 时点查询
  sentiment_check.py    验证层：IC、分层、事件研究、增量检验、权重对照、敏感性
  sentiment_sources.py  数据接入层：本地导入 / RSS / 字段模板

外部（主程序）一般只需要一个函数：

    from sentiment import daily_scores
    table = daily_scores()                 # 用示例数据
    table = daily_scores("我的数据.csv")    # 用自己的数据
"""
from __future__ import annotations

from pathlib import Path

__version__ = "3.0"
__all__ = ["daily_scores", "as_of_scores", "OUTPUT_DIR", "SAMPLE_CSV"]

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
SAMPLE_CSV = BASE_DIR / "sample_data" / "示例_舆情事件.csv"


def daily_scores(csv_path=None, scheme: str = "prior"):
    """算出日频舆情分数表（失败会抛异常，调用方自行兜底）。

    返回列：date, asset, score, z, coverage, evidence, dispersion,
            sum_weight, top_event, top_source, top_direction
    """
    import pandas as pd

    from . import sentiment_core as core

    path = Path(csv_path) if csv_path else SAMPLE_CSV
    if not path.exists():
        raise FileNotFoundError(f"找不到舆情数据文件：{path}")
    frame = pd.read_csv(path)
    items = core.prepare_items(frame)
    return core.build_daily_sentiment(items, scheme=scheme)


def as_of_scores(csv_path=None, asof=None, scheme: str = "prior"):
    """给某一个时点算一次舆情分数（用于「舆情雷达」按回放日期看当前状态）。"""
    table = daily_scores(csv_path, scheme=scheme)
    if asof is None:
        return table
    day = table["date"] <= __import__("pandas").Timestamp(asof)
    return table[day]