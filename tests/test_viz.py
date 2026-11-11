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
    assert at.title[0].value == "Valencia → Rotterdam"
    assert len(at.metric) == 6
    at.sidebar.toggle[0].set_value(True).run()               # Suez vs Cape comparison
    assert not at.exception
