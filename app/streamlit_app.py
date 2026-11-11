"""Interactive dashboard: pick two ports, a vessel and a deadline, see the optimized voyage on a map,
then ask Claude for a compliance report or questions, with your own Anthropic API key.

Run locally:   pip install -e ".[app,agents]"   then   streamlit run app/streamlit_app.py

API key policy: the AI features use only the key the user types in the sidebar. It is kept in this
browser session's memory, never written to disk, logged or put in a URL. A server-side
ANTHROPIC_API_KEY is ignored unless MARITIME_OPT_ALLOW_SERVER_KEY=1 is set, so a public
deployment never spends the deployer's credit on visitors.
"""

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
    apply_theme, cost_breakdown, cost_components, eur, hbar, mean_speed, route_map,
)
from maritime_optimizer.viz.html import DISCLAIMER
from maritime_optimizer.viz.theme import COST_SLOTS, NAVY, STATUS

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
BLUE, ORANGE = P["series"][0], P["series"][1]
# Routes are drawn in orange. In the corridor comparison each corridor keeps its own colour
# (Suez orange, Cape blue) whichever is cheaper: colour follows the entity, never the rank.
ROUTE_SLOTS = {"Optimal route": 1, "Via Suez": 1, "Via the Cape": 0}

st.set_page_config(page_title="Maritime optimizer", page_icon="⚓", layout="wide",
                   initial_sidebar_state="expanded")

# st.html (not st.markdown): blank lines inside a <style> would end a Markdown HTML block.
st.html(f"""<style>
:root{{--mo-page:{P["page"]};--mo-surface:{P["surface"]};--mo-border:{P["border"]};--mo-ink:{P["ink"]};
--mo-ink2:{P["ink2"]};--mo-muted:{P["muted"]};--mo-blue:{BLUE};--mo-orange:{ORANGE}}}
</style>""" + """<style>
[data-testid="stHeader"]{background:transparent}
[data-testid="stMainMenu"]{display:none}
.block-container{padding-top:1.8rem;max-width:1280px}
h1,h2,h3,h4{letter-spacing:-.01em}

/* sidebar */
.mo-brand{display:flex;gap:12px;align-items:center;margin:4px 0 18px}
.mo-brand .mark{width:38px;height:38px;border-radius:9px;display:grid;place-items:center;font-weight:700;
  font-size:14px;color:#fff;background:linear-gradient(135deg,var(--mo-blue),#1f5fae);letter-spacing:.02em}
.mo-brand b{display:block;font-size:16px;color:var(--mo-ink);font-weight:600}
.mo-brand small{color:var(--mo-muted);font-size:12px}
.mo-side{font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--mo-muted);
  margin:18px 0 6px;padding-top:14px;border-top:1px solid var(--mo-border)}

/* header */
.mo-hero{position:relative;overflow:hidden;padding:22px 26px 20px;border-radius:12px;margin-bottom:18px;
  background:linear-gradient(180deg,#13223d,var(--mo-surface));border:1px solid var(--mo-border)}
.mo-hero::before{content:"";position:absolute;inset:0 0 auto 0;height:3px;
  background:linear-gradient(90deg,var(--mo-blue),var(--mo-blue) 55%,var(--mo-orange))}
.mo-eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--mo-blue);font-weight:600}
.mo-route{font-size:32px;font-weight:600;line-height:1.2;margin:6px 0 18px;color:var(--mo-ink)}
.mo-route .arrow{color:var(--mo-orange);padding:0 10px;font-weight:400}
.mo-specs{display:flex;flex-wrap:wrap;gap:0}
.mo-spec{padding:0 22px 0 0;margin:0 22px 6px 0;border-right:1px solid var(--mo-border)}
.mo-spec:last-child{border-right:0}
.mo-spec span{display:block;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--mo-muted)}
.mo-spec b{font-size:14px;font-weight:500;color:var(--mo-ink)}

/* KPI cards */
[data-testid="stMetric"]{background:var(--mo-surface);border:1px solid var(--mo-border);border-radius:10px;
  padding:14px 16px}
[data-testid="stMetricLabel"] p{font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--mo-muted)}
[data-testid="stMetric"] [data-testid="stIconMaterial"]{color:var(--mo-blue)}
[data-testid="stMetricValue"]{font-size:clamp(1.05rem,1.55vw,1.5rem);font-weight:600;color:var(--mo-ink)}

/* itinerary */
.mo-itin{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:16px 0 10px}
.mo-stop{padding:8px 13px;border-radius:8px;border:1px solid var(--mo-border);background:var(--mo-surface);
  line-height:1.2}
.mo-stop b{display:block;font-size:13.5px;font-weight:600;color:var(--mo-ink)}
.mo-stop small{color:var(--mo-muted);font-size:11px;letter-spacing:.04em}
.mo-stop.end{border-color:var(--mo-blue);box-shadow:inset 3px 0 0 var(--mo-blue)}
.mo-stop.canal{border-style:dashed}
.mo-seg{display:flex;flex-direction:column;align-items:center;font-size:11px;color:var(--mo-ink2);
  min-width:78px}
.mo-seg::before{content:"";display:block;width:100%;height:2px;background:var(--mo-route,var(--mo-orange));border-radius:2px;
  margin-bottom:4px}

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
.mo-status .lbl{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mo-muted);
  margin:4px 0 8px 29px}
.mo-status p{font-size:13px;margin:0;color:var(--mo-ink2)}
.mo-empty{padding:26px 28px;border:1px solid var(--mo-border);border-radius:12px;background:var(--mo-surface);
  border-left:3px solid var(--mo-blue)}
.mo-empty b{display:block;font-size:16px;font-weight:600;margin-bottom:6px;color:var(--mo-ink)}
.mo-empty p{margin:0;color:var(--mo-ink2);font-size:13.5px}
.mo-empty a{color:#6aa8f0}
.mo-foot{font-size:11.5px;color:var(--mo-muted);margin-top:32px;border-top:1px solid var(--mo-border);
  padding-top:14px;line-height:1.55}
.mo-foot b{color:var(--mo-ink2)}
</style>""")


