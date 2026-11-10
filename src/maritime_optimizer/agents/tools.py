"""Deterministic optimizer tools shared by the MCP server and the Claude assistant.

Every number an agent reports comes from these functions, never from the model.
They take plain arguments (port codes, vessel names, prices) and return JSON-ready
dicts, so they can be exposed unchanged as MCP tools or Claude API tools.
"""

from dataclasses import replace
from functools import lru_cache
from typing import Dict, List, Optional

from ..geodata import ensure_geodata, has_geodata
from ..graph import MaritimeGraph, load_ports_from_csv, load_vessels_from_csv
from ..models import FuelType, VoyageResult
from ..optimizer import (
    compare_canal_routes, compare_eua_price_scenarios, compare_fuel_scenarios, optimize_voyage,
)
from ..routing import HAS_GEOPANDAS, describe_route
from ..scenarios import build_request

DISCLAIMER = ("Regulatory parameters follow EU ETS, FuelEU Maritime and MARPOL Annex VI as in force "
              "in 2026; vessel data, prices and tolls are illustrative estimates; calm water. "
              "Not legal or commercial advice.")


@lru_cache(maxsize=1)
def get_graph() -> MaritimeGraph:
    """The maritime graph, built once per process (and cached on disk)."""
    if HAS_GEOPANDAS and not has_geodata():
        ensure_geodata(verbose=False)
    return MaritimeGraph()


def _vessel(name: str):
    fleet = load_vessels_from_csv()
    match = {k.lower(): v for k, v in fleet.items()}.get(name.strip().lower())
    if match is None:
        raise ValueError(f"Unknown vessel {name!r}. Available: {', '.join(fleet)}")
    return match


def _port(code: str) -> str:
    code = code.strip().upper()
    ports = get_graph().ports
    if code not in ports:
        known = sorted(c for c, p in ports.items() if p.country != "(canal/strait)")
        raise ValueError(f"Unknown port {code!r}. Known ports: {', '.join(known)}")
    return code


def _request(origin: str, destination: str, vessel: str, eua_price: float, fuel: Optional[str],
             year: int, cargo_tons: Optional[float], max_transit_hours: float,
             time_charter_eur_day: Optional[float], include_fueleu_penalty: bool):
    v = _vessel(vessel)
    if fuel:
        try:
            v = replace(v, fuel_type=FuelType(fuel))
        except ValueError:
            raise ValueError(f"Unknown fuel {fuel!r}. Options: {[f.value for f in FuelType]}") from None
    kwargs = dict(origin_port=_port(origin), destination_port=_port(destination), year=year,
                  eua_price=eua_price, max_transit_hours=max_transit_hours,
                  cargo_tons=cargo_tons if cargo_tons else round(v.dwt * 0.85),
                  include_fueleu_penalty=include_fueleu_penalty)
    if time_charter_eur_day:
        kwargs["time_charter_rate"] = time_charter_eur_day
    return build_request(v, **kwargs)


def summarize(r: Optional[VoyageResult]) -> Optional[dict]:
    """Compact, JSON-ready summary of a VoyageResult (None if no feasible route)."""
    if r is None:
        return None
    g = get_graph()
    speed = (sum(leg.speed_knots * leg.distance_nm for leg in r.legs) / r.total_distance_nm
             if r.total_distance_nm else 0.0)
    deficit = r.fueleu_intensity - r.fueleu_max_allowed
    return {
        "vessel": r.vessel.name, "fuel": r.vessel.fuel_type.value, "year": r.year,
        "route": describe_route(r.route_ports),
        "route_ports": [g.ports[c].name for c in r.route_ports if not c.startswith("WP_")],
        "distance_nm": round(r.total_distance_nm, 1),
        "transit_days": round(r.total_transit_hours / 24, 2),
        "mean_speed_kn": round(speed, 2),
        "eu_ets_scope": r.geo_type.value,
        "canals_transited": sorted(set(r.canals_transited)),
        "costs_eur": {
            "fuel": round(r.total_fuel_cost_eur), "eu_ets": round(r.total_ets_cost_eur),
            "time_charter": round(r.total_time_cost_eur), "canal_tolls": round(r.total_canal_fees_eur),
            "fueleu_penalty": round(r.total_fueleu_penalty_eur), "total": round(r.total_cost_eur),
        },
        "emissions_t": {
            "fuel": round(r.total_fuel_tons, 1), "co2_physical": round(r.total_co2_combustion_tons, 1),
            "co2_for_eu_ets": round(r.total_co2_tons, 1),
            "ghg_co2e_tank_to_wake": round(r.total_ghg_co2e_tons, 1),
        },
        "cii": {"attained": round(r.cii_attained, 3), "required": round(r.cii_required, 3),
                "rating": r.cii_rating.value, "compliant": r.cii_compliant,
                "speed_cap_factor": r.cii_speed_cap_factor},
        "fueleu": {"intensity_gco2e_mj": round(r.fueleu_intensity, 2),
                   "limit_gco2e_mj": round(r.fueleu_max_allowed, 2), "compliant": r.fueleu_compliant,
                   "deficit_gco2e_mj": round(max(deficit, 0.0), 2)},
        "iso14083_gco2e_per_tkm": round(r.iso14083_intensity, 3),
    }


