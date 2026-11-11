"""MCP server exposing the optimizer as tools for Claude Desktop, Claude Code or any MCP client.

Run:  maritime-opt-mcp            (stdio transport)

Example Claude Code registration:
    claude mcp add maritime-optimizer -- maritime-opt-mcp

The server never calls a language model: the client's model decides which tool to call,
and every number in the answer comes from the deterministic optimizer.
"""

from typing import List, Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .. import __version__
from . import tools

INSTRUCTIONS = """\
Maritime voyage optimizer under EU ETS, FuelEU Maritime and MARPOL Annex VI (CII/EEXI).
Use port codes from list_ports and vessel names from list_vessels. Costs are in EUR,
distances in nautical miles, emissions in tonnes, ISO 14083 intensity in gCO2e per tonne-km.
Report numbers exactly as returned; do not estimate values the tools did not return.
Vessels are generic examples and prices are illustrative 2026 values (see 'disclaimer')."""

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

server = MCPServer(name="maritime-optimizer", version=__version__, instructions=INSTRUCTIONS)


@server.tool(annotations=READ_ONLY)
def list_ports() -> list:
    """List the ports that can be used as origin or destination (code, name, country, EU/EEA)."""
    return tools.list_ports()


@server.tool(annotations=READ_ONLY)
def list_vessels() -> list:
    """List the example fleet: name, type, deadweight, design speed, fuel and time-charter rate."""
    return tools.list_vessels()


@server.tool(annotations=READ_ONLY)
def optimize_route(origin: str, destination: str, vessel: str, eua_price: float = 65.0,
                   fuel: Optional[str] = None, year: int = 2026, cargo_tons: Optional[float] = None,
                   max_transit_hours: float = 1500.0, avoid: Optional[List[str]] = None,
                   time_charter_eur_day: Optional[float] = None,
                   include_fueleu_penalty: bool = False,
                   epl_max_speed_knots: Optional[float] = None) -> dict:
    """Optimize one voyage: cheapest feasible route and speed with its costs (fuel, EU ETS,
    time charter, tolls), emissions, CII rating, FuelEU status and ISO 14083 intensity.

    Args:
        origin: Origin port code, e.g. ESVLC.
        destination: Destination port code, e.g. NLRTM.
        vessel: Vessel name from list_vessels.
        eua_price: EU ETS allowance price in EUR per tonne CO2e.
        fuel: Override the vessel's fuel (HFO, MGO, LNG, BioLNG, HVO, Methanol_fossil, E-Methanol).
        year: Operating year (FuelEU and CII targets depend on it).
        cargo_tons: Cargo carried, for the ISO 14083 intensity (default 85 % of deadweight).
        max_transit_hours: Contractual maximum transit time in hours.
        avoid: Graph nodes to exclude, e.g. ["SUEZ"] to force the Cape route.
        time_charter_eur_day: Override the vessel's time-charter equivalent rate.
        include_fueleu_penalty: Price the FuelEU deficit (Annex IV penalty) into the optimum.
        epl_max_speed_knots: EEXI engine power limitation expressed as a maximum speed (knots).
    """
    return tools.optimize_route(origin, destination, vessel, eua_price, fuel, year, cargo_tons,
                                max_transit_hours, avoid, time_charter_eur_day, include_fueleu_penalty,
                                epl_max_speed_knots)


@server.tool(annotations=READ_ONLY)
def compare_fuels(origin: str, destination: str, vessel: str, fuels: Optional[List[str]] = None,
                  eua_price: float = 65.0, year: int = 2026, cargo_tons: Optional[float] = None,
                  max_transit_hours: float = 1500.0) -> dict:
    """Optimize the same voyage with several fuels (default HFO, MGO, LNG, BioLNG) and compare
    cost, EU ETS exposure, FuelEU compliance and ISO 14083 footprint."""
    return tools.compare_fuels(origin, destination, vessel, fuels, eua_price, year, cargo_tons,
                               max_transit_hours)


@server.tool(annotations=READ_ONLY)
def compare_corridors(origin: str, destination: str, vessel: str, eua_price: float = 65.0,
                      fuel: Optional[str] = None, year: int = 2026, cargo_tons: Optional[float] = None,
                      max_transit_hours: float = 2500.0) -> dict:
    """Compare the Suez Canal and Cape of Good Hope corridors, each optimized on its own,
    and report which is cheaper and by how much."""
    return tools.compare_corridors(origin, destination, vessel, eua_price, fuel, year, cargo_tons,
                                   max_transit_hours)


@server.tool(annotations=READ_ONLY)
def eua_sensitivity(origin: str, destination: str, vessel: str, prices: Optional[List[float]] = None,
                    fuel: Optional[str] = None, year: int = 2026, cargo_tons: Optional[float] = None,
                    max_transit_hours: float = 1500.0) -> dict:
    """Re-optimize a voyage across EU ETS allowance prices (default 40, 65, 90, 120, 150 EUR/t)
    to show how optimal speed, CO2 and total cost respond to the carbon price."""
    return tools.eua_sensitivity(origin, destination, vessel, prices, fuel, year, cargo_tons,
                                 max_transit_hours)


def main() -> None:
    """Entry point of the ``maritime-opt-mcp`` script (stdio transport)."""
    server.run("stdio")


if __name__ == "__main__":
    main()
