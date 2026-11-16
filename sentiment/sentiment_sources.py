# -*- coding: utf-8 -*-
"""淮策 Quant · 舆情数据接入层（可插拔，默认只用本地文件）。

【这个文件解决什么问题】
舆情内核（sentiment_core.py）只吃「标准格式的表格」。真实世界的数据来自各种地方，
格式五花八门。这个文件就是「接口层」：把不同来源的数据，统一成内核能吃的格式。

【设计原则：合规优先】
  ✅ 支持：本地 CSV / Excel 导出、公开 RSS/Atom 订阅源（美联储、交易所公告、财经媒体）
  ❌ 不支持、也永远不会内置：抓 X(Twitter)、Telegram、抖音、微信群聊、财经 App
     原因：这些要么违反平台条款、要么涉及隐私与《个人信息保护法》，
     拿它们做毕业设计，答辩时会被老师直接质疑数据来源合法性。
     如果你确实要用，请走官方付费 API 并保留授权凭证。

【标准列】（内核认这 9 列，中英文列名都能自动识别）
  发布时间 | 抓取时间 | 来源 | 作者 | 资产 | 内容类型 | 内容 | 链接 | 数据性质
  其中「来源」必须是下面 7 个之一，否则按 social 处理：
      official / minutes / media / research / analyst / social / group
  「内容类型」必须是：policy / fact / opinion / emotion / promo（留空则由程序按关键词猜）

【怎么用】（在 auto_trading_desktop 目录下运行）
  1) 生成一个空白模板，照着填：
        python sentiment/sentiment_sources.py --template 我的数据.csv
  2) 把你已经导出的 CSV 规范化（自动识别中英文列名、统一来源写法）：
        python sentiment/sentiment_sources.py --local 原始文件.csv --out 我的数据.csv
  3) 抓一个公开 RSS（需要能访问该网站；大陆网络可能连不上，连不上就用本地导入）：
        python sentiment/sentiment_sources.py --rss "https://example.com/feed.xml" --source media --asset BTC --out 我的数据.csv
  4) 规范化后直接跑验证：
        python sentiment/sentiment_check.py --csv 我的数据.csv
"""
from __future__ import annotations

