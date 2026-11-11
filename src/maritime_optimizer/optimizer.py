"""Pipeline de optimización: velocidad por arista + k-shortest-paths + filtros CII/FuelEU. TFG 25/26."""

from dataclasses import replace
from typing import Callable, Dict, List, Optional, Tuple
import networkx as nx

from .models import (
    Vessel, FuelType, CIIRating, LegResult, VoyageResult, OptimizationRequest,
)
from .config import (
    CII_PARAMS, get_fueleu_max_intensity,
    DEFAULT_FUEL_PRICES,
)
from .emissions import (
    calc_leg, calc_cii_attained, get_cii_rating, is_cii_compliant,
    calculate_iso14083_intensity,
)
from .graph import MaritimeGraph
from .routing import ROUTE_CORRECTION_FACTOR, haversine_nm


# Iteraciones de la búsqueda áurea por arista: 5 reproducen el óptimo de 8 con ~38 % menos cálculo.
_GS_ITERATIONS = 5


def optimize_speed_for_edge(
    vessel: Vessel, distance_nm: float, geo_factor: float, year: int,
    eua_price: float, fuel_prices: dict,
    min_speed: float = 8.0, max_speed: float = 0.0, speed_steps: int = 10,
    cargo_tons: float = 50_000, time_charter_rate: float = 15_000.0,
    fueleu_penalty_multiplier: float = 0.0, resistance_factor: float = 1.0,
) -> Tuple[LegResult, float]:
    """Devuelve el LegResult de mínimo coste y su velocidad (barrido grueso + golden-section)."""
    if max_speed <= 0:
        max_speed = vessel.design_speed

    # Closure que evita reescribir 9 argumentos en cada llamada.
    def _eval(speed: float) -> LegResult:
        return calc_leg(vessel, speed, distance_nm, geo_factor,
                        year, eua_price, fuel_prices, cargo_tons,
                        time_charter_rate, fueleu_penalty_multiplier, resistance_factor)

    # Fase 1 — barrido grueso uniforme.
    speeds = [min_speed + i * (max_speed - min_speed) / speed_steps
              for i in range(speed_steps + 1)]
    best_leg = None
    best_cost = float('inf')
    best_speed = min_speed
    best_idx = 0
    for idx, s in enumerate(speeds):
        leg = _eval(s)
        if leg.total_cost_eur < best_cost:
            best_cost, best_leg, best_speed, best_idx = leg.total_cost_eur, leg, s, idx

    # Fase 2 — refinamiento golden-section sobre el intervalo del mínimo grueso.
    low = speeds[max(0, best_idx - 1)]
    high = speeds[min(len(speeds) - 1, best_idx + 1)]
    for _ in range(_GS_ITERATIONS):
        m1, m2 = low + (high - low) * 0.382, low + (high - low) * 0.618
        leg1, leg2 = _eval(m1), _eval(m2)
        if leg1.total_cost_eur < leg2.total_cost_eur:
            high = m2
            if leg1.total_cost_eur < best_cost:
                best_cost, best_leg, best_speed = leg1.total_cost_eur, leg1, m1
        else:
            low = m1
            if leg2.total_cost_eur < best_cost:
                best_cost, best_leg, best_speed = leg2.total_cost_eur, leg2, m2
    return best_leg, best_speed


def speed_cost_coefficients(vessel: Vessel, geo_factor: float, year: int,
                            eua_price: float, fuel_prices: dict,
                            time_charter_rate: float,
                            fueleu_penalty_multiplier: float = 0.0) -> Tuple[float, float]:
    """Coeficientes (α, β) del coste por milla c(v) = α·v² + β/v para v ≤ v_diseño.

    Combustible y ETS son proporcionales al consumo por milla (∝ v²); el TCE al
    tiempo por milla (∝ 1/v). α se obtiene de calc_leg en una milla a v_diseño,
    así hereda cualquier cambio en precios, factores, α_geo o β_hielo.
    """
    vd = vessel.design_speed
    leg = calc_leg(vessel, vd, 1.0, geo_factor, year, eua_price,
                   fuel_prices, 1.0, time_charter_rate, fueleu_penalty_multiplier)
    # The FuelEU penalty is proportional to fuel burned, like fuel and ETS costs.
    alpha = (leg.fuel_cost_eur + leg.ets_cost_eur + leg.fueleu_penalty_eur) / vd ** 2
    beta = time_charter_rate / 24.0
    return alpha, beta


