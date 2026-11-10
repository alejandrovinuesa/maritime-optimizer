"""Minimal end-to-end example: optimize one voyage and compare two EUA prices.

Run with:  python examples/quickstart.py
"""

from maritime_optimizer import MaritimeGraph, load_vessels_from_csv, optimize_voyage
from maritime_optimizer.scenarios import build_request

graph = MaritimeGraph()                       # built once, then cached on disk
vessel = load_vessels_from_csv()["panamax_bulker"]

for eua in (65, 150):
    result = optimize_voyage(graph, build_request(
        vessel, origin_port="ESVLC", destination_port="NLRTM",
        eua_price=eua, cargo_tons=68_000, max_transit_hours=400))
    speed = result.legs[0].speed_knots
    print(f"EUA {eua:>3} EUR/t | {' -> '.join(result.route_ports)}")
    print(f"           speed {speed:.2f} kn | fuel {result.total_fuel_tons:.1f} t | "
          f"cost {result.total_cost_eur:,.0f} EUR | CII {result.cii_rating.value} | "
          f"FuelEU {'OK' if result.fueleu_compliant else 'deficit'} | "
          f"ISO 14083 {result.iso14083_intensity:.3f} gCO2e/t-km")
