"""Interactive dashboard: pick two ports, a vessel and a deadline, see the optimized voyage on a map,
then ask Claude for a compliance report or questions, with your own Anthropic API key.

Run locally:   pip install -e ".[app,agents]"   then   streamlit run app/streamlit_app.py

API key policy: the AI features use only the key the user types in the sidebar. It is kept in this
browser session's memory, never written to disk, logged or put in a URL. A server-side
ANTHROPIC_API_KEY is ignored unless MARITIME_OPT_ALLOW_SERVER_KEY=1 is set, so a public
deployment never spends the deployer's credit on visitors.
"""

import base64
import hashlib
import html
import json
import os
from dataclasses import replace

import pandas as pd
import streamlit as st

from maritime_optimizer import (
    FuelType, MaritimeGraph, compare_canal_routes, ensure_geodata, has_geodata,
    load_ports_from_csv, load_vessels_from_csv, optimize_voyage,
)
from maritime_optimizer.compliance import (
    fueleu_in_scope, voyage_fueleu_penalty_eur,
)
from maritime_optimizer.routing import is_canal_node, is_waypoint_node
from maritime_optimizer.scenarios import build_request
from maritime_optimizer.viz.figures import (
    apply_theme, cost_breakdown, cost_components, eur, hbar, mean_speed, num, route_map,
)
from maritime_optimizer.viz.html import DISCLAIMER
from maritime_optimizer.viz.labels import fuel_label, vessel_label, vessel_type_label
from maritime_optimizer.viz.theme import COST_SLOTS, NAVY, ROUTE_SLOTS, STATUS

try:  # the AI features need the 'agents' extra
    import anthropic

    from maritime_optimizer.agents._claude import AgentRefusal
    from maritime_optimizer.agents.assistant import ask
    from maritime_optimizer.agents.report import write_report
    from maritime_optimizer.agents.tools import summarize
    AGENTS = True
except ImportError:
    AGENTS = False

# Approximate list price of claude-opus-5-5, USD per million tokens (for the cost hint only).
PRICE_IN, PRICE_OUT = 4.0, 20.0

P = NAVY
BLUE = P["series"][0]
# Two route colours, used by the map, its legend, the itinerary and the headline: the optimal
# route is blue, the alternative purple. Cost terms use other slots (see viz.theme), so no colour
# means two things.
OPT_COLOR = P["series"][ROUTE_SLOTS["optimal"]]
ALT_COLOR = P["series"][ROUTE_SLOTS["alternative"]]

# Status → (colour, icon, label): colour never alone, always with an icon and a word.
STATUS_STYLE = {"compliant": (STATUS["good"], "✓", "Compliant"),
                "attention": (STATUS["warning"], "!", "Needs attention"),
                "non_compliant": (STATUS["critical"], "✕", "Non-compliant"),
                "info": (P["muted"], "i", "Information")}

# A ship on a dotted route between two ports (inline SVG, no image file to host).
LOGO_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" fill="none">'
            '<path d="M7 15Q20 4 33 15" stroke="#fff" stroke-opacity=".7" stroke-width="1.8" '
            'stroke-dasharray="2 3" stroke-linecap="round"/><circle cx="7" cy="15" r="2.2" fill="#fff"/>'
            '<circle cx="33" cy="15" r="2.2" fill="#fff"/>'
            '<path d="M8.5 25.5H31.5L28 32H12Z" fill="#fff"/>'
            '<rect x="13" y="20.5" width="12" height="5" fill="#fff"/>'
            '<rect x="18.5" y="16.5" width="5" height="4" fill="#fff"/></svg>')
LOGO = ('<img alt="Maritime Optimizer" width="28" height="28" src="data:image/svg+xml;base64,'
        + base64.b64encode(LOGO_SVG.encode()).decode() + '">')
LINKEDIN_URL = "https://www.linkedin.com/alejandrovinuesacano/"

st.set_page_config(page_title="Maritime optimizer", page_icon="⚓", layout="wide",
                   initial_sidebar_state="auto")   # auto: collapsed on phones so results show first

