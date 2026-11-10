"""The five demonstration scenarios, run with the generic example fleet.

They mirror the case studies of the original thesis (same routes and settings),
but use illustrative example vessels instead of real, named ships.

E1  Valencia → Rotterdam        intra-EU voyage (100 % EU ETS), bulk carrier
E2  Rotterdam → New York        extra-EU voyage (50 % EU ETS), container ship
E3  Marseille → Singapore       fuel comparison HFO / MGO / LNG / BioLNG
E4  Hamburg → Houston           EUA carbon price sensitivity (40-150 EUR/t)
E5  Singapore → Rotterdam       Suez Canal vs Cape of Good Hope
"""

from typing import Dict, Optional

from .graph import MaritimeGraph, load_vessels_from_csv
from .models import FuelType, OptimizationRequest, Vessel
from .optimizer import (
    compare_canal_routes, compare_eua_price_scenarios, compare_fuel_scenarios,
    optimize_voyage,
)

SCENARIO_IDS = ("E1", "E2", "E3", "E4", "E5")


def build_request(vessel: Vessel, **kwargs) -> OptimizationRequest:
    """OptimizationRequest that takes the vessel's own TCE unless overridden."""
    kwargs.setdefault("time_charter_rate", vessel.tce_eur_day)
    return OptimizationRequest(vessel=vessel, **kwargs)


def run_scenario(scenario_id: str, graph: Optional[MaritimeGraph] = None,
                 vessels: Optional[Dict[str, Vessel]] = None):
    """Run one scenario. Returns a VoyageResult (E1, E2), a list (E3, E4) or a dict (E5)."""
    graph = graph or MaritimeGraph()
    vessels = vessels or load_vessels_from_csv()
    sid = scenario_id.upper()
    if sid == "E1":
        return optimize_voyage(graph, build_request(
            vessels["panamax_bulker"], origin_port="ESVLC", destination_port="NLRTM",
            eua_price=65.0, cargo_tons=68_000, max_transit_hours=384))
    if sid == "E2":
        return optimize_voyage(graph, build_request(
            vessels["neopanamax_container"], origin_port="NLRTM", destination_port="USNYC",
            eua_price=65.0, cargo_tons=80_000, max_transit_hours=360))
    if sid == "E3":
        return compare_fuel_scenarios(graph, build_request(
            vessels["aframax_lng_tanker"], origin_port="FRMRS", destination_port="SGSIN",
            eua_price=65.0, cargo_tons=75_000, max_transit_hours=1200),
            fuel_types=[FuelType.HFO, FuelType.MGO, FuelType.LNG, FuelType.BioLNG])
    if sid == "E4":
        return compare_eua_price_scenarios(graph, build_request(
            vessels["neopanamax_container"], origin_port="DEHAM", destination_port="USHOU",
            eua_price=65.0, cargo_tons=80_000, max_transit_hours=600),
            prices=[40.0, 65.0, 90.0, 120.0, 150.0])
    if sid == "E5":
        return compare_canal_routes(graph, build_request(
            vessels["lr2_lng_tanker"], origin_port="SGSIN", destination_port="NLRTM",
            eua_price=65.0, cargo_tons=80_000, max_transit_hours=1500))
    raise ValueError(f"Unknown scenario {scenario_id!r}; expected one of {SCENARIO_IDS}")


def run_all(graph: Optional[MaritimeGraph] = None,
            vessels: Optional[Dict[str, Vessel]] = None) -> dict:
    """Run the five scenarios and return {scenario_id: result}."""
    graph = graph or MaritimeGraph()
    vessels = vessels or load_vessels_from_csv()
    return {sid: run_scenario(sid, graph, vessels) for sid in SCENARIO_IDS}
