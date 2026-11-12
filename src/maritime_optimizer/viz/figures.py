"""Plotly figure builders shared by the HTML dashboards and the Streamlit app.

Every figure is built in light-mode colours with a transparent background. Each
coloured trace carries ``meta={"slot": n}`` so a page can re-colour it for dark
mode without rebuilding the figure: in the browser with ``html.THEME_JS``, in Python
with :func:`apply_theme`.
"""

import math
import re
from typing import Dict, List, Optional, Sequence, Tuple

import plotly.graph_objects as go

from ..graph import MaritimeGraph
from ..models import VoyageResult
from ..routing import is_canal_node, is_waypoint_node
from .theme import COST_SLOTS, DARK, FONT, LIGHT

T = LIGHT
BAR_PX = 22          # bar thickness (spec: <= 24 px)


# ---------------------------------------------------------------- formatting

THIN = " "     # thin space: thousands separator everywhere


def num(x: float, digits: int = 0) -> str:
    """Number with thin-space thousands separators: 1 234 567, 62.5."""
    return f"{x:,.{digits}f}".replace(",", THIN)


def eur(x: float) -> str:
    """Compact euro amount: 950 EUR, 12.9k EUR, 2.47M EUR."""
    a = abs(x)
    if a >= 1e6:
        return f"{x / 1e6:.2f}M EUR"
    if a >= 1e4:
        return f"{x / 1e3:.1f}k EUR"
    return f"{num(x)} EUR"


def mean_speed(r: VoyageResult) -> float:
    """Distance-weighted mean sailing speed (kn)."""
    if not r.legs or r.total_distance_nm <= 0:
        return 0.0
    return sum(leg.speed_knots * leg.distance_nm for leg in r.legs) / r.total_distance_nm


def cost_components(r: VoyageResult) -> Dict[str, float]:
    """Objective Z split into its four terms, in COST_SLOTS order."""
    return {"Fuel": r.total_fuel_cost_eur, "EU ETS": r.total_ets_cost_eur,
            "Time charter": r.total_time_cost_eur, "Canal tolls": r.total_canal_fees_eur,
            "FuelEU penalty": r.total_fueleu_penalty_eur}


# ---------------------------------------------------------------- layout

def _layout(height: int, **kw) -> dict:
    axis = dict(gridcolor=T["grid"], linecolor=T["axis"], zeroline=False,
                tickfont=dict(color=T["muted"], size=11), title_font=dict(color=T["ink2"], size=12),
                showline=True, ticks="")
    base = dict(
        height=height, margin=dict(l=8, r=16, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=T["ink2"], size=12),
        hoverlabel=dict(font=dict(family=FONT, size=12)),
        xaxis=dict(axis), yaxis=dict(axis),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, traceorder="normal",
                    font=dict(color=T["ink2"])),
        barcornerradius=4, separators=f".{THIN}",   # decimal point, thin-space thousands
        showlegend=False,
    )
    base.update(kw)
    return base


# ---------------------------------------------------------------- route map

def _gc_arc(lat1: float, lon1: float, lat2: float, lon2: float, n: int) -> List[Tuple[float, float]]:
    """(lon, lat) points along the great circle from A to B, endpoints included."""
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    d = 2 * math.asin(math.sqrt(math.sin((p2 - p1) / 2) ** 2
                                + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2))
    if d < 1e-9:
        return [(lon1, lat1), (lon2, lat2)]
    pts = []
    for i in range(n + 1):
        f = i / n
        a, b = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
        x = a * math.cos(p1) * math.cos(l1) + b * math.cos(p2) * math.cos(l2)
        y = a * math.cos(p1) * math.sin(l1) + b * math.cos(p2) * math.sin(l2)
        z = a * math.sin(p1) + b * math.sin(p2)
        pts.append((math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))))
    return pts


_LABEL_CHAR_PX, _LABEL_LINE_PX = 7.4, 17     # rough size of 13 px label text
# Candidate directions (degrees, 0 = east, y up), nearest and most natural first: below, above, sides.
_LABEL_ANGLES = (270, 90, 0, 180, 300, 240, 60, 120, 330, 210, 30, 150)
_LABEL_RADII_PX = (46, 70, 100, 140, 190)