# st.html (not st.markdown): blank lines inside a <style> would end a Markdown HTML block.
st.html(f"""<style>
:root{{--mo-page:{P["page"]};--mo-surface:{P["surface"]};--mo-border:{P["border"]};--mo-ink:{P["ink"]};
--mo-ink2:{P["ink2"]};--mo-muted:{P["muted"]};--mo-blue:{BLUE};--mo-opt:{OPT_COLOR};--mo-alt:{ALT_COLOR}}}
</style>""" + """<style>
[data-testid="stHeader"]{background:transparent}
/* Streamlit chrome visitors should not see. stToolbar stays: it holds the sidebar toggle. */
[data-testid="stMainMenu"],[data-testid="stAppDeployButton"],
[data-testid="stDecoration"],[data-testid="stStatusWidget"],#MainMenu,footer{display:none!important}
.block-container{padding-top:1.8rem;max-width:1280px}
h1,h2,h3,h4{letter-spacing:-.01em}

/* sidebar */
.mo-brand{display:flex;gap:12px;align-items:center;margin:4px 0 18px}
.mo-brand .mark{width:40px;height:40px;border-radius:9px;display:grid;place-items:center;font-weight:700;
  font-size:14px;color:#fff;background:linear-gradient(135deg,var(--mo-blue),#1f5fae);letter-spacing:.02em}
.mo-brand b{display:block;font-size:16px;color:var(--mo-ink);font-weight:600}
.mo-brand small{color:var(--mo-ink2);font-size:12.5px}
.mo-side{font-size:12px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--mo-ink2);
  margin:18px 0 6px;padding-top:14px;border-top:1px solid var(--mo-border)}

/* header */
.mo-hero{position:relative;overflow:hidden;padding:22px 26px 20px;border-radius:12px;margin-bottom:18px;
  background:linear-gradient(180deg,#13223d,var(--mo-surface));border:1px solid var(--mo-border)}
.mo-hero::before{content:"";position:absolute;inset:0 0 auto 0;height:3px;
  background:linear-gradient(90deg,var(--mo-blue),transparent 70%)}
.mo-eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--mo-blue);font-weight:600}
.mo-route{font-size:32px;font-weight:600;line-height:1.2;margin:6px 0 18px;color:var(--mo-ink)}
.mo-route .arrow{color:var(--mo-muted);padding:0 10px;font-weight:400}
.mo-specs{display:flex;flex-wrap:wrap;gap:0}
.mo-spec{padding:0 16px 0 0;margin:0 16px 6px 0;border-right:1px solid var(--mo-border)}
.mo-spec:last-child{border-right:0}
.mo-spec span{display:block;font-size:12px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--mo-ink2)}
.mo-spec b{font-size:14px;font-weight:500;color:var(--mo-ink)}

/* headline: the result, before the supporting numbers */
.mo-headline{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px;padding:18px 24px 20px;
  border-radius:12px;margin-bottom:16px;border:1px solid var(--mo-border);
  border-left:5px solid var(--mo-route,var(--mo-opt));
  background:linear-gradient(90deg,color-mix(in srgb,var(--mo-route,var(--mo-opt)) 16%,var(--mo-surface)),
  var(--mo-surface) 75%)}
.mo-headline .lead{flex:0 0 100%;font-size:12px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--mo-ink2);font-weight:600}
.mo-headline .big{font-size:clamp(1.3rem,2.6vw,1.9rem);font-weight:700;color:var(--mo-ink);line-height:1.25}
.mo-headline .big small{font-size:.6em;font-weight:500;color:var(--mo-ink2);margin-left:6px}
.mo-headline .sep{color:var(--mo-muted);font-size:1.3rem}
.mo-headline .meta{font-size:1.05rem;color:var(--mo-ink2)}
.mo-headline .chip{display:inline-flex;align-items:center;gap:9px}
.mo-headline .chip i{width:12px;height:12px;border-radius:3px;background:var(--mo-route,var(--mo-opt))}

/* KPI cards */
[data-testid="stMetric"],.mo-kpi{min-height:124px;background:var(--mo-surface);
  border:1px solid var(--mo-border);border-radius:10px;
  padding:14px 16px}
.mo-kpi{height:100%;min-height:124px;box-sizing:border-box;padding:14px 12px}
.mo-kpi .lbl{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--mo-ink2);
  margin-bottom:9px;white-space:nowrap}
.mo-kpi .sub{font-size:12.5px;color:var(--mo-ink2);margin-top:8px;white-space:nowrap}
.mo-badge{display:inline-flex;align-items:center;gap:7px;padding:4px 11px 4px 5px;border-radius:999px;
  font-weight:600;font-size:12.5px;line-height:1.25;border:1px solid;
  color:color-mix(in srgb,var(--c) 55%,#fff)!important}
.mo-badge i{width:18px;height:18px;border-radius:50%;display:inline-grid;place-items:center;color:#0b1424;
  font-style:normal;font-size:11px;font-weight:800}
[data-testid="stMetricLabel"] p{white-space:nowrap;font-size:12px;letter-spacing:.08em;
  text-transform:uppercase;
  color:var(--mo-ink2)}
[data-testid="stMetric"] [data-testid="stIconMaterial"]{color:var(--mo-blue)}
[data-testid="stMetricValue"]{font-size:clamp(1rem,1.3vw,1.35rem);font-weight:600;color:var(--mo-ink)}

/* itinerary: a timeline, stops on a line, distance and speed above each stretch */
.mo-tl{display:flex;align-items:flex-start;padding:26px 4px 4px;margin-bottom:6px}
.mo-tl-stop{flex:0 0 16px;width:16px;display:flex;flex-direction:column;align-items:center}
.mo-tl-stop.first{align-items:flex-start}
.mo-tl-stop.last{align-items:flex-end}
.mo-tl-stop .dot{width:16px;height:16px;border-radius:50%;box-sizing:border-box;flex:none;
  border:3px solid var(--mo-route,var(--mo-opt));background:var(--mo-surface)}
.mo-tl-stop.first .dot,.mo-tl-stop.last .dot{background:var(--mo-route,var(--mo-opt))}
.mo-tl-stop.canal .dot{border-style:dashed}
.mo-tl-stop .lab{margin-top:9px;white-space:nowrap;text-align:center;line-height:1.3}
.mo-tl-stop.first .lab{text-align:left}
.mo-tl-stop.last .lab{text-align:right}
.mo-tl-stop .lab b{display:block;font-size:14.5px;font-weight:600;color:var(--mo-ink)}
.mo-tl-stop .lab small{font-size:12px;letter-spacing:.04em;color:var(--mo-ink2)}
.mo-tl-seg{flex:1 1 0;min-width:70px;height:16px;position:relative}
.mo-tl-seg::before{content:"";position:absolute;left:0;right:0;top:6.5px;height:3px;border-radius:2px;
  background:var(--mo-route,var(--mo-opt))}
.mo-tl-seg span{position:absolute;bottom:100%;left:0;right:0;margin-bottom:4px;text-align:center;
  font-size:12.5px;color:var(--mo-ink2);white-space:nowrap}

/* cards and lists */
.mo-card-title{font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;
  color:var(--mo-ink2);margin-bottom:2px}
.mo-costs{display:grid;gap:6px;margin-top:2px}
.mo-cost{display:grid;grid-template-columns:10px 1fr auto 46px;align-items:center;gap:12px;font-size:13.5px;
  padding:8px 12px;border-radius:8px;background:var(--mo-surface);border:1px solid var(--mo-border)}
.mo-cost .sw{width:10px;height:10px;border-radius:2px}
.mo-cost b{font-weight:600}
.mo-cost small{color:var(--mo-muted);text-align:right}
.mo-status{border-radius:10px;padding:14px 16px;border:1px solid var(--mo-border);height:100%;
  background:var(--mo-surface)}
.mo-status .head{display:flex;align-items:center;gap:9px;font-weight:600}
.mo-status .dot{width:20px;height:20px;border-radius:50%;display:inline-grid;place-items:center;
  color:#fff;font-size:12px;font-weight:700}
.mo-status .lbl{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--mo-ink2);
  margin:4px 0 8px 29px}
.mo-status p{font-size:13px;margin:0;color:var(--mo-ink2)}
.mo-empty{padding:26px 28px;border:1px solid var(--mo-border);border-radius:12px;background:var(--mo-surface);
  border-left:3px solid var(--mo-blue)}
.mo-empty b{display:block;font-size:16px;font-weight:600;margin-bottom:6px;color:var(--mo-ink)}
.mo-empty p{margin:0;color:var(--mo-ink2);font-size:13.5px}
.mo-empty a{color:#6aa8f0}
.mo-foot{font-size:12.5px;color:var(--mo-ink2);margin-top:32px;border-top:1px solid var(--mo-border);
  padding-top:14px;line-height:1.6}
.mo-foot a{color:#8dbcf5;text-decoration:underline;text-underline-offset:2px}
.mo-by{margin-top:8px;color:var(--mo-ink2)}
.mo-foot b{color:var(--mo-ink)}

/* the two AI tabs stand out: accent colour and an AI pill */
[data-testid="stTab"]{display:flex;align-items:center}
[data-testid="stTab"]:not(:first-child),
[data-testid="stTab"]:not(:first-child) p{color:#8dbcf5;font-weight:600}
[data-testid="stTab"]:not(:first-child)::after{content:"AI";margin-left:8px;
  font-size:10.5px;font-weight:700;letter-spacing:.04em;padding:1px 7px;border-radius:999px;
  background:var(--mo-blue);color:#06101f}
.mo-cta{display:flex;gap:16px;align-items:center;margin-top:14px;padding:16px 20px;border-radius:12px;
  border:1px solid var(--mo-border);border-left:4px solid var(--mo-blue);
  background:linear-gradient(90deg,#15284a,var(--mo-surface) 80%)}
.mo-cta b{display:block;font-size:15px;color:var(--mo-ink);margin-bottom:2px}
.mo-cta span{font-size:13.5px;color:var(--mo-ink2)}

@media (max-width:700px){
  .block-container{padding:3.2rem 1rem 2rem}
  .mo-hero{padding:16px 16px 12px}
  .mo-route{font-size:24px;margin-bottom:12px}
  .mo-spec{padding-right:14px;margin-right:14px}
  .mo-headline{padding:14px 16px}
  .mo-headline .sep{display:none}
  /* KPI cards two per row instead of six stacked full-width */
  [data-testid="stHorizontalBlock"]:has(.mo-kpi){flex-wrap:wrap!important;gap:.6rem!important}
  [data-testid="stHorizontalBlock"]:has(.mo-kpi)>div{flex:1 1 calc(50% - .6rem)!important;
    min-width:calc(50% - .6rem)!important;width:calc(50% - .6rem)!important}
  [data-testid="stMetric"],.mo-kpi{min-height:100px}
  .mo-tl{flex-direction:column;align-items:stretch;padding-top:6px}
  .mo-tl-stop,.mo-tl-stop.first,.mo-tl-stop.last{flex:none;width:auto;flex-direction:row;align-items:center;gap:12px}
  .mo-tl-stop .lab,.mo-tl-stop.first .lab,.mo-tl-stop.last .lab{margin-top:0;text-align:left}
  .mo-tl-seg{flex:none;height:auto;min-height:46px;margin-left:6.5px;
    border-left:3px solid var(--mo-route,var(--mo-opt));
    padding-left:22px;display:flex;align-items:center}
  .mo-tl-seg::before{display:none}
  .mo-tl-seg span{position:static;margin:0}
}
</style>""")


