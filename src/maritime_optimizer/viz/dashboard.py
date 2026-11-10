"""Self-contained HTML dashboards for the five demonstration scenarios.

Each page is a single HTML file (Plotly.js loaded from its CDN) with KPI tiles,
interactive charts, a data table behind every chart, light/dark mode and the
data disclaimer. Build them with ``maritime-opt dashboard all --out <dir>``.
"""

import html
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import plotly
import plotly.graph_objects as go

from .. import __version__
from ..graph import MaritimeGraph, load_vessels_from_csv
from ..models import VoyageResult
from ..scenarios import SCENARIO_IDS, run_scenario
from .figures import cost_breakdown, cost_components, eur, hbar, line_xy, mean_speed, route_map
from .theme import CORRIDOR_SLOTS, DARK, LIGHT, STATUS

PLOTLY_CDN = f"https://cdn.plot.ly/plotly-{plotly.offline.get_plotlyjs_version()}.min.js"

TITLES = {
    "E1": ("Valencia → Rotterdam", "Intra-EU voyage · 100 % EU ETS coverage · Panamax bulk carrier on HFO"),
    "E2": ("Rotterdam → New York", "Extra-EU voyage · 50 % EU ETS coverage · Neopanamax container ship on MGO"),
    "E3": ("Marseille → Singapore: fuel choice", "Same voyage with HFO, MGO, LNG and BioLNG · Aframax dual-fuel tanker"),
    "E4": ("Hamburg → Houston: carbon price", "EUA price from 40 to 150 EUR/t · Neopanamax container ship on MGO"),
    "E5": ("Singapore → Rotterdam: Suez or the Cape?", "Two corridors optimized separately · LR2 dual-fuel tanker on LNG"),
}

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


def _status(ok: bool, good: str, bad: str) -> str:
    return (f'<span class="status good"><i aria-hidden="true">✓</i>{good}</span>' if ok
            else f'<span class="status bad"><i aria-hidden="true">!</i>{bad}</span>')


