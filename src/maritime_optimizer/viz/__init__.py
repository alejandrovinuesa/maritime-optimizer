"""Charts and dashboards (requires the ``viz`` extra: ``pip install maritime-optimizer[viz]``)."""

from .dashboard import build_dashboard, write_dashboards
from .figures import cost_breakdown, hbar, line_xy, route_map

__all__ = ["build_dashboard", "cost_breakdown", "hbar", "line_xy", "route_map", "write_dashboards"]