def analytic_optimal_speed(vessel: Vessel, alpha: float, beta: float,
                           v_min: float, v_max: float) -> float:
    """Mínimo de c(v) = α·v² + β/v en [v_min, v_max]: v* = (β / 2α)^(1/3) recortada.

    c(v) es convexa en v > 0, así que recortar el óptimo libre al intervalo da
    el óptimo restringido. Por encima de v_diseño la carga del motor satura en
    1 y c(v) decrece (artefacto del modelo): si v_max > v_diseño se compara también
    con v_max.
    """
    vd = vessel.design_speed
    if alpha <= 0:
        v_star = v_min if beta <= 0 else min(v_max, vd)
    else:
        v_star = (beta / (2.0 * alpha)) ** (1.0 / 3.0)
    v_star = min(max(v_star, v_min), min(v_max, vd))
    if v_max > vd and v_max > v_star:
        cost = lambda v: (alpha * v ** 2 + beta / v if v <= vd
                          else (alpha * vd ** 3 + beta) / v)
        if cost(v_max) < cost(v_star):
            v_star = v_max
    return v_star


def apply_cii_filter(result: VoyageResult) -> VoyageResult:
    """Evalúa CII del viaje y marca cumplimiento (rating A/B/C → True)."""
    vessel = result.vessel
    if not vessel.is_cii_applicable:
        result.cii_compliant = True
        result.cii_rating = CIIRating.A
        return result
    vtype = vessel.vessel_type.value
    cii_att = calc_cii_attained(result.total_co2_tons, vessel.dwt,
                                result.total_distance_nm)
    params = CII_PARAMS.get(vtype) or CII_PARAMS["bulk_carrier"]
    cii_req = params.required_cii(vessel.dwt, result.year)
    rating = get_cii_rating(cii_att, cii_req, vtype)
    result.cii_attained = cii_att
    result.cii_required = cii_req
    result.cii_rating = rating
    result.cii_compliant = is_cii_compliant(rating)
    return result


def apply_fueleu_check(result: VoyageResult) -> VoyageResult:
    """Promedia las intensidades WtW de los tramos y compara con el umbral del año."""
    if not result.legs:
        return result
    total_fuel = result.total_fuel_tons or sum(l.fuel_consumed_tons for l in result.legs)
    if total_fuel > 0:
        weighted = sum(l.ghg_intensity_wtw_gco2e_mj * l.fuel_consumed_tons
                       for l in result.legs) / total_fuel
    else:
        weighted = 0.0
    result.fueleu_intensity = weighted
    result.fueleu_max_allowed = get_fueleu_max_intensity(result.year)
    result.fueleu_compliant = weighted <= result.fueleu_max_allowed
    return result


# Open-sea nodes of the Cape of Good Hope corridor: excluding only HOPE would let the
# "Suez" alternative still sail round Africa through the South Indian / SW Africa waypoints.
CAPE_CORRIDOR = frozenset({"HOPE", "WP_SOAF", "WP_SIND"})

# Reintento CII (memoria §4.5.3): si ninguna ruta llega a banda C, se limita v_max.
# CII ∝ consumo por milla ∝ v², así que bajar el techo de velocidad mejora el rating.
CII_RETRY_FACTORS = (0.9, 0.8, 0.7)


def optimize_voyage(graph: MaritimeGraph,
                    request: OptimizationRequest,
                    exclude_nodes: Optional[set] = None,
                    k_max: int = 10) -> Optional[VoyageResult]:
    """Optimiza el viaje; si el CII falla, reintenta con v_max × 0,9 / 0,8 / 0,7."""
    diag = {"cii_failed": False}
    result = _optimize_once(graph, request, exclude_nodes, k_max, diag)
    if result is not None or not diag["cii_failed"]:
        return result
    base_max = request.max_speed_knots
    for factor in CII_RETRY_FACTORS:
        capped = base_max * factor
        if capped < request.min_speed_knots:
            break
        retry = _optimize_once(graph, replace(request, max_speed_knots=capped),
                               exclude_nodes, k_max, {"cii_failed": False})
        if retry is not None:
            retry.cii_speed_cap_factor = factor
            return retry
    return None


