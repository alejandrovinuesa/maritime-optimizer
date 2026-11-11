"""Multi-voyage compliance: annual CII per ship and FuelEU Maritime pooling.

A single optimized voyage only *projects* compliance. MARPOL Annex VI rates the CII
per ship and calendar year, and FuelEU Maritime settles the compliance balance per
ship and reporting period, optionally pooled across ships (Reg. (EU) 2023/1805 art. 21).
These helpers aggregate several VoyageResults into those regulatory units.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from .config import (
    CII_PARAMS, EMISSION_FACTORS, FUELEU_PENALTY_EUR_PER_T_VLSFO, GEO_FACTOR_EXTRA_EU,
    GEO_FACTOR_INTRA_EU, VLSFO_ENERGY_MJ_PER_T, get_fueleu_max_intensity,
)
from .emissions import calc_cii_attained, get_cii_rating, is_cii_compliant
from .models import CIIRating, Vessel, VoyageGeoType, VoyageResult

_SCOPE = {VoyageGeoType.INTRA_EU: GEO_FACTOR_INTRA_EU,
          VoyageGeoType.EXTRA_EU: GEO_FACTOR_EXTRA_EU,
          VoyageGeoType.NON_EU: 0.0}


# ------------------------------------------------------------------ annual CII

@dataclass
class AnnualCII:
    vessel: str
    year: int
    voyages: int
    co2_tons: float
    distance_nm: float
    attained: float
    required: float
    rating: CIIRating
    compliant: bool


def annual_cii(vessel: Vessel, voyages: Sequence[VoyageResult], year: int) -> AnnualCII:
    """Attained CII of one ship over a calendar year: total CO₂ / (DWT × total distance).

    MARPOL Annex VI reg. 28 and MEPC.352(78) define the attained CII on the whole year,
    so a slow voyage can compensate a fast one. CO₂ is the ETS-accounted value (after
    biofuel zero-rating), consistently with the per-voyage check.
    """
    own = [v for v in voyages if v.vessel.name == vessel.name and v.year == year]
    if not own:
        raise ValueError(f"no voyages of {vessel.name} in {year}")
    co2 = sum(v.total_co2_tons for v in own)
    dist = sum(v.total_distance_nm for v in own)
    attained = calc_cii_attained(co2, vessel.dwt, dist)
    vtype = vessel.vessel_type.value
    params = CII_PARAMS.get(vtype) or CII_PARAMS["bulk_carrier"]
    required = params.required_cii(vessel.dwt, year)
    rating = get_cii_rating(attained, required, vtype)
    return AnnualCII(vessel.name, year, len(own), co2, dist, attained, required, rating,
                     is_cii_compliant(rating))


# ------------------------------------------------------------------ FuelEU balance and pooling

def fueleu_energy_in_scope_mj(voyage: VoyageResult) -> float:
    """Energy used on the voyage that falls under FuelEU (100 % intra-EU, 50 % extra-EU)."""
    energy = sum(leg.fuel_consumed_tons * 1000.0 * EMISSION_FACTORS[leg.fuel_type]["lcv"]
                 for leg in voyage.legs)
    return energy * _SCOPE[voyage.geo_type]


def compliance_balance_g(voyage: VoyageResult) -> float:
    """FuelEU compliance balance CB = (target − actual intensity) × energy in scope, in gCO₂e.

    Positive = surplus, negative = deficit (Reg. (EU) 2023/1805, Annex IV Part A).
    """
    target = get_fueleu_max_intensity(voyage.year)
    return (target - voyage.fueleu_intensity) * fueleu_energy_in_scope_mj(voyage)


def penalty_for_balance(cb_g: float, actual_intensity: float, consecutive_years: int = 1) -> float:
    """Annex IV Part B penalty for a (negative) balance; 0 for a surplus."""
    if cb_g >= 0 or actual_intensity <= 0:
        return 0.0
    base = -cb_g / (actual_intensity * VLSFO_ENERGY_MJ_PER_T) * FUELEU_PENALTY_EUR_PER_T_VLSFO
    return base * (1.0 + (consecutive_years - 1) / 10.0)


def fueleu_in_scope(voyage: VoyageResult) -> bool:
    """FuelEU applies to voyages with at least one EU/EEA port (same scope as the EU ETS)."""
    return _SCOPE[voyage.geo_type] > 0


def voyage_fueleu_penalty_eur(voyage: VoyageResult, consecutive_years: int = 1) -> float:
    """Annex IV penalty attributable to this voyage's deficit, whether or not it was priced into
    the objective (``include_fueleu_penalty``); equals the priced penalty at the same speeds.
    0 when compliant or out of scope."""
    return penalty_for_balance(compliance_balance_g(voyage), voyage.fueleu_intensity, consecutive_years)


# Shown with every voyage-level penalty figure.
FUELEU_FLEXIBILITY_NOTE = ("FuelEU pooling (art. 21) and banking/borrowing of compliance surplus "
                           "(art. 20) are not modelled and could reduce or avoid this penalty; the "
                           "penalty is assessed per ship and reporting period, not per voyage.")


@dataclass
class ShipBalance:
    vessel: str
    energy_mj: float
    intensity: float            # energy-weighted WtW gCO₂e/MJ
    balance_g: float
    penalty_alone_eur: float


@dataclass
class PoolResult:
    year: int
    ships: List[ShipBalance] = field(default_factory=list)
    pool_balance_g: float = 0.0
    valid: bool = False         # art. 21: the pool's total balance must be >= 0
    penalty_without_pool_eur: float = 0.0
    penalty_with_pool_eur: float = 0.0

    @property
    def saving_eur(self) -> float:
        return self.penalty_without_pool_eur - self.penalty_with_pool_eur


def fueleu_pool(voyages: Sequence[VoyageResult], year: int) -> PoolResult:
    """Pool the FuelEU balances of the ships behind a set of voyages for one reporting period.

    Each ship's balance sums its voyages; the pool is valid only if the total balance is
    non-negative (art. 21(4)), in which case no ship pays a penalty. Otherwise pooling is not
    allowed and every ship in deficit pays its own penalty.
    """
    by_ship: Dict[str, List[VoyageResult]] = {}
    for v in voyages:
        if v.year == year:
            by_ship.setdefault(v.vessel.name, []).append(v)
    result = PoolResult(year=year)
    for name, own in by_ship.items():
        energy = sum(fueleu_energy_in_scope_mj(v) for v in own)
        balance = sum(compliance_balance_g(v) for v in own)
        intensity = (sum(v.fueleu_intensity * fueleu_energy_in_scope_mj(v) for v in own) / energy
                     if energy > 0 else 0.0)
        result.ships.append(ShipBalance(name, energy, intensity, balance,
                                        penalty_for_balance(balance, intensity)))
    result.pool_balance_g = sum(s.balance_g for s in result.ships)
    result.valid = result.pool_balance_g >= 0
    result.penalty_without_pool_eur = sum(s.penalty_alone_eur for s in result.ships)
    result.penalty_with_pool_eur = 0.0 if result.valid else result.penalty_without_pool_eur
    return result
