"""Interactive dashboard: pick a vessel, a voyage and a carbon price, see the optimum.

Run locally:   pip install -e ".[app]"   then   streamlit run app/streamlit_app.py
"""

from dataclasses import replace

import pandas as pd
import streamlit as st

from maritime_optimizer import (
    FuelType, MaritimeGraph, compare_canal_routes, ensure_geodata, has_geodata,
    load_ports_from_csv, load_vessels_from_csv, optimize_voyage,
)
from maritime_optimizer.scenarios import build_request
from maritime_optimizer.viz.html import DISCLAIMER
from maritime_optimizer.viz.figures import cost_breakdown, eur, hbar, mean_speed, route_map
from maritime_optimizer.viz.theme import CORRIDOR_SLOTS

st.set_page_config(page_title="Maritime optimizer", layout="wide")


@st.cache_resource(show_spinner="Building the maritime graph…")
def get_graph() -> MaritimeGraph:
    if not has_geodata():
        ensure_geodata(verbose=False)
    return MaritimeGraph()


graph = get_graph()
fleet = load_vessels_from_csv()
ports = {p.code: p for p in load_ports_from_csv().values()}
port_label = {code: f"{p.name} ({code})" for code, p in sorted(ports.items(), key=lambda kv: kv[1].name)}

# ------------------------------------------------------------------ inputs (one row of filters)
with st.sidebar:
    st.header("Voyage")
    vessel_name = st.selectbox("Vessel", list(fleet), index=0)
    vessel = fleet[vessel_name]
    fuels = ["HFO", "MGO", "LNG", "BioLNG", "HVO", "E-Methanol"]
    fuel = st.selectbox("Fuel", fuels, index=fuels.index(vessel.fuel_type.value))
    codes = list(port_label)
    origin = st.selectbox("Origin", codes, index=codes.index("ESVLC"), format_func=port_label.get)
    destination = st.selectbox("Destination", codes, index=codes.index("NLRTM"), format_func=port_label.get)
    st.header("Market and contract")
    eua = st.slider("EUA price (EUR/t CO₂e)", 0, 200, 65, 5)
    tce = st.number_input("Time charter equivalent (EUR/day)", 1_000, 200_000, int(vessel.tce_eur_day), 500)
    cargo = st.number_input("Cargo (t)", 1_000, 400_000, int(min(vessel.dwt * 0.85, 400_000)), 1_000)
    tmax = st.number_input("Maximum transit time (h)", 24, 5_000, 1_500, 24)
    year = st.selectbox("Year", list(range(2026, 2036)), index=0)
    epl = st.number_input("EEXI speed cap from the engine power limitation (kn, 0 = none)",
                          0.0, float(vessel.design_speed), 0.0, 0.5)
    penalty = st.toggle("Price the FuelEU penalty into the optimum", value=False)
    corridors = st.toggle("Compare Suez vs Cape of Good Hope", value=False)

st.title(f"{ports[origin].name} → {ports[destination].name}")
st.caption(f"{vessel_name} · {vessel.vessel_type.value} · {vessel.dwt:,.0f} DWT · {fuel}")

if origin == destination:
    st.warning("Origin and destination are the same port.")
    st.stop()

vessel = replace(vessel, fuel_type=FuelType(fuel))
request = build_request(vessel, origin_port=origin, destination_port=destination, year=year,
                        eua_price=float(eua), cargo_tons=float(cargo), max_transit_hours=float(tmax),
                        time_charter_rate=float(tce), include_fueleu_penalty=penalty,
                        epl_max_speed_knots=float(epl) if epl else None)

if corridors:
    found = {k: r for k, r in compare_canal_routes(graph, request).items() if r is not None}
    names = {"suez": "Via Suez", "cape": "Via the Cape"}
    routes = {names[k]: r for k, r in found.items()}
    slots = {names[k]: CORRIDOR_SLOTS[k] for k in found}
else:
    result = optimize_voyage(graph, request)
    routes = {"Optimal route": result} if result else {}
    slots = None

if not routes:
    st.error("No feasible route: relax the maximum transit time or check the CII rating.")
    st.stop()

best_name, best = min(routes.items(), key=lambda kv: kv[1].total_cost_eur)

# ------------------------------------------------------------------ KPI row
cols = st.columns(6)
cols[0].metric("Total voyage cost", eur(best.total_cost_eur))
cols[1].metric("Optimal speed", f"{mean_speed(best):.2f} kn")
cols[2].metric("Transit", f"{best.total_transit_hours / 24:.1f} days")
cols[3].metric("CO₂ emitted", f"{best.total_co2_combustion_tons:,.0f} t")
cols[4].metric("CII rating", f"{'✓' if best.cii_compliant else '!'} {best.cii_rating.value}")
cols[5].metric("FuelEU 2026+", "✓ compliant" if best.fueleu_compliant else "! deficit",
               f"{best.fueleu_intensity - best.fueleu_max_allowed:+.2f} gCO₂e/MJ vs limit",
               delta_color="inverse")
if corridors and len(routes) == 2:
    other = next(r for n, r in routes.items() if n != best_name)
    st.info(f"**{best_name}** is cheaper by {eur(other.total_cost_eur - best.total_cost_eur)}.")
if best.cii_speed_cap_factor < 1:
    st.info(f"CII retry: speed capped at {best.cii_speed_cap_factor:.0%} of the maximum to reach rating C.")

# ------------------------------------------------------------------ charts
st.plotly_chart(route_map(graph, routes, slots, height=460), use_container_width=True, theme=None)
left, right = st.columns(2)
with left:
    st.subheader("Where the money goes")
    st.plotly_chart(cost_breakdown(list(routes.items())), use_container_width=True, theme=None)
with right:
    st.subheader("CO₂ by leg")
    legs = [f"{graph.ports[leg.origin].name} → {graph.ports[leg.destination].name}" for leg in best.legs]
    st.plotly_chart(hbar(legs, [leg.co2_combustion_tons for leg in best.legs], "t", ",.0f"),
                    use_container_width=True, theme=None)

with st.expander("Leg-by-leg data"):
    st.dataframe(pd.DataFrame([{
        "Leg": name, "Distance NM": round(leg.distance_nm, 1), "Speed kn": round(leg.speed_knots, 2),
        "Fuel t": round(leg.fuel_consumed_tons, 2), "CO₂ t": round(leg.co2_combustion_tons, 2),
        "Fuel EUR": round(leg.fuel_cost_eur), "ETS EUR": round(leg.ets_cost_eur),
    } for name, leg in zip(legs, best.legs, strict=True)]), hide_index=True, use_container_width=True)

st.caption(f"**Data notice.** {DISCLAIMER}")
