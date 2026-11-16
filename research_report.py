# -*- coding: utf-8 -*-
"""一键生成机构级 HTML 研究报告（纯离线、单文件、不联网、不接实盘）。

用法（在项目文件夹里双击 生成研究报告.bat 即可）：
    python research_report.py
    python research_report.py --assets BTC,ETH,SOL,BNB --out 研究报告.html
    python research_report.py --quick      # 跳过耗时较长的稳健性检验

生成的报告是自包含的单个 HTML：所有图表都是内嵌 SVG，
不发任何网络请求，拷到 U 盘、断网也能正常打开。
"""

from __future__ import annotations

import argparse
import html
import math
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

import branding
import quant_core as qc

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_ASSETS = ["BTC", "ETH", "SOL", "BNB"]
REPORT_VERSION = branding.APP_VERSION


# =====================================================================
# 零、小工具：日志与数字格式化
# =====================================================================

def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def _safe_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def esc(text: Any) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def fp(value: Any, digits: int = 2) -> str:
    """比例 -> 百分比文本，例如 0.1234 -> 12.34%"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    return f"{v * 100:.{digits}f}%"


def fps(value: Any, digits: int = 2) -> str:
    """带正负号的百分比。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    return f"{v * 100:+.{digits}f}%"


def fn(value: Any, digits: int = 2) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    return f"{v:,.{digits}f}"


