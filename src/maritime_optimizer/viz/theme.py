"""Colour tokens for charts and dashboards (light and dark mode).

Categorical slots follow a fixed order validated for colour-vision deficiency on
adjacent pairs (stacks, bars, lines). Assign them by the *entity* they represent,
never by rank, so a series keeps its colour across every chart.
"""

LIGHT = {
    "surface": "#fcfcfb",
    "page": "#f9f9f7",
    "ink": "#0b0b0b",
    "ink2": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "land": "#e9e8e2",
    "ocean": "#fcfcfb",
    "coast": "#c3c2b7",
    "border": "rgba(11,11,11,0.10)",
    "series": ["#2a78d6", "#eb6834", "#1baf7a", "#b58900",
               "#e87ba4", "#008300", "#6250d6", "#e34948"],
}

DARK = {
    "surface": "#1a1a19",
    "page": "#0d0d0d",
    "ink": "#f0efec",
    "ink2": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "axis": "#383835",
    "land": "#2c2c2a",
    "ocean": "#1a1a19",
    "coast": "#4a4a46",
    "border": "rgba(255,255,255,0.10)",
    "series": ["#3987e5", "#d95926", "#199e70", "#e2c044",
               "#d55181", "#008300", "#9085e9", "#e66767"],
}

# Navy dark theme of the Streamlit app: same validated series as DARK (they pass the lightness,
# chroma, CVD and contrast checks against this surface too), on blue-black surfaces.
NAVY = {
    "surface": "#111d33",
    "page": "#0b1424",
    "sidebar": "#08101e",
    "ink": "#f2f5fa",
    "ink2": "#b9c4d8",
    "muted": "#8391ab",
    "grid": "#1e2c47",
    "axis": "#2b3c5e",
    "land": "#1a2944",
    "ocean": "#0d1830",
    "coast": "#3a4f78",
    "border": "#22314f",
    "series": DARK["series"],
}

# Status colours are reserved for good/bad meaning and always ship with an icon + label.
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}

# Route colours: one pair for "optimal" and "alternative", never reused for anything else.
# Cost categories use a disjoint set of slots (orange, pink, green, amber, red), so a colour
# means one thing on every chart: blue/purple are routes, the rest are cost terms.
ROUTE_SLOTS = {"optimal": 0, "alternative": 6}
COST_SLOTS = {"Fuel": 1, "EU ETS": 4, "Time charter": 2, "Canal tolls": 3, "FuelEU penalty": 7}
# The static dashboards colour by corridor (they have no "optimal" notion), from the route pair.
CORRIDOR_SLOTS = {"suez": ROUTE_SLOTS["optimal"], "cape": ROUTE_SLOTS["alternative"]}

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
