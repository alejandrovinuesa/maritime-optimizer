"""Fórmulas de consumo, emisiones (MRV), EU ETS, CII, FuelEU e ISO 14083. TFG 25/26."""

from typing import Tuple

from .config import (
    EMISSION_FACTORS, GWP_CH4, GWP_N2O,
    ICE_CLASS_DEDUCTION, FUELEU_PENALTY_EUR_PER_T_VLSFO, VLSFO_ENERGY_MJ_PER_T,
    CII_RATING_VECTORS,
    DEFAULT_FUEL_PRICES, get_fueleu_max_intensity,
)
from .models import (
    Vessel, CIIRating, LegResult, VoyageResult,
)

NM_TO_KM = 1.852


def _wtw_components(fuel_tons: float, fuel_type: str
                    ) -> Tuple[float, float, float, float, float, float, float]:
    """Componentes TtW + WtT compartidos por MRV, FuelEU e ISO 14083.

    Devuelve (co2_t_ets, co2_t_raw, ch4_t, n2o_t, g_vo_raw, g_vep, energy_mj):
      - co2_t_ets:  CO₂ aplicado al EU ETS (con zero-rating si procede)
      - co2_t_raw:  CO₂ real, sin zero-rating (FuelEU / ISO 14083)
      - ch4_t/n2o_t: con CH₄ slip incluido (combustión + fugitivo)
      - g_vo_raw:   tank-to-wake en gCO₂e SIN zero-rating
      - g_vep:      well-to-tank en gCO₂e (energía × wtt_factor)
      - energy_mj:  energía del combustible (t × 1000 × LCV)
    """
    if fuel_tons <= 0:
        return (0.0,) * 7
    ef = EMISSION_FACTORS[fuel_type]
    fuel_g = fuel_tons * 1e6
    slip = ef["c_slip"] / 100.0
    burned_g = fuel_g * (1 - slip)
    slip_g = fuel_g * slip

    co2_raw_g = burned_g * ef["ef_co2"]
    co2_ets_g = 0.0 if ef["zero_rating"] else co2_raw_g
    ch4_g = burned_g * ef["ef_ch4"] + slip_g     # combustión + fugitivo
    n2o_g = burned_g * ef["ef_n2o"]

    g_vo_raw = co2_raw_g + ch4_g * GWP_CH4 + n2o_g * GWP_N2O
    energy_mj = fuel_tons * 1000.0 * ef["lcv"]
    g_vep = energy_mj * ef["wtt_factor"]

    return (co2_ets_g / 1e6, co2_raw_g / 1e6, ch4_g / 1e6, n2o_g / 1e6,
            g_vo_raw, g_vep, energy_mj)


def calc_fuel_consumption(vessel: Vessel, speed_knots: float,
                          distance_nm: float, resistance: float = 1.0) -> Tuple[float, float]:
    """Consumo (t) y tiempo (h) del tramo por la ley cúbica del Almirantazgo.

    ``resistance`` multiplies the calm-water consumption (added resistance from wind,
    waves or currents on that leg; 1.0 = calm water).
    """
    if speed_knots <= 0 or distance_nm <= 0:
        return 0.0, 0.0
    transit_hours = distance_nm / speed_knots
    load_factor = min((speed_knots / vessel.design_speed) ** 3, 1.0)
    fuel_tons = vessel.sfoc_base * vessel.mcr * load_factor * transit_hours / 1e6 * resistance
    return fuel_tons, transit_hours


def calc_port_fuel_consumption(vessel: Vessel, port_hours: float) -> float:
    """Consumo de auxiliares en puerto (cubierto 100 % por EU ETS)."""
    return vessel.sfoc_base * vessel.mcr * vessel.aux_power_port_pct * port_hours / 1e6


def calc_emissions_mrv(fuel_tons: float, fuel_type: str
                       ) -> Tuple[float, float, float, float]:
    """Emisiones MRV (Reg. 2023/2776, fórmulas 1–4); CO₂ con zero-rating."""
    co2_t, _, ch4_t, n2o_t, _, _, _ = _wtw_components(fuel_tons, fuel_type)
    ghg = co2_t + ch4_t * GWP_CH4 + n2o_t * GWP_N2O
    return co2_t, ch4_t, n2o_t, ghg


def calc_ets_cost(co2_tons: float, geo_factor: float, year: int,
                  eua_price: float, ice_class: bool = False,
                  include_ch4_n2o: bool = True,
                  ch4_tons: float = 0.0, n2o_tons: float = 0.0) -> float:
    """Coste EU ETS (Ec. 3.3): emisiones × α_geo × P_EUA, con ajuste hielo si aplica."""
    if include_ch4_n2o:
        emissions = co2_tons + ch4_tons * GWP_CH4 + n2o_tons * GWP_N2O
    else:
        emissions = co2_tons
    cost = emissions * geo_factor * eua_price
    if ice_class and year <= 2030:
        cost *= (1 - ICE_CLASS_DEDUCTION)
    return cost


def calc_cii_attained(co2_tons: float, dwt: float, distance_nm: float) -> float:
    """Attained CII = CO₂ (g) / (DWT × D_nm) — gCO₂/(t·NM)."""
    if dwt <= 0 or distance_nm <= 0:
        return float('inf')
    return co2_tons * 1e6 / (dwt * distance_nm)