def _optimize_once(graph: MaritimeGraph, request: OptimizationRequest,
                   exclude_nodes: Optional[set], k_max: int,
                   diag: dict) -> Optional[VoyageResult]:
    """k-shortest-paths (Yen) con velocidad por arista y reconstrucción sensible a T_max."""
    vessel = request.vessel
    origin, destination = request.origin_port, request.destination_port
    G = graph.graph
    if origin not in G or destination not in G:
        return None

    excluded = exclude_nodes or set()
    if origin in excluded or destination in excluded:
        return None
    search_graph = G if not excluded else G.copy()
    if excluded:
        search_graph.remove_nodes_from(excluded)

    year = request.year
    eua_price = request.eua_price
    fuel_prices = request.fuel_price_override or DEFAULT_FUEL_PRICES
    # α_geo se aplica con origen y destino del viaje, no del tramo.
    geo_factor = graph.get_geo_factor(origin, destination)
    geo_type = graph.classify_voyage_geo(origin, destination)
    analytic = request.speed_method == "analytic"

    # Con coste lineal en distancia y α_geo por viaje, v* es la misma en todas las aristas.
    if analytic:
        alpha, beta = speed_cost_coefficients(
            vessel, geo_factor, year, eua_price, fuel_prices,
            request.time_charter_rate, request.fueleu_penalty_multiplier)
        speed_cache: Dict[float, float] = {}

    def _v_star(w: float) -> float:
        """Closed-form optimal speed of an edge with added-resistance factor w (α scales with w)."""
        if w not in speed_cache:
            speed_cache[w] = analytic_optimal_speed(vessel, alpha * w, beta,
                                                    request.min_speed_knots,
                                                    request.max_speed_knots)
        return speed_cache[w]

    def _leg_at(speed: float, distance_nm: float, w: float = 1.0) -> LegResult:
        """Evalúa un tramo a velocidad fija (modo analítico)."""
        return calc_leg(vessel, speed, distance_nm, geo_factor, year, eua_price,
                        fuel_prices, request.cargo_tons, request.time_charter_rate,
                        request.fueleu_penalty_multiplier, w)

    def _apply_canal(leg: LegResult, edge_data: dict) -> LegResult:
        """Suma peaje y tiempo extra del canal al LegResult."""
        fee = edge_data.get("canal_fee_eur", 0.0)
        hours = edge_data.get("transit_hours_extra", 0.0)
        if fee > 0 or hours > 0:
            leg.canal_fee_eur = fee
            leg.via_canal = edge_data.get("via_canal")
            leg.time_cost_eur += request.time_charter_rate * (hours / 24.0)
            leg.transit_time_hours += hours
            leg.total_cost_eur = (leg.fuel_cost_eur + leg.ets_cost_eur + leg.time_cost_eur
                                  + leg.canal_fee_eur + leg.fueleu_penalty_eur)
        return leg

    # Peso para Yen: coste óptimo de cada arista, cacheado.
    cost_cache: Dict[Tuple[str, str], float] = {}

    def _edge_cost(u: str, v: str, _edge_data: dict) -> float:
        cached = cost_cache.get((u, v))
        if cached is not None:
            return cached
        edge_data = G.edges[u, v]
        w = request.resistance(u, v)
        if analytic:
            leg = _leg_at(_v_star(w), edge_data["distance_nm"], w)
        else:
            leg, _ = optimize_speed_for_edge(
                vessel=vessel, distance_nm=edge_data["distance_nm"],
                geo_factor=geo_factor, year=year, eua_price=eua_price,
                fuel_prices=fuel_prices, min_speed=request.min_speed_knots,
                max_speed=request.max_speed_knots, speed_steps=request.speed_steps,
                cargo_tons=request.cargo_tons,
                time_charter_rate=request.time_charter_rate,
                fueleu_penalty_multiplier=request.fueleu_penalty_multiplier,
                resistance_factor=w)
        _apply_canal(leg, edge_data)
        cost_cache[(u, v)] = leg.total_cost_eur
        return leg.total_cost_eur

    def _build_legs_analytic(path: List[str]) -> Optional[List[LegResult]]:
        """Velocidad uniforme óptima con T_max (KKT: c'(v)·v² igual en todos los tramos).

        Sin restricción activa todos los tramos van a v*. Si D/v* + horas de canal
        supera T_max, el óptimo convexo es la velocidad uniforme que agota T_max.
        """
        pairs = [(path[i], path[i + 1]) for i in range(len(path) - 1)]
        edges = [G.edges[u, v] for u, v in pairs]
        dists = [e["distance_nm"] for e in edges]
        ws = [request.resistance(u, v) for u, v in pairs]
        canal_hours = sum(e.get("transit_hours_extra", 0.0) for e in edges)
        sailing_budget = request.max_transit_hours - canal_hours
        if sailing_budget <= 0:
            return None
        v_lo, v_hi = request.min_speed_knots, request.max_speed_knots
        speeds = [_v_star(w) for w in ws]
        if sum(d / s for d, s in zip(dists, speeds, strict=True)) > sailing_budget + 1e-9:
            if sum(dists) / v_hi > sailing_budget + 1e-9:
                return None
            if len(set(ws)) == 1:
                # Same resistance on every leg: the KKT optimum is one uniform speed.
                speeds = [max(speeds[0], sum(dists) / sailing_budget)] * len(dists)
            else:
                # KKT: c_e'(v)·v² = 2·α·w_e·v³ − β is equal on every leg, so
                # v_e(λ) = ((β + λ) / (2·α·w_e))^(1/3); bisect λ until the deadline is met.
                def speeds_for(lam: float) -> List[float]:
                    return [min(max(((beta + lam) / (2 * alpha * w)) ** (1 / 3), v_lo), v_hi)
                            for w in ws]

                lo, hi = 0.0, 2 * alpha * max(ws) * v_hi ** 3
                for _ in range(100):
                    mid = (lo + hi) / 2
                    hours = sum(d / s for d, s in zip(dists, speeds_for(mid), strict=True))
                    lo, hi = (mid, hi) if hours > sailing_budget else (lo, mid)
                speeds = speeds_for(hi)
        legs: List[LegResult] = []
        for (u, v), edge_data, speed, w in zip(pairs, edges, speeds, ws, strict=True):
            leg = _leg_at(speed, edge_data["distance_nm"], w)
            leg.origin, leg.destination = u, v
            legs.append(_apply_canal(leg, edge_data))
        return legs

    def _build_legs(path: List[str]) -> Optional[List[LegResult]]:
        """Reconstruye los tramos elevando el suelo de velocidad para encajar en T_max."""
        if analytic:
            return _build_legs_analytic(path)
        legs: List[LegResult] = []
        time_accum = 0.0
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            edge_data = G.edges[u, v]
            distance_nm = edge_data["distance_nm"]
            remaining = request.max_transit_hours - time_accum
            if remaining <= 0:
                return None
            # Velocidad mínima necesaria para que el tramo quepa en el tiempo restante.
            eff_min = max(request.min_speed_knots, distance_nm / remaining)
            if eff_min > request.max_speed_knots:
                return None
            leg, _ = optimize_speed_for_edge(
                vessel=vessel, distance_nm=distance_nm, geo_factor=geo_factor,
                year=year, eua_price=eua_price, fuel_prices=fuel_prices,
                min_speed=eff_min, max_speed=request.max_speed_knots,
                speed_steps=request.speed_steps, cargo_tons=request.cargo_tons,
                time_charter_rate=request.time_charter_rate,
                fueleu_penalty_multiplier=request.fueleu_penalty_multiplier,
                resistance_factor=request.resistance(u, v))
            leg.origin, leg.destination = u, v
            _apply_canal(leg, edge_data)
            legs.append(leg)
            time_accum += leg.transit_time_hours
        return legs

    def _path_cost(path: List[str]) -> float:
        return sum(_edge_cost(path[i], path[i + 1], None) for i in range(len(path) - 1))

    def _evaluate(path: List[str], rank: int, optimal_cost: float) -> Optional[VoyageResult]:
        """Feasible (T_max and CII) VoyageResult for a candidate route, or None."""
        legs = _build_legs(path)
        if legs is None:
            return None
        result = VoyageResult(vessel=vessel, year=year, route_ports=path, legs=legs,
                              geo_type=geo_type, eua_price=eua_price,
                              cargo_tons=request.cargo_tons)
        result.aggregate()
        result.rank = rank
        result.optimal_cost_eur = optimal_cost
        result.epl_max_speed_knots = request.epl_max_speed_knots
        result.eexi_binding = bool(request.epl_max_speed_knots and legs and max(
            leg.speed_knots for leg in legs) >= request.epl_max_speed_knots - 1e-6)
        apply_cii_filter(result)
        apply_fueleu_check(result)
        if not result.cii_compliant:          # T_max is already guaranteed by _build_legs
            diag["cii_failed"] = True
            return None
        result.iso14083_intensity = calculate_iso14083_intensity(result)
        return result

    try:
        first: Optional[List[str]] = None
        if analytic:
            # A*: admissible heuristic = great-circle lower bound of the remaining distance
            # times the cost per mile at v* (every edge measures >= great circle × 1.15 and
            # tolls are >= 0), so the first path is the exact cheapest one.
            w_min = min([1.0, *(request.edge_resistance or {}).values()])
            per_nm = _leg_at(_v_star(w_min), 1.0, w_min).total_cost_eur
            dest = graph.ports[destination]

            def _h(n: str, _target: str) -> float:
                p = graph.ports[n]
                return haversine_nm(p.lat, p.lon, dest.lat, dest.lon) * ROUTE_CORRECTION_FACTOR * per_nm

            first = nx.astar_path(search_graph, origin, destination, heuristic=_h, weight=_edge_cost)
            optimal_cost = _path_cost(first)
            result = _evaluate(first, 1, optimal_cost)
            if result is not None:
                return result
        # Fallback: Yen's k-shortest paths, in increasing cost order.
        optimal_cost = None
        for k, path in enumerate(nx.shortest_simple_paths(search_graph, origin, destination,
                                                          weight=_edge_cost), start=1):
            path = list(path)
            if optimal_cost is None:
                optimal_cost = _path_cost(path)
            if path != first:
                result = _evaluate(path, k, optimal_cost)
                if result is not None:
                    return result
            if k >= k_max:
                break
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    return None
    return None


