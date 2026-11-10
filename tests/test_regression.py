"""Numerical regression of the five example scenarios.

Reference values live in ``tests/data/example_scenarios.json``. If a change is
*meant* to alter results, regenerate them and review the diff::

    python tests/test_regression.py --update
"""

import json
import sys
from pathlib import Path

import pytest

REFERENCE = Path(__file__).parent / "data" / "example_scenarios.json"


def _flat(r):
    return {
        "route": r.route_ports,
        "distance_nm": round(r.total_distance_nm, 3),
        "hours": round(r.total_transit_hours, 3),
        "fuel_t": round(r.total_fuel_tons, 4),
        "cost_eur": round(r.total_cost_eur, 2),
        "ets_eur": round(r.total_ets_cost_eur, 2),
        "iso14083": round(r.iso14083_intensity, 5),
        "cii": r.cii_rating.value,
        "fueleu_ok": r.fueleu_compliant,
    }


def compute_cases(graph=None) -> dict:
    from maritime_optimizer.scenarios import run_all
    res = run_all(graph)
    cases = {"E1": _flat(res["E1"]), "E2": _flat(res["E2"])}
    cases.update({f"E3-{r.vessel.fuel_type.value}": _flat(r) for r in res["E3"]})
    cases.update({f"E4-EUA{int(r.eua_price)}": _flat(r) for r in res["E4"]})
    cases.update({f"E5-{k}": _flat(r) for k, r in res["E5"].items()})
    return cases


def test_example_scenarios_match_reference(graph):
    expected = json.loads(REFERENCE.read_text(encoding="utf-8"))
    actual = compute_cases(graph)
    assert actual.keys() == expected.keys()
    for case, exp in expected.items():
        got = actual[case]
        assert got["route"] == exp["route"], case
        assert (got["cii"], got["fueleu_ok"]) == (exp["cii"], exp["fueleu_ok"]), case
        for field in ("distance_nm", "hours", "fuel_t", "cost_eur", "ets_eur", "iso14083"):
            assert got[field] == pytest.approx(exp[field], rel=1e-6), f"{case}.{field}"


if __name__ == "__main__" and "--update" in sys.argv:
    REFERENCE.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE.write_text(json.dumps(compute_cases(), indent=1, ensure_ascii=False),
                         encoding="utf-8")
    print(f"Reference updated: {REFERENCE}")