def get_cii_rating(cii_attained: float, cii_required: float,
                   vessel_type: str) -> CIIRating:
    """Rating CII A-E por umbrales d1-d4 (MEPC.354(78))."""
    d = CII_RATING_VECTORS.get(vessel_type) or CII_RATING_VECTORS["bulk_carrier"]
    if cii_attained <= d["d1"] * cii_required: return CIIRating.A
    if cii_attained <= d["d2"] * cii_required: return CIIRating.B
    if cii_attained <= d["d3"] * cii_required: return CIIRating.C
    if cii_attained <= d["d4"] * cii_required: return CIIRating.D
    return CIIRating.E


def is_cii_compliant(rating: CIIRating) -> bool:
    return rating in (CIIRating.A, CIIRating.B, CIIRating.C)


def calc_ghg_intensity_wtw(fuel_tons: float, fuel_type: str) -> float:
    """Intensidad WtW en gCO₂e/MJ para FuelEU (sin zero-rating, Ec. 4 art. 4)."""
    if fuel_tons <= 0:
        return 0.0
    _, _, _, _, g_vo_raw, g_vep, energy_mj = _wtw_components(fuel_tons, fuel_type)
    if energy_mj <= 0:
        return 0.0
    return (g_vo_raw + g_vep) / energy_mj


def calculate_iso14083_intensity(voyage: VoyageResult) -> float:
    """g(R,v,f) = (G_VO + G_VEP) / (M · Σd_k) en gCO₂e/(t·km), sin zero-rating (Ec. 3.13)."""
    cargo = voyage.cargo_tons
    distance_km = voyage.total_distance_nm * NM_TO_KM
    if cargo <= 0 or distance_km <= 0:
        return 0.0
    total = 0.0
    for leg in voyage.legs:
        if leg.fuel_consumed_tons <= 0 or leg.fuel_type not in EMISSION_FACTORS:
            continue
        _, _, _, _, g_vo, g_vep, _ = _wtw_components(leg.fuel_consumed_tons, leg.fuel_type)
        total += g_vo + g_vep
    return total / (cargo * distance_km)


def fueleu_penalty_eur(intensity_gco2e_mj: float, energy_in_scope_mj: float, year: int,
                      multiplier: float = 1.0) -> float:
    """FuelEU Annex IV penalty for a deficit; 0 if compliant or multiplier is 0.

    CB = (target - actual) x energy in scope (gCO2e); penalty = |CB| / (actual x 41 000) x 2 400.
    """
    target = get_fueleu_max_intensity(year)
    if multiplier <= 0 or intensity_gco2e_mj <= target or energy_in_scope_mj <= 0:
        return 0.0
    deficit_g = (intensity_gco2e_mj - target) * energy_in_scope_mj
    return (deficit_g / (intensity_gco2e_mj * VLSFO_ENERGY_MJ_PER_T)
            * FUELEU_PENALTY_EUR_PER_T_VLSFO * multiplier)


def calc_leg(vessel: Vessel, speed_knots: float, distance_nm: float,
             geo_factor: float, year: int, eua_price: float,
             fuel_prices: dict, cargo_tons: float = 50_000,
             time_charter_rate: float = 15_000.0,
             fueleu_penalty_multiplier: float = 0.0,
             resistance_factor: float = 1.0) -> LegResult:
    """Cálculo integrado de un tramo (Steps 1-7 del Documento Orientativo CE nº 1)."""
    fuel_type = vessel.fuel_type.value
    fuel_tons, transit_hours = calc_fuel_consumption(vessel, speed_knots, distance_nm,
                                                     resistance_factor)

    # Una única evaluación WtW reutilizada por MRV, ETS, FuelEU e ISO 14083.
    co2_ets, co2_raw, ch4_t, n2o_t, g_vo_raw, g_vep, energy_mj = \
        _wtw_components(fuel_tons, fuel_type)
    ghg_co2e = co2_ets + ch4_t * GWP_CH4 + n2o_t * GWP_N2O

    ets_cost = calc_ets_cost(
        co2_tons=co2_ets, geo_factor=geo_factor, year=year, eua_price=eua_price,
        ice_class=vessel.has_ice_class_deduction,
        include_ch4_n2o=True, ch4_tons=ch4_t, n2o_tons=n2o_t,
    )

    fuel_price = fuel_prices.get(fuel_type, DEFAULT_FUEL_PRICES.get(fuel_type, 600))
    fuel_cost = fuel_tons * fuel_price
    time_cost = time_charter_rate * (transit_hours / 24.0)

    # Intensidad WtW del tramo (FuelEU) — sin zero-rating.
    intensity_wtw = (g_vo_raw + g_vep) / energy_mj if energy_mj > 0 else 0.0
    intensity_cii = calc_cii_attained(co2_ets, vessel.dwt, distance_nm)
    penalty = fueleu_penalty_eur(intensity_wtw, energy_mj * geo_factor, year,
                                 fueleu_penalty_multiplier)

    return LegResult(
        origin="", destination="",
        distance_nm=distance_nm, speed_knots=speed_knots,
        transit_time_hours=transit_hours,
        fuel_consumed_tons=fuel_tons, fuel_type=fuel_type,
        co2_ttw_tons=co2_ets, co2_combustion_tons=co2_raw,
        ch4_ttw_tons=ch4_t, n2o_ttw_tons=n2o_t,
        ghg_co2e_tons=ghg_co2e,
        fuel_cost_eur=fuel_cost, ets_cost_eur=ets_cost,
        time_cost_eur=time_cost,
        fueleu_penalty_eur=penalty,
        total_cost_eur=fuel_cost + ets_cost + time_cost + penalty,
        ghg_intensity_wtw_gco2e_mj=intensity_wtw,
        ghg_intensity_gco2_per_tnm=intensity_cii,
    )