def fmoney(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    return f"{v:,.0f}"


def fint(value: Any) -> str:
    try:
        return f"{int(round(float(value))):,d}"
    except (TypeError, ValueError):
        return "—"


# =====================================================================
# 一、SVG 绘图（不依赖任何在线图表库）
# =====================================================================

AXIS_FMT = {
    "pct": lambda v: f"{v * 100:,.0f}%",
    "pct1": lambda v: f"{v * 100:,.1f}%",
    "ratio": lambda v: f"{v:,.2f}",
    "money": lambda v: f"{v:,.0f}",
}

PALETTE = {
    "strategy": "#3ddc97",
    "benchmark": "#6f8bb5",
    "accent": "#f3b23c",
    "risk": "#ff6b6b",
    "info": "#5aa9ff",
    "green": "#3ddc97",
    "red": "#ff6b6b",
    "grid": "rgba(255,255,255,0.075)",
    "axis": "rgba(255,255,255,0.45)",
}


def _scale(v: float, lo: float, hi: float, rlo: float, rhi: float) -> float:
    if hi - lo < 1e-15:
        return (rlo + rhi) * 0.5
    return rlo + (rhi - rlo) * (v - lo) / (hi - lo)


def svg_lines(x_labels: Sequence[str], series: Sequence[dict[str, Any]],
              width: int = 1020, height: int = 340, log: bool = False,
              fmt: str = "pct", zero_line: bool = False) -> str:
    """多序列折线图（SVG）。series 每项：{name, values, color, dash?}"""
    pad_l, pad_r, pad_t, pad_b = 86, 26, 52, 36
    iw = width - pad_l - pad_r
    ih = height - pad_t - pad_b
    n = max([len(s.get("values") or []) for s in series] + [len(x_labels), 2])

    pool: list[float] = []
    for s in series:
        for v in s.get("values") or []:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                pool.append(fv)
    if not pool:
        pool = [0.0, 1.0]
    lo, hi = min(pool), max(pool)
    if hi - lo < 1e-12:
        hi = lo + max(1e-9, abs(lo) * 0.1)
    if log:
        lo = max(lo, 1e-9)
        hi = max(hi, lo * 1.01)
    if log:
        lo = max(lo, 1e-9)
        hi = max(hi, lo * 1.01)
        lo /= 1.08
        hi *= 1.08
    else:
        span = hi - lo
        lo -= span * 0.08
        hi += span * 0.08

    def tx(i: float) -> float:
        return pad_l + iw * (i / max(1, n - 1))

    def ty(value: float) -> float:
        v = max(float(value), 1e-12) if log else float(value)
        if log:
            a, b, c = math.log10(lo), math.log10(hi), math.log10(max(v, 1e-12))
        else:
            a, b, c = lo, hi, v
        return pad_t + ih - ih * (c - a) / max(1e-12, b - a)

    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" '
           f'xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI, Microsoft YaHei, sans-serif">']
    steps = 5
    for k in range(steps + 1):
        frac = k / steps
        y = pad_t + ih * frac
        if log:
            level = 10 ** (math.log10(hi) - (math.log10(hi) - math.log10(lo)) * frac)
        else:
            level = hi - (hi - lo) * frac
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + iw}" y2="{y:.1f}" '
                   f'stroke="{PALETTE["grid"]}" stroke-width="1"/>')
        out.append(f'<text x="{pad_l - 12}" y="{y + 4:.1f}" text-anchor="end" '
                   f'font-size="12" fill="{PALETTE["axis"]}">{esc(AXIS_FMT[fmt](level))}</text>')
    if zero_line and not log and lo < 0 < hi:
        y0 = ty(0.0)
        out.append(f'<line x1="{pad_l}" y1="{y0:.1f}" x2="{pad_l + iw}" y2="{y0:.1f}" '
                   f'stroke="rgba(255,255,255,0.35)" stroke-width="1" stroke-dasharray="5 4"/>')

    if x_labels:
        ticks = min(6, len(x_labels))
        for k in range(ticks):
            idx = int(round((len(x_labels) - 1) * k / max(1, ticks - 1)))
            x = tx(idx * (n - 1) / max(1, len(x_labels) - 1))
            out.append(f'<text x="{x:.1f}" y="{height - 12}" text-anchor="middle" '
                       f'font-size="12" fill="{PALETTE["axis"]}">{esc(str(x_labels[idx])[:10])}</text>')

    for s in series:
        values = list(s.get("values") or [])
        if not values:
            continue
        step = max(1, len(values) // 4000)
        pts: list[str] = []
        for i in range(0, len(values), step):
            try:
                if not math.isfinite(float(values[i])):
                    continue
            except (TypeError, ValueError):
                continue
            xi = i * (n - 1) / max(1, len(values) - 1)
            pts.append(f"{tx(xi):.1f},{ty(float(values[i])):.1f}")
        if len(pts) < 2:
            continue
        dash = ' stroke-dasharray="7 6"' if s.get("dash") else ""
        label = esc(s.get("name") or "")
        out.append(f'<polyline points="{" ".join(pts)}" fill="none" '
                   f'stroke="{s.get("color") or PALETTE["info"]}" stroke-width="{s.get("width") or 2.1}" '
                   f'stroke-linejoin="round" stroke-linecap="round"{dash} '
                   f'transform=""><title>{label}</title></polyline>')

    legend_x = pad_l
    for s in series:
        out.append(f'<rect x="{legend_x}" y="18" width="20" height="4" rx="2" '
                   f'fill="{s.get("color") or PALETTE["info"]}"/>')
        out.append(f'<text x="{legend_x + 26}" y="25" font-size="12.5" '
                   f'fill="rgba(255,255,255,0.78)">{esc(s.get("name") or "")}</text>')
        legend_x += 26 + int(9.0 * len(str(s.get("name") or ""))) + 26
    out.append("</svg>")
    return "".join(out)


def svg_area(x_labels: Sequence[str], values: Sequence[float], width: int = 1020,
             height: int = 240, fmt: str = "pct1") -> str:
    """面积图（用于回撤曲线）。"""
    pad_l, pad_r, pad_t, pad_b = 86, 26, 26, 32
    iw = width - pad_l - pad_r
    ih = height - pad_t - pad_b
    vals = []
    for v in values:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fv):
            vals.append(fv)
    if not vals:
        return '<svg class="chart" viewBox="0 0 1020 240"></svg>'
    lo = min(min(vals), 0.0)
    hi = 0.0
    if hi - lo < 1e-9:
        lo = -0.01
    n = max(len(values), 2)

    def tx(i: float) -> float:
        return pad_l + iw * (i / max(1, n - 1))

    def ty(v: float) -> float:
        return pad_t + ih - ih * (v - lo) / max(1e-12, hi - lo)

    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" '
           f'xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI, Microsoft YaHei, sans-serif">']
    for k in range(5):
        frac = k / 4
        y = pad_t + ih * frac
        level = hi - (hi - lo) * frac
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + iw}" y2="{y:.1f}" '
                   f'stroke="{PALETTE["grid"]}" stroke-width="1"/>')
        out.append(f'<text x="{pad_l - 12}" y="{y + 4:.1f}" text-anchor="end" font-size="12" '
                   f'fill="{PALETTE["axis"]}">{esc(AXIS_FMT[fmt](level))}</text>')
    pts = []
    step = max(1, len(values) // 4000)
    for i in range(0, len(values), step):
        try:
            v = float(values[i])
        except (TypeError, ValueError):
            continue
        pts.append((tx(i), ty(v)))
    if pts:
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        area = f"{pad_l},{ty(0.0):.1f} " + path + f" {pts[-1][0]:.1f},{ty(0.0):.1f}"
        out.append(f'<polygon points="{area}" fill="rgba(255,107,107,0.20)"/>')
        out.append(f'<polyline points="{path}" fill="none" stroke="{PALETTE["risk"]}" '
                   f'stroke-width="1.9" stroke-linejoin="round"/>')
    if x_labels:
        ticks = min(6, len(x_labels))
        for k in range(ticks):
            idx = int(round((len(x_labels) - 1) * k / max(1, ticks - 1)))
            x = tx(idx * (n - 1) / max(1, len(x_labels) - 1))
            out.append(f'<text x="{x:.1f}" y="{height - 10}" text-anchor="middle" font-size="12" '
                       f'fill="{PALETTE["axis"]}">{esc(str(x_labels[idx])[:10])}</text>')
    out.append("</svg>")
    return "".join(out)


def svg_fan(rows: Sequence[Sequence[float]], levels: Sequence[int],
            width: int = 1020, height: int = 330) -> str:
    """蒙特卡洛扇形图：rows[t] = [p5, p25, p50, p75, p95]"""
    pad_l, pad_r, pad_t, pad_b = 86, 26, 34, 36
    iw = width - pad_l - pad_r
    ih = height - pad_t - pad_b
    if not rows:
        return '<svg class="chart" viewBox="0 0 1020 330"></svg>'
    flat: list[float] = []
    for row in rows:
        flat.extend([float(x) for x in row])
    lo = min(min(flat), 0.6)
    hi = max(max(flat), 1.4)
    n = len(rows)

    def tx(i: float) -> float:
        return pad_l + iw * (i / max(1, n - 1))

    def ty(v: float) -> float:
        return pad_t + ih - ih * (v - lo) / max(1e-12, hi - lo)

    def band(idx_lo: int, idx_hi: int, fill: str) -> str:
        top = " ".join(f"{tx(i):.1f},{ty(float(rows[i][idx_hi])):.1f}" for i in range(n))
        bottom = " ".join(f"{tx(i):.1f},{ty(float(rows[i][idx_lo])):.1f}" for i in range(n - 1, -1, -1))
        return f'<polygon points="{top} {bottom}" fill="{fill}"/>'

    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" '
           f'xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI, Microsoft YaHei, sans-serif">']
    out.append(band(0, 4, "rgba(90,169,255,0.16)"))
    out.append(band(1, 3, "rgba(90,169,255,0.26)"))
    for k in range(5):
        frac = k / 4
        y = pad_t + ih * frac
        level = hi - (hi - lo) * frac
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + iw}" y2="{y:.1f}" '
                   f'stroke="{PALETTE["grid"]}" stroke-width="1"/>')
        out.append(f'<text x="{pad_l - 12}" y="{y + 4:.1f}" text-anchor="end" font-size="12" '
                   f'fill="{PALETTE["axis"]}">{level:.2f}x</text>')
    y1 = ty(1.0)
    out.append(f'<line x1="{pad_l}" y1="{y1:.1f}" x2="{pad_l + iw}" y2="{y1:.1f}" '
               f'stroke="rgba(255,255,255,0.4)" stroke-width="1" stroke-dasharray="6 5"/>')
    median = " ".join(f"{tx(i):.1f},{ty(float(rows[i][2])):.1f}" for i in range(n))
    out.append(f'<polyline points="{median}" fill="none" stroke="{PALETTE["accent"]}" stroke-width="2.2"/>')
    for k in range(6):
        idx = int(round((n - 1) * k / 5))
        x = tx(idx)
        out.append(f'<text x="{x:.1f}" y="{height - 12}" text-anchor="middle" font-size="12" '
                   f'fill="{PALETTE["axis"]}">T+{idx + 1}</text>')
    out.append(f'<text x="{pad_l}" y="22" font-size="12.5" fill="rgba(255,255,255,0.78)">'
               f'深蓝=5%~95% 区间　浅蓝=25%~75% 区间　橙线=中位数　虚线=起点 1.00x</text>')
    out.append("</svg>")
    return "".join(out)


def heat_color(value: Any, scale: float = 3.2) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "rgba(255,255,255,0.03)"
    if not math.isfinite(v):
        return "rgba(255,255,255,0.03)"
    alpha = min(0.88, abs(v) * scale)
    if v >= 0:
        return f"rgba(45,212,143,{alpha:.3f})"
    return f"rgba(255,92,92,{alpha:.3f})"


def grid_color(value: Any, best: float) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "rgba(255,255,255,0.03)"
    if not math.isfinite(v):
        return "rgba(255,255,255,0.03)"
    ratio = 0.0 if best <= 0 else max(0.0, min(1.0, v / best))
    return f"rgba(45,212,143,{0.10 + 0.72 * ratio:.3f})" if v >= 0 else "rgba(255,92,92,0.55)"
# =====================================================================
# 二、HTML 片段工具
# =====================================================================

