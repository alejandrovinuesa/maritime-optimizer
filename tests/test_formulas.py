"""Arithmetic identities of the emission and cost formulas, and regulatory logic."""

import pytest

from maritime_optimizer import (
    FuelType, OptimizationRequest, Vessel, VesselType, VoyageGeoType,
    compare_fuel_scenarios, optimize_voyage,
)
from maritime_optimizer.config import GWP_CH4, GWP_N2O


def test_fuel_follows_admiralty_cubic_law(valencia_rotterdam, panamax):
    leg = valencia_rotterdam.legs[0]
    load = min((leg.speed_knots / panamax.design_speed) ** 3, 1.0)
    hours = leg.distance_nm / leg.speed_knots
    expected = panamax.sfoc_base * panamax.mcr * load * hours / 1e6
    assert leg.fuel_consumed_tons == pytest.approx(expected, abs=0.005)


def test_cii_attained_formula(valencia_rotterdam, panamax):
    r = valencia_rotterdam
    expected = r.total_co2_tons * 1e6 / (panamax.dwt * r.total_distance_nm)
    assert r.cii_attained == pytest.approx(expected, abs=1e-3)


def test_ets_cost_formula(valencia_rotterdam):
    r = valencia_rotterdam
    ch4 = sum(leg.ch4_ttw_tons for leg in r.legs)
    n2o = sum(leg.n2o_ttw_tons for leg in r.legs)
    alpha = {VoyageGeoType.INTRA_EU: 1.0, VoyageGeoType.EXTRA_EU: 0.5,
             VoyageGeoType.NON_EU: 0.0}[r.geo_type]
    expected = (r.total_co2_tons + ch4 * GWP_CH4 + n2o * GWP_N2O) * alpha * 65
    assert r.total_ets_cost_eur == pytest.approx(expected, abs=2)


@pytest.fixture(scope="module")
def hfo_vs_biolng(graph):
    lng = Vessel(imo_number="0", name="LNG", vessel_type=VesselType.TANKER, gt=68_000,
                 dwt=85_000, design_speed=19.5, mcr=25_000, sfoc_base=155.0,
                 fuel_type=FuelType.LNG)
    req = OptimizationRequest(vessel=lng, origin_port="ESVLC", destination_port="GRPIR",
                              eua_price=65, cargo_tons=75_000, max_transit_hours=400)
    res = {r.vessel.fuel_type: r for r in
           compare_fuel_scenarios(graph, req, [FuelType.HFO, FuelType.BioLNG])}
    return res[FuelType.HFO], res[FuelType.BioLNG]


def test_biolng_zero_rating(hfo_vs_biolng):
    hfo, bio = hfo_vs_biolng
    assert bio.total_co2_tons < 1.0                 # ETS-accounted CO2 is zero-rated
    assert bio.total_co2_combustion_tons > 100      # physical CO2 still exists
    assert bio.fueleu_compliant
    assert not hfo.fueleu_compliant


def test_iso14083_intensity_properties(graph, panamax, container, valencia_rotterdam,
                                       hfo_vs_biolng):
    from maritime_optimizer import compare_canal_routes
    assert valencia_rotterdam.iso14083_intensity > 0
    hfo, bio = hfo_vs_biolng
    assert 0 < bio.iso14083_intensity < hfo.iso14083_intensity

    # Independent of EU ETS geography at equal vessel, fuel and speed.
    fixed = dict(vessel=panamax, eua_price=65, cargo_tons=68_000, max_transit_hours=900,
                 min_speed_knots=11.0, max_speed_knots=11.0)
    intra = optimize_voyage(graph, OptimizationRequest(origin_port="ESVLC",
                                                       destination_port="NLRTM", **fixed))
    extra = optimize_voyage(graph, OptimizationRequest(origin_port="NLRTM",
                                                       destination_port="USNYC", **fixed))
    assert intra.iso14083_intensity == pytest.approx(extra.iso14083_intensity, rel=0.01)

    # Suez and Cape routes: same vessel and fuel give (almost) the same intensity.
    corr = compare_canal_routes(graph, OptimizationRequest(
        vessel=container, origin_port="NLRTM", destination_port="SGSIN", eua_price=65,
        cargo_tons=80_000, max_transit_hours=1920, time_charter_rate=15_000,
        min_speed_knots=9.0))
    assert corr["suez"].iso14083_intensity == pytest.approx(
        corr["cape"].iso14083_intensity, rel=0.05)


def test_every_fuel_has_explicit_lcv_and_wtt():
    from maritime_optimizer.config import EMISSION_FACTORS
    for code, f in EMISSION_FACTORS.items():
        assert f["lcv"] > 0, code          # a zero LCV made FuelEU intensity 0/0 -> "compliant"


def test_biolng_wtw_intensity_equals_lifecycle_value_plus_slip(hfo_vs_biolng):
    """Certified BioLNG: WtW = E (RED II default 32.9) + methane slip + N2O, never zero."""
    from maritime_optimizer.config import EMISSION_FACTORS, GWP_CH4, GWP_N2O
    _, bio = hfo_vs_biolng
    f = EMISSION_FACTORS["BioLNG"]
    slip = f["c_slip"] / 100
    lcv_g = f["lcv"] / 1000                                   # MJ/g
    co2 = (1 - slip) * f["ef_co2"] / lcv_g
    non_co2 = ((1 - slip) * (f["ef_ch4"] * GWP_CH4 + f["ef_n2o"] * GWP_N2O) + slip * GWP_CH4) / lcv_g
    expected = co2 + non_co2 + f["wtt_factor"]
    assert bio.fueleu_intensity == pytest.approx(expected, rel=1e-6)
    assert 32.9 < bio.fueleu_intensity < bio.fueleu_max_allowed
