"""Plain-text voyage reports and comparison tables."""

from typing import List

from .config import GWP_CH4, GWP_N2O
from .models import VoyageResult
from .routing import describe_route

W = 78        # width of single-voyage reports
WT = 132      # width of comparison tables


def _section(lines: List[str], title: str) -> None:
    lines.append(f"\n{'─' * W}")
    lines.append(f"  {title}")
    lines.append(f"{'─' * W}")


def _mean_speed(r: VoyageResult) -> float:
    """Distance-weighted mean sailing speed (excludes canal waiting time)."""
    if not r.legs or r.total_distance_nm <= 0:
        return 0.0
    return sum(leg.speed_knots * leg.distance_nm for leg in r.legs) / r.total_distance_nm


def format_voyage_report(result: VoyageResult) -> str:
    """Full report of one optimized voyage."""
    lines = ["=" * W, "  MARITIME VOYAGE OPTIMIZATION REPORT", "=" * W]

    v = result.vessel
    _section(lines, "VESSEL")
    imo = f" (IMO {v.imo_number})" if v.imo_number else ""
    lines.append(f"  Name:          {v.name}{imo}")
    lines.append(f"  Type:          {v.vessel_type.value}")
    lines.append(f"  GT / DWT:      {v.gt:,.0f} / {v.dwt:,.0f} t")
    lines.append(f"  Design speed:  {v.design_speed:.1f} kn")
    lines.append(f"  MCR:           {v.mcr:,.0f} kW")
    lines.append(f"  SFOC:          {v.sfoc_base:.1f} g/kWh")
    lines.append(f"  Fuel:          {v.fuel_type.value}")
    if v.ice_class:
        lines.append(f"  Ice class:     {v.ice_class} (5 % EU ETS deduction until 2030)")

    _section(lines, "OPTIMIZED ROUTE")
    lines.append(f"  Year:          {result.year}")
    lines.append(f"  Route:         {describe_route(result.route_ports)}")
    if result.canals_transited:
        lines.append(f"  Canals/straits: {', '.join(sorted(set(result.canals_transited)))}")
    lines.append(f"  EU ETS scope:  {result.geo_type.value} (Dir. 2003/87/EC art. 3ga)")
    lines.append(f"  Distance:      {result.total_distance_nm:,.1f} NM")
    lines.append(f"  Transit time:  {result.total_transit_hours:,.1f} h "
                 f"({result.total_transit_hours / 24:.1f} days)")
    if result.cii_speed_cap_factor < 1.0:
        lines.append(f"  CII retry:     speed capped at {result.cii_speed_cap_factor:.0%} "
                     f"of v_max to reach CII rating C")

    _section(lines, "LEGS")
    lines.append(f"  {'Leg':<20} {'Dist(NM)':>9} {'Speed(kn)':>9} "
                 f"{'Fuel(t)':>9} {'CO₂(t)':>9} {'Fuel EUR':>10} {'ETS EUR':>10}")
    rule = (f"  {'─' * 18:<20} {'─' * 9:>9} {'─' * 9:>9} "
            f"{'─' * 9:>9} {'─' * 9:>9} {'─' * 10:>10} {'─' * 10:>10}")
    lines.append(rule)
    for leg in result.legs:
        name = f"{leg.origin}→{leg.destination}"
        lines.append(f"  {name:<20} {leg.distance_nm:>9,.1f} {leg.speed_knots:>9.2f} "
                     f"{leg.fuel_consumed_tons:>9.2f} {leg.co2_combustion_tons:>9.2f} "
                     f"{leg.fuel_cost_eur:>10,.0f} {leg.ets_cost_eur:>10,.0f}")
    lines.append(rule)
    lines.append(f"  {'TOTAL':<20} {result.total_distance_nm:>9,.1f} {'':>9} "
                 f"{result.total_fuel_tons:>9.2f} {result.total_co2_combustion_tons:>9.2f} "
                 f"{result.total_fuel_cost_eur:>10,.0f} {result.total_ets_cost_eur:>10,.0f}")

    _section(lines, "COST SUMMARY")
    lines.append(f"  Fuel:                {result.total_fuel_cost_eur:>14,.2f} EUR")
    lines.append(f"  EU ETS allowances:   {result.total_ets_cost_eur:>14,.2f} EUR")
    lines.append(f"  Time charter (TCE):  {result.total_time_cost_eur:>14,.2f} EUR")
    lines.append(f"  Canal tolls:         {result.total_canal_fees_eur:>14,.2f} EUR")
    if result.total_fueleu_penalty_eur > 0:
        lines.append(f"  FuelEU penalty:      {result.total_fueleu_penalty_eur:>14,.2f} EUR"
                     "  (Annex IV, priced into the optimum)")
    lines.append(f"  TOTAL:               {result.total_cost_eur:>14,.2f} EUR")
    if result.total_cost_eur > 0:
        share = lambda x: x / result.total_cost_eur * 100
        lines.append(f"  Breakdown:           fuel {share(result.total_fuel_cost_eur):.0f} % | "
                     f"ETS {share(result.total_ets_cost_eur):.0f} % | "
                     f"time {share(result.total_time_cost_eur):.0f} % | "
                     f"tolls {share(result.total_canal_fees_eur):.0f} %"
                     + (f" | FuelEU {share(result.total_fueleu_penalty_eur):.0f} %"
                        if result.total_fueleu_penalty_eur > 0 else ""))

    _section(lines, "GHG EMISSIONS (Delegated Regulation (EU) 2023/2776)")
    co2_phys = result.total_co2_combustion_tons
    co2_ets = result.total_co2_tons
    lines.append(f"  CO₂ combustion:      {co2_phys:>14.2f} t  (physical, tank-to-wake)")
    if abs(co2_phys - co2_ets) > 0.01:
        lines.append(f"  CO₂ for EU ETS:      {co2_ets:>14.2f} t  (after biofuel zero-rating)")
    total_ch4 = sum(leg.ch4_ttw_tons for leg in result.legs)
    total_n2o = sum(leg.n2o_ttw_tons for leg in result.legs)
    lines.append(f"  CH₄ (incl. slip):    {total_ch4:>14.4f} t")
    lines.append(f"  N₂O:                 {total_n2o:>14.4f} t")
    lines.append(f"  Total GHG:           {result.total_ghg_co2e_tons:>14.2f} tCO₂e"
                 f"  (CO₂ ETS + CH₄×{GWP_CH4} + N₂O×{GWP_N2O})")

    _section(lines, "REGULATORY INDICATORS")
    lines.append(f"  CII attained:        {result.cii_attained:>14.4f} gCO₂/(t·NM)")
    lines.append(f"  CII required:        {result.cii_required:>14.4f} gCO₂/(t·NM)")
    lines.append(f"  CII rating:          {result.cii_rating.value:>14}  "
                 f"{'[COMPLIANT]' if result.cii_compliant else '[NON-COMPLIANT: SEEMP Part III plan]'}")
    lines.append(f"  WtW GHG intensity:   {result.fueleu_intensity:>14.2f} gCO₂e/MJ")
    lines.append(f"  FuelEU limit:        {result.fueleu_max_allowed:>14.2f} gCO₂e/MJ")
    if result.fueleu_compliant:
        lines.append("  FuelEU Maritime:     [COMPLIANT]")
    else:
        deficit = result.fueleu_intensity - result.fueleu_max_allowed
        lines.append(f"  FuelEU Maritime:     [DEFICIT {deficit:.2f} gCO₂e/MJ: "
                     f"penalty, pooling or banking needed]")
    lines.append(f"  ISO 14083 intensity: {result.iso14083_intensity:>14.3f} gCO₂e/(t·km)")

    if result.rank > 1 and result.optimal_cost_eur > 0:
        extra = (result.total_cost_eur - result.optimal_cost_eur) / result.optimal_cost_eur * 100
        _section(lines, "NOTE: FALLBACK ROUTE (k-shortest paths)")
        lines.append("  The cheapest route did not meet the transit-time limit or the CII")
        lines.append(f"  check. This is feasible route #{result.rank} by cost, "
                     f"{extra:.1f} % more expensive than the cheapest one.")

    lines.append(f"\n{'=' * W}")
    return "\n".join(lines)


