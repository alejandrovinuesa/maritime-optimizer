"""Command-line interface: ``maritime-opt <command>``.

Commands
--------
route      Optimize one voyage (route + speed) for a vessel.
scenario   Run a demonstration scenario (E1-E5) or all of them.
vessels    List the vessels of the fleet CSV.
ports      List the ports of the graph.
dashboard  Write interactive HTML dashboards for the scenarios (needs the ``viz`` extra).
ask        Ask a question in plain language; Claude answers using the optimizer (``agents`` extra).
report     Compliance report for a scenario: figures by the optimizer, narrative by Claude.
regulatory-update  Propose CSV changes from a new legal text (validated, never auto-applied).
mcp        Run the MCP server on stdio (same as ``maritime-opt-mcp``).
geodata    Download the Natural Earth land polygons (done automatically when missing).
doctor     Check dependencies, cartography, cache and run one test voyage.
"""

import argparse
import json
import sys
import time
from dataclasses import asdict, replace
from enum import Enum
from pathlib import Path
from typing import List, Optional

from . import __version__
from .geodata import ensure_geodata, has_geodata
from .graph import MaritimeGraph, load_ports_from_csv, load_vessels_from_csv, DEFAULT_VESSELS_CSV
from .models import FuelType, VoyageResult
from .optimizer import optimize_voyage
from .paths import USER_DIR
from .reporting import format_canal_comparison, format_comparison_table, format_voyage_report
from .routing import HAS_GEOPANDAS
from .scenarios import SCENARIO_IDS, build_request, run_scenario


def _json_default(obj):
    if isinstance(obj, Enum):
        return obj.value
    return str(obj)


def _to_json(obj) -> str:
    """Serialize VoyageResult(s), lists and dicts of them to JSON."""
    def conv(o):
        if isinstance(o, VoyageResult):
            return asdict(o)
        if isinstance(o, list):
            return [conv(x) for x in o]
        if isinstance(o, dict):
            return {k: conv(v) for k, v in o.items()}
        return o
    return json.dumps(conv(obj), default=_json_default, ensure_ascii=False, indent=2)


def _graph() -> MaritimeGraph:
    if HAS_GEOPANDAS and not has_geodata():
        ensure_geodata()
    return MaritimeGraph()


def _fleet(path: Optional[str]):
    return load_vessels_from_csv(Path(path)) if path else load_vessels_from_csv()


def cmd_route(args) -> int:
    fleet = _fleet(args.fleet)
    if args.vessel not in fleet:
        print(f"Unknown vessel {args.vessel!r}. Available: {', '.join(fleet)}", file=sys.stderr)
        return 2
    vessel = fleet[args.vessel]
    if args.fuel:
        vessel = replace(vessel, fuel_type=FuelType(args.fuel))
    kwargs = dict(origin_port=args.origin.upper(), destination_port=args.destination.upper(),
                  year=args.year, eua_price=args.eua, cargo_tons=args.cargo,
                  max_transit_hours=args.tmax, speed_method=args.speed_method,
                  include_fueleu_penalty=args.fueleu_penalty,
                  fueleu_consecutive_deficit_years=args.deficit_years)
    if args.tce is not None:
        kwargs["time_charter_rate"] = args.tce
    graph = _graph()
    avoid = {c.strip().upper() for c in args.avoid.split(",")} if args.avoid else None
    result = optimize_voyage(graph, build_request(vessel, **kwargs), exclude_nodes=avoid)
    if result is None:
        print("No feasible route (check ports, T_max, speed limits or CII).", file=sys.stderr)
        return 1
    print(_to_json(result) if args.json else format_voyage_report(result))
    return 0


def cmd_scenario(args) -> int:
    graph = _graph()
    ids: List[str] = list(SCENARIO_IDS) if args.id.lower() == "all" else [args.id.upper()]
    out = {}
    for sid in ids:
        res = run_scenario(sid, graph)
        out[sid] = res
        if args.json:
            continue
        print(f"\n### Scenario {sid}")
        if sid in ("E1", "E2"):
            print(format_voyage_report(res) if res else "No feasible route.")
        elif sid == "E3":
            print(format_comparison_table(res, label_key="fuel"))
        elif sid == "E4":
            print(format_comparison_table(res, label_key="price"))
        else:
            print(format_canal_comparison(res))
    if args.json:
        print(_to_json(out if len(ids) > 1 else out[ids[0]]))
    return 0


def cmd_vessels(args) -> int:
    for v in _fleet(args.fleet).values():
        ice = f", ice class {v.ice_class}" if v.ice_class else ""
        print(f"{v.name:24} {v.vessel_type.value:14} {v.fuel_type.value:7} "
              f"{v.dwt:>9,.0f} DWT  {v.design_speed:4.1f} kn  TCE {v.tce_eur_day:>7,.0f} EUR/day{ice}")
    return 0


