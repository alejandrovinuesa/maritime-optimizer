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
import math
import os
from dataclasses import replace
from pathlib import Path

import networkx as nx
import pandas as pd
import streamlit as st

from maritime_optimizer import (
    FuelType, MaritimeGraph, compare_canal_routes, ensure_geodata, has_geodata,
    load_ports_from_csv, load_vessels_from_csv, optimize_voyage,
)
from maritime_optimizer.compliance import (
    fueleu_in_scope, voyage_fueleu_penalty_eur,
)
from maritime_optimizer.optimizer import CAPE_CORRIDOR
from maritime_optimizer.routing import is_canal_node, is_waypoint_node
from maritime_optimizer.scenarios import build_request
from maritime_optimizer.viz.figures import (
    apply_theme, cost_breakdown, cost_components, eur, hbar, mean_speed, num, route_map,
)
from maritime_optimizer.viz.html import DISCLAIMER
from maritime_optimizer.viz.labels import fuel_label, vessel_label
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
LOGO = ('<img alt="Maritime Optimizer" width="26" height="26" src="data:image/svg+xml;base64,'
        + base64.b64encode(LOGO_SVG.encode()).decode() + '">')
LINKEDIN_URL = "https://www.linkedin.com/in/alejandrovinuesacano/"

st.set_page_config(page_title="Maritime optimizer", page_icon="⚓", layout="wide",
                   initial_sidebar_state="auto")   # auto: collapsed on phones so results show first

# Design tokens come from viz.theme; the rules live in app/style.css.
# st.html (not st.markdown): blank lines inside a <style> would end a Markdown HTML block.
st.html(f"""<style>
:root{{--mo-page:{P["page"]};--mo-surface:{P["surface"]};--mo-border:{P["border"]};--mo-ink:{P["ink"]};
--mo-ink2:{P["ink2"]};--mo-muted:{P["muted"]};--mo-blue:{BLUE};--mo-opt:{OPT_COLOR};--mo-alt:{ALT_COLOR}}}
{Path(__file__).with_name("style.css").read_text(encoding="utf-8")}</style>""")


@st.cache_resource(show_spinner="Building the maritime graph…")
def get_graph() -> MaritimeGraph:
    if not has_geodata():
        ensure_geodata(verbose=False)
    return MaritimeGraph()


CHART_FONT = '"IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif'
FASTEST = "Fastest feasible route"
DAYS_LIMIT = 5_000 / 24


@st.cache_data(show_spinner=False)
def corridor_choice(origin: str, destination: str) -> bool:
    """True when the voyage has a Suez-or-Cape choice: its shortest path uses one of the two."""
    try:
        path = nx.shortest_path(get_graph().graph, origin, destination, weight="distance_nm")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return False
    return bool(({"SUEZ"} | CAPE_CORRIDOR) & set(path))


def cheapest_voyage(graph: MaritimeGraph, request):
    """The cheapest feasible voyage, or None.

    The plain search follows the cheapest path and a few close variants; when the deadline rules
    those out (the Cape, say) it can miss a dearer but quicker corridor, so each one is tried too.
    """
    found = optimize_voyage(graph, request)
    if found is None:
        options = [r for r in compare_canal_routes(graph, request).values() if r is not None]
        found = min(options, key=lambda r: r.total_cost_eur, default=None)
    return found


def fastest_voyage(graph: MaritimeGraph, request):
    """The voyage for the tightest deadline that still has a route (found by bisection), or None.

    Used when the requested deadline is out of reach: the vessel sails as fast as its top speed
    and the CII rating allow, whatever it costs.
    """
    best = cheapest_voyage(graph, replace(request, max_transit_hours=1e7))
    if best is None:
        return None
    low, high = request.max_transit_hours, best.total_transit_hours
    while high - low > 1:
        mid = (low + high) / 2
        found = cheapest_voyage(graph, replace(request, max_transit_hours=mid))
        if found is None:
            low = mid
        else:
            best, high = found, min(mid, found.total_transit_hours)
    return best


def position(lat: float, lon: float) -> str:
    """Latitude and longitude as on a chart: 39°27′N 000°20′W."""
    def dm(x: float, width: int) -> str:
        minutes = round(abs(x) * 60)
        return f"{minutes // 60:0{width}d}°{minutes % 60:02d}′"
    return f"{dm(lat, 2)}{'N' if lat >= 0 else 'S'} {dm(lon, 3)}{'E' if lon >= 0 else 'W'}"