def _sweep(graph: MaritimeGraph, base: OptimizationRequest,
           mutate: Callable[[OptimizationRequest, object], OptimizationRequest],
           values: List[object]) -> List[VoyageResult]:
    """Helper común para los barridos compare_*: aplica mutate(base, v) y optimiza."""
    results = []
    for v in values:
        r = optimize_voyage(graph, mutate(base, v))
        if r:
            results.append(r)
    return results


def compare_fuel_scenarios(graph: MaritimeGraph, base_request: OptimizationRequest,
                           fuel_types: List[FuelType] = None) -> List[VoyageResult]:
    """Optimiza la ruta para cada combustible (impacto del zero-rating RED II)."""
    if fuel_types is None:
        fuel_types = [FuelType.HFO, FuelType.MGO, FuelType.LNG, FuelType.BioLNG]

    def _swap_fuel(req: OptimizationRequest, ft: FuelType) -> OptimizationRequest:
        return replace(req, vessel=replace(req.vessel, fuel_type=ft))

    return _sweep(graph, base_request, _swap_fuel, fuel_types)


def compare_eua_price_scenarios(graph: MaritimeGraph, base_request: OptimizationRequest,
                                prices: List[float] = None) -> List[VoyageResult]:
    """Sensibilidad de la ruta óptima al precio del EUA."""
    if prices is None:
        prices = [40.0, 65.0, 90.0, 120.0, 150.0]
    return _sweep(graph, base_request,
                  lambda req, p: replace(req, eua_price=p), prices)


def compare_canal_routes(graph: MaritimeGraph, request: OptimizationRequest
                         ) -> Dict[str, Optional[VoyageResult]]:
    """Suez vs Cape of Good Hope: optimize each corridor separately -> {"suez": ..., "cape": ...}."""
    return {
        "suez": optimize_voyage(graph, request, exclude_nodes=CAPE_CORRIDOR | {"HORN"}),
        "cape": optimize_voyage(graph, request, exclude_nodes={"SUEZ"}),
    }