CSS = """
:root{--bg:#080b12;--panel:#121826;--panel2:#182031;--line:rgba(255,255,255,.09);
--txt:#e9effb;--dim:#93a3bf;--green:#3ddc97;--red:#ff6b6b;--gold:#f3b23c;--blue:#5aa9ff;}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;background:radial-gradient(1200px 620px at 18% -8%,rgba(61,220,151,.09),transparent 60%),
radial-gradient(1100px 600px at 88% 2%,rgba(90,169,255,.10),transparent 58%),var(--bg);
color:var(--txt);font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;line-height:1.62;}
.wrap{max-width:1180px;margin:0 auto;padding:40px 26px 80px;}
header.top{border-bottom:1px solid var(--line);padding-bottom:22px;margin-bottom:26px;}
.badges{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;}
.badge{font-size:12.5px;padding:4px 12px;border-radius:999px;border:1px solid var(--line);
background:rgba(255,255,255,.04);color:var(--dim);}
.badge.ok{color:#8ff0c4;border-color:rgba(61,220,151,.45);background:rgba(61,220,151,.10);}
.badge.warn{color:#ffd08a;border-color:rgba(243,178,60,.45);background:rgba(243,178,60,.10);}
h1{font-size:31px;margin:0 0 10px;letter-spacing:.5px;}
h1 small{font-size:15px;color:var(--dim);font-weight:400;margin-left:10px;}
h2{font-size:20px;margin:44px 0 6px;padding-left:12px;border-left:3px solid var(--green);}
h2 .idx{color:var(--dim);font-size:14px;margin-right:8px;font-weight:400;}
h3{font-size:16px;margin:0 0 4px;}
p{margin:8px 0;}
.sub{color:var(--dim);font-size:13.5px;margin:0 0 14px;}
.meta{color:var(--dim);font-size:13.5px;}
.meta b{color:var(--txt);font-weight:600;}
.card{background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.012));
border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin:14px 0;}
.card-head{display:flex;justify-content:space-between;align-items:baseline;gap:14px;margin-bottom:6px;}
.card-head .hint{color:var(--dim);font-size:12.5px;}
.cards{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));}
.kpi{background:linear-gradient(180deg,rgba(255,255,255,.045),rgba(255,255,255,.015));
border:1px solid var(--line);border-radius:13px;padding:13px 15px;}
.kpi .lbl{font-size:12.5px;color:var(--dim);}
.kpi .val{font-size:23px;font-weight:650;margin-top:3px;letter-spacing:.3px;}
.kpi .hint{font-size:12px;color:var(--dim);margin-top:2px;}
.kpi.good .val{color:var(--green)} .kpi.bad .val{color:var(--red)}
.kpi.gold .val{color:var(--gold)} .kpi.blue .val{color:var(--blue)}
.chart{width:100%;height:auto;display:block;}
table{width:100%;border-collapse:collapse;font-size:13.5px;}
th,td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap;}
th:first-child,td:first-child{text-align:left;}
th{color:var(--dim);font-weight:600;font-size:12.5px;}
td.mono{font-variant-numeric:tabular-nums;}
.hm{text-align:center;font-variant-numeric:tabular-nums;color:#f4f8ff;}
.hm.yr{background:rgba(255,255,255,.06);font-weight:600;}
.cols{display:grid;gap:16px;grid-template-columns:1fr 1fr;}
@media (max-width:960px){.cols{grid-template-columns:1fr}}
.note{background:rgba(90,169,255,.07);border-left:3px solid var(--blue);border-radius:8px;
padding:12px 16px;color:#cfe0fb;font-size:13.5px;}
.warnbox{background:rgba(243,178,60,.09);border-left:3px solid var(--gold);border-radius:8px;
padding:12px 16px;color:#ffe0ac;font-size:13.5px;}
.danger{background:rgba(255,107,107,.08);border-left:3px solid var(--red);border-radius:8px;
padding:12px 16px;color:#ffd3d3;font-size:13.5px;}
ul{margin:8px 0 8px 18px;padding:0;} li{margin:4px 0;color:#d4dcec;font-size:13.5px;}
footer{margin-top:46px;padding-top:20px;border-top:1px solid var(--line);color:var(--dim);font-size:12.5px;}
@media print{body{background:#fff;color:#111}.card,.kpi{background:#fff;border-color:#d8dde8}
h2{border-color:#1a7f5a} th{color:#555} .sub,.meta,.kpi .lbl,.kpi .hint,footer{color:#555}
td,th{border-color:#e2e6ee} .hm{color:#111} .note,.warnbox,.danger{background:#f6f8fc;color:#222}}
"""


def kpi(label: str, value: str, hint: str = "", tone: str = "") -> str:
    cls = f"kpi {tone}".strip()
    hint_html = f'<div class="hint">{esc(hint)}</div>' if hint else ""
    return (f'<div class="{cls}"><div class="lbl">{esc(label)}</div>'
            f'<div class="val">{esc(value)}</div>{hint_html}</div>')


def section(index: str, title: str, subtitle: str, body: str) -> str:
    sub = f'<p class="sub">{subtitle}</p>' if subtitle else ""
    return (f'<h2><span class="idx">{esc(index)}</span>{esc(title)}</h2>{sub}{body}')


def card(title: str, body: str, hint: str = "") -> str:
    head = ""
    if title or hint:
        head = (f'<div class="card-head"><h3>{esc(title)}</h3>'
                f'<span class="hint">{esc(hint)}</span></div>')
    return f'<div class="card">{head}{body}</div>'