@st.cache_resource(show_spinner="Building the maritime graph…")
def get_graph() -> MaritimeGraph:
    if not has_geodata():
        ensure_geodata(verbose=False)
    return MaritimeGraph()


def on_phone() -> bool:
    """Phone browsers get a taller, tighter map and room for a wrapped chart legend."""
    agent = st.context.headers.get("User-Agent", "")
    return any(k in agent for k in ("Mobile", "Android", "iPhone"))


def chart(fig) -> None:
    # Plotly's fixed 8 px margins suit the static pages; here let axes grow to fit their labels.
    fig.update_xaxes(automargin=True)
    fig.update_yaxes(automargin=True)
    # No wheel zoom: scrolling the page over the map must scroll the page.
    st.plotly_chart(apply_theme(fig, "dark", palette=NAVY), width="stretch", theme=None,
                    config={"scrollZoom": False, "displaylogo": False})


def card_title(text: str) -> None:
    st.markdown(f'<div class="mo-card-title">{html.escape(text)}</div>', unsafe_allow_html=True)


def stretches(graph: MaritimeGraph, result):
    """The route as named stretches (port or canal to port or canal); open-sea waypoints folded in."""
    out, start, nm, hours, co2 = [], result.legs[0].origin, 0.0, 0.0, 0.0
    for leg in result.legs:
        nm += leg.distance_nm
        hours += leg.distance_nm / leg.speed_knots
        co2 += leg.co2_combustion_tons
        if is_waypoint_node(leg.destination) and leg is not result.legs[-1]:
            continue
        out.append(dict(origin=start, destination=leg.destination, nm=nm, knots=nm / hours, co2=co2))
        start, nm, hours, co2 = leg.destination, 0.0, 0.0, 0.0
    return out


