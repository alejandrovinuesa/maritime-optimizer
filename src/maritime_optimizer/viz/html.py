"""Shared HTML building blocks for the dashboards and the AI compliance reports.

Page CSS with light/dark tokens, the script that re-colours Plotly charts for the active
theme, the data disclaimer, and small components (KPI tile, status badge, data table,
chart card). Public so that every page in the package renders the same way.
"""

import html
import json
from typing import List, Sequence, Tuple

import plotly
import plotly.graph_objects as go

from ..models import VoyageResult
from .figures import cost_components
from .theme import DARK, LIGHT, STATUS

PLOTLY_CDN = f"https://cdn.plot.ly/plotly-{plotly.offline.get_plotlyjs_version()}.min.js"

DISCLAIMER = (
    "Research and decision-support tool. Regulatory parameters come from Delegated Regulation (EU) "
    "2023/2776, Regulation (EU) 2023/1805 (FuelEU Maritime), Directive 2003/87/EC as amended by "
    "Directive (EU) 2023/959, IMO resolutions MEPC.338(76), MEPC.353(78) and MEPC.354(78) and "
    "MARPOL Annex VI, in their version in force in 2026, and may change. Vessels are generic examples; "
    "MCR, SFOC, time-charter rates, fuel and EUA prices and canal tolls are illustrative estimates, "
    "not market quotes. Calm-water physics. Results do not replace official MRV, FuelEU or CII "
    "verification and are not legal or commercial advice."
)

# ------------------------------------------------------------------ page shell

CSS = """
:root{color-scheme:light;--page:%(lp)s;--surface:%(ls)s;--ink:%(li)s;--ink2:%(li2)s;--muted:%(lm)s;
--grid:%(lg)s;--border:%(lb)s;--good:%(good)s;--bad:%(crit)s;--warn:%(serious)s}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;
--page:%(dp)s;--surface:%(ds)s;--ink:%(di)s;--ink2:%(di2)s;--muted:%(dm)s;--grid:%(dg)s;--border:%(db)s}}
:root[data-theme="dark"]{color-scheme:dark;--page:%(dp)s;--surface:%(ds)s;--ink:%(di)s;--ink2:%(di2)s;
--muted:%(dm)s;--grid:%(dg)s;--border:%(db)s}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 40px}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-start;justify-content:space-between}
h1{font-size:24px;line-height:1.2;margin:0 0 4px;font-weight:650}
.sub{color:var(--ink2);margin:0}
nav{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
nav a,nav button{font:inherit;font-size:13px;color:var(--ink2);text-decoration:none;padding:4px 10px;
border:1px solid var(--border);border-radius:999px;background:var(--surface);cursor:pointer}
nav a[aria-current]{color:var(--ink);font-weight:600;border-color:var(--ink2)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(126px,1fr));gap:12px;margin:20px 0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 14px}
.tile .label{color:var(--ink2);font-size:12px}
.tile .value{font-size:22px;font-weight:600;margin-top:2px}
.tile.hero{grid-column:span 2}
.tile.hero .value{font-size:34px}
.tile .note{color:var(--muted);font-size:12px;margin-top:2px}
.status{display:inline-flex;align-items:center;gap:6px}
.status i{font-style:normal;width:18px;height:18px;border-radius:50%%;display:inline-grid;place-items:center;
color:#fff;font-size:12px;font-weight:700}
.status.good i{background:var(--good)} .status.bad i{background:var(--bad)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%%,520px),1fr));gap:16px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px;min-width:0}
.card.wide{grid-column:1/-1}
.card .plotly-graph-div{width:100%%!important;max-width:100%%}
.card h2{font-size:15px;margin:0 0 2px;font-weight:600}
.card p.cap{color:var(--ink2);margin:0 0 8px;font-size:13px}
details{margin-top:6px}
summary{cursor:pointer;color:var(--ink2);font-size:12px}
table{border-collapse:collapse;width:100%%;margin-top:6px;font-size:12px;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:4px 6px;border-bottom:1px solid var(--grid)}
th:first-child,td:first-child{text-align:left}
th{color:var(--ink2);font-weight:600}
.tablewrap{overflow-x:auto}
footer{margin-top:24px;color:var(--muted);font-size:12px;border-top:1px solid var(--grid);padding-top:12px}
footer a{color:var(--ink2)}
""" % dict(lp=LIGHT["page"], ls=LIGHT["surface"], li=LIGHT["ink"], li2=LIGHT["ink2"], lm=LIGHT["muted"],
           lg=LIGHT["grid"], lb=LIGHT["border"], dp=DARK["page"], ds=DARK["surface"], di=DARK["ink"],
           di2=DARK["ink2"], dm=DARK["muted"], dg=DARK["grid"], db=DARK["border"],
           good=STATUS["good"], crit=STATUS["critical"], serious=STATUS["serious"])