def _land_hits(points: List[Tuple[float, float]]) -> bool:
    """True when any (lon, lat) is on mapped land; False when the land data is unavailable."""
    try:
        from ..routing import get_land_detector
        det = get_land_detector()
        if not det.using_geopandas:
            return False
        import shapely
        return det.tree.query(shapely.points(points), predicate="intersects").shape[1] > 0
    except Exception:
        return False


def _place_labels(mids: Dict[str, Tuple[float, float]], texts: Dict[str, str],
                  route_pts: Dict[str, Tuple[List[float], List[float]]],
                  lon_rng: Tuple[float, float], lat_rng: Tuple[float, float],
                  px_w: float, px_h: float) -> Dict[str, Tuple[float, float, str]]:
    """Anchor (lon, lat) and text position for each route label, in open water.

    The anchor is offset from the route's midpoint, so a leader line can join them. A spot is
    accepted when its text box stays inside the map, touches no route line, no other label and no
    mapped land; the nearest such spot wins. Falls back to the nearest spot clear of the routes.
    """
    deg_px = max((lon_rng[1] - lon_rng[0]) / px_w, (lat_rng[1] - lat_rng[0]) / px_h)
    pts = [(lo, la) for lons, lats in route_pts.values() for lo, la in zip(lons, lats, strict=True)]
    margin, pad = 8 * deg_px, 6 * deg_px
    boxes: List[Tuple[float, float, float, float]] = []
    out: Dict[str, Tuple[float, float, str]] = {}
    for name, mid in mids.items():
        lines = re.sub(r"<[^>]+>", "", texts[name].replace("<br>", "\n")).split("\n")
        bw = (max(len(x) for x in lines) * _LABEL_CHAR_PX + 8) * deg_px
        bh = (len(lines) * _LABEL_LINE_PX + 4) * deg_px
        choice, fallback = None, None
        for r in _LABEL_RADII_PX:
            for ang in _LABEL_ANGLES:
                dx, dy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
                ax, ay = mid[0] + r * dx * deg_px, mid[1] + r * dy * deg_px
                if abs(dy) >= abs(dx) and dy > 0:
                    pos, box = "top center", (ax - bw / 2, ay, ax + bw / 2, ay + bh)
                elif abs(dy) >= abs(dx):
                    pos, box = "bottom center", (ax - bw / 2, ay - bh, ax + bw / 2, ay)
                elif dx > 0:
                    pos, box = "middle right", (ax, ay - bh / 2, ax + bw, ay + bh / 2)
                else:
                    pos, box = "middle left", (ax - bw, ay - bh / 2, ax, ay + bh / 2)
                x0, y0, x1, y1 = box
                if (x0 < lon_rng[0] + margin or x1 > lon_rng[1] - margin
                        or y0 < lat_rng[0] + margin or y1 > lat_rng[1] - margin):
                    continue
                if any(x0 - pad <= lo <= x1 + pad and y0 - pad <= la <= y1 + pad for lo, la in pts):
                    continue
                if any(x0 < b[2] and x1 > b[0] and y0 < b[3] and y1 > b[1] for b in boxes):
                    continue
                fallback = fallback or (ax, ay, pos, box)
                grid = [(x0 + (x1 - x0) * i / 4, y0 + (y1 - y0) * j / 2) for i in range(5) for j in range(3)]
                if not _land_hits(grid):
                    choice = (ax, ay, pos, box)
                    break
            if choice:
                break
        ax, ay, pos, box = choice or fallback or (mid[0], mid[1] + 40 * deg_px, "top center",
                                                  (mid[0] - bw / 2, mid[1], mid[0] + bw / 2, mid[1] + bh))
        boxes.append(box)
        out[name] = (ax, ay, pos)
    return out


