# -*- coding: utf-8 -*-
"""品牌配置（全程序唯一的"改名入口"）。

这套程序所有露在外面的名字都从这里取：浏览器界面标题、黑窗口标题、
桌面快捷方式名字、研究报告封面与页脚。想改成你自己的版本，只改下面几行即可，
不用动任何一行逻辑代码。

改完之后：
  1. 双击「启动自动交易.bat」   → 界面标题 / 黑窗口标题自动跟着变
  2. 双击「生成研究报告.bat」   → 报告封面 / 页脚自动跟着变
  3. 双击「创建桌面快捷方式.bat」→ 桌面图标名字自动跟着变
"""

# =================== 下面这些都可以随便改 ===================

SCHOOL_NAME = "黄淮学院"                                     # 学校名
APP_NAME = "淮策 Quant"                         # 主标题（界面左上角、报告封面）
APP_EN = "Huaice Quant · Huanghuai College"    # 英文副标题
APP_TAGLINE = "黄淮学院 · 虚拟盘策略研究与回测"               # 一句话说明
SAFE_NOTE = "本地虚拟盘 · 不连交易接口 · 不用真实资金"        # 界面上那条安全提示

AUTHOR_NAME = ""          # 你的名字，例如 "张三 · 金融工程 2022 级"
ADVISOR_NAME = ""         # 指导老师，例如 "李老师"
SHORTCUT_NAME = "淮策 Quant"                          # 桌面快捷方式的名字

# 版本号一般不用改。如果哪天改了它，界面 app.js 里的 FRONT_VERSION 要一起改，
# 否则界面会弹红条提示"版本不一致"。
APP_VERSION = "3.0"

# ==================== 下面不用改 ====================


def author_line() -> str:
    """作者 / 指导老师署名（两个都留空就是没有署名）。"""
    items = []
    if AUTHOR_NAME:
        items.append(f"作者：{AUTHOR_NAME}")
    if ADVISOR_NAME:
        items.append(f"指导老师：{ADVISOR_NAME}")
    return " · ".join(items)


def author_suffix() -> str:
    line = author_line()
    return f" · {line}" if line else ""


def render(text: str) -> str:
    """把模板里的 {{占位符}} 换成上面的配置。"""
    table = {
        "{{APP_NAME}}": APP_NAME,
        "{{APP_EN}}": APP_EN,
        "{{APP_TAGLINE}}": APP_TAGLINE,
        "{{SCHOOL}}": SCHOOL_NAME,
        "{{VERSION}}": APP_VERSION,
        "{{SAFE_NOTE}}": SAFE_NOTE,
        "{{AUTHOR}}": AUTHOR_NAME,
        "{{ADVISOR}}": ADVISOR_NAME,
        "{{AUTHOR_LINE}}": author_line(),
        "{{AUTHOR_SUFFIX}}": author_suffix(),
    }
    out = str(text)
    for key, value in table.items():
        out = out.replace(key, value)
    return out


if __name__ == "__main__":
    print(render("{{APP_NAME}} v{{VERSION}}（{{APP_TAGLINE}}）"))
    print(f"学校：{SCHOOL_NAME}")
    print(f"署名：{author_line() or '（未填写）'}")
    print(f"桌面快捷方式：{SHORTCUT_NAME}")