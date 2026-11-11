"""Carga de puertos/buques, MaritimeGraph y clasificación EU ETS del viaje. TFG 25/26."""

import csv
import hashlib
import pickle
import sys
from pathlib import Path
from typing import Dict, List, Optional
import networkx as nx

from .paths import CACHE_DIR, PACKAGE_DATA_DIR
from .models import Port, Vessel, VesselType, FuelType, VoyageGeoType
from .routing import (
    build_maritime_network, is_canal_node, is_waypoint_node,
    get_canal_info, LAND_SHP, HAS_GEOPANDAS,
)

# Subir si cambia el formato del pickle del grafo.
_GRAPH_CACHE_VERSION = 1


DEFAULT_PORTS_CSV = PACKAGE_DATA_DIR / "ports.csv"
DEFAULT_VESSELS_CSV = PACKAGE_DATA_DIR / "vessels_example.csv"


def load_ports_from_csv(filepath: Path = DEFAULT_PORTS_CSV) -> Dict[str, Port]:
    """Carga ports.csv: code,name,country,lat,lon,is_eu_eea,is_tent_t."""
    ports: Dict[str, Port] = {}
    with open(filepath, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ports[row["code"]] = Port(
                code=row["code"], name=row["name"], country=row["country"],
                lat=float(row["lat"]), lon=float(row["lon"]),
                is_eu_eea=row["is_eu_eea"].strip().lower() == "true",
                is_tent_t=row["is_tent_t"].strip().lower() == "true",
            )
    return ports


def load_vessels_from_csv(filepath: Path = DEFAULT_VESSELS_CSV) -> Dict[str, Vessel]:
    """Carga la flota: ...,fuel_type,ice_class,tce_eur_day. v* ∝ TCE^(1/3)."""
    vessels: Dict[str, Vessel] = {}
    with open(filepath, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ice = row.get("ice_class", "").strip() or None
            vessels[row["name"]] = Vessel(
                imo_number="", name=row["name"],
                vessel_type=VesselType(row["vessel_type"]),
                gt=float(row["gt"]), dwt=float(row["dwt"]),
                design_speed=float(row["design_speed"]),
                mcr=float(row["mcr"]), sfoc_base=float(row["sfoc_base"]),
                fuel_type=FuelType(row["fuel_type"]),
                ice_class=ice, tce_eur_day=float(row["tce_eur_day"]),
            )
    return vessels


class MaritimeGraph:
    """DiGraph de rutas con puertos, canales (tolled) y waypoints (fee-cero).

    El peaje se cobra una sola vez por tránsito: solo en la arista que entra
    al nodo canal. La inversa (salida del canal) tiene peaje cero.
    """

    def __init__(self, ports: Optional[Dict[str, Port]] = None,
                 max_distance_nm: float = 12_000, use_cache: bool = True):
        self.ports: Dict[str, Port] = ports or load_ports_from_csv()
        self.graph = nx.DiGraph()
        cache_file = CACHE_DIR / f"graph_{self._cache_key(max_distance_nm)}.pkl"
        if use_cache and self._load_cache(cache_file):
            return
        self._build(max_distance_nm)
        if use_cache:
            self._save_cache(cache_file)

    def _cache_key(self, max_distance_nm: float) -> str:
        """Hash de todo lo que define el grafo: código, puertos, parámetros y cartografía."""
        h = hashlib.sha256()
        h.update(f"v{_GRAPH_CACHE_VERSION}|{max_distance_nm}|{sys.version_info[:2]}|"
                 f"nx{nx.__version__}|geo{HAS_GEOPANDAS}".encode())
        # Código que construye el grafo (waypoints, tolerancias, peajes, dataclasses).
        # Sibling source files by path: importing the modules as package attributes
        # (`from . import models`) would go through __init__.py and create an import cycle.
        here = Path(__file__).parent
        for module_file in (here / "routing.py", Path(__file__), here / "models.py"):
            h.update(Path(module_file).read_bytes())
        for code in sorted(self.ports):
            h.update(repr(self.ports[code]).encode())
        if LAND_SHP.exists():
            st = LAND_SHP.stat()
            h.update(f"{st.st_size}_{int(st.st_mtime)}".encode())
        return h.hexdigest()[:16]

    def _load_cache(self, cache_file: Path) -> bool:
        """Carga el grafo del pickle local; False si no existe o está corrupto."""
        if not cache_file.exists():
            return False
        try:
            # Pickle solo de la caché local que genera esta misma clase.
            with open(cache_file, "rb") as f:
                self.graph, self.ports = pickle.load(f)
            return True
        except Exception:
            self.graph = nx.DiGraph()
            return False

    def _save_cache(self, cache_file: Path) -> None:
        """Guarda el grafo; si no se puede escribir, se sigue sin caché."""
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(".tmp")
            with open(tmp, "wb") as f:
                pickle.dump((self.graph, self.ports), f, protocol=pickle.HIGHEST_PROTOCOL)
            tmp.replace(cache_file)
            # Las claves antiguas ya no se pueden reutilizar: se borran.
            for old in CACHE_DIR.glob("graph_*.pkl"):
                if old != cache_file:
                    old.unlink(missing_ok=True)
        except OSError:
            pass

    def _build(self, max_distance_nm: float):
        all_nodes, edges = build_maritime_network(
            self.ports, max_direct_distance_nm=max_distance_nm)
        # all_nodes incluye canales/waypoints añadidos por routing.
        self.ports = all_nodes
        for code, port in all_nodes.items():
            self.graph.add_node(code, port=port, is_canal=is_canal_node(code))

        for edge in edges:
            is_intra = (self.ports[edge.origin].is_eu_eea
                        and self.ports[edge.destination].is_eu_eea)
            origin_is_canal = is_canal_node(edge.origin)
            dest_is_canal = is_canal_node(edge.destination)
            common = dict(distance_nm=edge.distance_nm, is_intra_eu=is_intra,
                          via_canal=edge.via_canal)

            # Dirección origen → destino: peaje del canal de destino.
            if dest_is_canal:
                c = get_canal_info(edge.destination)
                fwd_fee, fwd_hours, fwd_via = c.canal_fee_eur, c.transit_hours, c.code
            else:
                fwd_fee, fwd_hours, fwd_via = 0.0, 0.0, edge.via_canal
            self.graph.add_edge(edge.origin, edge.destination,
                                **{**common, "via_canal": fwd_via},
                                canal_fee_eur=fwd_fee,
                                transit_hours_extra=fwd_hours)

            # Dirección inversa: peaje del que ahora es destino.
            if origin_is_canal:
                c = get_canal_info(edge.origin)
                rev_fee, rev_hours, rev_via = c.canal_fee_eur, c.transit_hours, c.code
            else:
                rev_fee, rev_hours, rev_via = 0.0, 0.0, edge.via_canal
            self.graph.add_edge(edge.destination, edge.origin,
                                **{**common, "via_canal": rev_via},
                                canal_fee_eur=rev_fee,
                                transit_hours_extra=rev_hours)

    def get_distance(self, origin: str, dest: str) -> float:
        return self.graph[origin][dest]["distance_nm"]

    def is_edge_intra_eu(self, origin: str, dest: str) -> bool:
        return self.graph[origin][dest]["is_intra_eu"]

    def classify_voyage_geo(self, origin: str, destination: str) -> VoyageGeoType:
        """INTRA/EXTRA/NON-EU según Dir. 2003/87/CE art. 3 octies bis."""
        p1, p2 = self.ports.get(origin), self.ports.get(destination)
        if p1 is None or p2 is None:
            raise KeyError(f"Puerto no encontrado: {origin} o {destination}")
        if p1.is_eu_eea and p2.is_eu_eea:
            return VoyageGeoType.INTRA_EU
        if p1.is_eu_eea or p2.is_eu_eea:
            return VoyageGeoType.EXTRA_EU
        return VoyageGeoType.NON_EU

    def get_geo_factor(self, origin: str, destination: str) -> float:
        """α_geo de la Ec. 3.3: 1.0 / 0.5 / 0.0."""
        from .config import GEO_FACTOR_INTRA_EU, GEO_FACTOR_EXTRA_EU
        gtype = self.classify_voyage_geo(origin, destination)
        if gtype == VoyageGeoType.INTRA_EU: return GEO_FACTOR_INTRA_EU
        if gtype == VoyageGeoType.EXTRA_EU: return GEO_FACTOR_EXTRA_EU
        return 0.0

    def shortest_path(self, origin: str, destination: str) -> List[str]:
        """Camino más corto en distancia (sin coste)."""
        try:
            return nx.shortest_path(self.graph, origin, destination,
                                    weight="distance_nm")
        except nx.NetworkXNoPath:
            return []

    def get_edge_canal(self, origin: str, dest: str) -> Optional[str]:
        return self.graph[origin][dest].get("via_canal")

    def summary(self) -> str:
        """Resumen textual del grafo."""
        n_ports = n_canals = n_waypoints = n_eu = 0
        for code, d in self.graph.nodes(data=True):
            if is_waypoint_node(code):
                n_waypoints += 1
            elif d.get("is_canal"):
                n_canals += 1
            else:
                n_ports += 1
                if d["port"].is_eu_eea:
                    n_eu += 1
        return (f"Maritime graph: {n_ports} ports ({n_eu} EU/EEA, "
                f"{n_ports - n_eu} non-EU), {n_canals} canals/straits, "
                f"{n_waypoints} waypoints, "
                f"{self.graph.number_of_edges()} directed edges")
