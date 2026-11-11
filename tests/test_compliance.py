"""FuelEU penalty in the objective, annual CII per ship and FuelEU pooling."""

from dataclasses import replace

import pytest

from maritime_optimizer import FuelType, annual_cii, fueleu_pool, load_vessels_from_csv, optimize_voyage
from maritime_optimizer.compliance import compliance_balance_g, fueleu_energy_in_scope_mj
from maritime_optimizer.config import EMISSION_FACTORS
from maritime_optimizer.scenarios import build_request


@pytest.fixture(scope="module")
def fleet():
    return load_vessels_from_csv()


@pytest.fixture(scope="module")
def hfo_voyage_request(fleet):
    return build_request(fleet["panamax_bulker"], origin_port="ESVLC", destination_port="NLRTM",
                         max_transit_hours=600)


def test_penalty_matches_annex_iv_formula(graph, hfo_voyage_request):
    r = optimize_voyage(graph, replace(hfo_voyage_request, include_fueleu_penalty=True))
    energy = r.total_fuel_tons * 1000 * EMISSION_FACTORS["HFO"]["lcv"]           # intra-EU: 100 %
    expected = ((r.fueleu_intensity - r.fueleu_max_allowed) * energy
                / (r.fueleu_intensity * 41_000) * 2_400)
    assert r.total_fueleu_penalty_eur == pytest.approx(expected, rel=1e-9)
    assert r.total_cost_eur == pytest.approx(
        r.total_fuel_cost_eur + r.total_ets_cost_eur + r.total_time_cost_eur
        + r.total_canal_fees_eur + r.total_fueleu_penalty_eur, rel=1e-12)


def test_penalty_slows_the_ship_and_grows_with_consecutive_years(graph, hfo_voyage_request):
    off = optimize_voyage(graph, hfo_voyage_request)
    on1 = optimize_voyage(graph, replace(hfo_voyage_request, include_fueleu_penalty=True))
    on3 = optimize_voyage(graph, replace(hfo_voyage_request, include_fueleu_penalty=True,
                                         fueleu_consecutive_deficit_years=3))
    assert off.total_fueleu_penalty_eur == 0
    assert on3.legs[0].speed_knots < on1.legs[0].speed_knots < off.legs[0].speed_knots
    # Same speed would give exactly ×1.2; the optimizer slows down a bit more, so slightly less.
    assert 1.1 < on3.total_fueleu_penalty_eur / on1.total_fueleu_penalty_eur <= 1.2


def test_compliant_fuel_pays_no_penalty(graph, fleet, hfo_voyage_request):
    lng = replace(fleet["panamax_bulker"], fuel_type=FuelType.LNG)
    r = optimize_voyage(graph, replace(hfo_voyage_request, vessel=lng, include_fueleu_penalty=True))
    assert r.fueleu_compliant and r.total_fueleu_penalty_eur == 0


def test_annual_cii_aggregates_voyages(graph, fleet):
    bulk = fleet["panamax_bulker"]
    out = optimize_voyage(graph, build_request(bulk, origin_port="ESVLC", destination_port="NLRTM"))
    back = optimize_voyage(graph, build_request(bulk, origin_port="NLRTM", destination_port="ESVLC"))
    a = annual_cii(bulk, [out, back], 2026)
    expected = (out.total_co2_tons + back.total_co2_tons) * 1e6 / (
        bulk.dwt * (out.total_distance_nm + back.total_distance_nm))
    assert a.voyages == 2 and a.attained == pytest.approx(expected)
    assert a.required == pytest.approx(out.cii_required)
    with pytest.raises(ValueError):
        annual_cii(bulk, [out], 2030)


def test_pooling_with_a_biofuel_ship_cancels_the_penalty(graph, fleet):
    bulk = fleet["panamax_bulker"]
    hfo = optimize_voyage(graph, build_request(bulk, origin_port="ESVLC", destination_port="NLRTM"))
    bio_ship = replace(fleet["lr2_lng_tanker"], fuel_type=FuelType.BioLNG, name="bio")
    bio = optimize_voyage(graph, build_request(bio_ship, origin_port="ESVLC", destination_port="NLRTM"))
    assert compliance_balance_g(hfo) < 0 < compliance_balance_g(bio)
    assert fueleu_energy_in_scope_mj(hfo) > 0

    alone = fueleu_pool([hfo], 2026)
    assert not alone.valid and alone.penalty_with_pool_eur > 0
    pooled = fueleu_pool([hfo, bio], 2026)
    assert pooled.valid and pooled.penalty_with_pool_eur == 0
    assert pooled.saving_eur == pytest.approx(alone.penalty_without_pool_eur)


def test_voyage_penalty_estimate_matches_priced_penalty_and_scope(graph, fleet, hfo_voyage_request):
    from maritime_optimizer.compliance import fueleu_in_scope, voyage_fueleu_penalty_eur
    priced = optimize_voyage(graph, replace(hfo_voyage_request, include_fueleu_penalty=True))
    assert voyage_fueleu_penalty_eur(priced) == pytest.approx(priced.total_fueleu_penalty_eur, rel=1e-9)
    unpriced = optimize_voyage(graph, hfo_voyage_request)
    assert unpriced.total_fueleu_penalty_eur == 0 and voyage_fueleu_penalty_eur(unpriced) > 0
    # Extra-EU: half the energy in scope. Non-EU: out of scope, no penalty at all.
    v = fleet["panamax_bulker"]
    extra = optimize_voyage(graph, build_request(v, origin_port="NLRTM", destination_port="USNYC",
                                                 include_fueleu_penalty=True))
    energy = extra.total_fuel_tons * 1000 * EMISSION_FACTORS["HFO"]["lcv"] * 0.5
    expected = ((extra.fueleu_intensity - extra.fueleu_max_allowed) * energy
                / (extra.fueleu_intensity * 41_000) * 2_400)
    assert extra.total_fueleu_penalty_eur == pytest.approx(expected, rel=1e-9)
    non_eu = optimize_voyage(graph, build_request(v, origin_port="AEJEA", destination_port="BRSSZ",
                                                  max_transit_hours=3000, include_fueleu_penalty=True))
    assert not fueleu_in_scope(non_eu) and non_eu.total_fueleu_penalty_eur == 0
    assert voyage_fueleu_penalty_eur(non_eu) == 0


def test_agent_summary_reports_penalty_exposure_and_flexibility_note(graph, hfo_voyage_request):
    from maritime_optimizer.agents.tools import summarize
    f = summarize(optimize_voyage(graph, hfo_voyage_request))["fueleu"]
    assert f["in_scope"] and not f["compliant"] and f["estimated_penalty_eur"] > 0
    assert f["penalty_included_in_total"] is False and "pooling" in f["note"]
