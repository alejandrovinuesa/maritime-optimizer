"""maritime-optimizer: regulation-aware maritime route, speed and fuel optimization.

Minimizes voyage cost (fuel + EU ETS allowances + time charter + canal tolls)
subject to EEXI, CII, FuelEU Maritime and transit-time constraints, and reports
the ISO 14083 emissions intensity of the optimal solution.

Quick start::

    from maritime_optimizer import MaritimeGraph, load_vessels_from_csv, optimize_voyage
    from maritime_optimizer.scenarios import build_request

    graph = MaritimeGraph()
    vessel = load_vessels_from_csv()["panamax_bulker"]
    result = optimize_voyage(graph, build_request(
        vessel, origin_port="ESVLC", destination_port="NLRTM", eua_price=90))
    print(result.route_ports, result.total_cost_eur)
"""

from .compliance import AnnualCII, PoolResult, annual_cii, compliance_balance_g, fueleu_pool
from .geodata import ensure_geodata, has_geodata
from .graph import MaritimeGraph, load_ports_from_csv, load_vessels_from_csv
from .models import (
    CIIRating, FuelType, LegResult, OptimizationRequest, Port, Vessel,
    VesselType, VoyageGeoType, VoyageResult,
)
from .optimizer import (
    analytic_optimal_speed, compare_canal_routes, compare_eua_price_scenarios,
    compare_fuel_scenarios, optimize_voyage, speed_cost_coefficients,
)

__version__ = "0.1.0"

__all__ = [
    "AnnualCII", "PoolResult", "annual_cii", "compliance_balance_g", "fueleu_pool",
    "CIIRating", "FuelType", "LegResult", "MaritimeGraph", "OptimizationRequest",
    "Port", "Vessel", "VesselType", "VoyageGeoType", "VoyageResult",
    "analytic_optimal_speed", "compare_canal_routes", "compare_eua_price_scenarios",
    "compare_fuel_scenarios", "ensure_geodata", "has_geodata", "load_ports_from_csv",
    "load_vessels_from_csv", "optimize_voyage", "speed_cost_coefficients",
    "__version__",
]
