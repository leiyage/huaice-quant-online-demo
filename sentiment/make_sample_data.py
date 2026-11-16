# -*- coding: utf-8 -*-
"""淮策 Quant · 舆情模块 —— 离线示例数据生成器（不需要联网）。

用途：让整套舆情流程「下载完就能跑」，不需要 VPN、不需要 API Key、不抓任何网站。

数据构成（两段，文件里用「数据性质」这一列区分）：
  1. 真实事件节选：2017—2024 年间公开报道过的重大事件，日期与表述为教学简化，
     请以公开报道为准自行核对；仅用于演示「一条消息怎么进流程」。
  2. 合成示例：由固定随机种子生成的模拟条目，用来把每天的「覆盖率」撑起来，
     使 IC / 分层回测 / 事件研究这些统计量有足够样本可以计算。
     合成条目的方向与真实价格走势无关 —— 所以它不会凭空造出预测力。

重要（写论文时务必注明）：
  本文件生成的数据是「教学示例」，不是真实舆情档案。用它跑出来的任何统计结论
  都只能说明「代码流程通」，不能当作证据。请换成你自己合规采集的数据后再下结论。

运行方式（一般不用手动跑，示例 CSV 已随程序附带）：
  python sentiment/make_sample_data.py
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path
import sys

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
for _path in (str(BASE_DIR), str(BASE_DIR.parent)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:
    from . import weight_config as cfg
except ImportError:
    import weight_config as cfg

OUT_DIR = BASE_DIR / "sample_data"
OUT_CSV = OUT_DIR / "\u793a\u4f8b_\u8206\u60c5\u4e8b\u4ef6.csv"

SEED = 20250917
ASSETS = ["BTC", "ETH", "SOL", "BNB"]
START = datetime(2017, 8, 17)
END = datetime(2026, 9, 24)

# ---------------------------------------------------------------------
# 一、真实事件节选（date, asset, source, content_type, text）
# ---------------------------------------------------------------------
ANCHORS = [
    ("2017-09-04", "BTC", "official", "policy", "中国人民银行等七部委发布公告，将 ICO 定性为非法公开融资，要求立即停止"),
    ("2017-09-04", "ETH", "official", "policy", "监管部门叫停 ICO 融资，以太坊代币发行项目全面清退，市场悲观"),
    ("2017-12-17", "BTC", "media", "fact", "比特币价格逼近 20000 美元创历史新高，市场情绪极度乐观"),
    ("2017-12-22", "BTC", "media", "fact", "比特币自高点大幅回调，单周跌幅超过 30%，资金流出明显"),
    ("2018-01-26", "BTC", "official", "policy", "日本与韩国监管机构加强对虚拟货币交易所的审查与打击"),
    ("2018-02-06", "BTC", "media", "fact", "比特币跌破 6000 美元，全球风险资产同步回调，情绪转弱"),
    ("2018-11-15", "BTC", "media", "fact", "BCH 硬分叉引发算力战，市场恐慌抛售，价格跳水"),
    ("2018-12-15", "BTC", "media", "fact", "比特币跌至 3200 美元附近，熊市情绪蔓延，市场悲观"),
    ("2019-06-18", "BTC", "media", "fact", "Facebook 发布 Libra 白皮书，主流机构入场预期升温，市场看好"),
    ("2019-06-26", "BTC", "media", "fact", "比特币突破 13000 美元创年内新高，成交量明显放量"),
    ("2019-07-16", "BTC", "official", "policy", "美国国会就 Libra 举行听证会，监管提出质疑与禁止呼声"),
    ("2019-10-24", "BTC", "official", "policy", "中国强调把区块链作为核心技术自主创新重要突破口，情绪提振"),
    ("2019-10-25", "BTC", "media", "fact", "比特币单日大涨逾 40%，资金加速流入，市场强势反攻"),
    ("2020-03-12", "BTC", "media", "fact", "新冠引发全球流动性危机，比特币单日腰斩，市场恐慌抛售崩盘"),
    ("2020-03-15", "BTC", "official", "policy", "美联储紧急降息至零并启动无限量 QE，风险资产预期改善"),
    ("2020-05-11", "BTC", "official", "fact", "比特币完成第三次区块奖励减半，供给收缩被普遍视为利好"),
    ("2020-07-26", "BTC", "official", "policy", "美国 OCC 明确允许银行托管加密资产，机构入场通道打开"),
    ("2020-10-21", "BTC", "media", "fact", "PayPal 宣布支持加密货币支付，主流采用预期升温，资金流入"),
    ("2020-11-26", "BTC", "media", "fact", "比特币突破 19000 美元，机构资金持续净流入"),
    ("2021-02-08", "BTC", "media", "fact", "特斯拉宣布购入 15 亿美元比特币并接受支付，市场大涨"),
    ("2021-02-08", "BTC", "analyst", "opinion", "机构配置需求上升，我判断比特币中长期看多，建议逢低增持"),
    ("2021-04-14", "BTC", "media", "fact", "Coinbase 在纳斯达克上市，加密行业获得主流认可，情绪乐观"),
    ("2021-05-12", "BTC", "media", "fact", "特斯拉暂停比特币支付，市场情绪转弱，价格回调"),
    ("2021-05-19", "BTC", "official", "policy", "中国全面打击虚拟货币挖矿与交易，比特币暴跌，恐慌加剧"),
    ("2021-09-24", "BTC", "official", "policy", "发改委等部门进一步整治虚拟货币挖矿活动，监管趋严"),
    ("2021-10-19", "BTC", "media", "fact", "美国首只比特币期货 ETF 上市，资金流入预期升温"),
    ("2021-11-10", "BTC", "media", "fact", "比特币创下 69000 美元历史新高，市场极度乐观"),
    ("2021-12-04", "BTC", "media", "fact", "比特币周末闪崩，杠杆资金爆仓，市场恐慌"),
    ("2021-08-31", "SOL", "media", "fact", "Solana 生态 NFT 与 DeFi 活跃度上升，SOL 价格大幅上涨"),
    ("2022-01-20", "BTC", "official", "policy", "美联储加息预期升温，风险资产承压，紧缩担忧加剧"),
    ("2022-02-24", "BTC", "media", "fact", "俄乌冲突升级，全球避险情绪上升，加密市场回调"),
    ("2022-05-09", "BTC", "media", "fact", "LUNA 与 UST 崩盘，系统性风险扩散，市场恐慌抛售"),
    ("2022-05-09", "ETH", "media", "fact", "稳定币脱锚引发连锁抛售，以太坊跟随大跌，情绪悲观"),
    ("2022-06-13", "BTC", "media", "fact", "Celsius 冻结用户提现，流动性危机担忧加剧，市场下跌"),
    ("2022-06-16", "BTC", "official", "policy", "美联储宣布加息 75 个基点，为 1994 年以来最大幅度"),
    ("2022-07-13", "BTC", "official", "fact", "美国 6 月 CPI 同比 9.1% 创四十年新高，加息预期强化"),
    ("2022-09-15", "ETH", "official", "fact", "以太坊完成 The Merge 合并升级，转向权益证明，能耗大幅下降"),
    ("2022-11-08", "BTC", "media", "fact", "FTX 出现挤兑并停止提现，市场恐慌情绪急剧上升"),
    ("2022-11-11", "BTC", "media", "fact", "FTX 申请破产保护，行业信任危机，比特币跌破 16000"),
    ("2022-11-09", "SOL", "media", "fact", "Solana 与 FTX 关联度引发担忧，SOL 遭恐慌抛售暴跌"),
    ("2022-12-13", "BNB", "media", "fact", "币安遭大规模提现，市场担忧蔓延，BNB 承压下跌"),
    ("2023-01-12", "BTC", "official", "fact", "美国 12 月 CPI 回落至 6.5%，加息放缓预期提振风险资产"),
    ("2023-03-10", "BTC", "media", "fact", "硅谷银行倒闭引发银行体系担忧，市场避险情绪上升"),
    ("2023-03-19", "BTC", "media", "fact", "USDC 短暂脱锚后恢复，市场情绪修复，价格反弹"),
    ("2023-04-13", "ETH", "official", "fact", "以太坊上海升级完成，开放质押提款，网络信心提升"),
    ("2023-06-06", "BTC", "official", "policy", "美国 SEC 起诉币安与 Coinbase，监管打压引发抛售"),
    ("2023-06-15", "BTC", "media", "fact", "贝莱德递交现货比特币 ETF 申请，机构入场预期升温，市场大涨"),
    ("2023-07-13", "BTC", "media", "fact", "XRP 在诉讼中部分胜诉，监管不确定性下降，市场提振"),
    ("2023-08-17", "BTC", "media", "fact", "比特币跌破 26000 美元，成交量萎缩，市场情绪转弱"),
    ("2023-10-23", "BTC", "media", "fact", "比特币突破 34000 美元，现货 ETF 获批预期推动资金流入"),
    ("2023-11-21", "BNB", "official", "policy", "币安与美国监管达成和解并支付罚款，不确定性下降，情绪修复"),
    ("2023-12-25", "SOL", "media", "fact", "Solana 生态空投与交易活跃，SOL 强势上涨突破前高"),
    ("2024-01-10", "BTC", "official", "policy", "美国 SEC 批准 11 只现货比特币 ETF，行业里程碑，市场看好"),
    ("2024-03-13", "BTC", "media", "fact", "比特币突破 73000 美元创历史新高，ETF 资金持续净流入"),
    ("2024-03-18", "SOL", "media", "fact", "Solana 网络拥堵问题引发讨论，SOL 高位回调"),
    ("2024-04-13", "BTC", "media", "fact", "伊朗袭击以色列，地缘冲突升级，风险资产短线跳水"),
    ("2024-04-20", "BTC", "official", "fact", "比特币完成第四次减半，区块奖励降至 3.125 枚，长期利好"),
    ("2024-04-30", "BNB", "media", "fact", "币安 Launchpool 新项目上线，BNB 需求上升上涨"),
    ("2024-05-23", "ETH", "official", "policy", "SEC 批准现货以太坊 ETF 的 19b-4 文件，ETH 大涨"),
    ("2024-07-23", "ETH", "media", "fact", "现货以太坊 ETF 正式上市交易，资金流入符合预期"),
    ("2024-08-05", "BTC", "media", "fact", "日本央行加息引发日元套息交易平仓，全球股市暴跌，加密同步跳水"),
    ("2024-09-18", "BTC", "official", "policy", "美联储宣布降息 50 个基点，开启宽松周期，风险偏好回升"),
    ("2024-11-06", "BTC", "media", "fact", "特朗普赢得美国大选，加密友好预期推动比特币大涨创新高"),
    ("2024-12-04", "BTC", "media", "fact", "比特币首次突破 100000 美元，机构资金加速净流入"),
]

# ---------------------------------------------------------------------
# 二、合成填充用的模板（保证词典能识别出正确方向）
# ---------------------------------------------------------------------
TEMPLATES = {
    ("fact", 1): [
        "{a} 现货 ETF 单日资金净流入 {n} 亿美元，机构配置需求上升",
        "{a} 链上活跃地址数创阶段新高，网络需求增长，情绪乐观",
        "{a} 放量突破关键阻力位，资金持续流入，市场看多",
        "机构增持 {a}，托管余额回升，市场情绪转强",
    ],
    ("fact", -1): [
        "{a} 单日资金流出 {n} 亿美元，交易所余额上升，抛压担忧升温",
        "{a} 跌破关键支撑位，成交量萎缩，市场情绪转弱",
        "{a} 链上大额转账增加，市场担忧抛售，价格回调",
        "监管处罚消息影响 {a} 交易情绪，短期承压",
    ],
    ("opinion", 1): [
        "分析师看多 {a}，认为中期趋势仍然强势，建议增持",
        "我判断 {a} 上行空间仍大，逢回调可以加仓",
        "研报指 {a} 基本面改善，维持乐观判断",
    ],
    ("opinion", -1): [
        "分析师看空 {a}，提示短线回调风险，建议减持",
        "我判断 {a} 估值偏高，建议控制仓位等待企稳",
        "研报提示 {a} 存在下行风险，情绪偏谨慎悲观",
    ],
    ("emotion", 1): [
        "{a} 太强了，牛市来了，冲！",
        "满仓 {a}，看好后市大涨，兄弟们上车",
        "{a} 稳住，回调就是机会，坚决看多",
    ],
    ("emotion", -1): [
        "{a} 又崩了，割肉走人，太悲观了",
        "亏损严重，{a} 让人绝望，清仓算了",
        "{a} 快跑，感觉要爆仓了",
    ],
    ("policy", -1): [
        "监管机构就 {a} 交易风险发布提示，要求加强合规",
        "某国监管部门关注 {a} 市场波动，拟出台限制措施",
    ],
    ("policy", 1): [
        "监管机构明确 {a} 合规框架，行业不确定性下降，利好长期发展",
        "某国批准 {a} 相关试点，政策环境改善，市场看好",
    ],
}

PROMO_TEXTS = [
    "加群带你跟单 {a}，稳赚不赔，收益翻倍，扫码进群",
    "私信我带单 {a}，保本高收益，付费社群名额有限",
    "老师带你炒 {a}，内幕消息，稳赚，扫码加群",
]

SOURCE_POOL = (
    ["media"] * 30 + ["social"] * 22 + ["analyst"] * 12 + ["research"] * 10
    + ["minutes"] * 6 + ["official"] * 8 + ["group"] * 12
)

HANDLES = ["趋势猎手", "链上观察", "宏观笔记", "老王说币", "Quant猫", "加密小张",
           "CryptoLee", "市场先生", "分析师周", "研究员陈"]


def build_filler() -> list[dict]:
    rng = random.Random(SEED)
    rows: list[dict] = []
    for asset in ASSETS:
        latent = 0.0
        day = START
        while day <= END:
            latent = 0.55 * latent + rng.gauss(0.0, 1.0)
            # 密度：整体约 0.16 条/天/资产，保证覆盖率大致在 0.5 以上
            # 记忆系数 0.55：刻意让合成情绪不要太“粘”，
            # 否则高度自相关的情绪 + 趋势行情会造出虚假的预测力。
            n_items = rng.choices([0, 1, 2], weights=[72, 22, 6])[0]
            for _ in range(n_items):
                if latent > 0:
                    sign = 1
                elif latent < 0:
                    sign = -1
                else:
                    sign = rng.choice([1, -1])
                content_type = rng.choices(
                    ["fact", "opinion", "emotion", "policy", "promo"],
                    weights=[34, 32, 18, 6, 10])[0]
                if content_type == "promo":
                    text = rng.choice(PROMO_TEXTS).format(a=asset)
                else:
                    pool = TEMPLATES.get((content_type, sign))
                    if not pool:
                        pool = TEMPLATES[("opinion", sign)]
                    text = rng.choice(pool).format(a=asset, n=round(rng.uniform(0.5, 6.0), 1))
                source = rng.choice(SOURCE_POOL)
                hour = rng.randint(0, 23)
                minute = rng.randint(0, 59)
                pub = day + timedelta(hours=hour, minutes=minute)
                captured = pub + timedelta(minutes=rng.randint(1, 180))
                rows.append({
                    "\u53d1\u5e03\u65f6\u95f4": pub.strftime("%Y-%m-%d %H:%M:%S"),
                    "\u6293\u53d6\u65f6\u95f4": captured.strftime("%Y-%m-%d %H:%M:%S"),
                    "\u6765\u6e90": source,
                    "\u4f5c\u8005": rng.choice(HANDLES),
                    "\u8d44\u4ea7": asset,
                    "\u5185\u5bb9\u7c7b\u578b": content_type,
                    "\u5185\u5bb9": text,
                    "\u94fe\u63a5": f"https://example.invalid/demo/{len(rows)}",
                    "\u6570\u636e\u6027\u8d28": "\u5408\u6210\u793a\u4f8b",
                })
            day += timedelta(days=1)
    return rows


def build_anchors() -> list[dict]:
    rng = random.Random(SEED + 1)
    rows: list[dict] = []
    for date_str, asset, source, content_type, text in ANCHORS:
        pub = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(
            hours=rng.randint(0, 22), minutes=rng.randint(0, 59))
        captured = pub + timedelta(minutes=rng.randint(1, 90))
        rows.append({
            "\u53d1\u5e03\u65f6\u95f4": pub.strftime("%Y-%m-%d %H:%M:%S"),
            "\u6293\u53d6\u65f6\u95f4": captured.strftime("%Y-%m-%d %H:%M:%S"),
            "\u6765\u6e90": source,
            "\u4f5c\u8005": "\u516c\u5f00\u62a5\u9053" if source in ("media", "official") else rng.choice(HANDLES),
            "\u8d44\u4ea7": asset,
            "\u5185\u5bb9\u7c7b\u578b": content_type,
            "\u5185\u5bb9": text,
            "\u94fe\u63a5": "https://example.invalid/anchor",
            "\u6570\u636e\u6027\u8d28": "\u771f\u5b9e\u4e8b\u4ef6\u8282\u9009",
        })
    return rows


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = build_anchors() + build_filler()
    frame = pd.DataFrame(rows)
    frame["_t"] = pd.to_datetime(frame["\u53d1\u5e03\u65f6\u95f4"])
    frame = frame.sort_values("_t").drop(columns=["_t"]).reset_index(drop=True)
    frame.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    print("=" * 66)
    print("  \u6dee\u7b56 Quant \u00b7 \u79bb\u7ebf\u793a\u4f8b\u6570\u636e\u5df2\u751f\u6210")
    print("=" * 66)
    print(f"  \u8f93\u51fa\u6587\u4ef6 : {OUT_CSV}")
    print(f"  \u603b\u6761\u76ee : {len(frame)}")
    print(f"  \u771f\u5b9e\u4e8b\u4ef6 : {(frame['\u6570\u636e\u6027\u8d28'] == '\u771f\u5b9e\u4e8b\u4ef6\u8282\u9009').sum()}")
    print(f"  \u5408\u6210\u793a\u4f8b : {(frame['\u6570\u636e\u6027\u8d28'] == '\u5408\u6210\u793a\u4f8b').sum()}")
    print("  \u65f6\u95f4\u8303\u56f4 : " + frame["\u53d1\u5e03\u65f6\u95f4"].min()
          + "  \u2192  " + frame["\u53d1\u5e03\u65f6\u95f4"].max())
    print("  \u8d44\u4ea7\u5206\u5e03 : " + "  ".join(
        f"{k}={v}" for k, v in frame["\u8d44\u4ea7"].value_counts().items()))
    print("  \u63d0\u9192     : \u6559\u5b66\u793a\u4f8b\u6570\u636e\uff0c\u4e0d\u80fd\u5f53\u4f5c\u8bba\u6587\u8bc1\u636e\u3002")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())