def api_key() -> str:
    """The user's key from this session; the server's key only with an explicit opt-in."""
    key = st.session_state.get("anthropic_key", "").strip()
    if not key and os.environ.get("MARITIME_OPT_ALLOW_SERVER_KEY") == "1":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return key


def claude_client():
    # Pass the key explicitly: with api_key=None the SDK would fall back to the environment.
    return anthropic.Anthropic(api_key=api_key())


def run_agent(fn, *args, **kwargs):
    """Run an agent call and turn API problems into a message instead of a traceback."""
    try:
        return fn(*args, **kwargs)
    except anthropic.AuthenticationError:
        st.error("The API key was rejected. Check it in the sidebar.")
    except anthropic.PermissionDeniedError:
        st.error("This API key has no access to the model. Check your Anthropic workspace.")
    except anthropic.RateLimitError:
        st.error("Rate limit or spend limit reached on your Anthropic account. Try again later.")
    except AgentRefusal as exc:
        st.error(f"The model declined the request: {exc}")
    except anthropic.APIConnectionError:
        st.error("Could not reach the Anthropic API. Check your connection.")
    except anthropic.APIStatusError as exc:
        st.error(f"Anthropic API error ({exc.status_code}). Try again later.")
    return None


def cost_hint(tokens_in: int, tokens_out: int) -> str:
    usd = tokens_in / 1e6 * PRICE_IN + tokens_out / 1e6 * PRICE_OUT
    return f"{num(tokens_in)} input / {num(tokens_out)} output tokens · ≈ ${usd:.3f} on your account"


def itinerary_html(graph: MaritimeGraph, result, color: str) -> str:
    """Ports and canals of the route as a timeline: stops on a line, distance and speed per stretch.

    ``color`` is the route's colour on the map, so the line and the map read as the same route.
    """
    esc = html.escape
    first = result.legs[0].origin
    last = result.legs[-1].destination

    def stop(code: str, kind: str) -> str:
        sub = "CANAL / STRAIT" if kind == "canal" else code
        return (f'<div class="mo-tl-stop {kind}"><i class="dot"></i><div class="lab">'
                f'<b>{esc(graph.ports[code].name)}</b><small>{sub}</small></div></div>')

    parts = [stop(first, "first")]
    for seg in stretches(graph, result):
        parts.append(f'<div class="mo-tl-seg"><span>{num(seg["nm"])} NM · {seg["knots"]:.1f} kn</span></div>')
        end = seg["destination"]
        parts.append(stop(end, "last" if end == last else "canal" if is_canal_node(end) else ""))
    return f'<div class="mo-tl" style="--mo-route:{color}">{"".join(parts)}</div>'


def badge(status: str, text: str) -> str:
    """Status pill from STATUS_STYLE: colour + icon + words, never colour alone."""
    color, icon, _ = STATUS_STYLE[status]
    return (f'<span class="mo-badge" style="--c:{color};background:{color}1f;border-color:{color}66">'
            f'<i style="background:{color};color:{"#fff" if status == "non_compliant" else "#0b1424"}">'
            f'{icon}</i>{html.escape(text)}</span>')


def status_card(label: str, badge_html: str, sub: str, tip: str = "") -> str:
    return (f'<div class="mo-kpi" title="{html.escape(tip)}">'
            f'<div class="lbl">{html.escape(label)}</div>{badge_html}'
            f'<div class="sub">{html.escape(sub)}</div></div>')