def route_map(graph: MaritimeGraph, routes: Dict[str, VoyageResult],
              slots: Optional[Dict[str, int]] = None, height: int = 420,
              primary: Optional[str] = None, labels: Optional[Dict[str, str]] = None,
              aspect: float = 2.6) -> go.Figure:
    """Map of one or more optimized routes (great-circle arcs between graph nodes).

    ``primary`` names the optimal route: it is drawn thick and solid, the others thin, dashed and
    fainter. ``labels`` maps a route name to a text annotated at the middle of that route.
    ``aspect`` is the width/height of the map card, used to fit the extent (2.6 on a wide page).
    """
    slots = slots or {name: i for i, name in enumerate(routes)}
    fig = go.Figure()
    lons_all, lats_all, mids, pts = [], [], {}, {}
    for name, r in routes.items():
        lons, lats, mid = [], [], None
        total = sum(leg.distance_nm for leg in r.legs) or 1.0
        done = 0.0
        for u, v in zip(r.route_ports[:-1], r.route_ports[1:], strict=True):
            a, b = graph.ports[u], graph.ports[v]
            d = graph.graph.edges[u, v]["distance_nm"]
            n = max(2, int(d / 60))
            arc = _gc_arc(a.lat, a.lon, b.lat, b.lon, n)
            if mid is None and (done + d) / total >= 0.5:      # the point half-way along the route
                f = min(1.0, max(0.0, (0.5 * total - done) / d)) if d else 0.0
                mid = arc[min(n, round(f * n))]
            done += d
            if lons:
                arc = arc[1:]
            lons += [p[0] for p in arc]
            lats += [p[1] for p in arc]
        lons_all += lons
        lats_all += lats
        main = primary is None or name == primary
        fig.add_trace(go.Scattergeo(
            lon=lons, lat=lats, mode="lines", name=name, hoverinfo="skip",
            opacity=1.0 if main else 0.65,
            line=dict(width=2 if primary is None else 4.5 if main else 2, dash="solid" if main else "dash",
                      color=T["series"][slots[name]]),
            meta={"slot": slots[name]}))
        mids[name] = mid
        pts[name] = (lons, lats)
        # Invisible but generous hover targets on every node of the route.
        cum, hover = 0.0, []
        for i, code in enumerate(r.route_ports):
            if i:
                cum += r.legs[i - 1].distance_nm
            p = graph.ports[code]
            kind = "waypoint" if is_waypoint_node(code) else "canal/strait" if is_canal_node(code) else "port"
            hover.append(f"<b>{p.name}</b> ({kind})<br>{name}<br>{cum:,.0f} NM from origin")
        fig.add_trace(go.Scattergeo(
            lon=[graph.ports[c].lon for c in r.route_ports],
            lat=[graph.ports[c].lat for c in r.route_ports],
            mode="markers", hovertext=hover, hoverinfo="text", showlegend=False,
            marker=dict(size=14, opacity=0)))
    # Ports and transited canals as visible markers with a surface ring. Only the
    # endpoints and the corridor-defining nodes are labelled, to avoid collisions.
    labelled = {"SUEZ", "PANA", "HOPE", "HORN"}
    ends = {}
    for r in routes.values():
        labelled |= {r.route_ports[0], r.route_ports[-1]}
        ends[r.route_ports[0]], ends[r.route_ports[-1]] = "Origin", "Destination"
    shown = {}
    for r in routes.values():
        for code in r.route_ports:
            if not is_waypoint_node(code):
                shown[code] = graph.ports[code]
    fig.add_trace(go.Scattergeo(
        lon=[p.lon for p in shown.values()], lat=[p.lat for p in shown.values()],
        mode="markers+text", textposition="top center",
        text=[(f"<b>{p.name}</b>" if code in ends else p.name) if code in labelled else ""
              for code, p in shown.items()],
        hovertext=[f"{p.name} ({ends[code].lower()})" if code in ends else p.name
                   for code, p in shown.items()],
        hoverinfo="text",
        textfont=dict(color=T["ink2"], size=13), showlegend=False,
        marker=dict(size=[16 if code in ends else 9 for code in shown], color=T["ink2"],
                    symbol=["circle-open-dot" if ends.get(code) == "Destination" else "circle"
                            for code in shown],
                    line=dict(width=2, color=T["surface"])),
        meta={"role": "nodes"}))
    # Extent padded to the card's aspect ratio so the map fills the card.
    lon0, lon1, lat0, lat1 = min(lons_all), max(lons_all), min(lats_all), max(lats_all)
    lat_span = (lat1 - lat0) + 10
    lon_span = max((lon1 - lon0) + (8 if aspect >= 2 else 18), lat_span * aspect)
    lat_span = max(lat_span, lon_span / aspect)
    lon_c, lat_c = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    fig.update_geos(
        projection_type="natural earth", showcountries=False, showframe=False,
        showland=True, landcolor=T["land"], showocean=True, oceancolor=T["ocean"],
        showlakes=True, lakecolor=T["ocean"],
        showcoastlines=True, coastlinecolor=T["coast"], coastlinewidth=0.5,
        bgcolor="rgba(0,0,0,0)",
        lonaxis_range=[lon_c - lon_span / 2, lon_c + lon_span / 2],
        lataxis_range=[max(-85, lat_c - lat_span / 2), min(85, lat_c + lat_span / 2)])
    if labels:
        lon_rng = (lon_c - lon_span / 2, lon_c + lon_span / 2)
        lat_rng = (max(-85, lat_c - lat_span / 2), min(85, lat_c + lat_span / 2))
        shown_labels = {n: labels[n] for n in routes if n in labels and mids.get(n)}
        placed = _place_labels({n: mids[n] for n in shown_labels}, shown_labels, pts, lon_rng, lat_rng,
                               950 if aspect >= 2 else 340, height - 50)
        for name, (ax, ay, pos) in placed.items():
            color = T["series"][slots[name]]
            fig.add_trace(go.Scattergeo(     # leader line from the route to its label
                lon=[mids[name][0], ax], lat=[mids[name][1], ay], mode="lines+markers", hoverinfo="skip",
                showlegend=False, line=dict(width=1.2, dash="dot", color=color),
                marker=dict(size=[6, 0], color=color), meta={"slot": slots[name]}))
            fig.add_trace(go.Scattergeo(
                lon=[ax], lat=[ay], mode="text", text=[shown_labels[name]], hoverinfo="skip",
                textposition=pos, showlegend=False, textfont=dict(color=color, size=13),
                meta={"slot": slots[name], "role": "label"}))
    fig.update_layout(_layout(height, margin=dict(l=0, r=0, t=0, b=0),
                              showlegend=len(routes) > 1))
    return fig


