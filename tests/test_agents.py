"""Agent layer without network: deterministic tools, MCP server, and the Claude agents with a fake client."""

import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("mcp")

from maritime_optimizer.agents import tools  # noqa: E402
from maritime_optimizer.agents._claude import FALLBACK_BETA, AgentRefusal  # noqa: E402
from maritime_optimizer.agents.mcp_server import server  # noqa: E402
from maritime_optimizer.agents.regulatory import (  # noqa: E402
    CellMatch, Proposal, ProposedChange, build_diff, check_changes, current_tables, propose_update,
)
from maritime_optimizer.agents.report import (  # noqa: E402
    Finding, RegimeStatus, ReportNarrative, build_facts, unverified_numbers, write_report,
)
from maritime_optimizer.scenarios import run_scenario  # noqa: E402


# ------------------------------------------------------------------ fake Anthropic client

class FakeMessages:
    """Records calls and returns canned responses shaped like the SDK's."""

    def __init__(self, parsed=None, final_text="", stop_reason="end_turn", tool_names=()):
        self.parsed, self.final_text, self.stop_reason, self.tool_names = (
            parsed, final_text, stop_reason, tool_names)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(("parse", kwargs))
        return SimpleNamespace(parsed_output=self.parsed, stop_reason=self.stop_reason,
                               stop_details=None, model=kwargs["model"], content=[])

    def tool_runner(self, **kwargs):
        self.calls.append(("tool_runner", kwargs))
        uses = SimpleNamespace(stop_reason="tool_use", model=kwargs["model"],
                               content=[SimpleNamespace(type="tool_use", name=n) for n in self.tool_names])
        final = SimpleNamespace(stop_reason=self.stop_reason, model=kwargs["model"], stop_details=None,
                                content=[SimpleNamespace(type="text", text=self.final_text)])
        return iter([uses, final])


def fake_client(**kwargs):
    messages = FakeMessages(**kwargs)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


# ------------------------------------------------------------------ deterministic tools

def test_tools_summary_and_errors(graph):
    out = tools.optimize_route("SGSIN", "NLRTM", "lr2_lng_tanker", eua_price=120)
    r = out["result"]
    assert out["feasible"] and r["route_ports"][0] == "Singapore"
    assert r["costs_eur"]["total"] == sum(v for k, v in r["costs_eur"].items() if k != "total") \
        or abs(r["costs_eur"]["total"] - sum(v for k, v in r["costs_eur"].items() if k != "total")) <= 5
    assert {"cii", "fueleu", "iso14083_gco2e_per_tkm"} <= r.keys()
    with pytest.raises(ValueError, match="Unknown port"):
        tools.optimize_route("XXXXX", "NLRTM", "lr2_lng_tanker")
    with pytest.raises(ValueError, match="Unknown vessel"):
        tools.optimize_route("SGSIN", "NLRTM", "titanic")
    corr = tools.compare_corridors("SGSIN", "NLRTM", "lr2_lng_tanker")
    assert corr["cheapest"] in ("suez", "cape") and corr["saving_eur"] > 0


# ------------------------------------------------------------------ MCP server

def test_mcp_server_lists_and_calls_tools(graph):
    listed = asyncio.run(server.list_tools())
    assert {t.name for t in listed} == {"list_ports", "list_vessels", "optimize_route", "compare_fuels",
                                        "compare_corridors", "eua_sensitivity"}
    assert all(t.annotations.read_only_hint for t in listed)
    res = asyncio.run(server.call_tool("compare_fuels", {"origin": "FRMRS", "destination": "SGSIN",
                                                         "vessel": "aframax_lng_tanker"}))
    assert not res.is_error
    data = json.loads(res.content[0].text)
    assert set(data["by_fuel"]) == {"HFO", "MGO", "LNG", "BioLNG"}


# ------------------------------------------------------------------ assistant (tool runner)

def test_assistant_uses_tool_runner_with_fallbacks(graph):
    from maritime_optimizer.agents.assistant import TOOLS, ask
    client, msgs = fake_client(final_text="Suez is cheaper by 72,948 EUR.",
                               tool_names=["compare_corridors"])
    answer = ask("Suez or Cape?", client=client)
    kind, kwargs = msgs.calls[0]
    assert kind == "tool_runner" and kwargs["model"] == "claude-opus-5-5"
    assert kwargs["fallbacks"] == "default" and FALLBACK_BETA in kwargs["betas"]
    assert kwargs["tools"] == TOOLS
    assert answer.text.startswith("Suez") and answer.tool_calls == ["compare_corridors"]


def test_assistant_tool_errors_go_back_as_text(graph):
    from maritime_optimizer.agents.assistant import _call
    assert _call(tools.optimize_route, origin="XX", destination="NLRTM", vessel="x").startswith("Error:")


def test_refusal_is_raised(graph):
    from maritime_optimizer.agents.assistant import ask
    client, _ = fake_client(stop_reason="refusal")
    with pytest.raises(AgentRefusal):
        ask("...", client=client)


# ------------------------------------------------------------------ report agent