def format_comparison_table(results: List[VoyageResult], label_key: str = "fuel") -> str:
    """Side-by-side table of several voyages (fuel or EUA price sweep)."""
    lines = [f"\n{'=' * WT}", "  SCENARIO COMPARISON", f"{'=' * WT}"]
    lines.append(f"  {'Scenario':<14} {'Speed(kn)':>9} {'Fuel(t)':>8} "
                 f"{'CO₂(t)':>9} {'CO₂ ETS(t)':>10} {'GHG(t)':>8} "
                 f"{'Fuel EUR':>10} {'ETS EUR':>10} {'TCE EUR':>10} {'TOTAL EUR':>11} "
                 f"{'CII':>4} {'FuelEU':>7} {'ISO g/tkm':>10}")
    lines.append(f"  {'─' * (WT - 2)}")
    for r in results:
        label = r.vessel.fuel_type.value if label_key == "fuel" else f"EUA {r.eua_price:.0f} EUR/t"
        lines.append(
            f"  {label:<14} {_mean_speed(r):>9.2f} {r.total_fuel_tons:>8.1f} "
            f"{r.total_co2_combustion_tons:>9.1f} {r.total_co2_tons:>10.1f} "
            f"{r.total_ghg_co2e_tons:>8.1f} "
            f"{r.total_fuel_cost_eur:>10,.0f} {r.total_ets_cost_eur:>10,.0f} "
            f"{r.total_time_cost_eur:>10,.0f} {r.total_cost_eur:>11,.0f} "
            f"{r.cii_rating.value:>4} {'OK' if r.fueleu_compliant else 'DEFICIT':>7} "
            f"{r.iso14083_intensity:>10.3f}")
    lines.append(f"  {'─' * (WT - 2)}")
    lines.append("  CO₂ = physical combustion CO₂.  CO₂ ETS = after biofuel zero-rating, "
                 "the base of the ETS cost (× α_geo × EUA price).")
    lines.append(f"{'=' * WT}")
    return "\n".join(lines)