# ---------------------------------------------------------------- cost breakdown

def cost_breakdown(rows: Sequence[Tuple[str, VoyageResult]], narrow: bool = False) -> go.Figure:
    """Horizontal stacked bars of the objective Z (fuel, ETS, time charter, tolls) per alternative."""
    labels = [label for label, _ in rows]
    comps = [cost_components(r) for _, r in rows]
    fig = go.Figure()
    for comp, slot in COST_SLOTS.items():
        vals = [c[comp] for c in comps]
        if not any(vals):
            continue
        totals = [sum(c.values()) for c in comps]
        fig.add_trace(go.Bar(
            y=labels, x=vals, name=comp, orientation="h", meta={"slot": slot},
            marker=dict(color=T["series"][slot], line=dict(width=2, color=T["surface"])),
            customdata=[[eur(v), v / t * 100 if t else 0] for v, t in zip(vals, totals, strict=True)],
            hovertemplate=f"<b>%{{y}}</b><br>{comp}: %{{customdata[0]}} (%{{customdata[1]:.0f}} %)"
                          "<extra></extra>"))
    for label, c in zip(labels, comps, strict=True):
        fig.add_annotation(x=sum(c.values()), y=label, text=eur(sum(c.values())), showarrow=False,
                           xanchor="left", xshift=6, font=dict(color=T["ink"], size=12))
    n = len(rows)
    top = 100 if narrow else 36          # a narrow card wraps the legend onto more rows
    height = top + 20 + n * 44
    fig.update_layout(_layout(
        height, barmode="stack", bargap=1 - BAR_PX / 44, showlegend=True,
        margin=dict(l=8, r=90, t=top, b=8),
        xaxis=dict(_layout(0)["xaxis"], tickformat="~s", ticksuffix=" EUR", showline=False),
        yaxis=dict(_layout(0)["yaxis"], autorange="reversed", showgrid=False)))
    return fig


# ---------------------------------------------------------------- single-series bars and lines