@pytest.fixture(scope="module")
def e5_rows(graph):
    res = run_scenario("E5", graph)
    return [("Via Suez", res["suez"]), ("Via the Cape", res["cape"])]


def _narrative(summary: str) -> ReportNarrative:
    return ReportNarrative(
        headline="Suez is the cheaper corridor", executive_summary=summary,
        findings=[Finding(title="Toll vs time", detail="The toll is outweighed by ship-days.")],
        regulatory_status=[RegimeStatus(regime="FuelEU Maritime", status="compliant", explanation="LNG.")],
        recommendations=["Keep Suez."], caveats=["Illustrative prices."])


def test_report_facts_and_number_check(e5_rows):
    facts = build_facts("E5", e5_rows)
    saving = facts["comparison"]["cost_above_cheapest"]["Via the Cape"]["eur"]
    total = facts["alternatives"]["Via Suez"]["costs_eur"]["total"]
    assert unverified_numbers(f"The Cape costs {saving:,} EUR more; Suez totals {total / 1e6:.2f}M EUR "
                              "under Regulation (EU) 2023/1805 and MEPC.354(78).", facts) == []
    assert unverified_numbers("Suez saves 123,456 EUR.", facts) == ["123,456"]


def test_report_agent_and_html(graph, e5_rows):
    facts = build_facts("E5", e5_rows)
    saving = facts["comparison"]["cost_above_cheapest"]["Via the Cape"]["eur"]
    client, msgs = fake_client(parsed=_narrative(f"Suez saves {saving:,} EUR. It also saves 999,999 EUR."))
    report = write_report("E5 test", e5_rows, client=client)
    kind, kwargs = msgs.calls[0]
    assert kind == "parse" and kwargs["output_format"] is ReportNarrative and kwargs["fallbacks"] == "default"
    assert report.unverified_numbers == ["999,999"]
    page = report.to_html(graph, e5_rows)
    assert "Suez is the cheaper corridor" in page and "could not be matched" in page
    assert page.count('class="plotly-graph-div"') == 2


# ------------------------------------------------------------------ regulatory agent

SOURCE = """Annex II. The lower calorific value of heavy fuel oil shall be 0.0406 MJ/g.
Article 4. The GHG intensity limit for the period 2025-2029 shall be 89.0 gCO2e/MJ."""


def _change(**over):
    base = dict(csv_file="emission_factors_wtt.csv",
                row_match=[CellMatch(column="fuel_code", value="HFO")], column="lcv_mj_g",
                current_value="0.0405", proposed_value="0.0406", article="Annex II",
                verbatim_quote="The lower calorific value of heavy fuel oil shall be 0.0406 MJ/g.",
                rationale="Amended LCV.")
    base.update(over)
    return ProposedChange(**base)


def test_regulatory_checks_and_diff():
    tables = current_tables()
    assert "fuel_prices.csv" not in tables
    proposal = Proposal(summary="s", applies_from="2027-01-01", changes=[
        _change(),
        _change(current_value="0.9999"),                                         # wrong current value
        _change(verbatim_quote="The LCV of HFO is now 0.0406."),                 # invented quote
        _change(csv_file="fuel_prices.csv"),                                     # protected table
        _change(row_match=[CellMatch(column="fuel_code", value="LNG")]),         # matches 4 engine rows
    ])
    checked = check_changes(proposal, SOURCE, tables)
    assert [c.valid for c in checked] == [True, False, False, False, False]
    assert "current value" in checked[1].errors[0]
    assert "quote not found" in checked[2].errors[0]
    diff = build_diff(checked, tables)
    assert "-HFO,MCI,0.0405," in diff and "+HFO,MCI,0.0406," in diff
    body = [ln for ln in diff.splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---")]
    assert len(body) == 2                                                      # one line replaced


def test_regulatory_agent_never_writes_csvs(tmp_path):
    src = tmp_path / "amendment.txt"
    src.write_text(SOURCE, encoding="utf-8")
    before = {k: v for k, v in current_tables().items()}
    proposal = Proposal(summary="LCV amended", applies_from="2027", changes=[_change()])
    client, msgs = fake_client(parsed=proposal)
    review = propose_update(src, client=client)
    assert current_tables() == before
    assert msgs.calls[0][1]["output_format"] is Proposal
    assert review.checked[0].valid and "+HFO,MCI,0.0406," in review.diff
    assert "passes all checks" in review.to_markdown()


def test_number_check_ignores_standard_terms(e5_rows):
    facts = build_facts("E5", e5_rows)
    assert unverified_numbers("CH4 and N2O are weighted at GWP-100 under Annex IV and ISO 14083.",
                              facts) == []


def test_report_carries_ai_disclaimer(graph, e5_rows):
    client, _ = fake_client(parsed=_narrative("Summary."))
    page = write_report("E5", e5_rows, client=client).to_html(graph, e5_rows)
    assert 'aria-label="AI-generated content"' in page and "AI-generated narrative" in page
    assert page.index("AI-generated content.") < page.index("<h2>Summary</h2>")
