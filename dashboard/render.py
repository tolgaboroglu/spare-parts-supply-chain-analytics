"""Recreate the two Power BI report pages from the local warehouse and save them as PNG.

The original .pbix stays with the company it was built for. These pages copy the report's
layout and Power BI's default visual style (default theme colours, card / KPI / line / bar /
donut / table visuals, page tabs, collapsed filter pane) and use the same measures as
powerbi/measures.dax, on the synthetic data in this repository.

    python dashboard/render.py --db data/spareparts.duckdb --out docs/img
"""

from __future__ import annotations

import argparse
import base64
import html
import math
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.run_local import kpis  # noqa: E402

# Power BI default theme
PBI = ["#118DFF", "#12239E", "#E66C37", "#6B007B", "#E044A7", "#744EC2", "#D9B300", "#D64550"]
GOOD, BAD, NEUTRAL = "#1AAB40", "#D64554", "#F2C80F"
TXT, TXT2, AXIS, GRID = "#252423", "#605E5C", "#605E5C", "#E1DFDD"
CW, CH = 1280, 720          # report canvas (16:9)
TAB_H, PANE_W = 34, 30      # page tabs, collapsed filters pane


def esc(s) -> str:
    return html.escape(str(s))


def eur(v: float) -> str:
    """Power BI 'Auto' display units."""
    if abs(v) >= 1e6:
        return f"€{v / 1e6:.2f}M"
    if abs(v) >= 1e3:
        return f"€{v / 1e3:.0f}K" if abs(v) >= 1e5 else f"€{v / 1e3:.1f}K"
    return f"€{v:.0f}"


def num(v: float) -> str:
    return f"{v / 1e3:.0f}K" if abs(v) >= 1e4 else (f"{v / 1e3:.1f}K" if abs(v) >= 1e3 else f"{v:.0f}")


def nice_ticks(hi: float, lo: float = 0, n: int = 5) -> list[float]:
    span = (hi - lo) * 1.08
    raw = span / (n - 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step + 1e-9) * step
    ticks = [start]
    while ticks[-1] < hi:
        ticks.append(ticks[-1] + step)
    return ticks


# --------------------------------------------------------------------------- visuals
def visual(x, y, w, h, title, inner, pad_top=34) -> str:
    t = f'<div class="vt">{esc(title)}</div>' if title else ""
    return (f'<div class="vis" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px">{t}'
            f'<div class="vb" style="top:{pad_top if title else 0}px">{inner}</div></div>')


def card(x, y, w, h, value, label, color=TXT) -> str:
    inner = (f'<div class="card"><div class="cv" style="color:{color}">{value}</div>'
             f'<div class="cl">{esc(label)}</div></div>')
    return visual(x, y, w, h, "", inner)


def kpi_visual(x, y, w, h, title, value, goal, trend, higher_is_better=True, fmt=lambda v: f"{v:.2%}") -> str:
    ok = value >= goal if higher_is_better else value <= goal
    color = GOOD if ok else BAD
    diff = (value - goal) / goal
    icon = "&#10004;" if ok else "!"
    lo, hi = min(trend) * 0.995, max(trend) * 1.005
    pts = [(i * w / (len(trend) - 1), h - 6 - (v - lo) / (hi - lo) * (h * 0.28)) for i, v in enumerate(trend)]
    area = f"M0,{h} " + " ".join(f"L{px:.1f},{py:.1f}" for px, py in pts) + f" L{w},{h} Z"
    svg = (f'<svg class="kpitrend" width="{w}" height="{h}"><path d="{area}" fill="{color}" opacity=".13"/>'
           f'<polyline points="{" ".join(f"{px:.1f},{py:.1f}" for px, py in pts)}" fill="none" stroke="{color}" stroke-opacity=".45" stroke-width="1.5"/></svg>')
    inner = (f'{svg}<div class="kpi"><div class="kv" style="color:{color}">{fmt(value)} '
             f'<span class="ki" style="background:{color}">{icon}</span></div>'
             f'<div class="kg">Goal: {fmt(goal)} ({diff:+.2%})</div><div class="kl">{esc(title)}</div></div>')
    return visual(x, y, w, h, "", inner)