# What each summary field means, so a model reading the JSON does not have to guess.
DEFINITIONS = {
    "emissions_t.co2_physical": "CO2 actually emitted by combustion on board (tank-to-wake).",
    "emissions_t.co2_for_eu_ets": "CO2 counted for EU ETS: equals co2_physical except for certified "
                                  "biofuels, whose CO2 is zero-rated.",
    "emissions_t.ghg_co2e_tank_to_wake": "co2_for_eu_ets + CH4 x 28 + N2O x 265 (GWP-100), tank-to-wake "
                                         "only: includes engine methane slip, excludes upstream "
                                         "(well-to-tank) emissions. It is the EU ETS base since 2026.",
    "costs_eur.eu_ets": "Allowances for ghg_co2e_tank_to_wake x coverage (100 % intra-EU, 50 % "
                        "extra-EU) x EUA price, less the ice-class deduction if any.",
    "fueleu.intensity_gco2e_mj": "FuelEU Maritime well-to-wake GHG intensity (upstream + combustion "
                                 "+ slip) per MJ, compared with the limit for the year.",
    "iso14083_gco2e_per_tkm": "ISO 14083 well-to-wake intensity per tonne-km, without biofuel "
                              "zero-rating; a reporting metric with no limit.",
    "cii": "MARPOL Annex VI operational carbon intensity of this voyage alone; the legal rating "
           "is annual, per ship.",
    "costs_eur.time_charter": "Opportunity cost of the ship's days at sea (time-charter equivalent).",
}


# ------------------------------------------------------------------ public tools

def list_ports() -> List[dict]:
    """Ports available as origin or destination."""
    return [{"code": p.code, "name": p.name, "country": p.country, "eu_eea": p.is_eu_eea}
            for p in sorted(load_ports_from_csv().values(), key=lambda p: p.code)]


def list_vessels() -> List[dict]:
    """Vessels of the (example) fleet with their main particulars."""
    return [{"name": v.name, "type": v.vessel_type.value, "dwt": v.dwt, "design_speed_kn": v.design_speed,
             "fuel": v.fuel_type.value, "time_charter_eur_day": v.tce_eur_day, "ice_class": v.ice_class}
            for v in load_vessels_from_csv().values()]


def optimize_route(origin: str, destination: str, vessel: str, eua_price: float = 65.0,
                   fuel: Optional[str] = None, year: int = 2026, cargo_tons: Optional[float] = None,
                   max_transit_hours: float = 1500.0, avoid: Optional[List[str]] = None,
                   time_charter_eur_day: Optional[float] = None,
                   include_fueleu_penalty: bool = False) -> dict:
    """Cheapest feasible route and speed for one voyage."""
    req = _request(origin, destination, vessel, eua_price, fuel, year, cargo_tons, max_transit_hours,
                   time_charter_eur_day, include_fueleu_penalty)
    excluded = {c.strip().upper() for c in avoid} if avoid else None
    result = optimize_voyage(get_graph(), req, exclude_nodes=excluded)
    return {"result": summarize(result), "feasible": result is not None, "disclaimer": DISCLAIMER}


def compare_fuels(origin: str, destination: str, vessel: str, fuels: Optional[List[str]] = None,
                  eua_price: float = 65.0, year: int = 2026, cargo_tons: Optional[float] = None,
                  max_transit_hours: float = 1500.0) -> dict:
    """Same voyage optimized with each fuel: cost, FuelEU compliance and ISO 14083 footprint."""
    req = _request(origin, destination, vessel, eua_price, None, year, cargo_tons, max_transit_hours,
                   None, False)
    try:
        types = [FuelType(f) for f in (fuels or ["HFO", "MGO", "LNG", "BioLNG"])]
    except ValueError as exc:
        raise ValueError(f"{exc}. Options: {[f.value for f in FuelType]}") from None
    results = compare_fuel_scenarios(get_graph(), req, types)
    return {"by_fuel": {r.vessel.fuel_type.value: summarize(r) for r in results},
            "disclaimer": DISCLAIMER}


def compare_corridors(origin: str, destination: str, vessel: str, eua_price: float = 65.0,
                      fuel: Optional[str] = None, year: int = 2026, cargo_tons: Optional[float] = None,
                      max_transit_hours: float = 2500.0) -> dict:
    """Suez Canal vs Cape of Good Hope, each corridor optimized separately."""
    req = _request(origin, destination, vessel, eua_price, fuel, year, cargo_tons, max_transit_hours,
                   None, False)
    routes = compare_canal_routes(get_graph(), req)
    out: Dict[str, Optional[dict]] = {k: summarize(v) for k, v in routes.items()}
    valid = {k: v for k, v in routes.items() if v is not None}
    best = min(valid, key=lambda k: valid[k].total_cost_eur) if valid else None
    saving = (abs(valid["suez"].total_cost_eur - valid["cape"].total_cost_eur)
              if len(valid) == 2 else None)
    return {"corridors": out, "cheapest": best,
            "saving_eur": round(saving) if saving is not None else None, "disclaimer": DISCLAIMER}


def eua_sensitivity(origin: str, destination: str, vessel: str,
                    prices: Optional[List[float]] = None, fuel: Optional[str] = None, year: int = 2026,
                    cargo_tons: Optional[float] = None, max_transit_hours: float = 1500.0) -> dict:
    """How the optimal speed, CO₂ and cost respond to the EU ETS allowance price."""
    req = _request(origin, destination, vessel, 65.0, fuel, year, cargo_tons, max_transit_hours,
                   None, False)
    results = compare_eua_price_scenarios(get_graph(), req, prices or [40, 65, 90, 120, 150])
    return {"by_eua_price": {f"{r.eua_price:g}": summarize(r) for r in results},
            "disclaimer": DISCLAIMER}