def headline_html(best_name: str, best, other_name: str | None, other, color: str) -> str:
    """The result in one line: which route, what it saves, what it costs in time."""
    chip = f'<span class="big chip"><i></i>{html.escape(best_name)}</span>'
    if other is None:
        parts = [f'<span class="big">{eur(best.total_cost_eur)}</span>',
                 f'<span class="meta">{mean_speed(best):.1f} kn mean speed</span>',
                 f'<span class="meta">{best.total_transit_hours / 24:.1f} days</span>']
    else:
        saving = other.total_cost_eur - best.total_cost_eur
        pct = saving / other.total_cost_eur * 100 if other.total_cost_eur else 0
        days = (best.total_transit_hours - other.total_transit_hours) / 24
        vs = html.escape(other_name.removeprefix("Via "))
        parts = [f'<span class="big">saves {eur(saving)}<small>−{pct:.0f} %</small></span>'
                 if saving > 0.5 else '<span class="big">same cost</span>',
                 f'<span class="meta">{"+" if days > 0 else "−"}{num(abs(days), 1)} days vs {vs}</span>'
                 if abs(days) >= 0.05 else '<span class="meta">same transit time</span>']
    sep = '<span class="sep">·</span>'
    return (f'<div class="mo-headline" style="--mo-route:{color}"><div class="lead">Optimal route</div>'
            f'{chip}{sep}{sep.join(parts)}</div>')


graph = get_graph()
phone = on_phone()
fleet = load_vessels_from_csv()
ports = {p.code: p for p in load_ports_from_csv().values()}
port_label = {code: f"{p.name} ({code})" for code, p in sorted(ports.items(), key=lambda kv: kv[1].name)}
codes = list(port_label)
# Shareable scenarios: ?origin=ESVLC&destination=SGSIN&vessel=panamax_bulker&fuel=LNG&hours=2000&compare=1
# preset the inputs on the first run of a session (invalid values fall back to the defaults).
QUERY = st.query_params


def preset(name: str, valid, default):
    value = QUERY.get(name)
    return value if value in valid else default


st.session_state.setdefault("origin", preset("origin", codes, "ESVLC"))
st.session_state.setdefault("destination", preset("destination", codes, "NLRTM"))


def swap_ports() -> None:
    s = st.session_state
    s["origin"], s["destination"] = s["destination"], s["origin"]


def side_section(title: str) -> None:
    st.markdown(f'<div class="mo-side">{html.escape(title)}</div>', unsafe_allow_html=True)


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.html(f'<div class="mo-brand"><span class="mark">{LOGO}</span><div><b>Maritime Optimizer</b>'
            '<small>EU ETS · FuelEU Maritime · CII</small></div></div>')
    side_section("Voyage")
    origin = st.selectbox("Origin", codes, key="origin", format_func=port_label.get)
    destination = st.selectbox("Destination", codes, key="destination", format_func=port_label.get)
    st.button("Swap origin and destination", icon=":material/swap_vert:", on_click=swap_ports,
              width="stretch")
    hours = QUERY.get("hours", "")
    preset_hours = int(hours) if hours.isdigit() and 24 <= int(hours) <= 5_000 else 1_500
    # Entered in days (how a voyage deadline is thought of); the optimizer works in hours.
    days_max = st.number_input("Maximum transit time (days)", 1.0, 5_000 / 24, preset_hours / 24, 0.5,
                               format="%.1f")
    tmax = round(days_max * 24)
    st.caption(f"= {num(tmax)} h")
    side_section("Vessel")
    vessels = list(fleet)
    vessel_name = st.selectbox("Vessel", vessels, index=vessels.index(preset("vessel", vessels, vessels[0])),
                               format_func=vessel_label)
    vessel = fleet[vessel_name]
    fuels = ["HFO", "MGO", "LNG", "BioLNG", "HVO", "E-Methanol"]
    fuel = st.selectbox("Fuel", fuels, index=fuels.index(preset("fuel", fuels, vessel.fuel_type.value)),
                        format_func=fuel_label)
    corridors = st.toggle("Compare Suez vs Cape of Good Hope", value=QUERY.get("compare") == "1")
    with st.expander("Market, contract and regulation", icon=":material/tune:"):
        eua = st.slider("EUA price (EUR/t CO₂e)", 0, 200, 65, 5)
        tce = st.number_input("Time charter equivalent (EUR/day)", 1_000, 200_000,
                              int(vessel.tce_eur_day), 500)
        cargo = st.number_input("Cargo (t)", 1_000, 400_000, int(min(vessel.dwt * 0.85, 400_000)), 1_000)
        year = st.selectbox("Year", list(range(2026, 2036)), index=0)
        epl = st.number_input("EEXI speed cap from the engine power limitation (kn, 0 = none)",
                              0.0, float(vessel.design_speed), 0.0, 0.5)
        penalty = st.toggle("Price the FuelEU penalty into the optimum", value=False)

    side_section("Claude · your API key")
    if AGENTS:
        st.text_input("Anthropic API key", type="password", key="anthropic_key",
                      placeholder="sk-ant-…",
                      help="Used only for the AI report and the agent chat, billed to your account. "
                           "Kept in this browser session's memory; never stored, logged or shared.")
        if st.session_state.get("anthropic_key"):
            st.success("Key loaded for this session.", icon=":material/key:")
            # A callback runs before the widgets are rebuilt, so it may clear the key field.
            st.button("Forget key", icon=":material/logout:", width="stretch",
                      on_click=st.session_state.update, kwargs={"anthropic_key": ""})
        st.caption("Only enter your key in an instance you trust, or run the app locally.")
    else:
        st.info("Install the agents extra to enable the AI report and chat: "
                "`pip install 'maritime-optimizer[agents]'`")

