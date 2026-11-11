"""Dataclasses y enums del dominio marítimo. TFG 25/26."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum


class FuelType(str, Enum):
    """Combustibles del Reg. Delegado (UE) 2023/2776, Anexo I."""
    HFO = "HFO"
    MGO = "MGO"
    LNG = "LNG"
    BioLNG = "BioLNG"
    HVO = "HVO"
    METHANOL_FOSSIL = "Methanol_fossil"
    E_METHANOL = "E-Methanol"


class VesselType(str, Enum):
    """Categorías CII (MEPC.353(78))."""
    BULK_CARRIER = "bulk_carrier"
    TANKER = "tanker"
    CONTAINER = "container"
    GENERAL_CARGO = "general_cargo"


class CIIRating(str, Enum):
    """Calificación CII (MARPOL VI Reg. 28)."""
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class VoyageGeoType(str, Enum):
    """Tipología geográfica EU ETS (Dir. 2003/87/CE, art. 3 octies bis)."""
    INTRA_EU = "intra_eu"     # 100 % cobertura
    EXTRA_EU = "extra_eu"     # 50 % cobertura
    NON_EU = "non_eu"         # 0 % cobertura


@dataclass
class Port:
    """Puerto con metadatos geográficos y regulatorios."""
    code: str
    name: str
    country: str
    lat: float
    lon: float
    is_eu_eea: bool = True       # cobertura EU ETS (art. 3 octies bis)
    is_tent_t: bool = False      # red TEN-T (obliga OPS FuelEU desde 2030)

    @property
    def coords(self) -> Tuple[float, float]:
        return (self.lat, self.lon)


@dataclass
class Vessel:
    """Buque mercante con parámetros técnicos y de coste de oportunidad."""
    imo_number: str
    name: str
    vessel_type: VesselType
    gt: float                    # Arqueo Bruto
    dwt: float                   # Peso Muerto (t)
    design_speed: float          # nudos
    mcr: float                   # kW
    sfoc_base: float             # g/kWh — Ley del Almirantazgo: FC ∝ v²·d
    fuel_type: FuelType = FuelType.HFO
    ice_class: Optional[str] = None  # "IA" o "IA_Super" → deducción 5 % EU ETS

    # TCE diferenciado por buque; calibra v* ∝ TCE^(1/3).
    tce_eur_day: float = 15000.0

    # Auxiliares en puerto.
    aux_power_port_pct: float = 0.05

    @property
    def is_mrv_applicable(self) -> bool:
        """Ámbito MRV: ≥ 5000 GT (Reg. (UE) 2015/757)."""
        return self.gt >= 5000

    def epl_speed_knots(self, power_fraction: float) -> float:
        """Speed cap of an engine power limitation that allows ``power_fraction`` of MCR.

        In this model the propulsion power at design speed is the MCR (consumption scales
        with MCR·(v/v_d)³), so limiting power to a fraction f of MCR caps speed at v_d·f^(1/3).
        """
        if not 0 < power_fraction <= 1:
            raise ValueError("power_fraction must be in (0, 1]")
        return self.design_speed * power_fraction ** (1 / 3)

    @property
    def is_cii_applicable(self) -> bool:
        """Ámbito CII: ≥ 5000 GT (MARPOL VI Reg. 28)."""
        return self.gt >= 5000

    @property
    def has_ice_class_deduction(self) -> bool:
        """Deducción 5 % EU ETS para IA / IA Super (art. 12.3-sexies)."""
        return self.ice_class in ("IA", "IA_Super")


@dataclass
class LegResult:
    """Resultado MRV + coste de un tramo (arista del grafo)."""
    origin: str
    destination: str
    distance_nm: float
    speed_knots: float
    transit_time_hours: float

    # MRV (Reg. 2023/2776). co2_ttw_tons va con zero-rating aplicado (es el
    # CO₂ contable para EU ETS); co2_combustion_tons es el CO₂ físico real
    # — coinciden salvo en biocombustibles certificados RED II.
    fuel_consumed_tons: float
    fuel_type: str
    co2_ttw_tons: float
    co2_combustion_tons: float
    ch4_ttw_tons: float
    n2o_ttw_tons: float
    ghg_co2e_tons: float

    # Costes.
    fuel_cost_eur: float
    ets_cost_eur: float
    time_cost_eur: float = 0.0
    canal_fee_eur: float = 0.0
    via_canal: Optional[str] = None
    fueleu_penalty_eur: float = 0.0     # only when OptimizationRequest.include_fueleu_penalty
    total_cost_eur: float = 0.0

    # Intensidades por tramo (WtW FuelEU y proxy CII).
    ghg_intensity_wtw_gco2e_mj: float = 0.0
    ghg_intensity_gco2_per_tnm: float = 0.0


@dataclass
class VoyageResult:
    """Resultado agregado del viaje, con CII/FuelEU/ISO 14083 y rango k-shortest."""
    vessel: Vessel
    year: int
    route_ports: List[str]
    legs: List[LegResult]
    geo_type: VoyageGeoType
    eua_price: float = 0.0
    cargo_tons: float = 0.0

    # Agregados.
    total_distance_nm: float = 0.0
    total_fuel_tons: float = 0.0
    total_co2_tons: float = 0.0             # con zero-rating (CO₂ EU ETS)
    total_co2_combustion_tons: float = 0.0  # físico real (incluye biogénico)
    total_ghg_co2e_tons: float = 0.0
    total_fuel_cost_eur: float = 0.0
    total_ets_cost_eur: float = 0.0
    total_time_cost_eur: float = 0.0
    total_canal_fees_eur: float = 0.0
    total_fueleu_penalty_eur: float = 0.0
    total_cost_eur: float = 0.0
    total_transit_hours: float = 0.0
    canals_transited: List[str] = field(default_factory=list)

    # Restricciones regulatorias.
    cii_attained: float = 0.0
    cii_required: float = 0.0
    cii_rating: CIIRating = CIIRating.C
    cii_compliant: bool = True
    fueleu_intensity: float = 0.0
    fueleu_max_allowed: float = 0.0
    fueleu_compliant: bool = True

    # Métrica de reporte ISO 14083:2023, post-optimización.
    iso14083_intensity: float = 0.0

    # Posición de la ruta en la enumeración k-shortest-paths (Yen).
    rank: int = 1
    optimal_cost_eur: float = 0.0
    # Factor aplicado a v_max por el reintento CII (1.0 = sin reintento).
    cii_speed_cap_factor: float = 1.0
    # EEXI engine power limitation applied to this voyage, and whether it bounded the speed.
    epl_max_speed_knots: Optional[float] = None
    eexi_binding: bool = False

    def aggregate(self):
        """Suma los tramos en los totales del viaje."""
        self.total_distance_nm = sum(l.distance_nm for l in self.legs)
        self.total_fuel_tons = sum(l.fuel_consumed_tons for l in self.legs)
        self.total_co2_tons = sum(l.co2_ttw_tons for l in self.legs)
        self.total_co2_combustion_tons = sum(l.co2_combustion_tons for l in self.legs)
        self.total_ghg_co2e_tons = sum(l.ghg_co2e_tons for l in self.legs)
        self.total_fuel_cost_eur = sum(l.fuel_cost_eur for l in self.legs)
        self.total_ets_cost_eur = sum(l.ets_cost_eur for l in self.legs)
        self.total_time_cost_eur = sum(l.time_cost_eur for l in self.legs)
        self.total_canal_fees_eur = sum(l.canal_fee_eur for l in self.legs)
        self.total_fueleu_penalty_eur = sum(l.fueleu_penalty_eur for l in self.legs)
        self.total_cost_eur = sum(l.total_cost_eur for l in self.legs)
        self.total_transit_hours = sum(l.transit_time_hours for l in self.legs)
        self.canals_transited = [l.via_canal for l in self.legs if l.via_canal]


@dataclass
class OptimizationRequest:
    """Solicitud de optimización; TCE = coste de oportunidad temporal."""
    vessel: Vessel
    origin_port: str
    destination_port: str
    year: int = 2026
    max_transit_hours: float = 720
    eua_price: float = 65.0
    fuel_price_override: Optional[dict] = None
    min_speed_knots: float = 8.0
    max_speed_knots: float = 0.0       # operational top speed; 0 → design_speed del buque
    # EEXI (MARPOL Annex VI reg. 23): maximum speed reachable under the ship's engine power
    # limitation (EPL/ShaPoLi). None = no EPL. The effective cap is min(max_speed, this).
    # Vessel.epl_speed_knots(f) converts an EPL power fraction into this speed.
    epl_max_speed_knots: Optional[float] = None
    speed_steps: int = 5
    cargo_tons: float = 50_000
    time_charter_rate: float = 15_000.0
    # "analytic": v* en forma cerrada (exacta para coste lineal en distancia).
    # "golden": barrido + búsqueda áurea por arista (método de la memoria del TFG).
    speed_method: str = "analytic"
    # Price the FuelEU deficit into Z (Annex IV penalty). Off by default: FuelEU is then a
    # checked-and-reported constraint, as in the original thesis.
    include_fueleu_penalty: bool = False
    fueleu_consecutive_deficit_years: int = 1     # n in the (1 + (n - 1)/10) multiplier
    # Added-resistance factor per directed edge (u, v), e.g. from a weather forecast:
    # 1.15 = 15 % more fuel than in calm water on that leg. Missing edges = 1.0.
    edge_resistance: Optional[Dict[Tuple[str, str], float]] = None

    def __post_init__(self):
        if self.max_speed_knots <= 0:
            self.max_speed_knots = self.vessel.design_speed
        if self.epl_max_speed_knots is not None:
            if self.epl_max_speed_knots < self.min_speed_knots:
                raise ValueError(f"epl_max_speed_knots ({self.epl_max_speed_knots}) is below "
                                 f"min_speed_knots ({self.min_speed_knots})")
            self.max_speed_knots = min(self.max_speed_knots, self.epl_max_speed_knots)
        if self.edge_resistance and min(self.edge_resistance.values()) <= 0:
            raise ValueError("edge_resistance factors must be > 0")
        if self.fueleu_consecutive_deficit_years < 1:
            raise ValueError("fueleu_consecutive_deficit_years must be >= 1")
        if self.speed_method not in ("analytic", "golden"):
            raise ValueError(f"speed_method desconocido: {self.speed_method!r}")

    def resistance(self, u: str, v: str) -> float:
        """Added-resistance factor of the directed edge u→v (1.0 if not given)."""
        return self.edge_resistance.get((u, v), 1.0) if self.edge_resistance else 1.0

    @property
    def fueleu_penalty_multiplier(self) -> float:
        """0 when the penalty is not priced; otherwise the Annex IV consecutive-period factor."""
        if not self.include_fueleu_penalty:
            return 0.0
        return 1.0 + (self.fueleu_consecutive_deficit_years - 1) / 10.0