def on_phone() -> bool:
    """Phone browsers get a taller, tighter map and room for a wrapped chart legend."""
    agent = st.context.headers.get("User-Agent", "")
    return any(k in agent for k in ("Mobile", "Android", "iPhone"))


def chart(fig) -> None:
    # Plotly's fixed 8 px margins suit the static pages; here let axes grow to fit their labels.
    fig.update_xaxes(automargin=True)
    fig.update_yaxes(automargin=True)
    fig.update_layout(font_family=CHART_FONT, hoverlabel_font_family=CHART_FONT)
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


def tile(label: str, body: str, sub: str = "", gauge: tuple[float, str] | None = None, tip: str = "") -> str:
    """One tile of the instrument row: label, figure or badge, an optional gauge, a line of context."""
    bar = ""
    if gauge:
        share, color = gauge
        bar = (f'<div class="gauge"><i style="width:{min(max(share, 0), 1):.1%};background:{color}">'
               '</i></div>')
    return (f'<div class="mo-kpi" title="{html.escape(tip)}"><div class="lbl">{html.escape(label)}</div>'
            f'{body}{bar}<div class="sub">{sub}</div></div>')


def figure(text: str) -> str:
    """'1.32M EUR' as a large figure with a small unit."""
    value, _, unit = text.rpartition(" ")
    return f'<div class="val">{html.escape(value)}<small>{html.escape(unit)}</small></div>'


def headline_html(title: str, best, other_name: str | None, other, color: str, tmax: float,
                  via: str = "", why: str = "") -> str:
    """The result in three cells: which route, what it costs or saves, how long it takes.

    The distance is on the header track, so the route cell says how the voyage goes instead.
    ``why`` is a line under the cells for a result that needs explaining.
    """
    esc = html.escape
    days = best.total_transit_hours / 24
    spare = tmax - best.total_transit_hours
    # A phone has no room for the distance on the header track: it leads this line there instead.
    nm = f'<span class="nm">{num(best.total_distance_nm)} NM · </span>'
    if other is None:
        legs = via.count(" · ") + 2 if via else 1
        route = (f'<div class="big">{f"{legs} legs" if via else "Direct"}</div>'
                 f'<div class="meta">{nm}{esc(via) or "No port or canal on the way"}</div>')
        cost = (f'<div class="big">{eur(best.total_cost_eur)}</div>'
                f'<div class="meta">{mean_speed(best):.1f} kn mean speed</div>')
        slack = (f'<span class="late">{num(-spare)} h over the deadline</span>' if spare < -0.5
                 else "Deadline binding" if spare < 0.5 else f"{num(spare)} h inside the deadline")
        time = f'<div class="big">{days:.1f} days</div><div class="meta">{slack}</div>'
    else:
        saving = other.total_cost_eur - best.total_cost_eur
        pct = saving / other.total_cost_eur * 100 if other.total_cost_eur else 0
        extra = (best.total_transit_hours - other.total_transit_hours) / 24
        vs = esc(other_name.removeprefix("Via "))
        route = (f'<div class="big name"><i></i>{esc(title)}</div>'
                 f'<div class="meta">{nm}{esc(via) or "Direct"}</div>')
        title = "Optimal route"
        cost = (f'<div class="big">saves {eur(saving)}</div>'
                f'<div class="meta">−{pct:.0f} % vs {vs} ({eur(other.total_cost_eur)})</div>'
                if saving > 0.5 else f'<div class="big">same cost</div><div class="meta">as {vs}</div>')
        time = (f'<div class="big">{"+" if extra > 0 else "−"}{num(abs(extra), 1)} days</div>'
                if abs(extra) >= 0.05 else '<div class="big">same time</div>')
        time += f'<div class="meta">{days:.1f} days vs {other.total_transit_hours / 24:.1f} via {vs}</div>'
    return (f'<div class="mo-headline" style="--mo-route:{color}">'
            f'<div><div class="lead">{esc(title)}</div>{route}</div>'
            f'<div><div class="lead">Cost</div>{cost}</div>'
            f'<div><div class="lead">Time</div>{time}</div>'
            + (f'<div class="why">{esc(why)}</div>' if why else "") + "</div>")


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
            '<small>EU ETS · FuelEU · CII</small></div></div>')
    side_section("Voyage")
    origin = st.selectbox("Origin", codes, key="origin", format_func=port_label.get)
    destination = st.selectbox("Destination", codes, key="destination", format_func=port_label.get)
    st.button("Swap ports", icon=":material/swap_vert:", on_click=swap_ports, width="stretch")
    hours = QUERY.get("hours", "")
    preset_hours = int(hours) if hours.isdigit() and 24 <= int(hours) <= 5_000 else 1_500
    st.session_state.setdefault("days", preset_hours / 24)
    if "raise_days" in st.session_state:      # set below when the deadline was out of reach
        st.session_state["days"] = st.session_state.pop("raise_days")
    # Entered in days (how a voyage deadline is thought of); the optimizer works in hours.
    days_max = st.number_input("Maximum transit time (days)", 1.0, DAYS_LIMIT, step=0.5, format="%.1f",
                               key="days")
    tmax = round(days_max * 24)
    side_section("Vessel")
    vessels = list(fleet)
    vessel_name = st.selectbox("Vessel", vessels, index=vessels.index(preset("vessel", vessels, vessels[0])),
                               format_func=vessel_label)
    vessel = fleet[vessel_name]
    fuels = ["HFO", "MGO", "LNG", "BioLNG", "HVO", "E-Methanol"]
    fuel = st.selectbox("Fuel", fuels, index=fuels.index(preset("fuel", fuels, vessel.fuel_type.value)),
                        format_func=fuel_label)
    side_section("Carbon price")
    # One EU ETS allowance: the price of emitting a tonne of CO₂e.
    eua = st.slider("EU Allowance (EUA) price · EUR/t", 0, 200, 65, 5)
    # Offered only where the choice exists: the shortest path uses Suez or rounds the Cape.
    choice = corridor_choice(origin, destination)
    corridors = st.toggle("Compare Suez vs the Cape", disabled=not choice,
                          value=choice and QUERY.get("compare") == "1",
                          help=None if choice else "This voyage goes through neither Suez nor "
                                                   "the Cape of Good Hope.") and choice
    with st.expander("Charter, cargo, year and EEXI", icon=":material/tune:"):
        tce = st.number_input("Charter rate (EUR/day)", 1_000, 200_000, int(vessel.tce_eur_day), 500,
                              help="Time charter equivalent: what a day of the vessel is worth.")
        cargo = st.number_input("Cargo (t)", 1_000, 400_000, int(min(vessel.dwt * 0.85, 400_000)), 1_000)
        year = st.selectbox("Year", list(range(2026, 2036)), index=0)
        epl = st.number_input("EEXI speed cap (kn)", 0.0, float(vessel.design_speed), 0.0, 0.5,
                              help="Top speed under the engine power limitation. 0 = no limitation.")
        penalty = st.toggle("Price in the FuelEU penalty", value=False,
                            help="Add the FuelEU deficit penalty to the cost the optimizer minimises.")

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
specs = [("Vessel", vessel_label(vessel_name)), ("Deadweight", f"{num(vessel.dwt)} t"),
         ("Fuel", fuel_label(fuel)), ("Deadline", f"{num(tmax / 24, 1)} days ({num(tmax)} h)"),
         ("EUA price", f"{eua} EUR/t"), ("Year", str(year))]