# ------------------------------------------------------------------ header
specs = [("Vessel", vessel_label(vessel_name)), ("Type", vessel_type_label(vessel.vessel_type.value)),
         ("Deadweight", f"{num(vessel.dwt)} t"), ("Fuel", fuel_label(fuel)),
         ("Deadline", f"{num(tmax / 24, 1)} days ({num(tmax)} h)"),
         ("EUA price", f"{eua} EUR/t"), ("Year", str(year))]
st.markdown(
    f'<div class="mo-hero"><div class="mo-eyebrow">Optimized voyage</div>'
    f'<div class="mo-route">{html.escape(ports[origin].name)}<span class="arrow">→</span>'
    f'{html.escape(ports[destination].name)}</div><div class="mo-specs">'
    + "".join(f'<div class="mo-spec"><span>{k}</span><b>{html.escape(v)}</b></div>' for k, v in specs)
    + "</div></div>", unsafe_allow_html=True)

if origin == destination:
    st.warning("Origin and destination are the same port.", icon=":material/warning:")
    st.stop()

vessel = replace(vessel, fuel_type=FuelType(fuel))
request = build_request(vessel, origin_port=origin, destination_port=destination, year=year,
                        eua_price=float(eua), cargo_tons=float(cargo), max_transit_hours=float(tmax),
                        time_charter_rate=float(tce), include_fueleu_penalty=penalty,
                        epl_max_speed_knots=float(epl) if epl else None)

with st.spinner("Optimizing route and speed…"):
    if corridors:
        found = {k: r for k, r in compare_canal_routes(graph, request).items() if r is not None}
        names = {"suez": "Via Suez", "cape": "Via the Cape"}
        routes = {names[k]: r for k, r in found.items()}
    else:
        result = optimize_voyage(graph, request)
        routes = {"Optimal route": result} if result else {}

if not routes:
    st.error("No feasible route: relax the maximum transit time or check the CII rating.",
             icon=":material/block:")
    st.stop()

best_name, best = min(routes.items(), key=lambda kv: kv[1].total_cost_eur)
other_name, other = next(((n, r) for n, r in routes.items() if n != best_name), (None, None))
# Colour follows the role (optimal or alternative), the label says which corridor it is.
route_slots = {n: ROUTE_SLOTS["optimal" if n == best_name else "alternative"] for n in routes}
st.markdown(headline_html(best_name, best, other_name, other, OPT_COLOR), unsafe_allow_html=True)

# A new voyage invalidates the previous AI report (the chat keeps its history).
voyage_id = hashlib.sha256(json.dumps(
    [origin, destination, tmax, vessel_name, fuel, eua, tce, cargo, year, epl, penalty, corridors]
).encode()).hexdigest()[:12]
if st.session_state.get("report_voyage") != voyage_id:
    st.session_state.pop("report", None)

tab_voyage, tab_report, tab_chat = st.tabs(
    [":material/map: Voyage", ":material/description: AI report", ":material/forum: Ask the agent"])