def legend(title, items) -> str:
    return ('<div class="lg"><b>' + esc(title) + '</b>' +
            "".join(f'<span><i style="background:{c}"></i>{esc(n)}</span>' for n, c in items) + "</div>")


def line_chart(w, h, labels, series, y_fmt=num, y_title="", x_title="", lo=0.0, hi=None, const_line=None) -> str:
    left, right, top, bottom = 58, 14, 8, 42
    vals = [v for _, _, s in series for v in s]
    ticks = nice_ticks(hi if hi is not None else max(vals), lo)
    y0, y1 = ticks[0], ticks[-1]
    pw, ph = w - left - right, h - top - bottom
    X = lambda i: left + pw * (i + 0.5) / len(labels)  # noqa: E731
    Y = lambda v: top + ph * (1 - (v - y0) / (y1 - y0))  # noqa: E731
    o = [f'<svg width="{w}" height="{h}">']
    for t in ticks:
        o.append(f'<line x1="{left}" x2="{w - right}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="{GRID}" stroke-dasharray="1 3"/>')
        o.append(f'<text x="{left - 6}" y="{Y(t) + 4:.1f}" text-anchor="end" class="ax">{y_fmt(t)}</text>')
    for i, lab in enumerate(labels):
        if lab:
            o.append(f'<text x="{X(i):.1f}" y="{top + ph + 16}" text-anchor="middle" class="ax">{esc(lab)}</text>')
    if const_line:
        v, name, c = const_line
        o.append(f'<line x1="{left}" x2="{w - right}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{c}" stroke-width="1.5" stroke-dasharray="5 4"/>'
                 f'<text x="{w - right - 4}" y="{Y(v) - 6:.1f}" text-anchor="end" class="ax" style="fill:{c}">{esc(name)}</text>')
    for _, c, s in series:
        o.append(f'<polyline points="{" ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(s))}" fill="none" '
                 f'stroke="{c}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>')
    o.append(f'<text x="{left + pw / 2}" y="{h - 4}" text-anchor="middle" class="at">{esc(x_title)}</text>')
    o.append(f'<text transform="translate(12,{top + ph / 2}) rotate(-90)" text-anchor="middle" class="at">{esc(y_title)}</text>')
    o.append("</svg>")
    return "".join(o)


def bar_chart(w, h, rows, fmt, color=PBI[0], y_title="", x_title="", label_w=96) -> str:
    """Clustered bar chart (horizontal), Power BI default: square bars, data labels outside end."""
    top, bottom, right = 4, 34, 52
    ph = h - top - bottom
    pw = w - label_w - right
    vmax = nice_ticks(max(v for _, v in rows))[-1]
    band = ph / len(rows)
    o = [f'<svg width="{w}" height="{h}">']
    for t in nice_ticks(max(v for _, v in rows)):
        x = label_w + pw * t / vmax
        o.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{top + ph}" stroke="{GRID}" stroke-dasharray="1 3"/>')
        o.append(f'<text x="{x:.1f}" y="{top + ph + 14}" text-anchor="middle" class="ax">{fmt(t)}</text>')
    for i, (lab, v) in enumerate(rows):
        bh = band * 0.66
        by = top + band * i + (band - bh) / 2
        bw = pw * v / vmax
        o.append(f'<text x="{label_w - 8}" y="{by + bh / 2 + 4:.1f}" text-anchor="end" class="ax">{esc(lab)}</text>')
        o.append(f'<rect x="{label_w}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{color}"/>')
        o.append(f'<text x="{label_w + bw + 5:.1f}" y="{by + bh / 2 + 4:.1f}" class="dlab">{fmt(v)}</text>')
    o.append(f'<text x="{label_w + pw / 2}" y="{h - 3}" text-anchor="middle" class="at">{esc(x_title)}</text>')
    o.append(f'<text transform="translate(10,{top + ph / 2}) rotate(-90)" text-anchor="middle" class="at">{esc(y_title)}</text>')
    o.append("</svg>")
    return "".join(o)