def port_html(code: str, cls: str = "") -> str:
    p = ports[code]
    return (f'<div class="mo-port {cls}"><b>{html.escape(p.name)}</b><small>{code}'
            f'<span class="co"> · {position(p.lat, p.lon)}</span></small></div>')


def hero(note: str = "") -> None:
    """The passage, port to port; ``note`` (the distance) sits on the track between them."""
    track = f'<div class="mo-track">{f"<span>{html.escape(note)}</span>" if note else ""}</div>'
    st.markdown(
        f'<div class="mo-hero"><div class="mo-passage">{port_html(origin)}{track}'
        f'{port_html(destination, "to")}</div><div class="mo-specs">'
        + "".join(f'<div class="mo-spec k-{k.lower().replace(" ", "")}"><span>{k}</span>'
                  f'<b>{html.escape(v)}</b></div>' for k, v in specs)
        + "</div></div>", unsafe_allow_html=True)


if origin == destination:
    hero()
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
        result = cheapest_voyage(graph, request)
        routes = {"Optimal route": result} if result else {}

# Asked to compare, but only one corridor makes the deadline: say so, or the headline just switches.
why = ""
if corridors and len(routes) == 1:
    lost = next(n for k, n in names.items() if k not in found).removeprefix("Via ")
    why = (f"{lost[0].upper()}{lost[1:]} is not feasible within the {tmax / 24:g}-day deadline: "
           f"only {next(iter(routes)).removeprefix('Via ')} makes it.")
