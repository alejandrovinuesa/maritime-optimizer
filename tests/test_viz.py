"""Smoke tests for the dashboards, the dashboard CLI command and the Streamlit app."""

from pathlib import Path

import pytest

pytest.importorskip("plotly")

from maritime_optimizer.cli import main  # noqa: E402
from maritime_optimizer.viz import build_dashboard, route_map  # noqa: E402
from maritime_optimizer.viz.html import DISCLAIMER  # noqa: E402

APP = Path(__file__).parents[1] / "app" / "streamlit_app.py"


@pytest.mark.parametrize("sid", ["E1", "E2", "E3", "E4", "E5"])
def test_dashboard_page(graph, sid):
    page = build_dashboard(sid, graph)
    assert page.startswith("<!doctype html>")
    assert page.count('class="plotly-graph-div"') >= 2       # map + at least one chart
    assert "Show data table" in page                         # every chart has a table view
    assert DISCLAIMER[:40] in page


def test_route_map_has_one_line_per_route(graph):
    from maritime_optimizer.scenarios import run_scenario
    routes = run_scenario("E5", graph)
    fig = route_map(graph, {"Suez": routes["suez"], "Cape": routes["cape"]})
    lines = [t for t in fig.data if t.mode == "lines"]
    assert [t.name for t in lines] == ["Suez", "Cape"]


def test_dashboard_cli(graph, tmp_path, capsys):
    assert main(["dashboard", "E1", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "e1.html").exists()


def test_streamlit_app_runs(graph):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    hero = next(m.value for m in at.markdown if m.value.startswith('<div class="mo-hero"'))
    assert "Valencia" in hero and "Rotterdam" in hero
    assert len(at.metric) == 6
    at.sidebar.toggle[0].set_value(True).run()               # Suez vs Cape comparison
    assert not at.exception


def test_apply_theme_recolours_for_dark_mode(graph, e5_like_rows=None):
    from maritime_optimizer import optimize_voyage
    from maritime_optimizer.scenarios import build_request
    from maritime_optimizer import load_vessels_from_csv
    from maritime_optimizer.viz.figures import apply_theme, cost_breakdown, route_map
    from maritime_optimizer.viz.theme import DARK, LIGHT
    vessel = next(iter(load_vessels_from_csv().values()))
    r = optimize_voyage(graph, build_request(vessel, origin_port="ESVLC", destination_port="NLRTM"))
    light = route_map(graph, {"Optimal route": r})
    assert apply_theme(light, "light").layout.geo.landcolor == LIGHT["land"]
    dark = apply_theme(route_map(graph, {"Optimal route": r}), "dark")
    assert dark.layout.geo.landcolor == DARK["land"] and dark.layout.font.color == DARK["ink2"]
    assert dark.data[0].line.color == DARK["series"][0]
    bars = apply_theme(cost_breakdown([("A", r)]), "dark")
    assert bars.data[0].marker.color == DARK["series"][bars.data[0].meta["slot"]]

