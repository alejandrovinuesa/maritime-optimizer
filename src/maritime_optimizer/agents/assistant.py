"""Natural-language assistant: Claude answers voyage questions by calling the optimizer.

    from maritime_optimizer.agents import ask
    print(ask("Suez or the Cape for the LR2 tanker from Singapore to Rotterdam at 120 EUR/t?").text)

Claude chooses the tools and explains the result; every number comes from the tools.
"""

import json
from dataclasses import dataclass, field
from typing import Any, List, Optional

from anthropic import beta_tool

from . import tools
from ._claude import FALLBACK_KWARGS, MODEL, check_refusal, make_client, text_of

SYSTEM = """\
You are a maritime voyage analyst. You answer questions about routes, speeds, fuels, costs and
regulatory compliance (EU ETS, FuelEU Maritime, MARPOL Annex VI CII, ISO 14083) for a shipping
company, using the optimizer tools.

How to work:
- Get every figure from a tool call. Never estimate or recall a number the tools did not return;
  if a question needs a figure no tool provides, say so.
- Resolve port codes with list_ports and vessel names with list_vessels when unsure.
- When the user compares options (fuels, corridors, carbon prices), call the comparison tool
  rather than several single optimizations.
- Emission scopes: ghg_co2e_tank_to_wake is combustion plus methane slip with GWP weighting
  (no upstream); FuelEU intensity and ISO 14083 are well-to-wake (upstream included).

How to answer:
- Lead with the direct answer, then the two or three figures that support it (cost difference,
  speed, days, CO2, compliance status), then the regulatory reason behind the result.
- Use EUR, nautical miles, knots and tonnes, with the precision the tools return.
- End with one sentence noting that vessels are generic examples and prices are illustrative.
"""


def _call(fn, **kwargs) -> str:
    """Run a tool and return JSON; input errors go back to Claude as text so it can correct them."""
    try:
        return json.dumps(fn(**kwargs), ensure_ascii=False)
    except ValueError as exc:
        return f"Error: {exc}"


@beta_tool
def list_ports() -> str:
    """List the ports that can be used as origin or destination (code, name, country, EU/EEA)."""
    return _call(tools.list_ports)


@beta_tool
def list_vessels() -> str:
    """List the example fleet: name, type, deadweight, design speed, fuel and time-charter rate."""
    return _call(tools.list_vessels)


@beta_tool
def optimize_route(origin: str, destination: str, vessel: str, eua_price: float = 65.0,
                   fuel: Optional[str] = None, year: int = 2026, max_transit_hours: float = 1500.0,
                   avoid: Optional[List[str]] = None, include_fueleu_penalty: bool = False) -> str:
    """Optimize one voyage: cheapest feasible route and speed with costs, emissions, CII rating,
    FuelEU status and ISO 14083 intensity.

    Args:
        origin: Origin port code, e.g. ESVLC.
        destination: Destination port code, e.g. NLRTM.
        vessel: Vessel name from list_vessels.
        eua_price: EU ETS allowance price in EUR per tonne CO2e.
        fuel: Override the vessel's fuel (HFO, MGO, LNG, BioLNG, HVO, Methanol_fossil, E-Methanol).
        year: Operating year.
        max_transit_hours: Contractual maximum transit time in hours.
        avoid: Graph nodes to exclude, e.g. ["SUEZ"].
        include_fueleu_penalty: Price the FuelEU deficit (Annex IV penalty) into the optimum.
    """
    return _call(tools.optimize_route, origin=origin, destination=destination, vessel=vessel,
                 eua_price=eua_price, fuel=fuel, year=year, max_transit_hours=max_transit_hours,
                 avoid=avoid, include_fueleu_penalty=include_fueleu_penalty)


@beta_tool
def compare_fuels(origin: str, destination: str, vessel: str, fuels: Optional[List[str]] = None,
                  eua_price: float = 65.0, year: int = 2026) -> str:
    """Optimize the same voyage with several fuels and compare cost, FuelEU compliance and footprint.

    Args:
        origin: Origin port code.
        destination: Destination port code.
        vessel: Vessel name from list_vessels.
        fuels: Fuels to compare (default HFO, MGO, LNG, BioLNG).
        eua_price: EU ETS allowance price in EUR per tonne CO2e.
        year: Operating year.
    """
    return _call(tools.compare_fuels, origin=origin, destination=destination, vessel=vessel,
                 fuels=fuels, eua_price=eua_price, year=year)


@beta_tool
def compare_corridors(origin: str, destination: str, vessel: str, eua_price: float = 65.0,
                      fuel: Optional[str] = None, year: int = 2026) -> str:
    """Compare the Suez Canal and Cape of Good Hope corridors, each optimized on its own.

    Args:
        origin: Origin port code.
        destination: Destination port code.
        vessel: Vessel name from list_vessels.
        eua_price: EU ETS allowance price in EUR per tonne CO2e.
        fuel: Override the vessel's fuel.
        year: Operating year.
    """
    return _call(tools.compare_corridors, origin=origin, destination=destination, vessel=vessel,
                 eua_price=eua_price, fuel=fuel, year=year)


@beta_tool
def eua_sensitivity(origin: str, destination: str, vessel: str,
                    prices: Optional[List[float]] = None, fuel: Optional[str] = None) -> str:
    """Re-optimize a voyage across EU ETS allowance prices to show how speed, CO2 and cost respond.

    Args:
        origin: Origin port code.
        destination: Destination port code.
        vessel: Vessel name from list_vessels.
        prices: Allowance prices in EUR/t (default 40, 65, 90, 120, 150).
        fuel: Override the vessel's fuel.
    """
    return _call(tools.eua_sensitivity, origin=origin, destination=destination, vessel=vessel,
                 prices=prices, fuel=fuel)


TOOLS = [list_ports, list_vessels, optimize_route, compare_fuels, compare_corridors, eua_sensitivity]


@dataclass
class Answer:
    text: str
    tool_calls: List[str] = field(default_factory=list)
    model: str = MODEL
    input_tokens: int = 0
    output_tokens: int = 0


def ask(question: str, client: Optional[Any] = None, model: str = MODEL,
        max_iterations: int = 12) -> Answer:
    """Answer a voyage question with Claude driving the optimizer tools (Tool Runner)."""
    client = make_client(client)
    runner = client.beta.messages.tool_runner(
        model=model, max_tokens=16000, system=SYSTEM, tools=TOOLS,
        messages=[{"role": "user", "content": question}],
        output_config={"effort": "medium"}, max_iterations=max_iterations, **FALLBACK_KWARGS)
    calls: List[str] = []
    final = None
    tokens_in = tokens_out = 0
    for message in runner:
        final = message
        calls += [b.name for b in message.content if getattr(b, "type", None) == "tool_use"]
        usage = getattr(message, "usage", None)
        tokens_in += getattr(usage, "input_tokens", 0) or 0
        tokens_out += getattr(usage, "output_tokens", 0) or 0
    if final is None:
        raise RuntimeError("The assistant returned no message.")
    check_refusal(final)
    return Answer(text=text_of(final), tool_calls=calls, model=getattr(final, "model", model),
                  input_tokens=tokens_in, output_tokens=tokens_out)