late = False
if not routes:
    with st.spinner("Looking for the fastest voyage…"):
        fastest = fastest_voyage(graph, request)
    if fastest is None:
        hero()
        st.error("No feasible route between these ports for this vessel and CII target.",
                 icon=":material/block:")
        st.stop()
    floor = math.ceil(fastest.total_transit_hours) + 1
    top = mean_speed(fastest)
    if tmax < floor <= 5_000:
        # The deadline asks for more speed than the vessel may sail: raise it to the limit and rerun.
        limit = ("its design speed" if top >= vessel.design_speed - 0.05
                 else "the EEXI speed cap" if epl and top >= epl - 0.05
                 else "the fastest that keeps the CII rating at C")
        st.session_state["raise_days"] = floor / 24
        st.session_state["raised"] = (
            f"**Deadline raised from {num(tmax / 24, 1)} to {floor / 24:.1f} days.** "
            f"Anything shorter needs more than {top:.1f} kn, {limit}.")
        st.rerun()
    # Could not be raised (already at the limit): show the fastest voyage, flagged as late.
    routes, late = {FASTEST: fastest}, True
    st.warning(f"**No route meets the {num(tmax / 24, 1)}-day deadline.** The quickest this vessel can "
               f"make the voyage is **{fastest.total_transit_hours / 24:.1f} days** at {top:.1f} kn, "
               "shown below.", icon=":material/timer_off:")

best_name, best = min(routes.items(), key=lambda kv: kv[1].total_cost_eur)
other_name, other = next(((n, r) for n, r in routes.items() if n != best_name), (None, None))
# Colour follows the role (optimal or alternative), the label says which corridor it is.
route_slots = {n: ROUTE_SLOTS["optimal" if n == best_name else "alternative"] for n in routes}
hero(f"{num(best.total_distance_nm)} NM")
if "raised" in st.session_state:
    st.info(st.session_state.pop("raised"), icon=":material/speed:")
