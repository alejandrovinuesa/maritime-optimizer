"""Constantes regulatorias cargadas de data/regulatory/*.csv al import. TFG 25/26."""

import csv
from dataclasses import dataclass, field
from typing import Dict

from .paths import REGULATORY_DIR as REG_DIR

# GWP-100 (Reg. Delegado (UE) 2020/1044).
GWP_CH4 = 28
GWP_N2O = 265


def _safe_float(s: str, default: float = 0.0) -> float:
    s = s.strip()
    if not s:
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _safe_bool(s: str) -> bool:
    return s.strip().lower() in ("true", "1", "yes", "sí", "si")


def _load_emission_factors() -> Dict[str, dict]:
    """Combina TtW (Tabla 2.1) y WtT (Tabla 2.3); para LNG/BioLNG usa el dual conservador."""
    factors: Dict[str, dict] = {}

    with open(REG_DIR / "emission_factors_ttw.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            factors[row["fuel_code"]] = {
                "name": row["fuel_name"],
                "ef_co2": _safe_float(row["ef_co2_g_g"]),
                "ef_ch4": _safe_float(row["ef_ch4_g_g"]),
                "ef_n2o": _safe_float(row["ef_n2o_g_g"]),
                "c_slip": _safe_float(row["c_slip_pct"]),
                "zero_rating": _safe_bool(row["zero_rating_eligible"]),
            }

    seen: set = set()
    with open(REG_DIR / "emission_factors_wtt.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row["fuel_code"]
            if code in seen:
                continue
            # Para LNG/BioLNG el CSV trae varios motores: nos quedamos con el dual de baja velocidad.
            if code == "LNG" and row["engine_type"] != "Otto_VB_dual":
                continue
            if code == "BioLNG" and row["engine_type"] != "Otto_VM_dual":
                continue
            seen.add(code)
            if code not in factors:
                continue
            lcv_mj_g = _safe_float(row["lcv_mj_g"])
            if lcv_mj_g <= 0:
                raise ValueError(f"emission_factors_wtt.csv: {code} has no lower calorific value")
            wtt_cell = row["wtt_co2eq_g_mj"].strip()
            if wtt_cell == "E_minus_Cf_LCV":
                # Certified biofuels / RFNBOs (FuelEU Annex I): WtT = E − Cf_CO2/LCV, so the
                # well-to-wake intensity equals the lifecycle value E plus non-CO2 tailpipe gases.
                e_value = _safe_float(row.get("lifecycle_e_g_mj", ""), default=-1.0)
                if e_value < 0:
                    raise ValueError(f"emission_factors_wtt.csv: {code} needs lifecycle_e_g_mj")
                wtt = e_value - _safe_float(row["cf_co2_g_g"]) / lcv_mj_g
            else:
                try:
                    wtt = float(wtt_cell)
                except ValueError:
                    raise ValueError(f"emission_factors_wtt.csv: {code} has non-numeric WtT "
                                     f"{wtt_cell!r}") from None
            factors[code]["lcv"] = lcv_mj_g * 1000
            factors[code]["wtt_factor"] = wtt

    # Every fuel must have explicit LCV and WtT: a silent default would fake FuelEU results.
    missing = [code for code, f_data in factors.items() if "lcv" not in f_data]
    if missing:
        raise ValueError(f"emission_factors_wtt.csv: no well-to-tank row for {missing}")
    return factors


EMISSION_FACTORS: Dict[str, dict] = _load_emission_factors()


def _load_lng_engine_slip() -> Dict[str, float]:
    """C_slip (%) por tipo de motor LNG dual-fuel."""
    with open(REG_DIR / "lng_engine_slip.csv", encoding="utf-8") as f:
        return {row["engine_type"]: _safe_float(row["c_slip_pct"])
                for row in csv.DictReader(f)}


LNG_ENGINE_SLIP: Dict[str, float] = _load_lng_engine_slip()


# Factor α_geo del EU ETS (art. 3 octies bis Dir. 2003/87/CE).
# FuelEU Maritime penalty, Reg. (EU) 2023/1805 Annex IV Part B:
#   penalty = |CB| / (GHGIE_actual x 41 000 MJ/t) x 2 400 EUR/t VLSFO-equivalent,
#   x (1 + (n - 1) / 10) for the n-th consecutive reporting period with a deficit.
FUELEU_PENALTY_EUR_PER_T_VLSFO = 2400.0
VLSFO_ENERGY_MJ_PER_T = 41_000.0

# EU ETS phase-in (Directive 2003/87/EC art. 3gb as amended by 2023/959): shipping surrenders
# allowances for 40 % of 2024 emissions and 70 % of 2025 emissions, 100 % from 2026. The tool
# models voyages from 2026 on, so the coverage factor is always 1.00 and the phase-in is not
# modelled (see data/regulatory/README.md).
GEO_FACTOR_INTRA_EU = 1.0
GEO_FACTOR_EXTRA_EU = 0.5
GEO_FACTOR_PORT_EU = 1.0

# Deducción clase de hielo (art. 12.3-sexies, hasta 2030).
ICE_CLASS_DEDUCTION = 0.05


@dataclass
class CIIParams:
    """Parámetros CII por tipo de buque (Reg. 28 MARPOL VI)."""
    a: float
    c: float
    z_factors: Dict[int, float] = field(default_factory=dict)

    def reference_cii(self, dwt: float) -> float:
        return self.a * (dwt ** (-self.c))

    def required_cii(self, dwt: float, year: int) -> float:
        """CII_req = CII_ref × (1 − Z(year)/100)."""
        z = self.z_factors.get(year)
        if z is None:
            z = self.z_factors[max(self.z_factors)] if self.z_factors else 11.0
        return self.reference_cii(dwt) * (1 - z / 100)


def _load_cii_z_factors() -> Dict[int, float]:
    with open(REG_DIR / "cii_reduction_factors.csv", encoding="utf-8") as f:
        return {int(row["year"]): _safe_float(row["reduction_z_pct"])
                for row in csv.DictReader(f)}


def _load_cii_params() -> Dict[str, CIIParams]:
    z = _load_cii_z_factors()
    params: Dict[str, CIIParams] = {}
    with open(REG_DIR / "cii_reference_params.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            params[row["vessel_type"]] = CIIParams(
                a=_safe_float(row["a_coefficient"]),
                c=_safe_float(row["c_exponent"]),
                z_factors=dict(z),
            )
    return params


CII_PARAMS: Dict[str, CIIParams] = _load_cii_params()


def _load_cii_rating_vectors() -> Dict[str, dict]:
    """Umbrales d1-d4 del rating CII (MEPC.354(78))."""
    vectors: Dict[str, dict] = {}
    with open(REG_DIR / "cii_rating_thresholds.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            vectors[row["vessel_type"]] = {
                "d1": _safe_float(row["d1_A_threshold"]),
                "d2": _safe_float(row["d2_B_threshold"]),
                "d3": _safe_float(row["d3_C_threshold"]),
                "d4": _safe_float(row["d4_D_threshold"]),
            }
    return vectors


CII_RATING_VECTORS: Dict[str, dict] = _load_cii_rating_vectors()


# FuelEU Maritime (Reg. (UE) 2023/1805, Anexo I).
FUELEU_REFERENCE_INTENSITY = 91.16   # gCO₂e/MJ (línea base 2020)


def _load_fueleu_targets() -> Dict[int, float]:
    with open(REG_DIR / "fueleu_targets.csv", encoding="utf-8") as f:
        return {int(row["period_start"]): _safe_float(row["reduction_pct"])
                for row in csv.DictReader(f)}


FUELEU_REDUCTION_TARGETS: Dict[int, float] = _load_fueleu_targets()


def get_fueleu_max_intensity(year: int) -> float:
    """Intensidad GHGI máxima permitida en gCO₂e/MJ para el año dado."""
    reduction = 0.0
    for y in sorted(FUELEU_REDUCTION_TARGETS):
        if year >= y:
            reduction = FUELEU_REDUCTION_TARGETS[y]
    return FUELEU_REFERENCE_INTENSITY * (1 - reduction / 100)


def _load_fuel_prices() -> Dict[str, float]:
    with open(REG_DIR / "fuel_prices.csv", encoding="utf-8") as f:
        return {row["fuel_code"]: _safe_float(row["price_eur_t"])
                for row in csv.DictReader(f)}


DEFAULT_FUEL_PRICES: Dict[str, float] = _load_fuel_prices()
DEFAULT_EUA_PRICE = 65.0   # EUR/t-CO₂