def column_chart(w, h, rows, fmt, x_title="", y_title="") -> str:
    left, right, top, bottom = 50, 10, 18, 40
    pw, ph = w - left - right, h - top - bottom
    ticks = nice_ticks(max(v for _, v, _ in rows))
    vmax = ticks[-1]
    band = pw / len(rows)
    o = [f'<svg width="{w}" height="{h}">']
    for t in ticks:
        y = top + ph * (1 - t / vmax)
        o.append(f'<line x1="{left}" x2="{w - right}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}" stroke-dasharray="1 3"/>')
        o.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" class="ax">{num(t)}</text>')
    for i, (lab, v, c) in enumerate(rows):
        bw = band * 0.62
        bx = left + band * i + (band - bw) / 2
        bh = ph * v / vmax
        o.append(f'<rect x="{bx:.1f}" y="{top + ph - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{c}"/>')
        o.append(f'<text x="{bx + bw / 2:.1f}" y="{top + ph - bh - 5:.1f}" text-anchor="middle" class="dlab">{fmt(v)}</text>')
        o.append(f'<text x="{bx + bw / 2:.1f}" y="{top + ph + 15}" text-anchor="middle" class="ax">{esc(lab)}</text>')
    o.append(f'<text x="{left + pw / 2}" y="{h - 3}" text-anchor="middle" class="at">{esc(x_title)}</text>')
    o.append(f'<text transform="translate(11,{top + ph / 2}) rotate(-90)" text-anchor="middle" class="at">{esc(y_title)}</text>')
    o.append("</svg>")
    return "".join(o)


def donut(w, h, rows, fmt) -> str:
    cx, cy = w / 2, h / 2 + 4
    r_out, r_in = min(w, h) * 0.30, min(w, h) * 0.18
    total = sum(v for _, v, _ in rows)
    a = -math.pi / 2
    o = [f'<svg width="{w}" height="{h}">']
    for name, v, c in rows:
        da = 2 * math.pi * v / total
        a2 = a + da
        large = 1 if da > math.pi else 0
        p = lambda r, ang: (cx + r * math.cos(ang), cy + r * math.sin(ang))  # noqa: E731
        (x1, y1), (x2, y2), (x3, y3), (x4, y4) = p(r_out, a), p(r_out, a2), p(r_in, a2), p(r_in, a)
        o.append(f'<path d="M{x1:.1f},{y1:.1f} A{r_out},{r_out} 0 {large} 1 {x2:.1f},{y2:.1f} L{x3:.1f},{y3:.1f} '
                 f'A{r_in},{r_in} 0 {large} 0 {x4:.1f},{y4:.1f} Z" fill="{c}" stroke="#fff" stroke-width="1"/>')
        mid = (a + a2) / 2
        lx1, ly1 = p(r_out + 4, mid)
        lx2, ly2 = p(r_out + 18, mid)
        right = math.cos(mid) >= 0
        lx3 = lx2 + (14 if right else -14)
        o.append(f'<polyline points="{lx1:.1f},{ly1:.1f} {lx2:.1f},{ly2:.1f} {lx3:.1f},{ly2:.1f}" fill="none" stroke="#A19F9D"/>')
        o.append(f'<text x="{lx3 + (4 if right else -4):.1f}" y="{ly2 + 4:.1f}" text-anchor="{"start" if right else "end"}" '
                 f'class="dlab">{fmt(v)} ({v / total:.0%})</text>')
        a = a2
    o.append("</svg>")
    return "".join(o)


ICON = {
    "red": f'<svg width="12" height="12"><circle cx="6" cy="6" r="5.5" fill="{BAD}"/></svg>',
    "yellow": f'<svg width="12" height="12"><path d="M6 .5 11.5 11H.5Z" fill="{NEUTRAL}"/></svg>',
    "green": f'<svg width="12" height="12"><circle cx="6" cy="6" r="5.5" fill="{GOOD}"/></svg>',
}