@st.cache_resource(show_spinner="Building the maritime graph…")
def get_graph() -> MaritimeGraph:
    if not has_geodata():
        ensure_geodata(verbose=False)
    return MaritimeGraph()


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
    return f"{tokens_in:,} input / {tokens_out:,} output tokens · ≈ ${usd:.3f} on your account"


def itinerary_html(graph: MaritimeGraph, result, color: str) -> str:
    """Ports and canals of the route as linked stops, with distance and mean speed between them.

    ``color`` is the route's colour on the map, so the strip and the line read as the same route.
    """
    esc = html.escape
    first = result.legs[0].origin
    last = result.legs[-1].destination
    parts = [f'<div class="mo-stop end"><b>{esc(graph.ports[first].name)}</b><small>{first}</small></div>']
    for s in stretches(graph, result):
        stop = s["destination"]
        parts.append(f'<div class="mo-seg"><span>{s["nm"]:,.0f} NM</span>'
                     f'<span>{s["knots"]:.1f} kn</span></div>')
        kind = "end" if stop == last else "canal" if is_canal_node(stop) else ""
        parts.append(f'<div class="mo-stop {kind}"><b>{esc(graph.ports[stop].name)}</b>'
                     f'<small>{"CANAL / STRAIT" if kind == "canal" else stop}</small></div>')
    return f'<div class="mo-itin" style="--mo-route:{color}">{"".join(parts)}</div>'