def cmd_ports(args) -> int:
    for p in sorted(load_ports_from_csv().values(), key=lambda p: p.code):
        eu = "EU/EEA" if p.is_eu_eea else "non-EU"
        print(f"{p.code:6} {p.name:18} {p.country:16} {eu:7} ({p.lat:7.3f}, {p.lon:8.3f})")
    return 0


def cmd_dashboard(args) -> int:
    try:
        from .viz import write_dashboards
    except ImportError:
        print("Dashboards need Plotly: pip install 'maritime-optimizer[viz]'", file=sys.stderr)
        return 2
    _graph()  # make sure the cartography is available before building
    ids = list(SCENARIO_IDS) if args.id.lower() == "all" else [args.id.upper()]
    for path in write_dashboards(Path(args.out), ids):
        print(path)
    return 0


_AGENTS_HINT = "This command needs the agents extra: pip install 'maritime-optimizer[agents]'"
_AUTH_HINT = ("No Anthropic credentials found. Set ANTHROPIC_API_KEY, or run `ant auth login`, "
              "then try again.")


def _agent_errors(fn):
    """Turn credential, refusal and API errors of the agent commands into one-line messages."""
    def wrapper(args) -> int:
        try:
            return fn(args)
        except TypeError as exc:
            if "authentication" not in str(exc).lower():
                raise
            print(_AUTH_HINT, file=sys.stderr)
            return 2
        except Exception as exc:
            name = type(exc).__name__
            if name == "AuthenticationError":
                print(_AUTH_HINT, file=sys.stderr)
            elif name == "AgentRefusal" or (name.endswith("Error")
                                            and type(exc).__module__.startswith("anthropic")):
                print(f"{name}: {exc}", file=sys.stderr)
            else:
                raise
            return 1
    return wrapper


@_agent_errors
def cmd_ask(args) -> int:
    try:
        from .agents.assistant import ask
    except ImportError:
        print(_AGENTS_HINT, file=sys.stderr)
        return 2
    answer = ask(args.question, model=args.model)
    print(answer.text)
    if args.verbose:
        print(f"\n[tools called: {', '.join(answer.tool_calls) or 'none'} | model: {answer.model} | "
              f"tokens in/out: {answer.input_tokens:,}/{answer.output_tokens:,}]", file=sys.stderr)
    return 0


@_agent_errors
def cmd_report(args) -> int:
    try:
        from .agents.report import write_report
        from .viz.dashboard import TITLES
    except ImportError:
        print(_AGENTS_HINT, file=sys.stderr)
        return 2
    graph = _graph()
    sid = args.id.upper()
    res = run_scenario(sid, graph)
    if sid in ("E1", "E2"):
        rows = [("Optimal voyage", res)]
    elif sid == "E3":
        rows = [(r.vessel.fuel_type.value, r) for r in res]
    elif sid == "E4":
        rows = [(f"EUA {r.eua_price:g} EUR/t", r) for r in res]
    else:
        rows = [(name, r) for name, r in (("Via Suez", res["suez"]), ("Via the Cape", res["cape"])) if r]
    title = f"{sid} · {TITLES[sid][0]}"
    report = write_report(title, rows, model=args.model)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.to_html(graph, rows), encoding="utf-8")
    print(f"{out}  [model: {report.model} | tokens in/out: "
          f"{report.input_tokens:,}/{report.output_tokens:,}]")
    if report.unverified_numbers:
        print("Unverified figures in the narrative: " + ", ".join(report.unverified_numbers),
              file=sys.stderr)
    return 0


@_agent_errors
def cmd_regulatory_update(args) -> int:
    try:
        from .agents.regulatory import propose_update
    except ImportError:
        print(_AGENTS_HINT, file=sys.stderr)
        return 2
    review = propose_update(Path(args.source), model=args.model)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "proposal.md").write_text(review.to_markdown(), encoding="utf-8")
    if review.diff:
        (out_dir / "proposal.diff").write_text(review.diff, encoding="utf-8")
    valid = sum(c.valid for c in review.checked)
    print(f"{valid} of {len(review.checked)} proposed changes passed the checks. "
          f"Review {out_dir / 'proposal.md'}" + (f" and apply {out_dir / 'proposal.diff'} with git apply."
                                               if review.diff else "."))
    return 0


def cmd_mcp(args) -> int:
    try:
        from .agents.mcp_server import main as mcp_main
    except ImportError:
        print(_AGENTS_HINT, file=sys.stderr)
        return 2
    mcp_main()
    return 0


def cmd_geodata(args) -> int:
    return 0 if ensure_geodata() else 1


