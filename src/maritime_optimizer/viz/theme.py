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
    "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
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
    "series": ["#3987e5", "#d95926", "#199e70", "#c98500",
               "#d55181", "#008300", "#9085e9", "#e66767"],
}

# Status colours are reserved for good/bad meaning and always ship with an icon + label.
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}

# Fixed entity → slot assignments, shared by every chart.
COST_SLOTS = {"Fuel": 0, "EU ETS": 1, "Time charter": 2, "Canal tolls": 3, "FuelEU penalty": 4}
CORRIDOR_SLOTS = {"suez": 0, "cape": 1}

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