def hbar(labels: Sequence[str], values: Sequence[float], unit: str, fmt: str = ",.2f",
         ref: Optional[float] = None, ref_label: str = "", slot: int = 0,
         x_from_zero: bool = True, x_title: str = "") -> go.Figure:
    """One series of horizontal bars with the value at the tip and an optional reference line."""
    text = [f"{v:{fmt}} {unit}".replace(",", THIN) for v in values]
    fig = go.Figure(go.Bar(
        y=list(labels), x=list(values), orientation="h", text=text, textposition="outside",
        textfont=dict(color=T["ink"]), cliponaxis=False, meta={"slot": slot},
        marker=dict(color=T["series"][slot], line=dict(width=2, color=T["surface"])),
        hovertemplate="<b>%{y}</b><br>%{x:" + fmt + "} " + unit + "<extra></extra>"))
    n = len(labels)
    xmax = max(list(values) + ([ref] if ref else [])) * 1.18
    layout = _layout(40 + n * 40 + (26 if x_title else 0), bargap=1 - BAR_PX / 40,
                     margin=dict(l=8, r=24, t=8, b=8))
    layout["xaxis"].update(range=[0 if x_from_zero else None, xmax], showline=False,
                           **({"title": dict(text=x_title, standoff=6)} if x_title else {}))
    layout["yaxis"].update(autorange="reversed", showgrid=False)
    fig.update_layout(layout)
    if ref is not None:
        fig.add_vline(x=ref, line=dict(color=T["ink2"], width=1.5))
        fig.add_annotation(x=ref, y=1.0, yref="paper", text=ref_label, showarrow=False,
                           xanchor="left", xshift=4, yanchor="bottom",
                           font=dict(color=T["ink2"], size=11))
        fig.update_layout(margin=dict(t=26))
    return fig


def line_xy(x: Sequence[float], y: Sequence[float], x_title: str, y_title: str,
            y_fmt: str = ",.2f", unit: str = "", slot: int = 0, height: int = 260) -> go.Figure:
    """One series line (2 px) with 8 px markers and the last value labelled."""
    fig = go.Figure(go.Scatter(
        x=list(x), y=list(y), mode="lines+markers", meta={"slot": slot},
        line=dict(width=2, color=T["series"][slot]),
        marker=dict(size=9, color=T["series"][slot], line=dict(width=2, color=T["surface"])),
        hovertemplate=f"{x_title}: %{{x}}<br>{y_title}: %{{y:{y_fmt}}} {unit}<extra></extra>"))
    fig.add_annotation(x=x[-1], y=y[-1], text=f"{y[-1]:{y_fmt}} {unit}", showarrow=False,
                       xanchor="left", xshift=8, font=dict(color=T["ink"], size=12))
    layout = _layout(height, margin=dict(l=8, r=70, t=8, b=8), hovermode="x")
    layout["xaxis"].update(title=x_title, showgrid=False, tickvals=list(x))
    layout["yaxis"].update(title=y_title, tickformat=y_fmt)
    fig.update_layout(layout)
    return fig


# ---------------------------------------------------------------- dark mode (server side)

def apply_theme(fig: go.Figure, mode: str = "light", palette: Optional[dict] = None) -> go.Figure:
    """Re-colour a figure built by this module for ``mode`` ("light" or "dark"), in place.

    Python twin of ``html.THEME_JS``: same tokens, same rules, for hosts such as Streamlit
    that render the figure on the server. ``palette`` replaces the dark tokens (e.g.
    ``theme.NAVY``); it must have the keys of ``theme.DARK``.
    """
    if mode != "dark":
        return fig
    p = palette or DARK
    fig.update_layout(font_color=p["ink2"], hoverlabel_bgcolor=p["surface"],
                      hoverlabel_font_color=p["ink"], legend_font_color=p["ink2"])
    axis = dict(gridcolor=p["grid"], linecolor=p["axis"], tickfont_color=p["muted"],
                title_font_color=p["ink2"])
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    if "geo" in fig.layout and fig.layout.geo.projection.type:
        fig.update_geos(landcolor=p["land"], oceancolor=p["ocean"], lakecolor=p["ocean"],
                        coastlinecolor=p["coast"])
    for a in fig.layout.annotations:
        a.font.color = p["ink"] if a.xanchor == "left" and a.yref != "paper" else p["ink2"]
    for s in fig.layout.shapes:
        s.line.color = p["ink2"]
    for tr in fig.data:
        meta = tr.meta if isinstance(tr.meta, dict) else {}
        tr.marker.line.color = p["surface"]
        if "slot" in meta:
            tr.marker.color = p["series"][meta["slot"]]
            if "line" in tr:
                tr.line.color = p["series"][meta["slot"]]
        if meta.get("role") == "label":
            tr.textfont.color = p["series"][meta["slot"]]
        if meta.get("role") == "nodes":
            tr.marker.color = p["ink2"]
            tr.textfont.color = p["ink2"]
        if tr.type == "bar" and tr.text is not None:
            tr.textfont.color = p["ink"]
    return fig