def table(cols, rows, total=None, aligns=None) -> str:
    aligns = aligns or ["l"] * len(cols)
    head = "".join(f'<th class="{a}">{esc(c)}</th>' for c, a in zip(cols, aligns))
    body = "".join("<tr>" + "".join(f'<td class="{a}">{cell}</td>' for cell, a in zip(r, aligns)) + "</tr>" for r in rows)
    foot = ("<tr class='tot'>" + "".join(f'<td class="{a}">{cell}</td>' for cell, a in zip(total, aligns)) + "</tr>") if total else ""
    return f'<table class="tbl"><tr>{head}</tr>{body}{foot}</table>'


def databar(v, vmax, text) -> str:
    return (f'<span class="db"><i style="width:{100 * v / vmax:.0f}%"></i></span>{text}')


def scale_bg(v, lo, mid, hi) -> str:
    """Power BI colour scale: red -> yellow -> green (light tints)."""
    def mix(c1, c2, t):
        return "#" + "".join(f"{round(int(c1[i:i + 2], 16) + (int(c2[i:i + 2], 16) - int(c1[i:i + 2], 16)) * t):02x}" for i in (1, 3, 5))
    red, yel, grn = "#F4B4B9", "#FBE7A1", "#B5E3BF"
    if v <= mid:
        return mix(red, yel, max(0, min(1, (v - lo) / (mid - lo))))
    return mix(yel, grn, max(0, min(1, (v - mid) / (hi - mid))))


# --------------------------------------------------------------------------- page frame
def font_face() -> str:
    css = []
    for weight in (300, 400, 600, 700):
        f = ROOT / "dashboard/fonts" / f"open-sans-latin-{weight}-normal.woff2"
        if f.exists():
            css.append(f"@font-face{{font-family:'Report';font-weight:{weight};"
                       f"src:url(data:font/woff2;base64,{base64.b64encode(f.read_bytes()).decode()}) format('woff2')}}")
    return "".join(css)


CSS = f"""
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:{CW + PANE_W}px;height:{CH + TAB_H}px;background:#fff;font-family:'Segoe UI','Report',sans-serif;color:{TXT};overflow:hidden;position:relative}}
.canvas{{position:absolute;left:0;top:0;width:{CW}px;height:{CH}px;background:#EAEAEA;overflow:hidden}}
.hdr{{position:absolute;left:0;top:0;width:{CW}px;height:70px;background:#12239E;color:#fff;padding:12px 20px}}
.hdr h1{{font-size:21px;font-weight:600;letter-spacing:-.005em}}
.hdr .st{{font-size:12px;opacity:.82;margin-top:2px}}
.sls{{position:absolute;right:20px;top:11px;display:flex;gap:10px}}
.sl{{width:168px}}
.sl .sh{{font-size:11px;font-weight:600;color:#fff;margin-bottom:3px}}
.sl .sd{{background:#fff;color:{TXT};font-size:12px;height:26px;padding:4px 8px;display:flex;justify-content:space-between;align-items:center;border:1px solid #C8C6C4}}
.sl .sd svg{{flex:none}}
.vis{{position:absolute;background:#fff;border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.18)}}
.vt{{position:absolute;left:12px;top:9px;right:12px;font-size:14px;color:{TXT};white-space:nowrap}}
.vb{{position:absolute;left:0;right:0;bottom:0}}
.card{{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center}}
.cv{{font-size:34px;font-weight:300;letter-spacing:-.01em;line-height:1.1}}
.cl{{font-size:12px;color:{TXT2};margin-top:4px}}
.kpitrend{{position:absolute;left:0;top:0;border-radius:4px}}
.kpi{{position:relative;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;padding-top:12px}}
.kv{{font-size:30px;font-weight:600;line-height:1.1;display:flex;align-items:center;gap:6px}}
.ki{{display:inline-flex;width:18px;height:18px;border-radius:50%;color:#fff;font-size:11px;align-items:center;justify-content:center;font-weight:700}}
.kg{{font-size:11.5px;color:{TXT2};margin-top:3px}}
.kl{{font-size:12px;color:{TXT};margin-top:1px;font-weight:600}}
.lg{{position:absolute;left:12px;top:0;font-size:11px;color:{TXT2};display:flex;gap:12px;align-items:center}}
.lg b{{color:{TXT};font-weight:600}}
.lg span{{display:flex;align-items:center;gap:5px}}
.lg i{{width:9px;height:9px;border-radius:50%;display:inline-block}}
svg text{{font-family:'Segoe UI','Report',sans-serif}}
.ax{{font-size:11px;fill:{AXIS}}}
.at{{font-size:11.5px;fill:{TXT};font-weight:600}}
.dlab{{font-size:11px;fill:{TXT2}}}
.tbl{{width:100%;border-collapse:collapse;font-size:11.5px;font-variant-numeric:tabular-nums}}
.tbl th{{font-weight:600;color:{TXT};padding:5px 8px;border-bottom:1px solid #C8C6C4;text-align:left;vertical-align:bottom;font-size:11.5px}}
.tbl td{{padding:4px 8px;color:{TXT};white-space:nowrap}}
.tbl tr:nth-child(odd) td{{background:#F3F2F1}}
.tbl tr.tot td{{font-weight:600;background:#fff;border-top:1px solid {TXT}}}
.tbl .r{{text-align:right}}
.tbl .c{{text-align:center}}
.tbl td svg{{vertical-align:-1px}}
.db{{display:inline-block;width:62px;height:12px;vertical-align:-2px;margin-right:6px;background:transparent}}
.db i{{display:block;height:100%;background:#9AC8FF}}
.tabs{{position:absolute;left:0;top:{CH}px;width:{CW + PANE_W}px;height:{TAB_H}px;background:#F3F2F1;border-top:1px solid #E1DFDD;display:flex;align-items:stretch;font-size:12.5px;color:{TXT2}}}
.tabs .nav{{width:54px;display:flex;align-items:center;justify-content:center;gap:12px;color:#A19F9D;font-size:9px}}
.tabs .tb{{padding:0 18px;display:flex;align-items:center;border-right:1px solid #E1DFDD}}
.tabs .tb.on{{background:#fff;color:{TXT};font-weight:600;box-shadow:inset 0 -3px 0 {TXT}}}
.tabs .plus{{padding:0 14px;display:flex;align-items:center;font-size:16px}}
.tabs .pg{{margin-left:auto;padding:0 14px;display:flex;align-items:center;font-size:11.5px}}
.pane{{position:absolute;left:{CW}px;top:0;width:{PANE_W}px;height:{CH}px;background:#fff;border-left:1px solid #E1DFDD}}
.pane .pt{{position:absolute;top:40px;left:7px;writing-mode:vertical-rl;font-size:12.5px;color:{TXT}}}
.pane svg{{position:absolute;left:8px;top:16px}}
"""