import argparse
import html as html_lib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
ROOT = BASE_DIR.parent
for _p in (str(BASE_DIR), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from . import weight_config as cfg
except ImportError:
    import weight_config as cfg

STANDARD_COLUMNS = ["发布时间", "抓取时间", "来源", "作者", "资产",
                    "内容类型", "内容", "链接", "数据性质"]
SOURCE_CHOICES = list(cfg.SOURCE_WEIGHTS)
CONTENT_CHOICES = list(cfg.CONTENT_WEIGHTS)

# 来源名称的常见写法 -> 标准写法
SOURCE_ALIASES = {
    "official": ["official", "官方", "公告", "政府", "监管", "美联储", "fomc", "sec"],
    "minutes": ["minutes", "纪要", "会议纪要", "讲话", "发言", "speech"],
    "media": ["media", "新闻", "媒体", "财经", "news", "reuters", "bloomberg", "cnbc", "wsj"],
    "research": ["research", "研究", "研报", "机构", "report"],
    "analyst": ["analyst", "分析师", "大v", "大V", "博主", "kol", "influencer"],
    "social": ["social", "社交", "微博", "推特", "twitter", "x", "论坛", "帖子"],
    "group": ["group", "群聊", "群", "转发", "匿名", "telegram群"],
}

# 标题里出现这些词，就猜它是哪个币
ASSET_KEYWORDS = {
    "BTC": ["btc", "bitcoin", "比特币"],
    "ETH": ["eth", "ethereum", "以太坊", "以太"],
    "SOL": ["sol", "solana"],
    "BNB": ["bnb", "binance coin", "币安币"],
}


def normalize_source(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in SOURCE_CHOICES:
        return text
    for key, aliases in SOURCE_ALIASES.items():
        if any(alias.lower() in text for alias in aliases):
            return key
    return cfg.DEFAULT_SOURCE


def normalize_content(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in CONTENT_CHOICES:
        return text
    alias = {"公告": "fact", "事实": "fact", "观点": "opinion", "分析": "opinion",
             "情绪": "emotion", "政策": "policy", "监管": "policy", "推广": "promo"}
    return alias.get(text, "")


def guess_asset(text: str, default: str = "BTC") -> str:
    low = str(text or "").lower()
    for asset, keywords in ASSET_KEYWORDS.items():
        if any(k.lower() in low for k in keywords):
            return asset
    return default


def clean_text(raw: str) -> str:
    """去掉 HTML 标签、压缩空白。"""
    text = re.sub(r"<[^>]+>", " ", str(raw or ""))
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# =====================================================================
# 一、本地导入
# =====================================================================

FRAME_ALIASES = {
    "发布时间": ["发布时间", "时间", "日期", "time", "date", "published", "pub_time", "datetime"],
    "抓取时间": ["抓取时间", "采集时间", "captured", "fetched", "crawled"],
    "来源": ["来源", "来源类型", "渠道", "平台", "source", "channel"],
    "作者": ["作者", "博主", "账号", "用户", "author"],
    "资产": ["资产", "标的", "币种", "asset", "symbol", "ticker"],
    "内容类型": ["内容类型", "类型", "category", "content_type", "type"],
    "内容": ["内容", "文本", "正文", "标题", "text", "content", "title", "message"],
    "链接": ["链接", "来源链接", "url", "link"],
}


def standardize(frame: pd.DataFrame) -> pd.DataFrame:
    """把任意列名的表格，变成标准 9 列。"""
    lowered = {str(c).strip().lower(): c for c in frame.columns}
    rename = {}
    for target, aliases in FRAME_ALIASES.items():
        if target in frame.columns:
            continue
        for alias in aliases:
            key = str(alias).strip().lower()
            if key in lowered and lowered[key] not in rename:
                rename[lowered[key]] = target
                break
    out = frame.rename(columns=rename).copy()

    if "发布时间" not in out.columns:
        raise ValueError("缺少时间列：需要「发布时间 / 时间 / 日期 / time / date」中的任意一列。")
    if "内容" not in out.columns:
        out["内容"] = ""
    if "资产" not in out.columns:
        out["资产"] = out["内容"].map(lambda t: guess_asset(t))
    if "抓取时间" not in out.columns:
        out["抓取时间"] = out["发布时间"]
    if "链接" not in out.columns:
        out["链接"] = ""
    if "作者" not in out.columns:
        out["作者"] = ""
    if "数据性质" not in out.columns:
        out["数据性质"] = "本地导入"

    out["来源"] = out["来源"].map(normalize_source) if "来源" in out.columns else cfg.DEFAULT_SOURCE
    if "内容类型" in out.columns:
        out["内容类型"] = out["内容类型"].map(normalize_content)
    else:
        out["内容类型"] = ""
    out["内容"] = out["内容"].map(clean_text)
    out["资产"] = out["资产"].astype(str).str.upper().str.strip()
    return out[STANDARD_COLUMNS]


def load_local_csv(path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str)
    return standardize(frame)


def load_local_folder(folder) -> pd.DataFrame:
    folder = Path(folder)
    parts = []
    for path in sorted(folder.glob("*.csv")):
        try:
            parts.append(load_local_csv(path))
        except Exception as exc:  # pragma: no cover
            print(f"  [跳过] {path.name}：{type(exc).__name__}: {exc}")
    if not parts:
        raise SystemExit(f"文件夹里没有可用的 CSV：{folder}")
    return pd.concat(parts, ignore_index=True)


# =====================================================================
# 二、RSS / Atom 订阅源（可选，需要网络）
# =====================================================================

def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1].lower()


def parse_feed(xml_bytes: bytes, source: str = "media", default_asset: str = "BTC",
               tag: str = "RSS 抓取", assume_published: bool = False,
               max_text_chars: int | None = 800, require_published: bool = False) -> list:
    """解析 RSS 2.0 或 Atom。返回标准列的行列表。

    ⚠ 重要：RSS 只能给你“现在”的条目。默认把「抓取时间」设为当前时间，
    这是防前视的正确做法：今天才抓到的新闻，今天才算“可得”。
    代价是：用 RSS 反向补齐历史数据会失败（所有条目都堆在今天）。
    若你的数据源本身携带真实历史时间戳（例如官方档案、你自己持续采集的库），
    可以加 --assume-published 把抓取时间设为发布时间。
    """
    root = ET.fromstring(xml_bytes)
    entries = [node for node in root.iter() if _strip_ns(node.tag) in ("item", "entry")]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for node in entries:
        title = link = pub = updated = author = summary = ""
        for child in node:
            name = _strip_ns(child.tag)
            text = " ".join(child.itertext()).strip()
            if name == "title":
                title = text
            elif name == "link":
                link = child.attrib.get("href", text)
            elif name in ("pubdate", "published", "date"):
                pub = pub or text
            elif name == "updated":
                updated = text
            elif name in ("author", "creator"):
                author = text
            elif name in ("description", "summary", "content"):
                summary = text
        body = clean_text(f"{title} {summary}")
        if not body:
            continue
        rows.append({
            "发布时间": (pub or updated) if require_published else (pub or updated or now),
            "时间字段": "发布时间" if pub else ("更新时间" if updated else "缺失 / 推定"),
            "抓取时间": (pub or now) if assume_published else now,
            "来源": normalize_source(source),
            "作者": clean_text(author),
            "资产": guess_asset(body, default_asset),
            "内容类型": "fact",
            "内容": body if max_text_chars is None else body[:max_text_chars],
            "链接": link,
            "数据性质": tag,
        })
    return rows


def fetch_feed(url: str, source: str = "media", default_asset: str = "BTC",
               timeout: int = 15, assume_published: bool = False) -> list:
    """抓取一个公开 RSS/Atom。若网络不通，给出友善的提示。"""
    request = Request(url, headers={"User-Agent": "HuaiceQuant/3.0 (student research)"})
    try:
        with urlopen(request, timeout=timeout) as resp:  # noqa: S310
            payload = resp.read()
    except URLError as exc:
        raise SystemExit(
            f"抓取失败：{exc}\n"
            "  提示：大陆网络访问境外站点常常需要代理；如果连不上，\n"
            "        请改用本地导入（--local），更合规也更稳定。") from exc
    return parse_feed(payload, source=source, default_asset=default_asset,
                      assume_published=assume_published)


# =====================================================================
# 三、输出
# =====================================================================

def write_template(path) -> Path:
    path = Path(path)
    example = pd.DataFrame([
        {"发布时间": "2025-03-01 09:00:00", "抓取时间": "2025-03-01 09:05:00",
         "来源": "official", "作者": "美联储", "资产": "BTC", "内容类型": "policy",
         "内容": "美联储宣布降息 25 个基点，市场流动性预期改善",
         "链接": "https://example.com/news/1", "数据性质": "手工录入"},
        {"发布时间": "2025-03-02 14:00:00", "抓取时间": "2025-03-02 14:11:00",
         "来源": "analyst", "作者": "某分析师", "资产": "BTC", "内容类型": "opinion",
         "内容": "降息利好风险资产，我判断比特币会上涨",
         "链接": "", "数据性质": "手工录入"},
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    example.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def save_rows(rows, out_path) -> Path:
    out_path = Path(out_path)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise SystemExit("没有解析到任何条目。")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out_path, index=False, encoding="utf-8-sig")
    return out_path


def summarize(frame: pd.DataFrame, title: str) -> None:
    print("=" * 66)
    print(f"  {title}")
    print("=" * 66)
    print(f"  条目数   : {len(frame)}")
    if "来源" in frame.columns:
        print("  来源分布 : " + "  ".join(f"{k}={v}" for k, v in frame["来源"].value_counts().items()))
    if "资产" in frame.columns:
        print("  资产分布 : " + "  ".join(f"{k}={v}" for k, v in frame["资产"].value_counts().items()))
    if "发布时间" in frame.columns:
        times = pd.to_datetime(frame["发布时间"], errors="coerce")
        if times.notna().any():
            print(f"  时间范围 : {times.min()}  ->  {times.max()}")
    print("=" * 66)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="淮策 Quant · 舆情数据接入层（本地导入 / RSS）。只做数据整理，不联网除非显式 --rss。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "合规说明：本工具不抓取 X / Telegram / 抖音 / 微信群聊 / 财经 App 等平台内容。\n"
            "         这些平台没有合法公开接口，抓取会违反平台条款并可能触犯个人信息保护相关法规。\n"
        ))
    parser.add_argument("--template", type=str, default="", help="生成一个空白/示例模板 CSV")
    parser.add_argument("--local", type=str, default="", help="导入本地 CSV 并规范化")
    parser.add_argument("--folder", type=str, default="", help="批量导入一个文件夹里的所有 CSV")
    parser.add_argument("--rss", type=str, default="", help="抓取一个公开 RSS/Atom 源（需要网络）")
    parser.add_argument("--source", type=str, default="media", help="RSS 来源类型（默认 media）")
    parser.add_argument("--asset", type=str, default="BTC", help="RSS 默认资产（猜不出来时用）")
    parser.add_argument("--out", type=str, default="", help="输出文件路径")
    parser.add_argument("--assume-published", action="store_true",
                        help="RSS 抓取时，把抓取时间当成发布时间（仅当数据源本身有真实历史时间戳时使用）")
    args = parser.parse_args()

    if args.template:
        path = write_template(args.template)
        print(f"已生成模板：{path}")
        print("  用 Excel 或记事本打开，照着示例填你自己的数据即可。")
        return 0

    if args.local:
        frame = load_local_csv(args.local)
        summarize(frame, "本地导入 -> 规范化完成")
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(out, index=False, encoding="utf-8-sig")
            print(f"  已写出：{out}")
            print(f"  下一步：python sentiment/sentiment_check.py --csv \"{out}\"")
        return 0

    if args.folder:
        frame = load_local_folder(args.folder)
        summarize(frame, "批量导入 -> 规范化完成")
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(out, index=False, encoding="utf-8-sig")
            print(f"  已写出：{out}")
        return 0

    if args.rss:
        if args.assume_published:
            print("  提醒：你开了 --assume-published，抓取时间将等于发布时间。")
            print("         这只在数据源本身携带真实历史时间戳时才合理，否则会引入前视偏差。")
        rows = fetch_feed(args.rss, source=args.source, default_asset=args.asset,
                          assume_published=args.assume_published)
        frame = pd.DataFrame(rows)[STANDARD_COLUMNS]
        summarize(frame, f"RSS 抓取完成（{args.source}）")
        if args.out:
            out = save_rows(rows, args.out)
            print(f"  已写出：{out}")
        else:
            print("  提示：加 --out 你的文件.csv 就能存下来。")
        return 0

    parser.print_help()
    print()
    print("最常用的三条命令：")
    print("  python sentiment/sentiment_sources.py --template 我的数据.csv")
    print("  python sentiment/sentiment_sources.py --local 原始导出.csv --out 我的数据.csv")
    print("  python sentiment/sentiment_check.py --csv 我的数据.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
