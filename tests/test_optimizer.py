"""Optimizer properties: symmetry, monotonicity, k-shortest paths, analytic speed, CII retry."""

import pytest

from maritime_optimizer import (
    FuelType, OptimizationRequest, Vessel, VesselType, compare_eua_price_scenarios,
    optimize_voyage,
)
from maritime_optimizer.config import DEFAULT_FUEL_PRICES
from maritime_optimizer.emissions import calc_leg
from maritime_optimizer.optimizer import _optimize_once


def test_symmetry(graph, panamax):
    kw = dict(vessel=panamax, eua_price=65, cargo_tons=68_000, max_transit_hours=600)
    ab = optimize_voyage(graph, OptimizationRequest(origin_port="ESVLC",
                                                    destination_port="NLRTM", **kw))
    ba = optimize_voyage(graph, OptimizationRequest(origin_port="NLRTM",
                                                    destination_port="ESVLC", **kw))
    assert ab.total_distance_nm == pytest.approx(ba.total_distance_nm, abs=1.0)
    assert ab.total_cost_eur == pytest.approx(ba.total_cost_eur, abs=100)


def test_monotonic_in_eua_price(graph, panamax):
    base = OptimizationRequest(vessel=panamax, origin_port="ESALG", destination_port="DEHAM",
                               eua_price=65, cargo_tons=68_000, max_transit_hours=600)
    res = compare_eua_price_scenarios(graph, base, [0, 25, 50, 75, 100, 150, 200])
    speeds = [sum(leg.speed_knots for leg in r.legs) / len(r.legs) for r in res]
    co2 = [r.total_co2_tons for r in res]
    cost = [r.total_cost_eur for r in res]
    assert all(b <= a + 0.01 for a, b in zip(speeds, speeds[1:], strict=False))
    assert all(b <= a + 0.1 for a, b in zip(co2, co2[1:], strict=False))
    assert all(b >= a - 1 for a, b in zip(cost, cost[1:], strict=False))


def _nyc(container, tmax, method="analytic"):
    return OptimizationRequest(vessel=container, origin_port="NLRTM",
                               destination_port="USNYC", eua_price=65, cargo_tons=80_000,
                               max_transit_hours=tmax, speed_method=method)


def test_k_shortest_paths_respects_tmax(graph, container):
    generous = optimize_voyage(graph, _nyc(container, 2400))
    tight = optimize_voyage(graph, _nyc(container, 360))
    impossible = optimize_voyage(graph, _nyc(container, 120))
    assert generous.total_transit_hours > 360
    assert tight.total_transit_hours <= 360 + 1e-6
    assert tight.total_transit_hours < generous.total_transit_hours
    assert impossible is None


@pytest.mark.parametrize("origin,destination,eua", [
    ("ESVLC", "NLRTM", 65), ("ESALG", "DEHAM", 150), ("NLRTM", "USNYC", 65),
])
def test_analytic_speed_matches_golden_search(graph, panamax, origin, destination, eua):
    kw = dict(vessel=panamax, origin_port=origin, destination_port=destination,
              eua_price=eua, cargo_tons=68_000, max_transit_hours=2400)
    ra = optimize_voyage(graph, OptimizationRequest(speed_method="analytic", **kw))
    rg = optimize_voyage(graph, OptimizationRequest(speed_method="golden", **kw))
    speeds = [leg.speed_knots for leg in ra.legs]
    v_golden = sum(leg.speed_knots * leg.distance_nm for leg in rg.legs) / rg.total_distance_nm
    assert ra.route_ports == rg.route_ports
    assert ra.total_cost_eur <= rg.total_cost_eur + 1e-6     # closed form is the exact optimum
    assert max(speeds) - min(speeds) < 1e-9                  # same v* on every edge
    assert abs(speeds[0] - v_golden) < 0.1

    # Interior v* is a local minimum of the leg cost.
    v = speeds[0]
    if 8.0 < v < panamax.design_speed:
        geo = graph.get_geo_factor(origin, destination)
        cost = lambda s: calc_leg(panamax, s, 100.0, geo, 2026, eua, DEFAULT_FUEL_PRICES,
                                  68_000, 15_000.0).total_cost_eur
        assert cost(v) <= min(cost(v - 0.05), cost(v + 0.05))


@pytest.mark.parametrize("tmax", [340, 360, 380])
def test_binding_tmax_gives_uniform_speed_no_worse_than_greedy(graph, container, tmax):
    """Under a binding deadline the optimum is one uniform speed (KKT). The greedy leg-by-leg
    speed-up of the golden method may sprint on the last legs, fail the CII, or find nothing."""
    ra = optimize_voyage(graph, _nyc(container, tmax, "analytic"))
    rg = optimize_voyage(graph, _nyc(container, tmax, "golden"))
    speeds = [leg.speed_knots for leg in ra.legs]
    assert ra is not None
    assert max(speeds) - min(speeds) < 1e-9
    assert ra.total_transit_hours == pytest.approx(tmax, abs=0.1)
    if rg is not None and rg.route_ports == ra.route_ports:
        assert ra.total_cost_eur <= rg.total_cost_eur + 1e-6


def test_cii_retry_caps_speed(graph):
    bulk = Vessel(imo_number="0", name="High-TCE bulker", vessel_type=VesselType.BULK_CARRIER,
                  gt=30_000, dwt=40_000, design_speed=14.5, mcr=12_000, sfoc_base=180.0,
                  fuel_type=FuelType.HFO)
    req = OptimizationRequest(vessel=bulk, origin_port="ESVLC", destination_port="NLRTM",
                              time_charter_rate=60_000, max_transit_hours=2000,
                              cargo_tons=30_000)
    diag = {"cii_failed": False}
    assert _optimize_once(graph, req, None, 10, diag) is None
    assert diag["cii_failed"]
    r = optimize_voyage(graph, req)
    assert r is not None and r.cii_compliant
    assert r.cii_speed_cap_factor < 1.0
    assert max(leg.speed_knots for leg in r.legs) <= 14.5 * r.cii_speed_cap_factor + 1e-9


@pytest.mark.parametrize("origin,destination", [
    ("ESVLC", "NLRTM"), ("SGSIN", "NLRTM"), ("DEHAM", "USHOU"), ("CNSHA", "BRSSZ"), ("TRIST", "AEJEA"),
])
def test_astar_matches_dijkstra(graph, panamax, monkeypatch, origin, destination):
    """A* with the great-circle heuristic must return the exact cheapest route."""
    import networkx as nx
    req = OptimizationRequest(vessel=panamax, origin_port=origin, destination_port=destination,
                              eua_price=65, cargo_tons=68_000, max_transit_hours=10_000)
    with_astar = optimize_voyage(graph, req)
    monkeypatch.setattr(nx, "astar_path",
                        lambda G, s, t, heuristic, weight: nx.dijkstra_path(G, s, t, weight=weight))
    with_dijkstra = optimize_voyage(graph, req)
    assert with_astar.total_cost_eur == pytest.approx(with_dijkstra.total_cost_eur, rel=1e-12)