CORRIDOR_NAMES = {"suez": "via Suez Canal", "cape": "via Cape of Good Hope"}


def format_canal_comparison(routes: dict) -> str:
    """Suez vs Cape of Good Hope, side by side (output of compare_canal_routes)."""
    lines = [f"\n{'=' * WT}", "  CORRIDOR COMPARISON: Suez Canal vs Cape of Good Hope", f"{'=' * WT}"]
    lines.append(f"  {'Alternative':<26} {'Dist(NM)':>9} {'Days':>6} "
                 f"{'Speed(kn)':>9} {'Fuel EUR':>11} {'ETS EUR':>10} {'TCE EUR':>11} "
                 f"{'Tolls EUR':>10} {'TOTAL EUR':>13} {'ISO g/tkm':>10}")
    lines.append(f"  {'─' * (WT - 2)}")
    for key, name in CORRIDOR_NAMES.items():
        r = routes.get(key)
        if r is None:
            lines.append(f"  {name:<26} (no feasible route within the transit-time limit)")
            continue
        lines.append(
            f"  {name:<26} {r.total_distance_nm:>9,.0f} {r.total_transit_hours / 24:>6.1f} "
            f"{_mean_speed(r):>9.2f} {r.total_fuel_cost_eur:>11,.0f} "
            f"{r.total_ets_cost_eur:>10,.0f} {r.total_time_cost_eur:>11,.0f} "
            f"{r.total_canal_fees_eur:>10,.0f} {r.total_cost_eur:>13,.0f} "
            f"{r.iso14083_intensity:>10.3f}")
    lines.append(f"  {'─' * (WT - 2)}")
    valid = {k: v for k, v in routes.items() if v is not None}
    if len(valid) == 2:
        best = min(valid, key=lambda k: valid[k].total_cost_eur)
        worst = next(k for k in valid if k != best)
        diff = valid[worst].total_cost_eur - valid[best].total_cost_eur
        lines.append(f"  Best: {CORRIDOR_NAMES[best]}, saves {diff:,.0f} EUR "
                     f"({diff / valid[worst].total_cost_eur:.1%}) over the alternative")
    elif len(valid) == 1:
        lines.append(f"  Only feasible alternative: {CORRIDOR_NAMES[next(iter(valid))]}")
    else:
        lines.append("  No alternative has a feasible route.")
    lines.append(f"{'=' * WT}")
    return "\n".join(lines)