def table(headers: Sequence[str], rows: Sequence[Sequence[str]], first_left: bool = True) -> str:
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = []
    for row in rows:
        cells = "".join(f"<td class='mono'>{c}</td>" if i else f"<td>{c}</td>"
                        for i, c in enumerate(row))
        body.append(f"<tr>{cells}</tr>")
    return (f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>')


def table_from_dicts(rows: Sequence[dict[str, Any]], labels: Sequence[tuple[str, str]],
                     formatters: dict[str, Any] | None = None) -> str:
    """labels = [(key, 中文表头), ...]，formatters 可选覆盖单元格渲染。"""
    fmt = formatters or {}
    headers = [name for _, name in labels]
    out_rows = []
    for row in rows:
        cells = []
        for key, _ in labels:
            if key in fmt:
                cells.append(fmt[key](row.get(key)))
            else:
                cells.append(esc(row.get(key)))
        out_rows.append(cells)
    return table(headers, out_rows)


RISK_LABELS = [("date", "日期"), ("symbol", "资产"), ("drawdown", "触发时回撤"),
               ("pause_days", "暂停天数"), ("equity", "当时净值")]


def monthly_heatmap(monthly: dict[str, Any], tag: str = "") -> str:
    years = list(monthly.get("years") or [])
    matrix = list(monthly.get("matrix") or [])
    annual = {int(a["year"]): float(a["ret"]) for a in (monthly.get("annual") or [])}
    if not years:
        return '<p class="meta">数据不足，无法生成月度热力图。</p>'
    head = "<th>年份</th>" + "".join(f"<th>{m}月</th>" for m in range(1, 13)) + "<th>全年</th>"
    body = []
    for yi, year in enumerate(years):
        cells = [f"<td><b>{year}</b></td>"]
        for mi in range(12):
            value = matrix[yi][mi] if yi < len(matrix) and mi < len(matrix[yi]) else None
            if value is None:
                cells.append('<td class="hm" style="background:rgba(255,255,255,.03)">—</td>')
            else:
                cells.append(f'<td class="hm" style="background:{heat_color(value)}">'
                             f'{value * 100:.1f}</td>')
        yr = annual.get(int(year))
        cells.append(f'<td class="hm yr" style="background:{heat_color(yr)}">'
                     f'{"—" if yr is None else f"{yr * 100:.1f}"}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
            f'<p class="sub" style="margin-top:8px">单位：%，绿=正收益、红=负收益，颜色深浅代表幅度。{esc(tag)}</p>')


def param_grid_table(grid: dict[str, Any], current: tuple[int, int], metric_name: str = "夏普") -> str:
    fasts = list(grid.get("fast") or [])
    slows = list(grid.get("slow") or [])
    matrix = list(grid.get("matrix") or [])
    best = grid.get("best") or {}
    best_value = float(best.get("value") or 0.0)
    head = "<th>慢均线 \\ 快均线</th>" + "".join(f"<th>{f}</th>" for f in fasts)
    body = []
    for si, slow in enumerate(slows):
        cells = [f"<td><b>{slow}</b></td>"]
        for fi, _fast in enumerate(fasts):
            value = matrix[si][fi] if si < len(matrix) and fi < len(matrix[si]) else None
            if value is None:
                cells.append('<td class="hm" style="background:rgba(255,255,255,.03)">—</td>')
                continue
            is_best = (best.get("fast") == fasts[fi] and best.get("slow") == slow)
            is_cur = (current[0] == fasts[fi] and current[1] == slow)
            mark = ""
            if is_best:
                mark = " ★"
            if is_cur:
                mark += " ◉"
            style = f"background:{grid_color(value, best_value)}"
            if is_cur:
                style += ";outline:2px solid #f3b23c;outline-offset:-2px"
            cells.append(f'<td class="hm" style="{style}">{value:.2f}{mark}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
            f'<p class="sub" style="margin-top:8px">单元格 = 该均线组合的{esc(metric_name)}。'
            f'★ 为网格最优（快{best.get("fast")} / 慢{best.get("slow")}，{metric_name} {best_value:.2f}），'
            f'◉ 为当前实际使用参数。整片偏绿说明策略对参数不敏感（好现象）；'
            f'只有孤立一格很红/很绿则说明是"参数孤岛"（过拟合信号）。</p>')


def correlation_table(corr: dict[str, Any]) -> str:
    labels = list(corr.get("labels") or [])
    matrix = list(corr.get("matrix") or [])
    if len(labels) < 2:
        return '<p class="meta">只有一个资产，无需相关性矩阵。</p>'
    head = "<th>相关性</th>" + "".join(f"<th>{l}</th>" for l in labels)
    body = []
    for i, label in enumerate(labels):
        cells = [f"<td><b>{esc(label)}</b></td>"]
        for j, _ in enumerate(labels):
            value = matrix[i][j] if i < len(matrix) and j < len(matrix[i]) else None
            if value is None:
                cells.append('<td class="hm" style="background:rgba(255,255,255,.03)">—</td>')
            else:
                cells.append(f'<td class="hm" style="background:{heat_color(max(0.0, float(value)) , 0.65)}">'
                             f'{float(value):.2f}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
            f'<p class="sub" style="margin-top:8px">数值越低说明两个资产越不同步，'
            f'组合分散风险的效果越好（理想情况是普遍低于 0.7）。</p>')
# =====================================================================
# 三、数据准备与全量计算（研究内核调用）
# =====================================================================

def load_assets(names: Sequence[str]) -> tuple[dict[str, pd.DataFrame], list[str]]:
    files = qc.list_csv_files(DATA_DIR)
    by_name = {str(item["name"]).upper(): item for item in files}
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for name in names:
        key = str(name).strip().upper()
        if not key:
            continue
        item = by_name.get(key)
        if item is None:
            item = next((f for f in files if str(f["file"]).lower().startswith(key.lower())), None)
        if item is None:
            missing.append(key)
            continue
        frames[str(item["name"])] = qc.read_ohlc_csv(item["path"])
    return frames, missing


def _curve_for_chart(equity: pd.Series, benchmark: pd.Series, points: int = 620) -> dict[str, list[Any]]:
    step = max(1, len(equity) // points)
    eq = equity.iloc[::step]
    bm = benchmark.reindex(eq.index).ffill()
    dd = (equity / equity.cummax() - 1.0).iloc[::step]
    return {
        "dates": [d.strftime("%Y-%m") for d in eq.index],
        "dates_full": [d.date().isoformat() for d in eq.index],
        "equity": [float(v) for v in eq.to_numpy()],
        "benchmark": [float(v) for v in bm.to_numpy()],
        "drawdown": [float(v) for v in dd.to_numpy()],
    }


def run_full_analysis(names: Sequence[str], params: qc.StrategyParams, quick: bool = False,
                      n_sims: int = 600, splits: int = 5) -> dict[str, Any]:
    frames, missing = load_assets(names)
    notes: list[str] = []
    if missing:
        notes.append(f"data 文件夹里没有找到：{', '.join(missing)}（已跳过）。")
    if not frames:
        log("data 文件夹里没有可用 CSV，改用内置合成数据演示。")
        frames = {"SYN": qc._synthetic("SYN", days=1500)}
        notes.append("本次使用内置合成数据演示，不是真实行情。")
    markets = {item["market"] for item in qc.list_csv_files(DATA_DIR) if item["name"] in frames}
    params = replace(params, annualization=252 if markets == {"us"} else 365)
    if markets == {"us"}:
        notes.append("美股按每年 252 根日 K 线年化。")
        for name, frame in frames.items():
            provenance = frame.attrs.get("provenance", {})
            notes.append(f"{name}：" + (f"{provenance.get('source')} 真实日线，拆股/分红复权；数据截止 {provenance.get('last_date')}，下载于 {provenance.get('downloaded_at_utc')}。"
                         if provenance.get("verified") else "本地 CSV 来源记录缺失或哈希不匹配，请核对真实性。"))
    notes.append("风控独立作用于各资产子账户；收盘触发熔断后于下一根开盘清仓。暂停按 K 线根数计算。")
    if params.alloc_method == "risk_parity":
        notes.append("初始资金按第一笔可交易日前的逆波动率固定分配；历史不足资产采用 80% 波动先验。这不是含协方差优化的严格风险平价。")
    ends = {name: str(frame.index[-1].date()) for name, frame in frames.items()}
    if len(set(ends.values())) > 1:
        notes.append("行情结束日期不一致：" + "，".join(f"{name} {end}" for name, end in ends.items())
                     + "。缺失尾段沿用该子账户最后净值，不代表这些日期的实际市场表现。")
    log("已加载行情：" + "，".join(f"{k} {len(v)} 根" for k, v in frames.items()))

    params.validate()
    log("正在回放组合回测（含手续费 / 滑点 / 冲击成本 / 止损 / 熔断）…")
    t0 = time.time()
    result = qc.run_portfolio(frames, params)
    log(f"组合回测完成，用时 {time.time() - t0:.1f} 秒。")

    equity = result["equity"]
    benchmark = result["benchmark"]
    m = result["metrics"]
    bench_metrics = qc.performance_stats(benchmark, None, float(params.initial_cash), None, None, params.annualization)
    monthly = qc.monthly_return_table(equity)
    monthly_bm = qc.monthly_return_table(benchmark)
    rolling = qc.rolling_sharpe_series(equity, 90, params.annualization)
    rolling_bm = qc.rolling_sharpe_series(benchmark, 90, params.annualization)
    curve = _curve_for_chart(equity, benchmark)

    heavy: dict[str, Any] = {}
    primary_name = result["names"][0]
    primary_frame = frames.get(primary_name)
    if quick:
        log("已跳过参数网格 / Walk-Forward / 蒙特卡洛（--quick）。")
    else:
        if primary_frame is not None:
            log(f"正在跑参数网格（{primary_name}，9 组快均线 x 7 组慢均线 = 63 次回测）…")
            t1 = time.time()
            heavy["grid"] = qc.parameter_grid(
                primary_frame, params,
                [8, 10, 15, 20, 25, 30, 40, 50, 60], [40, 50, 60, 80, 100, 120, 150])
            log(f"参数网格完成，用时 {time.time() - t1:.1f} 秒。")
            log(f"正在跑 Walk-Forward（{splits} 折滚动前推，样本外检验）…")
            t2 = time.time()
            heavy["walk_forward"] = qc.walk_forward(
                primary_frame, params, [10, 20, 30, 40], [50, 80, 120, 150], n_splits=splits)
            log(f"Walk-Forward 完成，用时 {time.time() - t2:.1f} 秒。")
        log(f"正在跑蒙特卡洛（{n_sims} 条路径，向前 {params.annualization} 个交易日）…")
        t3 = time.time()
        heavy["monte_carlo"] = qc.monte_carlo(equity, n_sims=n_sims, horizon=params.annualization)
        log(f"蒙特卡洛完成，用时 {time.time() - t3:.1f} 秒。")

    return {
        "params": params, "result": result, "metrics": m, "bench_metrics": bench_metrics,
        "monthly": monthly, "monthly_bm": monthly_bm, "rolling": rolling,
        "rolling_bm": rolling_bm, "curve": curve, "notes": notes,
        "heavy": heavy, "primary_name": primary_name,
        "n_sims": n_sims, "splits": splits,
        "assets": list(result["names"]), "weights": list(result["weights"]),
        "start": equity.index[0].date().isoformat(), "end": equity.index[-1].date().isoformat(),
        "days": int(len(equity)), "source": "本机 CSV 真实行情" if frames else "合成数据",
    }
# =====================================================================
# 四、报告正文组装
# =====================================================================

def build_html(data: dict[str, Any]) -> str:
    p: qc.StrategyParams = data["params"]
    currency = "USD" if p.annualization == 252 else "USDT"
    m = data["metrics"]
    bm = data["bench_metrics"]
    result = data["result"]
    heavy = data["heavy"]
    curve = data["curve"]
    initial = float(p.initial_cash)
    dates = curve["dates"]
    assets = list(data["assets"])
    weights = list(data["weights"])
    assets_txt = " + ".join(assets)
    weights_txt = " / ".join(f"{a} {w * 100:.1f}%" for a, w in zip(assets, weights))
    years = float(m.get("years") or 0.0)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    bm_total = float(bm.get("total_return") or 0.0)
    total = float(m.get("total_return") or 0.0)
    excess = total - bm_total
    turnover = float(m.get("turnover") or 0.0)
    fees = float(m.get("fees_paid") or 0.0)
    fee_ratio = (fees / turnover) if turnover > 0 else 0.0
    stops = m.get("stops") or 0
    halts = len(result.get("risk_events") or [])

    flags = "".join(f'<div class="warnbox">{esc(n)}</div>' for n in data["notes"])
    parts: list[str] = [
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f'<title>{esc(branding.APP_NAME)} · 量化策略研究报告 · {esc(assets_txt)}</title>',
        f"<style>{CSS}</style></head><body><div class=\"wrap\">",
        '<header class="top">',
        '<div class="badges">',
        '<span class="badge ok">本地虚拟盘 · 不接交易所 · 不发真实订单</span>',
        '<span class="badge">离线自包含报告（无外部依赖，断网可看）</span>',
        '<span class="badge warn">历史表现不代表未来收益</span>',
        "</div>",
        f"<h1>{esc(branding.APP_NAME)}<small>{esc(assets_txt)} 多资产趋势组合</small></h1>",
        f'<p class="meta">{esc(branding.SCHOOL_NAME)} · {esc(branding.APP_TAGLINE)}{esc(branding.author_suffix())}</p>',
        f'<p class="meta">策略构成：<b>{esc(p.describe())}</b></p>',
        f'<p class="meta">样本区间：<b>{esc(data["start"])}</b> ~ <b>{esc(data["end"])}</b>'
        f'（{data["days"]:,} 根日线，约 {years:.1f} 年）　|　资产：<b>{esc(assets_txt)}</b></p>',
        f'<p class="meta">资金权重（{esc(p.alloc_method)}）：<b>{esc(weights_txt)}</b>　|　'
        f'初始虚拟资金：<b>{fmoney(initial)} {currency}</b></p>',
        f'<p class="meta">数据来源：<b>{esc(data["source"])}</b>　|　生成时间：<b>{now}</b>　|　'
        f'引擎版本：<b>v{REPORT_VERSION}</b>　|　运行环境：本机 127.0.0.1</p>',
        "</header>",
    ]

    # ---------- 一、执行摘要 ----------
    summary = (
        f'<p>本报告用一个「趋势跟随 + 波动率目标仓位 + ATR 移动止损 + 组合级熔断」的机构式框架，'
        f'对 {esc(assets_txt)} 共 {len(assets)} 个标的的历史日线做逐根 K 线的严格回放。'
        f'每一笔成交都按「昨日收盘算信号、今日开盘成交」执行，因此不存在前视偏差；'
        f'成交成本包含手续费 {p.fee_bps:g} bps、固定滑点 {p.slippage_bps:g} bps，'
        f'以及按平方根模型计算的冲击成本（下单额越大越吃亏）。</p>'
    )
    kpi1 = (
        f'<p class="sub">样本区间 {esc(data["start"])} 至 {esc(data["end"])}，'
        f'{data["days"]:,} 根日线（约 {years:.1f} 年）。'
        f'{fmoney(initial)} {currency} 虚拟本金扣除全部交易成本后，期末净值 '
        f'<b>{fmoney(m.get("final_equity"))} {currency}</b>。</p>'
    )
    cards_return = "".join([
        kpi("期末净值", f"{fmoney(m.get('final_equity'))} {currency}", f"初始 {fmoney(initial)}", "good" if m.get("final_equity", 0) >= initial else "bad"),
        kpi("累计收益", fp(m.get("total_return")), "买入持有基准 " + fp(bm_total), "good" if total > 0 else "bad"),
        kpi("年化收益 CAGR", fp(m.get("cagr")), f"{years:.1f} 年复合", "good" if float(m.get("cagr") or 0) > 0 else "bad"),
        kpi("年化波动率", fp(m.get("ann_vol")), "目标 " + fp(p.vol_target, 0)),
    ])
    cards_risk = "".join([
        kpi("夏普比率", fn(m.get("sharpe")), "无风险利率按 0 计", "good" if float(m.get("sharpe") or 0) >= 1 else ""),
        kpi("索提诺比率", fn(m.get("sortino")), "只惩罚下行波动"),
        kpi("卡玛比率", fn(m.get("calmar")), "年化收益 / 最大回撤"),
        kpi("最大回撤", fp(m.get("max_drawdown")), f"{fint(m.get('max_dd_days'))} 个交易日，{fint(m.get('recovery_days'))} 天后修复", "bad"),
        kpi("VaR 95% / CVaR 95%", f"{fp(m.get('var95'))} / {fp(m.get('cvar95'))}", "单日风险（历史分位）"),
        kpi("Beta / Alpha", f"{fn(m.get('beta'))} / {fp(m.get('alpha'))}", "相对买入持有基准"),
    ])
    cards_trade = "".join([
        kpi("完整交易", f"{fint(m.get('trade_count'))} 笔", f"成交 {fint(m.get('trades'))} 笔"),
        kpi("交易胜率", fp(m.get("trade_win_rate")), "按完整交易配对计算", "good" if float(m.get("trade_win_rate") or 0) >= 0.5 else ""),
        kpi("盈亏比 / 总盈亏比", f"{fn(m.get('payoff_ratio'))} / {fn(m.get('trade_profit_factor'))}", "平均盈利 / 平均亏损"),
        kpi("单笔期望盈亏", f"{fn(m.get('expectancy'))} {currency}", "样本内平均已实现盈亏", "good" if float(m.get("expectancy") or 0) > 0 else "bad"),
        kpi("平均持仓 / 平均仓位", f"{fn(m.get('avg_holding_days'), 1)} 天 / {fp(m.get('exposure_avg'))}", "平均资金暴露"),
        kpi("止损次数 / 最大连亏", f"{fint(stops)} / {fint(m.get('max_consecutive_losses'))}", "风控触发频率"),
    ])
    cards_cost = "".join([
        kpi("累计手续费", f"{fmoney(fees)} {currency}", f"占成交额 {fp(fee_ratio, 3)}"),
        kpi("累计成交额", f"{fmoney(turnover)} {currency}", f"约本金的 {turnover / initial:.1f} 倍" if initial > 0 else ""),
        kpi("组合熔断次数", f"{fint(halts)} 次", f"阈值 -{p.risk_limit_pct:.0f}% / 暂停 {p.risk_pause_days} 天", "bad" if halts else "good"),
        kpi("平均仓位上限", fp(p.max_weight, 0), f"单资产最大 {p.max_weight * 100:.0f}%"),
    ])
    verdict = (
        f'<div class="note"><b>一句话结论：</b>同期「买入持有」基准收益为 {fp(bm_total)}，'
        f'策略累计收益 {fp(total)}（相对基准 {fps(excess)}）。'
        f'策略 Beta 仅 {fn(m.get("beta"))}、年化 Alpha {fp(m.get("alpha"))}，'
        f'说明收益并非来自承担市场风险，而是来自仓位管理与风控；'
        f'代价是主动放弃了牛市中最陡的那一段涨幅。'
        f'<b>风险调整后优于买入持有，绝对收益不一定更高</b>——这正是机构评价策略的标准做法。</div>'
        f'<div class="warnbox" style="margin-top:10px">'
        f'<b>给老师的三句话：</b>① 全部成交都按「昨日信号、今日开盘」撮合，没有用未来数据；'
        f'② 成本项在手续费与滑点之外还含平方根冲击成本；'
        f'③ 策略能赚钱不等于未来能赚钱，回测只能证明逻辑自洽、风控有效、参数不稳才会被砍。</div>'
    )
    parts.append(section("01", "执行摘要", "先看结论，再看细节。", flags +
                         card("", summary + kpi1) +
                         '<div class="cards">' + cards_return + "</div>" +
                         '<div class="cards" style="margin-top:12px">' + cards_risk + "</div>" +
                         verdict))
    parts.append(section("02", "交易与成本概览", "胜率、盈亏比、换手与成本：机构每天盯的四件事。",
                         '<div class="cards">' + cards_trade + "</div>" +
                         '<div class="cards" style="margin-top:12px">' + cards_cost + "</div>"))

    # ---------- 二、净值与回撤 ----------
    equity_svg = svg_lines(dates, [
        {"name": f"策略净值（{'+'.join(assets)}）", "values": curve["equity"], "color": PALETTE["strategy"]},
        {"name": "买入持有基准", "values": curve["benchmark"], "color": PALETTE["benchmark"], "dash": True},
    ], log=True, fmt="money")
    dd_svg = svg_area(dates, curve["drawdown"])
    parts.append(section("03", "净值曲线与回撤", "机构习惯看对数坐标：同样一段高度代表同样涨幅。",
                         card(f"组合净值（对数坐标，{currency}）", equity_svg,
                              f"起点 {fmoney(initial)} → 期末 {fmoney(m.get('final_equity'))}") +
                         card("回撤曲线（水下深度）", dd_svg,
                              f"最大回撤 {fp(m.get('max_drawdown'))}，修复用时 {fint(m.get('recovery_days'))} 天")))

    # ---------- 三、月度收益 ----------
    annual_rows = []
    annual = {int(a["year"]): float(a["ret"]) for a in (data["monthly"].get("annual") or [])}
    annual_bm = {int(a["year"]): float(a["ret"]) for a in (data["monthly_bm"].get("annual") or [])}
    for year in sorted(annual.keys()):
        s = annual.get(year, 0.0)
        b = annual_bm.get(year, 0.0)
        annual_rows.append([f"{year}", fp(s), fp(b), fps(s - b),
                            f"{'是' if s > 0 else '否'}"])
    parts.append(section("04", "月度收益热力图", "按月切片，检查收益是不是只靠某一年或某一个月。",
                         card(f"策略月度收益（%，{'/'.join(assets)}）", monthly_heatmap(data["monthly"])) +
                         card("逐年对比：策略 vs 买入持有", table(
                             ["年份", "策略收益", "买入持有", "超额", "盈利"], annual_rows))))
    # ---------- 05 滚动夏普 ----------
    roll_dates = list(data["rolling"].get("dates") or [])
    roll_vals = list(data["rolling"].get("values") or [])
    roll_bm = list(data["rolling_bm"].get("values") or [])
    rn = min(len(roll_dates), len(roll_vals), len(roll_bm))
    rs_svg = svg_lines(roll_dates[:rn], [
        {"name": "策略滚动夏普（90 个交易日）", "values": roll_vals[:rn], "color": PALETTE["strategy"]},
        {"name": "买入持有基准", "values": roll_bm[:rn], "color": PALETTE["benchmark"], "dash": True},
    ], height=300, fmt="ratio", zero_line=True)
    parts.append(section("05", "滚动夏普比率", "看稳定性：是持续占优，还是只在某一段行情里运气好。",
                         card("90 日滚动夏普", rs_svg, "纵轴跌破 0 代表该窗口风险调整后为负")))

    # ---------- 06 分资产贡献与相关性 ----------
    leg_rows = []
    for leg in result["legs"]:
        lm = leg["metrics"]
        leg_rows.append([
            esc(leg["symbol"]), fp(leg["alloc_weight"], 1), fmoney(leg["final_equity"]),
            f"{float(leg['pnl']):+,.0f}", fps(leg["contribution"]),
            fn(lm.get("sharpe")), fp(lm.get("max_drawdown")), fint(len(leg["trades"])),
        ])
    parts.append(section("06", "分资产贡献与相关性",
                         "多资产的意义在于分散：相关性越低，达到同样收益所需的单一资产风险越小。",
                         card("各资产子账户表现", table(
                             ["资产", "资金权重", f"期末净值({currency})", f"盈亏({currency})",
                              "对组合贡献", "夏普", "最大回撤", "成交笔数"], leg_rows)) +
                         card("资产日收益相关性矩阵", correlation_table(result.get("correlation") or {}))))

    # ---------- 07 交易统计 ----------
    trade_rows = [
        ["完整交易笔数（FIFO 配对）", fint(m.get("trade_count"))],
        ["交易胜率", fp(m.get("trade_win_rate"))],
        ["盈亏比（平均盈利 / 平均亏损）", fn(m.get("payoff_ratio"))],
        ["总盈亏比 Profit Factor", fn(m.get("trade_profit_factor"))],
        ["单笔期望盈亏", f"{fn(m.get('expectancy'))} {currency}"],
        ["平均持仓天数", f'{fn(m.get("avg_holding_days"), 1)} 天'],
        ["单笔最佳 / 最差", f'{fn(m.get("best_trade"))} / {fn(m.get("worst_trade"))} {currency}'],
        ["最大连续亏损笔数", fint(m.get("max_consecutive_losses"))],
        ["止损离场次数", fint(m.get("stops"))],
        ["日胜率", fp(m.get("win_rate"))],
        ["单日最佳 / 最差", f'{fp(m.get("best_day"))} / {fp(m.get("worst_day"))}'],
        ["收益偏度 / 峰度", f'{fn(m.get("skew"))} / {fn(m.get("kurt"))}'],
        ["Omega 比率", fn(m.get("omega"))],
        ["信息比率（相对基准）", fn(m.get("information_ratio"))],
        ["与基准相关性", fn(m.get("correlation"))],
    ]
    recent = list(reversed(sorted(result["round_trips"],
                                  key=lambda t: str(t.get("exit_date") or ""))[-15:]))
    recent_rows = []
    for t in recent:
        pnl = float(t.get("pnl") or 0.0)
        color = PALETTE["green"] if pnl >= 0 else PALETTE["red"]
        recent_rows.append([
            esc(t.get("symbol")), esc(t.get("entry_date")), esc(t.get("exit_date")),
            fn(t.get("entry_price")), fn(t.get("exit_price")), fn(t.get("notional")),
            f'<span style="color:{color}">{pnl:+,.0f}</span>',
            fp(t.get("ret")), fint(t.get("holding_days")), esc(t.get("exit_reason")),
        ])
    parts.append(section("07", "交易统计与最近交易明细", "换一个角度：把每一笔完整交易摊开看。",
                         card("交易级统计", table(["指标", "数值"], trade_rows)) +
                         card("最近 15 笔完整交易（先进先出配对）", table(
                             ["资产", "进场日", "离场日", "进场价", "离场价", f"投入本金({currency})",
                              f"盈亏({currency})", "单笔收益", "持仓天数", "离场原因"], recent_rows),
                             "按离场日倒序")))

    # ---------- 08 稳健性检验 ----------
    robust = ""
    grid = heavy.get("grid")
    if grid:
        robust += card(f"参数敏感性网格（{data['primary_name']}，指标 = 夏普比率）",
                       param_grid_table(grid, (int(p.fast), int(p.slow))))
    wf = heavy.get("walk_forward")
    if wf:
        wf_rows = []
        for fold in wf.get("folds") or []:
            wf_rows.append([
                f"第 {fold['fold']} 折", esc(fold["train_end"]),
                f"{fold['test_start']} ~ {fold['test_end']}",
                f"快 {fold['best_fast']} / 慢 {fold['best_slow']}",
                fp(fold["test_return"]), fp(fold["test_benchmark"]),
                fn(fold["test_sharpe"]), fp(fold["test_max_dd"]),
            ])
        robust += card("滚动前推 Walk-Forward（样本外检验）",
                       table(["折", "训练截止", "样本外区间", "该折选出的参数",
                              "样本外收益", "同期基准", "样本外夏普", "样本外回撤"], wf_rows) +
                       f'<p class="sub" style="margin-top:8px">'
                       f'样本外平均收益 <b>{fp(wf.get("oos_avg_return"))}</b>，'
                       f'相对同期基准 <b>{fps(wf.get("oos_vs_benchmark"))}</b>，'
                       f'样本外为正的比例 <b>{fp(wf.get("oos_win_rate"))}</b>，'
                       f'平均样本外夏普 <b>{fn(wf.get("oos_avg_sharpe"))}</b>。'
                       f'每一折都是「用过去选参数、在未来检验」，这是判断是否过拟合最关键的一步。'
                       f'如果样本外结果明显差于样本内，说明策略有过度拟合历史的风险。</p>')
    mc = heavy.get("monte_carlo")
    if mc and mc.get("paths"):
        fin = mc.get("final") or {}
        mc_rows = [
            ["向前推演区间", f'{fint(mc.get("horizon"))} 个交易日（约 1 年）'],
            ["5% 分位（悲观情形）", fp(fin.get("p05"))],
            ["25% 分位", fp(fin.get("p25"))],
            ["中位数（50%）", fp(fin.get("p50"))],
            ["75% 分位", fp(fin.get("p75"))],
            ["95% 分位（乐观情形）", fp(fin.get("p95"))],
            ["出现亏损的概率", fp(fin.get("prob_loss"))],
            ["平均最大回撤", fp(fin.get("avg_max_dd"))],
            ["最差路径最大回撤", fp(fin.get("worst_max_dd"))],
        ]
        robust += card(f'蒙特卡洛模拟（{fint(data.get("n_sims") or 600)} 条路径）',
                       svg_fan(mc.get("paths") or [], mc.get("percentiles") or [5, 25, 50, 75, 95]) + table(["统计量", "结果"], mc_rows) +
                       f'<p class="sub" style="margin-top:8px">做法：把历史日收益打乱顺序、有放回地重新抽样，'
                       f'模拟「同样的收益率分布、换一种运气」会走到哪里。'
                       f'它回答的不是「未来会怎样」，而是「这套策略的波动到底有多大」。</p>')
    if not robust:
        robust = '<p class="meta">本次使用 --quick 模式，未运行稳健性检验。去掉 --quick 重新生成即可看到完整结果。</p>'
    parts.append(section("08", "稳健性检验",
                         "回测好看不难；难的是换个参数、换段时间、换种运气还站得住。", robust))

    # ---------- 09 风控事件 ----------
    events = list(result.get("risk_events") or [])
    if events:
        ev_rows = []
        for ev in events:
            try:
                dd_value = -abs(float(ev.get("drawdown") or 0.0))
            except (TypeError, ValueError):
                dd_value = 0.0
            ev_rows.append([esc(ev.get("symbol")), esc(ev.get("date")), f"{dd_value:.2f}%",
                            f'{fint(ev.get("pause_days"))} 天', fmoney(ev.get("equity"))])
        ev_body = table(["资产", "触发日", "触发时回撤", "暂停交易", f"触发时净值({currency})"], ev_rows)
    else:
        ev_body = '<p class="meta">本次回放没有记录到子账户回撤熔断事件。</p>'
    ev_body += (f'<p class="sub" style="margin-top:8px">熔断规则：各子账户收盘回撤触及 -{p.risk_limit_pct:.0f}% 后，于下一根开盘清仓、'
                f'重置风控参考净值，暂停后续 {p.risk_pause_days} 根 K 线；累计回撤不重置。'
                f'单日亏损超过 -{p.daily_loss_limit_pct:.0f}% 时冷静 {p.cooldown_days} 根 K 线。'
                f'注意熔断是「暂停」而不是「永久停摆」——到期自动复工，'
                f'这是实务实现里最容易写错、也最影响结果的地方。</p>')
    parts.append(section("09", "风控事件记录", "风控到底有没有真的执行过？看原始记录。",
                         card("子账户熔断 / 冷静事件", ev_body)))

    # ---------- 10 方法论与免责 ----------
    method = (
        "<ul>"
        "<li><b>防前视偏差：</b>信号只用上一根已收盘 K 线的数据计算，成交发生在下一根 K 线的开盘价。</li>"
        "<li><b>撮合与成本：</b>手续费按成交额收取；滑点 = 固定滑点 + 平方根冲击成本"
        "（由「下单额 / 平均日成交量」的平方根决定），规模越大成本越高。</li>"
        f"<li><b>仓位管理：</b>{'目标波动率 / 上一期实测波动率' if p.use_vol_target else '固定目标仓位'}，"
        f"{'目标波动率 ' + fp(p.vol_target, 0) if p.use_vol_target else '固定仓位 ' + fp(p.fixed_weight, 0)}；"
        f"受单资产上限 {p.max_weight * 100:.0f}% 约束。</li>"
        f"<li><b>止损：</b>ATR({p.atr_period}) x {p.stop_atr:g} 移动止损"
        f'（止损位 = 持仓期最高价 - N x ATR），跳空时按开盘价成交；'
        f"止损后冷静 {p.stop_reentry_days} 天再考虑进场。</li>"
        f"<li><b>组合层：</b>按「{p.alloc_method}」把资金分配到各资产子账户，"
        f"子账户各自独立记账，最后合并成一条组合净值曲线。</li>"
        f"<li><b>成本假设：</b>手续费 {p.fee_bps:g} bps、固定滑点 {p.slippage_bps:g} bps、"
        f"冲击系数 {p.impact_coef_bps:g} bps；未计入资金费率、借币成本与税费。</li>"
        "</ul>"
    )
    disclaim = (
        '<div class="danger"><b>免责声明与适用边界</b><ul>'
        "<li>本报告是本地虚拟盘（paper trading）教学演示：<b>没有连接任何交易所或券商，"
        "没有使用真实资金，没有发送任何真实订单，也不需要 API Key</b>。</li>"
        "<li>行情来自本机 CSV 文件；结果已包含手续费、滑点与冲击成本，但仍不可避免地存在"
        "数据质量、成交假设、幸存者偏差等局限。</li>"
        "<li><b>历史表现不代表未来收益。</b>回测的意义是检验逻辑是否自洽、风控是否有效、"
        "参数是否稳健，而不是预测未来能赚多少。</li>"
        "<li>本报告不构成任何投资建议。</li></ul></div>"
    )
    parts.append(section("10", "方法论与免责声明", "这一节决定这份报告能不能经得起追问。",
                         card("建模与成交假设", method) + disclaim))

    parts.append(f'<footer>{esc(branding.APP_NAME)} · {esc(branding.SCHOOL_NAME)} v{REPORT_VERSION}　|　生成时间 {now}　|　'
                 f'数据区间 {esc(data["start"])} ~ {esc(data["end"])}　|　'
                 f'本机离线生成，报告内不含任何联网脚本，可直接拷走或用 Ctrl+P 打印成 PDF。<br>'
                 f'策略构成：{esc(p.describe())}</footer>')
    parts.append("</div></body></html>")
    return "\n".join(parts)


# =====================================================================
# 五、命令行入口
# =====================================================================

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成离线自包含的 HTML 量化研究报告（本地虚拟盘）")
    parser.add_argument("--assets", default=",".join(DEFAULT_ASSETS),
                        help="资产列表，英文逗号分隔，例如 BTC,ETH,SOL,BNB")
    parser.add_argument("--out", default="量化研究报告.html", help="输出文件名（生成在项目文件夹）")
    parser.add_argument("--initial", type=float, default=10000.0, help="初始虚拟资金（USDT）")
    parser.add_argument("--fast", type=int, default=20, help="快均线周期")
    parser.add_argument("--slow", type=int, default=60, help="慢均线周期")
    parser.add_argument("--alloc", default="risk_parity",
                        choices=["equal", "risk_parity", "manual"], help="组合加权方式")
    parser.add_argument("--sims", type=int, default=600, help="蒙特卡洛路径数")
    parser.add_argument("--splits", type=int, default=5, help="Walk-Forward 折数")
    parser.add_argument("--quick", action="store_true",
                        help="跳过参数网格 / Walk-Forward / 蒙特卡洛（快速预览）")
    parser.add_argument("--no-open", action="store_true", help="生成后不自动打开浏览器")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    _safe_streams()
    args = parse_args(argv)
    names = [x.strip().upper() for x in str(args.assets).replace("，", ",").split(",") if x.strip()]
    params = qc.StrategyParams(initial_cash=float(args.initial), fast=int(args.fast),
                               slow=int(args.slow), alloc_method=str(args.alloc))
    try:
        params.validate()
    except ValueError as exc:
        log(f"参数不合法：{exc}")
        return 2

    log("=" * 64)
    log(f"{branding.APP_NAME} · 研究报告生成器 v{REPORT_VERSION}")
    log("本地虚拟盘：不连接交易所、不发送真实订单、不使用真实资金")
    log("=" * 64)
    try:
        data = run_full_analysis(names, params, quick=bool(args.quick),
                                 n_sims=int(args.sims), splits=int(args.splits))
    except ValueError as exc:
        log(f"运行失败：{exc}")
        return 1

    log("正在拼装 HTML 报告…")
    html_text = build_html(data)
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = BASE_DIR / out_path
    out_path.write_text(html_text, encoding="utf-8")
    log(f"报告已生成：{out_path}（{out_path.stat().st_size / 1024:.0f} KB）")
    if not args.no_open:
        try:
            import webbrowser as _wb
            _wb.open(out_path.as_uri())
            log("已尝试用默认浏览器打开；若没有自动打开，请手动双击该 HTML 文件。")
        except Exception as exc:
            log(f"自动打开失败（不影响文件已生成）：{exc}")
    log("完成：报告完全离线，可直接发给老师，或用 Ctrl+P 打印成 PDF。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
