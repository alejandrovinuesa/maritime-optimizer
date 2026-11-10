"""Geometría marítima: distancias, detección de tierra y red de canales/waypoints. TFG 25/26."""

import importlib.util
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .models import Port
from .paths import GEODATA_DIR

# GeoPandas/Shapely se importan solo al construir el LandDetector (~0,5 s); con el grafo
# en caché no hacen falta.
HAS_GEOPANDAS = (importlib.util.find_spec("geopandas") is not None
                 and importlib.util.find_spec("shapely") is not None)
gpd = shapely = LineString = None


def _import_geo_libs() -> None:
    """Importa GeoPandas y Shapely bajo demanda."""
    global gpd, shapely, LineString
    if gpd is None:
        import geopandas as _gpd
        import shapely as _shapely
        from shapely.geometry import LineString as _LineString
        gpd, shapely, LineString = _gpd, _shapely, _LineString


EARTH_RADIUS_NM = 3440.065
ROUTE_CORRECTION_FACTOR = 1.15        # ortodrómica → distancia real

# Recorte alrededor de cada extremo para el test de cruce de tierra
# (puertos en estuarios y nodos sobre el istmo).
ENDPOINT_TRIM_DEG = 0.7
# Mapped land an edge may cross: small islands and coastline imprecision at 1:50M (~10 km).
MAX_LAND_CROSSING_NM = 5.4

LAND_SHP = GEODATA_DIR / "ne_50m_land.shp"

# Shallow banks that large ships cannot cross but the 1:50M coastline shows as tiny islands
# (removed by the erosion). Polygons as (lon, lat), deliberately coarse.
NO_GO_AREAS = {
    # Little and Great Bahama Banks, from Grand Bahama to the Cay Sal side of the Old Bahama Channel.
    "Bahama Banks": [(-79.2, 27.6), (-77.0, 27.6), (-75.6, 26.0), (-73.0, 22.5), (-74.5, 20.8),
                     (-77.5, 21.6), (-79.0, 22.8), (-79.4, 24.3), (-79.3, 25.9), (-79.2, 27.6)],
    # Florida Keys and the shallow Florida Bay behind them.
    "Florida Keys and Bay": [(-80.25, 25.45), (-80.15, 25.15), (-80.6, 24.85), (-81.2, 24.55),
                             (-82.2, 24.5), (-82.2, 24.75), (-81.0, 25.35), (-80.25, 25.45)],
}


@dataclass
class Canal:
    """Canal/estrecho real (fee > 0) o waypoint de navegación (fee = 0)."""
    code: str
    name: str
    lat: float
    lon: float
    canal_fee_eur: float = 0.0
    transit_hours: float = 0.0


