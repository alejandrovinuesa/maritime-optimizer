"""Maritime graph: size, geographic sanity and cache consistency."""

from maritime_optimizer import MaritimeGraph, OptimizationRequest, load_ports_from_csv, optimize_voyage


def test_graph_size_and_connectivity(graph):
    import networkx as nx
    assert graph.summary().startswith(
        "Maritime graph: 23 ports (13 EU/EEA, 10 non-EU), 10 canals/straits, 59 waypoints")
    assert graph.graph.number_of_edges() == 532
    assert nx.number_strongly_connected_components(graph.graph) == 1


def test_no_edge_crosses_land_or_shallow_banks(graph):
    """Every edge's great-circle arc (the path its distance measures) crosses at most
    MAX_LAND_CROSSING_NM of mapped land and never a no-go area."""
    from maritime_optimizer.routing import MAX_LAND_CROSSING_NM, get_land_detector
    det = get_land_detector()
    for u, v in graph.graph.edges():
        a, b = graph.ports[u], graph.ports[v]
        assert det.land_crossing_nm(a.lat, a.lon, b.lat, b.lon) <= MAX_LAND_CROSSING_NM, (u, v)
        arc = det._arc_parts(a.lat, a.lon, b.lat, b.lon)
        assert not any(det.no_go.intersects(line) for line in arc), (u, v)


def _distance(graph, vessel, origin, destination):
    r = optimize_voyage(graph, OptimizationRequest(
        vessel=vessel, origin_port=origin, destination_port=destination,
        eua_price=65, cargo_tons=50_000, max_transit_hours=200))
    assert r is not None
    return r.total_distance_nm


def test_valencia_algeciras_not_detoured(graph, panamax):
    # Real distance along the SE Spanish coast is ~530 NM; a detour via Barcelona was ~860.
    assert _distance(graph, panamax, "ESVLC", "ESALG") / 530 < 1.5


def test_valencia_genoa_direct_edge_kept(graph, panamax):
    # Open western Mediterranean, ~510 NM; losing the edge to coastal slivers gave ~1300.
    assert _distance(graph, panamax, "ESVLC", "ITGOA") / 510 < 1.4


def test_cache_matches_fresh_build(graph):
    fresh = MaritimeGraph(use_cache=False)
    key = lambda e: (e[0], e[1])
    assert sorted(fresh.graph.nodes) == sorted(graph.graph.nodes)
    assert (sorted(fresh.graph.edges(data=True), key=key)
            == sorted(graph.graph.edges(data=True), key=key))


def test_cache_key_changes_with_ports(graph):
    fewer = {k: p for k, p in load_ports_from_csv().items() if k != "USHOU"}
    assert MaritimeGraph(ports=fewer, use_cache=False)._cache_key(12_000) != graph._cache_key(12_000)
