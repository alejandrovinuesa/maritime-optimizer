"""Per-edge added resistance (weather hook): closed-form speeds, KKT deadline, A* bound."""

from dataclasses import replace

import pytest

from maritime_optimizer import OptimizationRequest, optimize_voyage


@pytest.fixture(scope="module")
def base(panamax):
    return OptimizationRequest(vessel=panamax, origin_port="ESVLC", destination_port="NLRTM",
                               eua_price=65, cargo_tons=68_000, max_transit_hours=2000)


def test_unit_factors_change_nothing(graph, base):
    plain = optimize_voyage(graph, base)
    ones = {(u, v): 1.0 for u, v in graph.graph.edges()}
    same = optimize_voyage(graph, replace(base, edge_resistance=ones))
    assert same.route_ports == plain.route_ports
    assert same.total_cost_eur == pytest.approx(plain.total_cost_eur, rel=1e-12)


def test_headwind_leg_sails_slower_by_the_cube_root(graph, base):
    """A headwind on an unavoidable leg (every edge leaving the origin) slows only that leg,
    by exactly w^(-1/3); a headwind on an avoidable leg may instead change the route."""
    w = 1.3
    out_edges = {(base.origin_port, v): w for v in graph.graph.successors(base.origin_port)}
    plain = optimize_voyage(graph, base)
    windy = optimize_voyage(graph, replace(base, edge_resistance=out_edges))
    first, rest = windy.legs[0], windy.legs[1:]
    assert first.speed_knots == pytest.approx(rest[0].speed_knots * w ** (-1 / 3), rel=1e-9)
    assert windy.total_cost_eur > plain.total_cost_eur


def test_binding_deadline_satisfies_kkt(graph, base):
    plain = optimize_voyage(graph, base)
    legs = sorted(plain.legs, key=lambda x: -x.distance_nm)[:2]
    factors = {(legs[0].origin, legs[0].destination): 1.4, (legs[1].origin, legs[1].destination): 0.9}
    tmax = plain.total_transit_hours * 0.85
    r = optimize_voyage(graph, replace(base, edge_resistance=factors, max_transit_hours=tmax))
    assert r.total_transit_hours == pytest.approx(tmax, abs=0.05)
    # KKT: w_e · v_e³ equal on every leg whose speed is not clipped.
    w = [factors.get((x.origin, x.destination), 1.0) for x in r.legs]
    k = [wi * x.speed_knots ** 3 for wi, x in zip(w, r.legs, strict=True)]
    assert max(k) / min(k) == pytest.approx(1.0, abs=1e-6)


def test_astar_stays_exact_with_resistance(graph, base, monkeypatch):
    import networkx as nx
    factors = {(u, v): 1.0 + 0.5 * ((hash((u, v)) % 7) / 7) for u, v in graph.graph.edges()}
    factors[next(iter(factors))] = 0.8           # a tailwind lowers the A* bound
    req = replace(base, origin_port="SGSIN", destination_port="NLRTM", edge_resistance=factors)
    a = optimize_voyage(graph, req)
    monkeypatch.setattr(nx, "astar_path",
                        lambda G, s, t, heuristic, weight: nx.dijkstra_path(G, s, t, weight=weight))
    d = optimize_voyage(graph, req)
    assert a.total_cost_eur == pytest.approx(d.total_cost_eur, rel=1e-12)


def test_severe_weather_can_change_the_corridor(graph):
    from maritime_optimizer import load_vessels_from_csv
    from maritime_optimizer.scenarios import build_request
    req = build_request(load_vessels_from_csv()["lr2_lng_tanker"], origin_port="SGSIN",
                        destination_port="NLRTM", cargo_tons=80_000, max_transit_hours=3000)
    calm = optimize_voyage(graph, req)
    assert "SUEZ" in calm.route_ports
    red_sea = {"WP_REDS", "WP_REDN", "WP_ADEN", "GBE"}
    storm = {(u, v): 3.0 for u, v in graph.graph.edges() if red_sea & {u, v}}
    rough = optimize_voyage(graph, replace(req, edge_resistance=storm))
    assert "SUEZ" not in rough.route_ports