# Canales tolled (SCA 2024 / PCA 2024) + ~47 waypoints de navegación en mar abierto.
# Los WP_* tienen fee y tiempo = 0; existen para evitar que la línea recta entre
# puertos cruce tierra y fragmente el grafo. Mover uno puede desconectar un corredor.
GLOBAL_CANALS: List[Canal] = [
    Canal("SUEZ", "Suez Canal", 30.6, 32.3, 650_000, 14),
    Canal("PANA", "Panama Canal", 9.08, -79.68, 450_000, 12),
    Canal("GIBR", "Strait of Gibraltar", 35.95, -5.6, 0, 2),
    Canal("BOSP", "Bosphorus Strait", 41.1, 29.05, 0, 3),
    Canal("MALA", "Strait of Malacca", 2.5, 101.5, 0, 12),
    Canal("GBE",  "Bab-el-Mandeb", 12.6, 43.4, 0, 4),
    Canal("DENM", "Danish Straits", 55.5, 12.5, 0, 6),
    Canal("DOVR", "Strait of Dover", 51.0, 1.4, 0, 2),
    Canal("HOPE", "Cape of Good Hope", -34.35, 18.47, 0, 6),
    Canal("HORN", "Cape Horn", -55.98, -67.28, 0, 8),

    # Mediterráneo W → Gibraltar (WP_BALE evita que la línea Valencia→sur de Iberia corte la Península).
    Canal("WP_BALE", "Balearic Sea",            lat=38.5,  lon=0.7),
    Canal("WP_SESP", "SE Spain",          lat=37.0,  lon=-0.5),
    Canal("WP_ALBO", "Alboran Sea",        lat=36.2,  lon=-2.5),
    # Mediterráneo central y oriental.
    Canal("WP_TYRR", "Tyrrhenian Sea",           lat=39.5,  lon=11.0),
    Canal("WP_SICI", "Strait of Sicily",      lat=37.0,  lon=11.5),
    Canal("WP_MALT", "South of Malta",         lat=35.5,  lon=14.5),
    Canal("WP_IONI", "Ionian Sea",            lat=36.5,  lon=18.0),
    Canal("WP_MATA", "South of Peloponnese",    lat=35.8,  lon=22.0),
    Canal("WP_AEGE", "Aegean Sea",              lat=38.0,  lon=25.0),
    Canal("WP_NAEG", "North Aegean Sea",lat=39.5,  lon=25.0),
    Canal("WP_CRET", "South of Crete",          lat=34.0,  lon=25.0),
    Canal("WP_LEVA", "Eastern Mediterranean", lat=33.8,  lon=30.0),
    Canal("WP_MARM", "Sea of Marmara",        lat=40.4,  lon=26.9),
    Canal("WP_DARD", "Dardanelles",            lat=40.1,  lon=26.1),
    # Ligurian Sea: keeps Valencia/Barcelona → Genoa off the Ligurian coast on the great circle.
    Canal("WP_LIGU", "Ligurian Sea",           lat=43.2,  lon=7.0),
    # Myrtoan Sea, east of Cape Maleas: approach to Piraeus through the Saronic Gulf.
    Canal("WP_MALE", "East of Cape Maleas",    lat=36.3,  lon=23.7),
    # Atlántico ibérico.
    Canal("WP_CADZ", "Gulf of Cadiz",        lat=36.0,  lon=-8.0),
    Canal("WP_SVIN", "Cape St. Vincent",   lat=36.5,  lon=-10.0),
    Canal("WP_WIBE", "West of Iberia",       lat=40.0,  lon=-11.0),
    Canal("WP_FINI", "Cape Finisterre",       lat=43.5,  lon=-11.0),
    Canal("WP_BISC", "Bay of Biscay",      lat=46.5,  lon=-7.5),
    Canal("WP_USHA", "Ushant",              lat=48.5,  lon=-6.5),
    # Mar del Norte / Báltico.
    Canal("WP_NSEA", "North Sea",         lat=55.0,  lon=3.0),
    Canal("WP_ELB1", "Lower Elbe",             lat=53.85, lon=9.0),
    Canal("WP_ELB2", "Elbe Estuary",lat=53.95, lon=8.5),
    Canal("WP_GBIG", "German Bight",         lat=54.25, lon=7.8),
    Canal("WP_SKAG", "Skagerrak",             lat=57.8,  lon=8.5),
    Canal("WP_SKGN", "Skagen Point",           lat=57.9,  lon=10.9),
    Canal("WP_KATT", "Kattegat",              lat=56.5,  lon=11.5),
    # Atlántico abierto.
    Canal("WP_NATL", "North Atlantic",       lat=46.0,  lon=-30.0),
    Canal("WP_WATL", "West Atlantic",       lat=39.5,  lon=-65.0),
    Canal("WP_CVRD", "Cape Verde",            lat=14.0,  lon=-28.0),
    Canal("WP_RIOJ", "Off Rio de Janeiro",     lat=-24.5, lon=-42.0),
    Canal("WP_SATL", "South Atlantic",         lat=-12.0, lon=-22.0),
    # Mar Rojo / Suez (saltos cortos para que el nodo SUEZ no quede aislado).
    Canal("WP_GSUE", "Gulf of Suez (north)", lat=29.5,  lon=32.65),
    Canal("WP_GSUS", "Gulf of Suez (south)",   lat=28.4,  lon=33.3),
    Canal("WP_REDN", "North Red Sea",        lat=24.0,  lon=36.0),
    Canal("WP_REDS", "South Red Sea",          lat=18.0,  lon=40.0),
    Canal("WP_ADEN", "Gulf of Aden",         lat=12.5,  lon=48.0),
    # Océano Índico.
    Canal("WP_ARAB", "Arabian Sea",           lat=13.0,  lon=62.0),
    Canal("WP_INDO", "Indian Ocean",         lat=-2.0,  lon=78.0),
    Canal("WP_ACEH", "NW Sumatra",   lat=7.5,   lon=93.5),
    Canal("WP_MALN", "NW Malacca Strait", lat=6.5, lon=96.5),
    # Cabo de Buena Esperanza.
    Canal("WP_SOAF", "SW Africa",    lat=-34.0, lon=16.0),
    Canal("WP_SIND", "South Indian Ocean",        lat=-37.0, lon=55.0),
    # Golfo Pérsico.
    Canal("WP_PGLF", "Persian Gulf",         lat=26.0,  lon=55.8),
    Canal("WP_HORM", "Strait of Hormuz",     lat=26.5,  lon=56.5),
    Canal("WP_OMAN", "Gulf of Oman",         lat=23.0,  lon=61.0),
    # Sudeste asiático / China.
    Canal("WP_SCSE", "South China Sea", lat=6.0, lon=106.0),
    Canal("WP_LUZO", "Luzon Strait",     lat=20.0,  lon=121.0),
    Canal("WP_ECHN", "East China Sea", lat=26.0,  lon=123.0),
    Canal("WP_YANG", "Yangtze Approach", lat=31.0, lon=123.0),
    # Caribe / Golfo de México (cadena FLAE → FLAS → GULF rodea Florida por los Estrechos).
    Canal("WP_MONA", "Mona Passage",           lat=18.3,  lon=-67.8),
    Canal("WP_CARI", "Caribbean Sea",            lat=14.0,  lon=-68.0),
    Canal("WP_FLAE", "Atlantic east of Florida", lat=28.0, lon=-79.6),
    Canal("WP_FLAN", "Northern Straits of Florida (Miami-Bimini)", lat=26.0, lon=-79.8),
    Canal("WP_FLAM", "Straits of Florida (off Key Largo)", lat=24.7, lon=-80.0),
    Canal("WP_FLAS", "Straits of Florida (south of the Keys)", lat=24.0, lon=-82.0),
    Canal("WP_GULF", "Gulf of Mexico",       lat=27.0,  lon=-88.0),
]


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia ortodrómica en millas náuticas."""
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lon2 - lon1)
    a = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
    return EARTH_RADIUS_NM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def great_circle_points(lat1: float, lon1: float, lat2: float, lon2: float,
                        n: int) -> List[Tuple[float, float]]:
    """n+1 (lon, lat) points along the great circle from A to B, endpoints included."""
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    d = 2 * math.asin(math.sqrt(math.sin((p2 - p1) / 2) ** 2
                                + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2))
    if d < 1e-12:
        return [(lon1, lat1), (lon2, lat2)]
    pts = []
    for i in range(n + 1):
        f = i / n
        a, b = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
        x = a * math.cos(p1) * math.cos(l1) + b * math.cos(p2) * math.cos(l2)
        y = a * math.cos(p1) * math.sin(l1) + b * math.cos(p2) * math.sin(l2)
        z = a * math.sin(p1) + b * math.sin(p2)
        pts.append((math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))))
    return pts


def _open_sea_arc(lat1: float, lon1: float, lat2: float, lon2: float) -> List[Tuple[float, float]]:
    """Great-circle points of an edge, minus those within ENDPOINT_TRIM_DEG (angular) of each end."""
    dist = haversine_nm(lat1, lon1, lat2, lon2)
    pts = great_circle_points(lat1, lon1, lat2, lon2, max(12, int(dist / 50)))
    trim_nm = ENDPOINT_TRIM_DEG * 60.0                      # 1° of arc = 60 NM
    return [(lon, lat) for lon, lat in pts
            if haversine_nm(lat, lon, lat1, lon1) >= trim_nm
            and haversine_nm(lat, lon, lat2, lon2) >= trim_nm]


def _line_length_nm(geom) -> float:
    """Great-circle length (NM) of a (Multi)LineString in lon/lat degrees."""
    if geom.is_empty:
        return 0.0
    lines = getattr(geom, "geoms", [geom])
    total = 0.0
    for line in lines:
        coords = list(getattr(line, "coords", []))
        total += sum(haversine_nm(a[1], a[0], b[1], b[0]) for a, b in zip(coords, coords[1:], strict=False))
    return total


class LandDetector:
    """Decides whether a graph edge is navigable (GeoPandas/Shapely, pure-Python bbox fallback).

    Rule: the great-circle arc of the edge, minus ENDPOINT_TRIM_DEG around each end, may cross
    at most MAX_LAND_CROSSING_NM of mapped land (small islands and 1:50M coastline imprecision)
    and must not touch any NO_GO_AREAS polygon (shallow banks).
    """

    def __init__(self):
        self.tree = None
        self.land_parts = []
        self.no_go = None
        self.using_geopandas = False
        if HAS_GEOPANDAS and LAND_SHP.exists():
            try:
                _import_geo_libs()
                from shapely.geometry import Polygon
                parts = gpd.read_file(LAND_SHP).explode(index_parts=False).geometry
                self.land_parts = list(parts)
                self.tree = shapely.STRtree(self.land_parts)
                self.no_go = shapely.union_all([Polygon(p) for p in NO_GO_AREAS.values()])
                shapely.prepare(self.no_go)
                self.using_geopandas = True
            except Exception as e:
                print(f"[routing] Warning: could not load Natural Earth ({e}); bbox fallback.")

    def crosses_land(self, lat1: float, lon1: float,
                     lat2: float, lon2: float) -> bool:
        if self.using_geopandas:
            return self._crosses_land_geometric(lat1, lon1, lat2, lon2)
        return self._crosses_land_fallback(lat1, lon1, lat2, lon2)

    def land_crossing_nm(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Nautical miles of mapped land crossed by the trimmed great-circle arc of an edge."""
        total = 0.0
        for line in self._arc_parts(lat1, lon1, lat2, lon2):
            for idx in self.tree.query(line, predicate="intersects"):
                # Clip the polygon to the line's bounding box first: continents have ~10^4 vertices.
                local = shapely.clip_by_rect(self.land_parts[idx], *line.bounds)
                total += _line_length_nm(local.intersection(line))
        return total

    def _arc_parts(self, lat1, lon1, lat2, lon2) -> list:
        """Trimmed great-circle arc as LineStrings, split at the antimeridian."""
        pts = _open_sea_arc(lat1, lon1, lat2, lon2)
        parts, current = [], pts[:1]
        for prev, cur in zip(pts, pts[1:], strict=False):
            if abs(cur[0] - prev[0]) > 180:          # antimeridian: start a new part
                if len(current) > 1:
                    parts.append(LineString(current))
                current = []
            current.append(cur)
        if len(current) > 1:
            parts.append(LineString(current))
        return parts

    def _crosses_land_geometric(self, lat1, lon1, lat2, lon2) -> bool:
        parts = self._arc_parts(lat1, lon1, lat2, lon2)
        if not parts:
            return False          # both ends inside the trimmed zones: adjacent nodes
        if any(self.no_go.intersects(line) for line in parts):
            return True
        return self.land_crossing_nm(lat1, lon1, lat2, lon2) > MAX_LAND_CROSSING_NM

    def _crosses_land_fallback(self, lat1, lon1, lat2, lon2) -> bool:
        """Fallback puro-Python con bounding boxes continentales (menos preciso)."""
        # Cajas continentales conservadoras; dejan libres Mediterráneo, Mar Rojo, Báltico, Mar del Norte.
        LAND_BOXES = [
            (20, 33, -10, 10), (22, 30, 10, 25), (22, 30, 25, 32),
            (28.7, 30.7, 33.2, 34.4),    # Sinaí (bloquea Med↔Mar Rojo sin canal)
            (-35, 18, -15, 6), (-35, 18, 18, 30), (-35, 10, 30, 38),
            (17, 28, 43, 48), (28, 31, 35, 48),
            (48, 70, 15, 40), (45, 75, 40, 170),
            (25, 45, 52, 90), (20, 40, 75, 140),
            (30, 48, -105, -82), (50, 70, -140, -70), (18, 30, -108, -90),
            (-40, 8, -72, -45), (-35, -12, 118, 145),
        ]
        for lon, lat in _open_sea_arc(lat1, lon1, lat2, lon2):
            for lat_min, lat_max, lon_min, lon_max in LAND_BOXES:
                if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
                    return True
        return False