# ------------------------------------------------------------------ voyage tab
with tab_voyage:
    cols = st.columns([1.1, 1, 1.1, 1, 1.1, 1.3])   # the status cards need more room
    kpi = dict(height="stretch")
    cols[0].metric("Cost", eur(best.total_cost_eur), icon=":material/euro:", **kpi)
    cols[1].metric("Speed", f"{mean_speed(best):.2f} kn", icon=":material/speed:", **kpi)
    cols[2].metric("Transit", f"{best.total_transit_hours / 24:.1f} days",
                   f"{num(tmax - best.total_transit_hours)} h spare", delta_color="off", delta_arrow="off",
                   icon=":material/schedule:", **kpi)
    cols[3].metric("CO₂", f"{num(best.total_co2_combustion_tons)} t", icon=":material/co2:", **kpi)
    cii = best.cii_rating.value
    cii_status = "compliant" if best.cii_compliant else "non_compliant" if cii == "E" else "attention"
    cii_text = f"{cii} · " + ("compliant" if best.cii_compliant else "below target")
    cols[4].markdown(status_card("CII rating", badge(cii_status, cii_text),
                                 "Target: C or better"), unsafe_allow_html=True)
    in_scope = fueleu_in_scope(best)
    gap = best.fueleu_intensity - best.fueleu_max_allowed
    if in_scope:
        fe = (badge("compliant", f"Compliant {gap:+.2f} g/MJ".replace("-", "−")) if best.fueleu_compliant
              else badge("attention", f"Deficit {gap:+.2f} g/MJ"))
        cols[5].markdown(status_card("FuelEU", fe, "GHG vs the limit",
                                     "Well-to-wake GHG intensity minus the FuelEU limit for the selected "
                                     "year, in gCO₂e/MJ (positive = above the limit)."),
                         unsafe_allow_html=True)
    else:
        cols[5].markdown(status_card("FuelEU", badge("info", "Not applicable"), "Outside EU scope",
                                     "FuelEU Maritime applies only to voyages with an EU/EEA port."),
                         unsafe_allow_html=True)
    if best.cii_speed_cap_factor < 1:
        st.info(f"CII retry: speed capped at {best.cii_speed_cap_factor:.0%} of the maximum "
                "to reach rating C.", icon=":material/slow_motion_video:")
    if best.eexi_binding:
        st.info(f"EEXI: the engine power limitation caps the speed at "
                f"{best.epl_max_speed_knots:.1f} kn.", icon=":material/settings:")
    if in_scope and not best.fueleu_compliant:
        priced = best.total_fueleu_penalty_eur > 0
        fine = best.total_fueleu_penalty_eur if priced else voyage_fueleu_penalty_eur(best)
        st.warning(f"FuelEU deficit: estimated penalty **{eur(fine)}** "
                   f"({'included in' if priced else 'not in'} the voyage cost). Pooling, banking and "
                   "borrowing are not modelled and could avoid it.", icon=":material/gavel:")

    with st.container(border=True):
        card_title("Route" + (f" · cheapest: {best_name}" if len(routes) > 1 else ""))
        st.markdown(itinerary_html(graph, best, P["series"][route_slots[best_name]]), unsafe_allow_html=True)
        map_labels = {n: f"<b>{n}</b><br>{num(r.total_distance_nm)} NM"
                      + (f" · saves {eur(other.total_cost_eur - r.total_cost_eur)}"
                         if other is not None and n == best_name and other.total_cost_eur > r.total_cost_eur
                         else "") for n, r in routes.items()}
        fig = route_map(graph, routes, route_slots, height=330 if phone else 480, primary=best_name,
                        labels=None if phone else map_labels,   # no room for text on a phone
                        aspect=1.1 if phone else 2.6)
        fig.update_layout(paper_bgcolor=P["ocean"])  # sea to the card edges, no strip beside the map
        chart(fig)
    left, right = st.columns(2)
    with left, st.container(border=True, height="stretch"):
        card_title("Where the money goes")
        chart(cost_breakdown(list(routes.items()), narrow=phone))
        parts = {k: v for k, v in cost_components(best).items() if v}
        total = sum(parts.values())
        rows_html = "".join(
            f'<div class="mo-cost"><span class="sw" style="background:{P["series"][COST_SLOTS[k]]}">'
            f'</span><span>{html.escape(k)}</span><b>{eur(v)}</b><small>{v / total:.0%}</small></div>'
            for k, v in parts.items())
        st.markdown(f'<div class="mo-costs">{rows_html}</div>', unsafe_allow_html=True)
    with right, st.container(border=True, height="stretch"):
        card_title("CO₂ by stretch")
        segs = stretches(graph, best)
        names = [f"{graph.ports[s['origin']].name} → {graph.ports[s['destination']].name}" for s in segs]
        chart(hbar(names, [s["co2"] for s in segs], "t", ",.0f"))
    legs = [f"{graph.ports[leg.origin].name} → {graph.ports[leg.destination].name}" for leg in best.legs]
    with st.expander("Leg-by-leg data", icon=":material/table_rows:"):
        st.dataframe(pd.DataFrame([{
            "Leg": name, "Distance NM": round(leg.distance_nm, 1), "Speed kn": round(leg.speed_knots, 2),
            "Fuel t": round(leg.fuel_consumed_tons, 2), "CO₂ t": round(leg.co2_combustion_tons, 2),
            "Fuel EUR": round(leg.fuel_cost_eur), "ETS EUR": round(leg.ets_cost_eur),
        } for name, leg in zip(legs, best.legs, strict=True)]), hide_index=True, width="stretch")
    st.markdown('<div class="mo-cta"><div><b>Need this as a regulatory brief?</b><span>Open the '
                '<b style="display:inline">AI report</b> tab: Claude writes a compliance summary of this '
                'voyage (EU ETS, FuelEU, CII, ISO 14083) and every figure in it is checked against the '
                'optimizer.'
                '</span></div></div>', unsafe_allow_html=True)

NO_KEY = "Enter your Anthropic API key in the sidebar"
KEY_HELP = ("Create one at <a href='https://console.anthropic.com/settings/keys' target='_blank'>"
            "console.anthropic.com</a>. Calls are billed to your account; the key never leaves this "
            "session.")


def empty_state(title: str, body: str) -> None:
    st.markdown(f'<div class="mo-empty"><b>{title}</b><p>{body}</p></div>', unsafe_allow_html=True)