# Re-colours every Plotly chart when the effective theme changes (OS setting or toggle).
THEME_JS = """
const PAL = %s;
function effectiveTheme(){
  const t = document.documentElement.dataset.theme;
  if (t === 'light' || t === 'dark') return t;
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
function paint(){
  const p = PAL[effectiveTheme()];
  document.querySelectorAll('.js-plotly-plot').forEach(gd => {
    const lay = {'font.color': p.ink2, 'hoverlabel.bgcolor': p.surface, 'hoverlabel.font.color': p.ink,
                 'legend.font.color': p.ink2};
    Object.keys(gd.layout).filter(k => /^[xy]axis/.test(k)).forEach(k => {
      lay[k + '.gridcolor'] = p.grid; lay[k + '.linecolor'] = p.axis;
      lay[k + '.tickfont.color'] = p.muted; lay[k + '.title.font.color'] = p.ink2;
    });
    if (gd.layout.geo) Object.assign(lay, {'geo.landcolor': p.land, 'geo.oceancolor': p.ocean, 'geo.lakecolor': p.ocean,
                                           'geo.coastlinecolor': p.coast});
    (gd.layout.annotations || []).forEach((a, i) => {
      lay['annotations[' + i + '].font.color'] = a.xanchor === 'left' && a.yref !== 'paper' ? p.ink : p.ink2;
    });
    (gd.layout.shapes || []).forEach((s, i) => { lay['shapes[' + i + '].line.color'] = p.ink2; });
    Plotly.relayout(gd, lay);
    gd.data.forEach((tr, i) => {
      const m = tr.meta || {};
      const up = {'marker.line.color': p.surface};
      if (m.slot !== undefined) { const c = p.series[m.slot]; up['marker.color'] = c; up['line.color'] = c; }
      if (m.role === 'nodes') { up['marker.color'] = p.ink2; up['textfont.color'] = p.ink2; }
      if (tr.type === 'bar' && tr.text) up['textfont.color'] = p.ink;
      Plotly.restyle(gd, up, [i]);
    });
  });
}
function cycleTheme(){
  const order = ['auto', 'light', 'dark'];
  const cur = document.documentElement.dataset.theme || 'auto';
  const next = order[(order.indexOf(cur) + 1) %% order.length];
  if (next === 'auto') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = next;
  document.getElementById('theme').textContent = 'Theme: ' + next;
  paint();
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', paint);
window.addEventListener('load', () => {
  document.querySelectorAll('.js-plotly-plot').forEach(gd => Plotly.Plots.resize(gd));
  paint();
});
""" % json.dumps({"light": LIGHT, "dark": DARK})


def status_badge(ok: bool, good: str, bad: str) -> str:
    """Status label with an icon (never colour alone)."""
    return (f'<span class="status good"><i aria-hidden="true">✓</i>{good}</span>' if ok
            else f'<span class="status bad"><i aria-hidden="true">!</i>{bad}</span>')


def tile(label: str, value: str, note: str = "", hero: bool = False) -> str:
    """KPI tile: label, value and optional note."""
    cls = "tile hero" if hero else "tile"
    note_html = f'<div class="note">{note}</div>' if note else ""
    return f'<div class="{cls}"><div class="label">{label}</div><div class="value">{value}</div>{note_html}</div>'


def data_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Collapsible data table: the accessible twin of every chart."""
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>"
                   for row in rows)
    return (f'<details><summary>Show data table</summary><div class="tablewrap"><table>'
            f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></details>')


def chart_card(title: str, caption: str, fig: go.Figure, table: str, div_id: str,
               wide: bool = False) -> str:
    """Card with a title, caption, Plotly chart (no inline Plotly.js) and its data table."""
    plot = fig.to_html(full_html=False, include_plotlyjs=False, div_id=div_id,
                       config={"displayModeBar": False, "responsive": True})
    cls = "card wide" if wide else "card"
    return f'<section class="{cls}"><h2>{title}</h2><p class="cap">{caption}</p>{plot}{table}</section>'


def cost_table_rows(rows: Sequence[Tuple[str, VoyageResult]]) -> List[List[str]]:
    """Rows of the cost table (one per alternative), matching COST_HEADERS."""
    out = []
    for label, r in rows:
        c = cost_components(r)
        out.append([label] + [f"{v:,.0f}" for v in c.values()] + [f"{r.total_cost_eur:,.0f}"])
    return out


COST_HEADERS = ["", "Fuel EUR", "EU ETS EUR", "Time charter EUR", "Canal tolls EUR",
                "FuelEU penalty EUR", "Total EUR"]