def cmd_doctor(args) -> int:
    ok = True
    print(f"maritime-optimizer {__version__} | Python {sys.version.split()[0]}")
    print(f"User data dir: {USER_DIR}")
    print(f"GeoPandas/Shapely installed: {HAS_GEOPANDAS}")
    if HAS_GEOPANDAS and not has_geodata():
        ok &= ensure_geodata()
    print(f"Natural Earth land polygons: {'OK' if has_geodata() else 'MISSING (bbox fallback)'}")
    t0 = time.perf_counter()
    graph = MaritimeGraph()
    print(f"{graph.summary()} ({time.perf_counter() - t0:.2f} s)")
    vessel = load_vessels_from_csv()["panamax_bulker"]
    r = optimize_voyage(graph, build_request(vessel, origin_port="ESVLC",
                                             destination_port="NLRTM"))
    ok &= r is not None
    print("Test voyage ESVLC→NLRTM: " + (f"OK, {r.total_cost_eur:,.0f} EUR" if r else "FAILED"))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="maritime-opt", description=__doc__.split("\n")[0])
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("route", help="optimize one voyage")
    r.add_argument("origin", help="origin port code, e.g. ESVLC")
    r.add_argument("destination", help="destination port code, e.g. NLRTM")
    r.add_argument("--vessel", required=True, help="vessel name from the fleet CSV")
    r.add_argument("--fleet", help=f"fleet CSV (default: {DEFAULT_VESSELS_CSV.name})")
    r.add_argument("--fuel", choices=[f.value for f in FuelType], help="override the vessel fuel")
    r.add_argument("--year", type=int, default=2026)
    r.add_argument("--eua", type=float, default=65.0, help="EU ETS allowance price, EUR/t")
    r.add_argument("--cargo", type=float, default=50_000, help="cargo mass, t")
    r.add_argument("--tmax", type=float, default=720, help="maximum transit time, h")
    r.add_argument("--tce", type=float, help="time charter equivalent, EUR/day (default: vessel)")
    r.add_argument("--avoid", help="comma-separated graph nodes to exclude, e.g. SUEZ")
    r.add_argument("--speed-method", choices=["analytic", "golden"], default="analytic")
    r.add_argument("--fueleu-penalty", action="store_true",
                   help="price the FuelEU deficit (Annex IV penalty) into the objective")
    r.add_argument("--deficit-years", type=int, default=1,
                   help="consecutive FuelEU deficit periods, for the penalty multiplier")
    r.add_argument("--json", action="store_true", help="print the full result as JSON")
    r.set_defaults(func=cmd_route)

    s = sub.add_parser("scenario", help="run a demonstration scenario")
    s.add_argument("id", help=f"one of {', '.join(SCENARIO_IDS)} or 'all'")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scenario)

    v = sub.add_parser("vessels", help="list fleet vessels")
    v.add_argument("--fleet")
    v.set_defaults(func=cmd_vessels)

    sub.add_parser("ports", help="list ports").set_defaults(func=cmd_ports)
    d = sub.add_parser("dashboard", help="write HTML dashboards")
    d.add_argument("id", help=f"one of {', '.join(SCENARIO_IDS)} or 'all'")
    d.add_argument("--out", default="dashboards", help="output directory (default: ./dashboards)")
    d.set_defaults(func=cmd_dashboard)

    a = sub.add_parser("ask", help="ask a question; Claude answers with the optimizer")
    a.add_argument("question")
    a.add_argument("--model", default="claude-opus-5-5")
    a.add_argument("-v", "--verbose", action="store_true", help="show the tools Claude called")
    a.set_defaults(func=cmd_ask)

    rp = sub.add_parser("report", help="compliance report for a scenario (narrative by Claude)")
    rp.add_argument("id", help=f"one of {', '.join(SCENARIO_IDS)}")
    rp.add_argument("--out", default="report.html")
    rp.add_argument("--model", default="claude-opus-5-5")
    rp.set_defaults(func=cmd_report)

    ru = sub.add_parser("regulatory-update", help="propose CSV changes from a new legal text")
    ru.add_argument("source", help="text, HTML or PDF of the new act or amendment")
    ru.add_argument("--out-dir", default="regulatory-proposal")
    ru.add_argument("--model", default="claude-opus-5-5")
    ru.set_defaults(func=cmd_regulatory_update)

    sub.add_parser("mcp", help="run the MCP server on stdio").set_defaults(func=cmd_mcp)
    sub.add_parser("geodata", help="download cartography").set_defaults(func=cmd_geodata)
    sub.add_parser("doctor", help="check the installation").set_defaults(func=cmd_doctor)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    # Reports use Unicode (CO₂, →, ✓); legacy Windows code pages (cp1252) cannot encode them.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    return args.func(args)