graph = get_graph()
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
    st.markdown('<div class="mo-brand"><span class="mark">MO</span><div><b>Maritime Optimizer</b>'
                '<small>EU ETS · FuelEU Maritime · CII</small></div></div>', unsafe_allow_html=True)
    side_section("Voyage")
    origin = st.selectbox("Origin", codes, key="origin", format_func=port_label.get)
    destination = st.selectbox("Destination", codes, key="destination", format_func=port_label.get)
    st.button("Swap origin and destination", icon=":material/swap_vert:", on_click=swap_ports,
              width="stretch")
    hours = QUERY.get("hours", "")
    tmax = st.number_input("Maximum transit time (h)", 24, 5_000,
                           int(hours) if hours.isdigit() and 24 <= int(hours) <= 5_000 else 1_500, 24)
    side_section("Vessel")
    vessels = list(fleet)
    vessel_name = st.selectbox("Vessel", vessels, index=vessels.index(preset("vessel", vessels, vessels[0])))
    vessel = fleet[vessel_name]
    fuels = ["HFO", "MGO", "LNG", "BioLNG", "HVO", "E-Methanol"]
    fuel = st.selectbox("Fuel", fuels, index=fuels.index(preset("fuel", fuels, vessel.fuel_type.value)))
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
specs = [("Vessel", vessel_name), ("Type", vessel.vessel_type.value.replace("_", " ")),
         ("Deadweight", f"{vessel.dwt:,.0f} t"), ("Fuel", fuel), ("Deadline", f"{tmax:,} h"),
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
    cols = st.columns(6)
    kpi = dict(height="stretch")
    cols[0].metric("Voyage cost", eur(best.total_cost_eur), icon=":material/euro:", **kpi)
    cols[1].metric("Optimal speed", f"{mean_speed(best):.2f} kn", icon=":material/speed:", **kpi)
    cols[2].metric("Transit", f"{best.total_transit_hours / 24:.1f} days",
                   f"{tmax - best.total_transit_hours:,.0f} h spare", delta_color="off", delta_arrow="off",
                   icon=":material/schedule:", **kpi)
    cols[3].metric("CO₂ emitted", f"{best.total_co2_combustion_tons:,.0f} t", icon=":material/co2:", **kpi)
    cols[4].metric("CII rating", f"{'✓' if best.cii_compliant else '!'} {best.cii_rating.value}",
                   icon=":material/eco:", **kpi)
    in_scope = fueleu_in_scope(best)
    gap = best.fueleu_intensity - best.fueleu_max_allowed
    if in_scope:
        cols[5].metric("FuelEU", "✓ compliant" if best.fueleu_compliant else "! deficit",
                       f"{gap:+.2f} g/MJ", delta_color="inverse", icon=":material/local_gas_station:",
                       help="Well-to-wake GHG intensity minus the FuelEU limit for the selected year, in "
                            "gCO₂e/MJ (positive = above the limit).", **kpi)
    else:
        cols[5].metric("FuelEU", "n/a", "outside EU scope", delta_color="off", delta_arrow="off",
                       icon=":material/local_gas_station:",
                       help="FuelEU Maritime applies only to voyages with an EU/EEA port.", **kpi)
    if corridors and len(routes) == 2:
        other = next(r for n, r in routes.items() if n != best_name)
        st.info(f"**{best_name}** is cheaper by {eur(other.total_cost_eur - best.total_cost_eur)}.",
                icon=":material/compare_arrows:")
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

    st.markdown(itinerary_html(graph, best, P["series"][ROUTE_SLOTS[best_name]]), unsafe_allow_html=True)
    with st.container(border=True):
        card_title("Route" + (f" · cheapest: {best_name}" if len(routes) > 1 else ""))
        fig = route_map(graph, routes, {n: ROUTE_SLOTS[n] for n in routes}, height=480)
        fig.update_layout(paper_bgcolor=P["ocean"])  # sea to the card edges, no strip beside the map
        chart(fig)
    left, right = st.columns(2)
    with left, st.container(border=True, height="stretch"):
        card_title("Where the money goes")
        chart(cost_breakdown(list(routes.items())))
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

# Status → (colour, icon, label): colour never alone, always with an icon and a word.
STATUS_STYLE = {"compliant": (STATUS["good"], "✓", "Compliant"),
                "attention": (STATUS["warning"], "!", "Needs attention"),
                "non_compliant": (STATUS["critical"], "✕", "Non-compliant"),
                "info": (P["muted"], "i", "Information")}
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
    if AGENTS and api_key() and st.button("Generate AI report", type="primary",
                                           icon=":material/auto_awesome:"):
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

st.markdown(f'<div class="mo-foot"><b>Data notice.</b> {html.escape(DISCLAIMER)}</div>',
            unsafe_allow_html=True)