CHEVRON = '<svg width="10" height="10" viewBox="0 0 10 10"><path d="M1.5 3.5 5 7l3.5-3.5" fill="none" stroke="#605E5C" stroke-width="1.3"/></svg>'
FUNNEL = '<svg width="14" height="14" viewBox="0 0 14 14"><path d="M1 2h12L8.5 7.5V12l-3-1.5v-3Z" fill="none" stroke="#252423" stroke-width="1.1"/></svg>'


def page(sub: str, tab: int, body: str) -> str:
    slicers = "".join(f'<div class="sl"><div class="sh">{h}</div><div class="sd"><span>{v}</span>{CHEVRON}</div></div>'
                      for h, v in [("Year", "2025"), ("Warehouse", "All"), ("Part Category", "All")])
    tabs = "".join(f'<div class="tb{" on" if i == tab else ""}">{t}</div>'
                   for i, t in enumerate(["Inventory &amp; Service Level", "Supplier Performance"]))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{font_face()}{CSS}</style></head><body>
<div class="canvas">
  <div class="hdr"><h1>Spare Parts Supply Chain</h1><div class="st">{esc(sub)} · refreshed daily 05:00 by Azure Data Factory · synthetic data</div>
  <div class="sls">{slicers}</div></div>
  {body}
</div>
<div class="pane">{FUNNEL}<div class="pt">Filters</div></div>
<div class="tabs"><div class="nav">&#9664; &#9654;</div>{tabs}<div class="plus">+</div><div class="pg">Page {tab + 1} of 2</div></div>
</body></html>"""


def month_labels(months):
    short = ["", "", "", "Apr", "", "", "Jul", "", "", "Oct", "", ""]
    return [("Jan " + m[:4]) if m.endswith("-01") else short[int(m[5:]) - 1] for m in months]


# --------------------------------------------------------------------------- page 1
def page_inventory(con, k: dict) -> str:
    last = int(k["as_of"])
    M, G = 16, 12
    cw = (CW - 2 * M - 4 * G) / 5
    xs = [M + i * (cw + G) for i in range(5)]

    fill_trend = [v for _, v in con.execute(
        """SELECT d.YearMonth, sum(QuantityShipped) / sum(QuantityOrdered) FROM dw.fact_dealer_order_line f
           JOIN dw.dim_date d ON d.DateKey = f.OrderDateKey WHERE d.Year = 2025 GROUP BY 1 ORDER BY 1""").fetchall()]
    cards = "".join([
        card(xs[0], 82, cw, 100, eur(k["stock_value"]), "Stock Value"),
        card(xs[1], 82, cw, 100, str(k["critical_parts"]), "Critical Parts (below safety stock)", BAD if k["critical_parts"] else TXT),
        kpi_visual(xs[2], 82, cw, 100, "Fill Rate", k["fill_rate"], 0.97, fill_trend),
        card(xs[3], 82, cw, 100, f"{k['stock_turnover']:.1f}", f"Stock Turnover · {k['days_of_inventory']:.0f} Days of Inventory"),
        card(xs[4], 82, cw, 100, eur(k["slow_moving_value"]), f"Slow-moving Stock ({k['slow_moving_share']:.0%} of value)"),
    ])

    season = con.execute("""SELECT d.YearMonth, p.Category, sum(f.QuantityOrdered) FROM dw.fact_dealer_order_line f
                              JOIN dw.dim_part p USING (PartKey) JOIN dw.dim_date d ON d.DateKey = f.OrderDateKey
                             WHERE p.Category IN ('Electrical', 'HVAC') GROUP BY 1, 2 ORDER BY 1""").fetchall()
    months = sorted({m for m, _, _ in season})
    by = {c: [next(v for m2, c2, v in season if m2 == m and c2 == c) for m in months] for c in ("Electrical", "HVAC")}
    lw, lh = 760, 262
    line = visual(M, 194, lw, lh, "Quantity Ordered by Month and Category",
                  legend("Category", [("Electrical", PBI[0]), ("HVAC", PBI[2])]) +
                  f'<div style="position:absolute;top:24px">{line_chart(lw, lh - 58, month_labels(months), [("Electrical", PBI[0], by["Electrical"]), ("HVAC", PBI[2], by["HVAC"])], y_title="Quantity Ordered", x_title="Month")}</div>')

    cats = con.execute(f"""SELECT p.Category, sum(s.StockValue) FROM dw.fact_stock_monthly s JOIN dw.dim_part p USING (PartKey)
                            WHERE SnapshotDateKey = {last} GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    bx = M + lw + G
    bw = CW - M - bx
    bars = visual(bx, 194, bw, lh, "Stock Value by Category",
                  bar_chart(bw, lh - 40, cats, eur, y_title="Category", x_title="Stock Value", label_w=112))

    crit = con.execute(f"""
        WITH st AS (SELECT PartKey, sum(QuantityOnHand) qty FROM dw.fact_stock_monthly WHERE SnapshotDateKey = {last} GROUP BY 1),
             dm AS (SELECT PartKey, sum(QuantityOrdered) / 365.0 per_day FROM dw.fact_dealer_order_line
                     WHERE OrderDateKey BETWEEN 20250101 AND {last} GROUP BY 1),
             po AS (SELECT PartKey, min(RequestedDateKey) due, sum(QuantityOrdered) qty FROM dw.fact_purchase_order_line
                     WHERE OrderDateKey <= {last} AND RequestedDateKey > {last} GROUP BY 1)
        SELECT p.PartNumber, p.PartName, st.qty, p.SafetyStock, st.qty / nullif(dm.per_day, 0), po.due, coalesce(po.qty, 0)
          FROM st JOIN dw.dim_part p USING (PartKey) LEFT JOIN dm USING (PartKey) LEFT JOIN po USING (PartKey)
         WHERE st.qty < p.SafetyStock ORDER BY st.qty * 1.0 / p.SafetyStock""").fetchall()
    rows = []
    for pn, name, qty, ss, cover, due, open_qty in crit[:6]:
        icon = ICON["red"] if due is None or qty / ss < 0.6 else ICON["yellow"]
        due_s = f"{str(due)[6:8]}.{str(due)[4:6]}.{str(due)[:4]}" if due else "No open PO"
        rows.append([icon, esc(pn), esc(name), str(qty), str(ss), databar(qty, ss, f"{qty / ss:.0%}"),
                     f"{cover:.0f}" if cover else "", str(open_qty), due_s])
    total = ["", "Total", f"{len(crit)} parts", str(sum(r[2] for r in crit)), str(sum(r[3] for r in crit)), "",
             "", str(sum(r[6] for r in crit)), ""]
    tbl = visual(M, 468, lw, CH - 468 - M, "Critical Parts",
                 '<div style="padding:0 10px">' + table(
                     ["", "Part Number", "Description", "On Hand", "Safety Stock", "% of Safety", "Days Cover", "Open PO Qty", "Next PO Due"],
                     rows, total, ["c", "l", "l", "r", "r", "l", "r", "r", "r"]) + "</div>")

    wh = con.execute(f"""SELECT w.WarehouseName, sum(StockValue) FROM dw.fact_stock_monthly s JOIN dw.dim_warehouse w USING (WarehouseKey)
                          WHERE SnapshotDateKey = {last} GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    drows = [(n, v, PBI[i]) for i, (n, v) in enumerate(wh)]
    dn = visual(bx, 468, bw, CH - 468 - M, "Stock Value by Warehouse",
                legend("Warehouse", [(n, c) for n, _, c in drows]) +
                f'<div style="position:absolute;top:22px">{donut(bw, CH - 468 - M - 56, drows, eur)}</div>')
    return page("Inventory & Service Level", 0, cards + line + bars + tbl + dn)


# --------------------------------------------------------------------------- page 2
def page_suppliers(con, k: dict) -> str:
    last = int(k["as_of"])
    M, G = 16, 12
    cw = (CW - 2 * M - 4 * G) / 5
    xs = [M + i * (cw + G) for i in range(5)]

    otd = con.execute("""SELECT d.YearMonth, avg(IsOnTime::INT) FROM dw.fact_goods_receipt g JOIN dw.dim_date d ON d.DateKey = g.ReceiptDateKey
                         GROUP BY 1 ORDER BY 1""").fetchall()
    score = con.execute(f"""
        SELECT s.SupplierName, s.Country, count(*) n, avg(IsOnTime::INT) otd, coalesce(avg(CASE WHEN DelayDays > 0 THEN DelayDays END), 0) dly,
               sum(QuantityRejected) / sum(QuantityReceived) rej,
               (SELECT sum(OrderValue) FROM dw.fact_purchase_order_line po WHERE po.SupplierKey = s.SupplierKey
                   AND po.OrderDateKey BETWEEN 20250101 AND {last}) val
          FROM dw.fact_goods_receipt g JOIN dw.dim_supplier s USING (SupplierKey)
         WHERE ReceiptDateKey BETWEEN 20250101 AND {last} GROUP BY s.SupplierKey, 1, 2""").fetchall()
    scored = sorted([(*r, round(100 * (0.60 * r[3] + 0.25 * (1 - min(r[5] * 20, 1)) + 0.15 * (1 - min(r[4] / 14, 1)))))
                     for r in score], key=lambda t: -t[6])
    po_value = sum(t[6] for t in scored)
    act = sum(1 for t in scored if t[7] < 70)
    cards = "".join([
        kpi_visual(xs[0], 82, cw, 100, "On-time Delivery %", k["on_time_delivery"], 0.95, [v for m, v in otd if m >= "2025"]),
        card(xs[1], 82, cw, 100, f"{k['avg_delay_late_days']:.1f}", "Avg Delay of Late Receipts (days)"),
        card(xs[2], 82, cw, 100, f"{k['rejection_rate']:.2%}", "Rejection Rate"),
        card(xs[3], 82, cw, 100, eur(po_value), "Purchase Order Value"),
        card(xs[4], 82, cw, 100, str(act), "Suppliers with Score below 70", BAD if act else TXT),
    ])

    lw, lh = 700, 238
    line = visual(M, 194, lw, lh, "On-time Delivery % by Month",
                  line_chart(lw, lh - 36, month_labels([m for m, _ in otd]), [("OTD", PBI[0], [v for _, v in otd])],
                             y_fmt=lambda v: f"{v:.0%}", y_title="On-time Delivery %", x_title="Month", lo=0.6, hi=1.0,
                             const_line=(0.95, "Target 95%", BAD)))

    buckets = dict(con.execute(f"""SELECT CASE WHEN DelayDays <= 0 THEN 0 WHEN DelayDays <= 3 THEN 1 WHEN DelayDays <= 7 THEN 2
                                          WHEN DelayDays <= 14 THEN 3 ELSE 4 END b, count(*)
                                FROM dw.fact_goods_receipt WHERE ReceiptDateKey BETWEEN 20250101 AND {last} GROUP BY 1""").fetchall())
    names = ["On time", "1–3 days", "4–7 days", "8–14 days", "15+ days"]
    cx = M + lw + G
    cwid = CW - M - cx
    cols = visual(cx, 194, cwid, lh, "Goods Receipts by Delay",
                  column_chart(cwid, lh - 36, [(names[b], buckets.get(b, 0), PBI[0] if b == 0 else PBI[2]) for b in range(5)],
                               lambda v: f"{v:,}", x_title="Delay", y_title="Goods Receipts"))

    rows = []
    for nm, c, n, o, d, r, v, sc in scored[:7]:
        icon = ICON["green"] if sc >= 80 else (ICON["yellow"] if sc >= 70 else ICON["red"])
        rows.append([esc(nm), esc(c), eur(v), f"{n:,}",
                     f'<span style="display:block;margin:-4px -8px;padding:4px 8px;background:{scale_bg(o, 0.75, 0.88, 0.97)}">{o:.1%}</span>',
                     f"{d:.1f}", f"{r:.2%}", databar(sc, 100, str(sc)), icon])
    allr = con.execute(f"""SELECT count(*), avg(IsOnTime::INT), avg(CASE WHEN DelayDays > 0 THEN DelayDays END),
                                  sum(QuantityRejected) / sum(QuantityReceived)
                             FROM dw.fact_goods_receipt WHERE ReceiptDateKey BETWEEN 20250101 AND {last}""").fetchone()
    total = ["Total", f"{len(scored)} suppliers", eur(po_value), f"{allr[0]:,}", f"{allr[1]:.1%}", f"{allr[2]:.1f}", f"{allr[3]:.2%}", "", ""]
    tbl = visual(M, 444, CW - 2 * M, CH - 444 - M, "Supplier Scorecard (top 7 by purchase order value)",
                 '<div style="padding:0 10px">' + table(
                     ["Supplier", "Country", "PO Value", "Receipts", "On-time %", "Avg Delay (days)", "Rejection %", "Score", "Status"],
                     rows, total, ["l", "l", "r", "r", "r", "r", "r", "l", "c"]) + "</div>")
    return page("Supplier Performance", 1, cards + line + cols + tbl)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data/spareparts.duckdb"))
    ap.add_argument("--out", type=Path, default=ROOT / "docs/img")
    args = ap.parse_args()
    con = duckdb.connect(args.db, read_only=True)
    k = kpis(con)
    pages = {"dashboard-inventory": page_inventory(con, k), "dashboard-suppliers": page_suppliers(con, k)}

    from playwright.sync_api import sync_playwright

    args.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": CW + PANE_W, "height": CH + TAB_H}, device_scale_factor=2)
        for name, doc in pages.items():
            (ROOT / "dashboard" / f"{name}.html").write_text(doc)
            pg.set_content(doc)
            pg.wait_for_timeout(300)
            pg.screenshot(path=str(args.out / f"{name}.png"))
            print("wrote", args.out / f"{name}.png")
        browser.close()


if __name__ == "__main__":
    main()
