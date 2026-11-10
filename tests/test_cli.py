"""Smoke tests of the command-line interface."""

import json

from maritime_optimizer.cli import main


def test_ports_and_vessels(capsys):
    assert main(["ports"]) == 0
    assert "NLRTM" in capsys.readouterr().out
    assert main(["vessels"]) == 0
    assert "panamax_bulker" in capsys.readouterr().out


def test_route_json(graph, capsys):
    assert main(["route", "ESVLC", "NLRTM", "--vessel", "panamax_bulker", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["route_ports"][0] == "ESVLC" and data["route_ports"][-1] == "NLRTM"
    assert data["total_cost_eur"] > 0


def test_route_avoid_suez(graph, capsys):
    assert main(["route", "SGSIN", "NLRTM", "--vessel", "lr2_lng_tanker",
                 "--tmax", "2000", "--avoid", "SUEZ", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    from maritime_optimizer.optimizer import CAPE_CORRIDOR
    assert "SUEZ" not in data["route_ports"] and CAPE_CORRIDOR & set(data["route_ports"])


def test_unknown_vessel(graph, capsys):
    assert main(["route", "ESVLC", "NLRTM", "--vessel", "nope"]) == 2