def _tile(label: str, value: str, note: str = "", hero: bool = False) -> str:
    cls = "tile hero" if hero else "tile"
    note_html = f'<div class="note">{note}</div>' if note else ""
    return f'<div class="{cls}"><div class="label">{label}</div><div class="value">{value}</div>{note_html}</div>'


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>"
                   for row in rows)
    return (f'<details><summary>Show data table</summary><div class="tablewrap"><table>'
            f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></details>')


def _card(title: str, caption: str, fig: go.Figure, table: str, div_id: str, wide: bool = False) -> str:
    plot = fig.to_html(full_html=False, include_plotlyjs=False, div_id=div_id,
                       config={"displayModeBar": False, "responsive": True})
    cls = "card wide" if wide else "card"
    return f'<section class="{cls}"><h2>{title}</h2><p class="cap">{caption}</p>{plot}{table}</section>'


def _page(sid: str, tiles: List[str], cards: List[str]) -> str:
    title, sub = TITLES[sid]
    nav = "".join(f'<a href="{s.lower()}.html"{" aria-current=page" if s == sid else ""}>{s}</a>'
                  for s in SCENARIO_IDS)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{sid} · {html.escape(title)}</title>
<script src="{PLOTLY_CDN}"></script>
<style>{CSS}</style></head>
<body><div class="wrap">
<header><div><h1>{html.escape(title)}</h1><p class="sub">{html.escape(sub)}</p></div>
<nav aria-label="Scenarios">{nav}<button id="theme" type="button" onclick="cycleTheme()">Theme: auto</button></nav>
</header>
<div class="tiles">{''.join(tiles)}</div>
<div class="grid">{''.join(cards)}</div>
<footer><p><strong>Data notice.</strong> {html.escape(DISCLAIMER)}</p>
<p>Generated by <a href="https://github.com/alexvinuesa22-debug/maritime-optimizer">maritime-optimizer</a>
v{__version__} · EUA 65 EUR/t unless stated · year 2026.</p></footer>
</div><script>{THEME_JS}</script></body></html>"""


# ------------------------------------------------------------------ shared pieces

def _cost_rows(rows: Sequence[Tuple[str, VoyageResult]]) -> List[List[str]]:
    out = []
    for label, r in rows:
        c = cost_components(r)
        out.append([label] + [f"{v:,.0f}" for v in c.values()] + [f"{r.total_cost_eur:,.0f}"])
    return out


COST_HEADERS = ["", "Fuel EUR", "EU ETS EUR", "Time charter EUR", "Canal tolls EUR",
                "FuelEU penalty EUR", "Total EUR"]


def _cost_card(rows: Sequence[Tuple[str, VoyageResult]], caption: str, div_id: str,
               wide: bool = False) -> str:
    return _card("Where the money goes", caption, cost_breakdown(rows),
                 _table(COST_HEADERS, _cost_rows(rows)), div_id, wide)


def _map_card(graph: MaritimeGraph, routes: Dict[str, VoyageResult], caption: str,
              slots: Optional[Dict[str, int]] = None) -> str:
    rows = []
    for name, r in routes.items():
        names = [graph.ports[c].name for c in r.route_ports]
        rows.append([name, " → ".join(names), f"{r.total_distance_nm:,.0f}",
                     f"{r.total_transit_hours / 24:.1f}"])
    return _card("Optimized route", caption, route_map(graph, routes, slots),
                 _table(["Route", "Nodes", "Distance NM", "Days"], rows), "map", wide=True)


def _voyage_tiles(r: VoyageResult) -> List[str]:
    deficit = r.fueleu_intensity - r.fueleu_max_allowed
    return [
        _tile("Total voyage cost", eur(r.total_cost_eur), "fuel + ETS + time + tolls", hero=True),
        _tile("Optimal speed", f"{mean_speed(r):.1f} kn", f"design speed {r.vessel.design_speed:.0f} kn"),
        _tile("Transit", f"{r.total_transit_hours / 24:.1f} days", f"{r.total_distance_nm:,.0f} NM"),
        _tile("CO₂ emitted", f"{r.total_co2_combustion_tons:,.0f} t", f"ETS cost {eur(r.total_ets_cost_eur)}"),
        _tile("CII rating", _status(r.cii_compliant, f"{r.cii_rating.value}", f"{r.cii_rating.value}"),
              "MARPOL Annex VI reg. 28"),
        _tile("FuelEU 2026", _status(r.fueleu_compliant, "Compliant", "Deficit"),
              f"{r.fueleu_intensity:.1f} vs {r.fueleu_max_allowed:.2f} gCO₂e/MJ"
              + ("" if r.fueleu_compliant else f" (+{deficit:.2f})")),
        _tile("ISO 14083 intensity", f"{r.iso14083_intensity:.2f}", "gCO₂e per tonne-km"),
    ]


# ------------------------------------------------------------------ scenarios

def _single_voyage(sid: str, graph: MaritimeGraph, r: VoyageResult) -> str:
    legs = [f"{graph.ports[leg.origin].name} → {graph.ports[leg.destination].name}" for leg in r.legs]
    co2 = [leg.co2_combustion_tons for leg in r.legs]
    leg_rows = [[name, f"{leg.distance_nm:,.0f}", f"{leg.speed_knots:.2f}", f"{leg.fuel_consumed_tons:.1f}",
                 f"{leg.co2_combustion_tons:.1f}", f"{leg.ets_cost_eur:,.0f}"]
                for name, leg in zip(legs, r.legs, strict=True)]
    cards = [
        _map_card(graph, {"Optimal route": r}, "Cheapest feasible route on the maritime graph. "
                  "Hover a node for its cumulative distance."),
        _cost_card([("This voyage", r)], "Objective Z split into its terms. Time charter (TCE) is the "
                   "opportunity cost of the ship's days at sea.", "cost"),
        _card("CO₂ by leg", "Emissions scale with distance: every leg sails at the same optimal speed.",
              hbar(legs, co2, "t", ",.0f"),
              _table(["Leg", "Distance NM", "Speed kn", "Fuel t", "CO₂ t", "ETS EUR"], leg_rows), "legs"),
    ]
    return _page(sid, _voyage_tiles(r), cards)


def _fuels(graph: MaritimeGraph, results: List[VoyageResult]) -> str:
    labels = [r.vessel.fuel_type.value for r in results]
    cheapest = min(results, key=lambda r: r.total_cost_eur)
    cleanest = min(results, key=lambda r: r.iso14083_intensity)
    compliant = [r.vessel.fuel_type.value for r in results if r.fueleu_compliant]
    limit = results[0].fueleu_max_allowed
    tiles = [
        _tile("Cheapest fuel", f"{cheapest.vessel.fuel_type.value} · {eur(cheapest.total_cost_eur)}",
              "lowest total voyage cost", hero=True),
        _tile("Lowest footprint", f"{cleanest.vessel.fuel_type.value} · {cleanest.iso14083_intensity:.2f}",
              "ISO 14083 gCO₂e/t·km"),
        _tile("FuelEU 2026 compliant", ", ".join(compliant) or "none", f"limit {limit:.2f} gCO₂e/MJ"),
        _tile("Premium of cleanest", f"+{eur(cleanest.total_cost_eur - cheapest.total_cost_eur)}",
              f"{(cleanest.total_cost_eur / cheapest.total_cost_eur - 1):.1%} over the cheapest"),
    ]
    rows = list(zip(labels, results, strict=True))
    intensity_rows = [[lab, f"{r.fueleu_intensity:.2f}", "yes" if r.fueleu_compliant else "no",
                       f"{r.iso14083_intensity:.3f}", f"{r.total_co2_combustion_tons:,.0f}",
                       f"{r.total_co2_tons:,.0f}", f"{mean_speed(r):.2f}"] for lab, r in rows]
    cards = [
        _cost_card(rows, "BioLNG pays almost no ETS (biofuel zero-rating) but its fuel is twice as expensive "
                   "and it sails slower.", "cost"),
        _card("FuelEU well-to-wake intensity", f"Vertical line: the 2026 limit ({limit:.2f} gCO₂e/MJ). "
              "Bars past it are in deficit.",
              hbar(labels, [r.fueleu_intensity for r in results], "gCO₂e/MJ", ",.1f", ref=limit,
                   ref_label="2026 limit"),
              _table(["Fuel", "WtW gCO₂e/MJ", "FuelEU OK", "ISO g/t·km", "CO₂ t", "CO₂ for ETS t",
                      "Speed kn"], intensity_rows), "fueleu"),
        _card("ISO 14083 footprint", "Real well-to-wake emissions per tonne-km, with no zero-rating. "
              "Fossil LNG is close to HFO because of methane slip.",
              hbar(labels, [r.iso14083_intensity for r in results], "g/t·km", ",.3f"),
              _table(["Fuel", "ISO 14083 gCO₂e/t·km"], [[lab, f"{r.iso14083_intensity:.3f}"]
                                                         for lab, r in rows]), "iso"),
        _map_card(graph, {"Route (all fuels)": cheapest}, "All four fuels take the same route via Suez."),
    ]
    return _page("E3", tiles, cards)


def _eua(graph: MaritimeGraph, results: List[VoyageResult]) -> str:
    prices = [int(r.eua_price) for r in results]
    speeds = [mean_speed(r) for r in results]
    co2 = [r.total_co2_combustion_tons for r in results]
    cost = [r.total_cost_eur for r in results]
    lo, hi = results[0], results[-1]
    tiles = [
        _tile("Cost increase", f"+{(hi.total_cost_eur / lo.total_cost_eur - 1):.1%}",
              f"EUA {prices[0]} → {prices[-1]} EUR/t (×{prices[-1] / prices[0]:.2f})", hero=True),
        _tile("Optimal speed", f"{speeds[0]:.2f} → {speeds[-1]:.2f} kn",
              f"{(speeds[-1] / speeds[0] - 1):+.1%}"),
        _tile("CO₂ emitted", f"{co2[0]:,.0f} → {co2[-1]:,.0f} t", f"{(co2[-1] / co2[0] - 1):+.1%}"),
        _tile("ETS share of cost", f"{lo.total_ets_cost_eur / lo.total_cost_eur:.1%} → "
              f"{hi.total_ets_cost_eur / hi.total_cost_eur:.1%}", "of total voyage cost"),
    ]
    sweep_rows = [[f"{p}", f"{s:.2f}", f"{c:,.0f}", f"{z:,.0f}", f"{r.total_ets_cost_eur:,.0f}"]
                  for p, s, c, z, r in zip(prices, speeds, co2, cost, results, strict=True)]
    sweep_tab = _table(["EUA EUR/t", "Speed kn", "CO₂ t", "Total EUR", "ETS EUR"], sweep_rows)
    cards = [
        _card("Optimal speed vs carbon price", "A dearer carbon price slows the ship down: "
              "regulation-induced slow steaming.",
              line_xy(prices, speeds, "EUA price (EUR/t)", "Speed (kn)", ".2f", "kn"), sweep_tab, "speed"),
        _card("CO₂ vs carbon price", "Fuel per mile grows with speed squared, so a small speed cut "
              "removes a larger share of CO₂.",
              line_xy(prices, co2, "EUA price (EUR/t)", "CO₂ (t)", ",.0f", "t"), sweep_tab, "co2"),
        _cost_card([(f"EUA {p}", r) for p, r in zip(prices, results, strict=True)],
                   "The optimizer absorbs part of the carbon price by trading fuel for time.", "cost",
                   wide=True),
        _map_card(graph, {"Route": results[0]}, "The route does not change with the carbon price; "
                  "only the speed does."),
    ]
    return _page("E4", tiles, cards)


def _corridors(graph: MaritimeGraph, routes: Dict[str, Optional[VoyageResult]]) -> str:
    names = {"suez": "Via Suez", "cape": "Via the Cape"}
    valid = {k: r for k, r in routes.items() if r is not None}
    best = min(valid, key=lambda k: valid[k].total_cost_eur)
    other = next(k for k in valid if k != best)
    b, o = valid[best], valid[other]
    tiles = [
        _tile("Best corridor", names[best], f"saves {eur(o.total_cost_eur - b.total_cost_eur)} "
              f"({(o.total_cost_eur - b.total_cost_eur) / o.total_cost_eur:.1%})", hero=True),
        _tile("Extra days via the Cape", f"{(valid['cape'].total_transit_hours - valid['suez'].total_transit_hours) / 24:.1f}",
              f"+{valid['cape'].total_distance_nm - valid['suez'].total_distance_nm:,.0f} NM"),
        _tile("Suez toll", eur(valid["suez"].total_canal_fees_eur), "representative per-transit value"),
        _tile("ISO 14083 intensity", f"{b.iso14083_intensity:.3f} / {o.iso14083_intensity:.3f}",
              "same ship, fuel and speed: distance cancels out"),
    ]
    rows = [(names[k], valid[k]) for k in ("suez", "cape") if k in valid]
    by_name = {names[k]: valid[k] for k in ("suez", "cape") if k in valid}
    slots = {names[k]: CORRIDOR_SLOTS[k] for k in valid}
    cards = [
        _map_card(graph, by_name, "Both corridors, each optimized on its own.", slots),
        _cost_card(rows, "The toll is paid only via Suez; the Cape costs more fuel, ETS and ship-days.",
                   "cost", wide=True),
    ]
    return _page("E5", tiles, cards)


def build_dashboard(sid: str, graph: Optional[MaritimeGraph] = None, vessels=None) -> str:
    """HTML of one scenario dashboard."""
    graph = graph or MaritimeGraph()
    vessels = vessels or load_vessels_from_csv()
    sid = sid.upper()
    res = run_scenario(sid, graph, vessels)
    if sid in ("E1", "E2"):
        return _single_voyage(sid, graph, res)
    if sid == "E3":
        return _fuels(graph, res)
    if sid == "E4":
        return _eua(graph, res)
    return _corridors(graph, res)


def index_page() -> str:
    """Landing page linking the five scenario dashboards."""
    items = "".join(
        f'<a class="tile" href="{sid.lower()}.html"><div class="label">{sid}</div>'
        f'<div class="value" style="font-size:17px">{html.escape(t)}</div>'
        f'<div class="note">{html.escape(s)}</div></a>'
        for sid, (t, s) in TITLES.items())
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>maritime-optimizer dashboards</title><style>{CSS}
a.tile{{color:inherit;text-decoration:none}} a.tile:hover{{border-color:var(--ink2)}}</style></head>
<body><div class="wrap"><h1>maritime-optimizer · example dashboards</h1>
<p class="sub">Route, speed and fuel optimization under the EU ETS, FuelEU Maritime and MARPOL Annex VI.
Five scenarios with a generic example fleet.</p>
<div class="tiles" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr))">{items}</div>
<footer><p><strong>Data notice.</strong> {html.escape(DISCLAIMER)}</p>
<p><a href="https://github.com/alexvinuesa22-debug/maritime-optimizer">Source code on GitHub</a></p></footer>
</div></body></html>"""


def write_dashboards(out_dir: Path, ids: Sequence[str] = SCENARIO_IDS) -> List[Path]:
    """Write <id>.html for each scenario (plus index.html) into out_dir and return the paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    graph, vessels = MaritimeGraph(), load_vessels_from_csv()
    paths = []
    for sid in ids:
        path = out_dir / f"{sid.lower()}.html"
        path.write_text(build_dashboard(sid, graph, vessels), encoding="utf-8")
        paths.append(path)
    index = out_dir / "index.html"
    index.write_text(index_page(), encoding="utf-8")
    paths.append(index)
    return paths