# ------------------------------------------------------------------ AI report tab
with tab_report:
    st.markdown("#### Compliance report")
    st.caption("The optimizer computes every figure; Claude writes the narrative and an automatic check "
               "flags any number that does not match. One request, billed to your key.")
    if not AGENTS:
        st.info("Install the agents extra to enable this tab.")
    elif not api_key():
        st.info(f"{NO_KEY} to generate a report.", icon=":material/key:")
        empty_state("A regulatory brief of this voyage",
                    "Headline and executive summary · status for EU ETS, FuelEU Maritime, CII and "
                    "ISO 14083 · findings and recommendations · automatic check of every figure · "
                    "HTML download.<br><br>" + KEY_HELP)
    elif "report" not in st.session_state:
        empty_state("No report yet for this voyage",
                    "Claude reads the optimizer output and writes a regulatory summary.")
    if AGENTS and st.button("Generate compliance report", type="primary", icon=":material/auto_awesome:",
                            disabled=not api_key(),
                            help=None if api_key() else f"{NO_KEY} first.") and api_key():
        rows = list(routes.items())
        with st.spinner("Claude is writing the report…"):
            report = run_agent(write_report, f"{ports[origin].name} → {ports[destination].name}",
                               rows, client=claude_client())
        if report:
            st.session_state.update(report=(report, rows), report_voyage=voyage_id)
            st.rerun()

    if "report" in st.session_state:
        report, rows = st.session_state["report"]
        n = report.narrative
        st.warning("**AI-generated content.** Written by an AI model from the optimizer's output. The "
                   "figures are computed by the optimizer and checked automatically, but the wording, "
                   "interpretations and recommendations may contain errors. Not legal, regulatory or "
                   "commercial advice.", icon=":material/smart_toy:")
        st.markdown(f"### {n.headline}")
        st.write(n.executive_summary)
        if report.unverified_numbers:
            st.error("Figures in the narrative that do not match the optimizer output: "
                     + ", ".join(report.unverified_numbers) + ". Trust the Voyage tab.")
        status_cols = st.columns(max(len(n.regulatory_status), 1))
        for col, s in zip(status_cols, n.regulatory_status, strict=False):
            color, icon, label = STATUS_STYLE[s.status]
            col.markdown(f'<div class="mo-status"><div class="head"><span class="dot" '
                         f'style="background:{color}">{icon}</span>{html.escape(s.regime)}</div>'
                         f'<div class="lbl">{label}</div><p>{html.escape(s.explanation)}</p></div>',
                         unsafe_allow_html=True)
        st.write("")
        left, right = st.columns(2)
        with left, st.container(border=True):
            card_title("Findings")
            for f in n.findings:
                st.markdown(f"- **{f.title}.** {f.detail}")
        with right, st.container(border=True):
            card_title("Recommendations")
            for r in n.recommendations:
                st.markdown(f"- {r}")
        with st.expander("Caveats", icon=":material/info:"):
            for c in n.caveats:
                st.markdown(f"- {c}")
        st.caption(cost_hint(report.input_tokens, report.output_tokens))
        st.download_button("Download report (HTML)", report.to_html(graph, rows), icon=":material/download:",
                           file_name=f"report_{origin}_{destination}.html", mime="text/html")

# ------------------------------------------------------------------ agent chat tab
SUGGESTIONS = ["Why is this the optimal speed?", "Suez or the Cape for this voyage?",
               "What if the EUA price rises to 120 EUR/t?", "Cheapest FuelEU-compliant fuel?"]
AVATAR = {"assistant": ":material/sailing:", "user": ":material/person:"}

with tab_chat:
    st.markdown("#### Ask the agent")
    st.caption("Claude answers with the optimizer's tools and knows the voyage on screen. "
               "Each question is a request billed to your key.")
    history = st.session_state.setdefault("chat", [])
    ready = AGENTS and bool(api_key())
    pending = None
    if not ready:
        st.info(f"{NO_KEY} to chat with the agent.", icon=":material/key:")
        empty_state("Ask anything about this voyage",
                    "Why this speed? · Suez or the Cape? · What if the EUA price doubles? · "
                    "Which fuel passes FuelEU? The agent re-runs the optimizer to answer.<br><br>"
                    + KEY_HELP)
    elif not history:
        st.caption("Suggested questions")
        for col, text in zip(st.columns(len(SUGGESTIONS)), SUGGESTIONS, strict=True):
            if col.button(text, width="stretch"):
                pending = text
    for turn in history:
        with st.chat_message(turn["role"], avatar=AVATAR[turn["role"]]):
            st.markdown(turn["content"])
            if turn.get("meta"):
                st.caption(turn["meta"])
    if history and st.button("Clear conversation", icon=":material/delete:"):
        st.session_state["chat"] = []
        st.rerun()

    typed = st.chat_input("Ask about this voyage, e.g. what if the EUA price rises to 120 EUR/t?"
                          if ready else "Enter your API key in the sidebar to chat", disabled=not ready)
    question = typed or pending
    if question and ready:
        with st.chat_message("user", avatar=AVATAR["user"]):
            st.markdown(question)
        context = json.dumps({"inputs": {"origin": origin, "destination": destination, "vessel": vessel_name,
                                         "fuel": fuel, "max_transit_hours": tmax, "eua_price": eua,
                                         "year": year, "epl_max_speed_knots": epl or None},
                              "results": {name: summarize(r) for name, r in routes.items()}},
                             ensure_ascii=False)
        with st.chat_message("assistant", avatar=AVATAR["assistant"]):
            with st.spinner("Thinking…"):
                answer = run_agent(ask, question, client=claude_client(),
                                   history=[{"role": t["role"], "content": t["content"]} for t in history],
                                   context=context)
            if answer:
                st.markdown(answer.text)
                meta = (f"Tools: {', '.join(answer.tool_calls) or 'none'} · "
                        + cost_hint(answer.input_tokens, answer.output_tokens))
                st.caption(meta)
                history += [{"role": "user", "content": question},
                            {"role": "assistant", "content": answer.text, "meta": meta}]

linkedin = (f' · <a href="{LINKEDIN_URL}" target="_blank" rel="noopener">LinkedIn</a>' if LINKEDIN_URL
            else "")
st.markdown(f'<div class="mo-foot"><b>Data notice.</b> {html.escape(DISCLAIMER)}'
            '<div class="mo-by">Built by Alejandro Viñuesa · <a href="https://github.com/alejandrovinuesa" '
            f'target="_blank" rel="noopener">GitHub</a>{linkedin}</div></div>', unsafe_allow_html=True)