via = " · ".join(graph.ports[c].name for c in best.route_ports[1:-1] if not is_waypoint_node(c))
st.markdown(headline_html(best_name, best, other_name, other, OPT_COLOR, tmax,
                          f"via {via}" if via else "", why), unsafe_allow_html=True)

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
    spare = tmax - best.total_transit_hours
    binding = spare < 0.5          # the deadline, not cost, is what sets the speed
    speed, nm = mean_speed(best), best.total_distance_nm
    amber = STATUS["warning"]
    tiles = [
        tile("Cost", figure(eur(best.total_cost_eur)), f"{num(best.total_cost_eur / nm)} EUR per NM"),
        tile("Speed", figure(f"{speed:.2f} kn"), f"{speed / vessel.design_speed:.0%} of "
             f"{vessel.design_speed:.1f} kn design", gauge=(speed / vessel.design_speed, BLUE),
             tip="Distance-weighted mean sailing speed against the vessel's design speed."),
        tile("Transit", figure(f"{best.total_transit_hours / 24:.1f} days"),
             f'<b style="color:{STATUS["critical"]}">{num(-spare)} h late</b>' if late
             else f'<b style="color:{amber}">Deadline binding</b>' if binding else f"{num(spare)} h spare",
             gauge=(best.total_transit_hours / tmax,
                    STATUS["critical"] if late else amber if binding else BLUE),
             tip="Transit time against the maximum transit time."),
        tile("CO₂", figure(f"{num(best.total_co2_combustion_tons)} t"),
             f"{num(best.total_co2_combustion_tons * 1000 / nm)} kg per NM"),
    ]
    cii = best.cii_rating.value
    cii_status = "compliant" if best.cii_compliant else "non_compliant" if cii == "E" else "attention"
    cii_text = f"{cii} · " + ("compliant" if best.cii_compliant else "below target")
    tiles.append(tile("CII rating", badge(cii_status, cii_text), "Target: C or better"))
    in_scope = fueleu_in_scope(best)
    gap = best.fueleu_intensity - best.fueleu_max_allowed
    if in_scope:
        fe = (badge("compliant", f"Compliant {gap:+.2f} g/MJ".replace("-", "−")) if best.fueleu_compliant
              else badge("attention", f"Deficit {gap:+.2f} g/MJ"))
        tiles.append(tile("FuelEU", fe, "GHG vs the limit",
                          tip="Well-to-wake GHG intensity minus the FuelEU limit for the selected "
                              "year, in gCO₂e/MJ (positive = above the limit)."))
    else:
        tiles.append(tile("FuelEU", badge("info", "Not applicable"), "Outside EU scope",
                          tip="FuelEU Maritime applies only to voyages with an EU/EEA port."))
    st.markdown(f'<div class="mo-kpis">{"".join(tiles)}</div>', unsafe_allow_html=True)
    if best.cii_speed_cap_factor < 1:
        st.info(f"CII retry: speed capped at {best.cii_speed_cap_factor:.0%} of the maximum "
                "to reach rating C.", icon=":material/slow_motion_video:")
    if best.eexi_binding:
        st.info(f"EEXI: the engine power limitation caps the speed at "
                f"{best.epl_max_speed_knots:.1f} kn.", icon=":material/settings:")
    if in_scope and not best.fueleu_compliant:
        priced = best.total_fueleu_penalty_eur > 0
        fine = best.total_fueleu_penalty_eur if priced else voyage_fueleu_penalty_eur(best)
        # One line: the FuelEU tile already says "Deficit", this only adds what it costs.
        st.markdown(f'<div class="mo-note"><b>FuelEU deficit</b> · estimated penalty <b>{eur(fine)}</b>, '
                    f"{'included in' if priced else 'not in'} the voyage cost. Pooling, banking and "
                    "borrowing are not modelled and could avoid it.</div>", unsafe_allow_html=True)

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
        # A faint graticule every 10°, as on a chart.
        grid = dict(showgrid=True, gridcolor=P["grid"], gridwidth=0.6, dtick=10)
        fig.update_geos(lonaxis=grid, lataxis=grid)
        chart(fig)
    left, right = st.columns(2)
    with left, st.container(border=True, height="stretch"):
        card_title("Where the money goes")
        # Room to breathe: the legend as its own row across the card (Plotly's wraps inside the
        # narrow plot area), taller rows, a clear axis.
        used = [k for k in COST_SLOTS if any(cost_components(r)[k] for r in routes.values())]
        st.markdown('<div class="mo-legend">' + "".join(
            f'<span><i style="background:{P["series"][COST_SLOTS[k]]}"></i>{html.escape(k)}</span>'
            for k in used) + "</div>", unsafe_allow_html=True)
        fig = cost_breakdown(list(routes.items()), narrow=phone)
        row = 58
        fig.update_layout(height=46 + len(routes) * row, bargap=1 - 24 / row, showlegend=False,
                          margin=dict(l=8, r=96, t=10, b=10))
        chart(fig)
        parts = {k: v for k, v in cost_components(best).items() if v}
        total = sum(parts.values())
        rows_html = "".join(
            f'<div class="mo-cost"><span class="sw" style="background:{P["series"][COST_SLOTS[k]]}">'
            f'</span><span>{html.escape(k)}</span><b>{eur(v)}</b><small>{v / total:.0%}</small></div>'
            for k, v in parts.items())
        rows_html += (f'<div class="mo-cost total"><span></span><span>Total'
                      f'{" · " + html.escape(best_name) if len(routes) > 1 else ""}</span>'
                      f'<b>{eur(total)}</b><small></small></div>')
        st.markdown(f'<div class="mo-costs">{rows_html}</div>', unsafe_allow_html=True)
    with right, st.container(border=True):
        card_title("CO₂ by stretch")
        segs = stretches(graph, best)
        names = [f"{graph.ports[s['origin']].name} → {graph.ports[s['destination']].name}" for s in segs]
        chart(hbar(names, [s["co2"] for s in segs], "t", ",.0f", x_title="t CO₂"))
    legs = [f"{graph.ports[leg.origin].name} → {graph.ports[leg.destination].name}" for leg in best.legs]
    with st.expander("Leg-by-leg data", icon=":material/table_rows:"):
        table = pd.DataFrame([{
            "Leg": name, "Distance NM": leg.distance_nm, "Speed kn": leg.speed_knots,
            "Fuel t": leg.fuel_consumed_tons, "CO₂ t": leg.co2_combustion_tons,
            "Fuel EUR": leg.fuel_cost_eur, "ETS EUR": leg.ets_cost_eur,
        } for name, leg in zip(legs, best.legs, strict=True)])
        formats = {"Distance NM": 0, "Speed kn": 2, "Fuel t": 1, "CO₂ t": 1, "Fuel EUR": 0, "ETS EUR": 0}
        st.dataframe(table.style.format({c: (lambda x, d=d: num(x, d)) for c, d in formats.items()}),
                     hide_index=True, width="stretch")
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
        for col, text in zip(st.columns(2) * 2, SUGGESTIONS, strict=True):   # two per row: no cut labels
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