_land_detector: Optional[LandDetector] = None


def get_land_detector() -> LandDetector:
    """Singleton del LandDetector."""
    global _land_detector
    if _land_detector is None:
        _land_detector = LandDetector()
    return _land_detector


def crosses_land(p1: Port, p2: Port) -> bool:
    return get_land_detector().crosses_land(p1.lat, p1.lon, p2.lat, p2.lon)


@dataclass
class EdgeSpec:
    """Arista candidata del grafo marítimo."""
    origin: str
    destination: str
    distance_nm: float
    canal_fee_eur: float = 0.0
    transit_hours_extra: float = 0.0
    via_canal: Optional[str] = None


def _canal_as_port(canal: Canal) -> Port:
    return Port(code=canal.code, name=canal.name, country="(canal/strait)",
                lat=canal.lat, lon=canal.lon, is_eu_eea=False, is_tent_t=False)


def build_maritime_network(
    ports: Dict[str, Port],
    max_direct_distance_nm: float = 6_000,
    max_port_to_canal_nm: float = 8_000,
) -> Tuple[Dict[str, Port], List[EdgeSpec]]:
    """Genera nodos (puertos + canales/waypoints) y aristas cuya línea recta no cruza tierra."""
    all_nodes: Dict[str, Port] = dict(ports)
    for canal in GLOBAL_CANALS:
        all_nodes[canal.code] = _canal_as_port(canal)
    canals_by_code = {c.code: c for c in GLOBAL_CANALS}

    edges: List[EdgeSpec] = []
    node_codes = list(all_nodes.keys())
    detector = get_land_detector()

    for i, code_a in enumerate(node_codes):
        node_a = all_nodes[code_a]
        is_canal_a = code_a in canals_by_code
        for j in range(i + 1, len(node_codes)):
            code_b = node_codes[j]
            node_b = all_nodes[code_b]
            is_canal_b = code_b in canals_by_code

            # Filtro barato (distancia) antes del caro (cruce de tierra); ambos deben cumplirse.
            distance = (haversine_nm(node_a.lat, node_a.lon,
                                     node_b.lat, node_b.lon)
                        * ROUTE_CORRECTION_FACTOR)
            max_dist = max_port_to_canal_nm if (is_canal_a or is_canal_b) else max_direct_distance_nm
            if distance > max_dist:
                continue
            if detector.crosses_land(node_a.lat, node_a.lon,
                                     node_b.lat, node_b.lon):
                continue

            # geo_module asigna el peaje según dirección; aquí solo marcamos via_canal/fee.
            fee = transit_extra = 0.0
            via_canal = None
            if is_canal_a and is_canal_b:
                ca, cb = canals_by_code[code_a], canals_by_code[code_b]
                via_canal = (ca if ca.canal_fee_eur >= cb.canal_fee_eur else cb).code
            elif is_canal_a or is_canal_b:
                canal = canals_by_code[code_a] if is_canal_a else canals_by_code[code_b]
                fee, transit_extra, via_canal = canal.canal_fee_eur, canal.transit_hours, canal.code

            edges.append(EdgeSpec(origin=code_a, destination=code_b,
                                  distance_nm=distance,
                                  canal_fee_eur=fee,
                                  transit_hours_extra=transit_extra,
                                  via_canal=via_canal))
    return all_nodes, edges


def get_canal_info(code: str) -> Optional[Canal]:
    """Devuelve el Canal por código (None si no existe)."""
    for c in GLOBAL_CANALS:
        if c.code == code:
            return c
    return None


def is_canal_node(code: str) -> bool:
    """¿Es alguno de los nodos canal/waypoint registrados?"""
    return any(c.code == code for c in GLOBAL_CANALS)


def is_waypoint_node(code: str) -> bool:
    """¿Es waypoint de mar abierto (sin peaje ni tiempo de tránsito)?"""
    c = get_canal_info(code)
    return c is not None and c.canal_fee_eur == 0 and c.transit_hours == 0


def describe_route(path: List[str]) -> str:
    """Ruta legible: sustituye códigos de canal por su nombre."""
    parts = []
    for code in path:
        canal = get_canal_info(code)
        parts.append(canal.name if canal else code)
    return " → ".join(parts)
