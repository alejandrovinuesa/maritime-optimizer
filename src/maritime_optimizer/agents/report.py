"""Compliance report agent: deterministic numbers and charts, narrative written by Claude.

The optimizer computes every figure, comparison and chart. Claude receives those facts as JSON
and writes the narrative (summary, findings, regulatory status, recommendations) as structured
output. A checker then flags any number in the narrative that does not match the facts.
"""

import html
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel

from ..graph import MaritimeGraph
from ..models import VoyageResult
from ..viz.dashboard import CSS, DISCLAIMER, PLOTLY_CDN, THEME_JS, _card, _table, _cost_rows, COST_HEADERS
from ..viz.figures import cost_breakdown, route_map
from ._claude import FALLBACK_KWARGS, MODEL, check_refusal, make_client
from .tools import DEFINITIONS, summarize

SYSTEM = """\
You write voyage compliance reports for a shipping company's fleet managers and auditors.
You receive the optimizer's results as JSON facts. Write the narrative of the report.

Rules:
- Use only numbers that appear in the facts, written with the same or less precision
  (2,468,664 may become 2.47M; 11.64 may become 11.6). Do not compute new figures: the
  differences, shares and rankings you may need are already in "comparison".
- Use the metrics exactly as "definitions" describes them; do not attribute a figure to a
  scope (tank-to-wake, well-to-wake, upstream) other than the one defined.
- Qualitative claims ("largest", "main", "most", "driver") must agree with the figures: check
  "cost_shares_percent" before calling any cost item the largest or main one.
- Explain results through their regulatory mechanism: EU ETS coverage (100 % intra-EU, 50 %
  extra-EU), FuelEU Maritime well-to-wake intensity limit, CII rating, ISO 14083 intensity.
- Status per regime: "compliant", "attention" (compliant but close to a limit or exposed to a
  price), "non_compliant", or "info" (a reporting metric with no limit, such as ISO 14083).
- Recommendations must be actionable for an operator (fuel, speed, corridor, pooling, contract).
- Caveats: state the model limits that matter for this case (illustrative prices, generic
  vessels, calm water, single voyage instead of annual figures).
- Plain, precise English. No marketing tone.
"""


class Finding(BaseModel):
    title: str
    detail: str


class RegimeStatus(BaseModel):
    regime: Literal["EU ETS", "FuelEU Maritime", "CII", "ISO 14083"]
    status: Literal["compliant", "attention", "non_compliant", "info"]
    explanation: str


class ReportNarrative(BaseModel):
    headline: str
    executive_summary: str
    findings: List[Finding]
    regulatory_status: List[RegimeStatus]
    recommendations: List[str]
    caveats: List[str]


@dataclass
class Report:
    title: str
    facts: Dict[str, Any]
    narrative: ReportNarrative
    unverified_numbers: List[str] = field(default_factory=list)
    model: str = MODEL
    input_tokens: int = 0
    output_tokens: int = 0

    def to_html(self, graph: MaritimeGraph, rows: Sequence[Tuple[str, VoyageResult]]) -> str:
        return render_html(self, graph, rows)


# ------------------------------------------------------------------ facts

def build_facts(title: str, rows: Sequence[Tuple[str, VoyageResult]]) -> Dict[str, Any]:
    """JSON facts for the model: one summary per alternative plus precomputed comparisons."""
    alts = {label: summarize(r) for label, r in rows}
    facts: Dict[str, Any] = {"title": title, "definitions": DEFINITIONS, "alternatives": alts}
    if len(rows) > 1:
        cheapest = min(rows, key=lambda lr: lr[1].total_cost_eur)
        lowest = min(rows, key=lambda lr: lr[1].iso14083_intensity)
        facts["comparison"] = {
            "cheapest": cheapest[0],
            "lowest_iso14083": lowest[0],
            "fueleu_compliant": [label for label, r in rows if r.fueleu_compliant],
            "cost_above_cheapest": {
                label: {"eur": round(r.total_cost_eur - cheapest[1].total_cost_eur),
                        "percent": round((r.total_cost_eur / cheapest[1].total_cost_eur - 1) * 100, 1)}
                for label, r in rows},
            "differences_vs_cheapest": {
                label: {"transit_days": round((r.total_transit_hours - cheapest[1].total_transit_hours) / 24, 1),
                        "distance_nm": round(r.total_distance_nm - cheapest[1].total_distance_nm),
                        "fuel_t": round(r.total_fuel_tons - cheapest[1].total_fuel_tons),
                        "ghg_co2e_tank_to_wake_t": round(r.total_ghg_co2e_tons - cheapest[1].total_ghg_co2e_tons),
                        "fuel_eur": round(r.total_fuel_cost_eur - cheapest[1].total_fuel_cost_eur),
                        "eu_ets_eur": round(r.total_ets_cost_eur - cheapest[1].total_ets_cost_eur),
                        "time_charter_eur": round(r.total_time_cost_eur - cheapest[1].total_time_cost_eur),
                        "canal_tolls_eur": round(r.total_canal_fees_eur - cheapest[1].total_canal_fees_eur)}
                for label, r in rows if label != cheapest[0]},
            "iso14083_vs_cheapest_percent": {
                label: round((r.iso14083_intensity / cheapest[1].iso14083_intensity - 1) * 100, 1)
                for label, r in rows if cheapest[1].iso14083_intensity},
        }
    for label, r in rows:
        if r.total_cost_eur:
            alts[label]["cost_shares_percent"] = {
                k: round(v / r.total_cost_eur * 100, 1) for k, v in {
                    "fuel": r.total_fuel_cost_eur, "eu_ets": r.total_ets_cost_eur,
                    "time_charter": r.total_time_cost_eur, "canal_tolls": r.total_canal_fees_eur,
                    "fueleu_penalty": r.total_fueleu_penalty_eur}.items()}
    return facts


# ------------------------------------------------------------------ number check

_IGNORE = re.compile(
    r"\b\d{4}/\d+\b|MEPC\.\d+\(\d+\)|ISO\s*\d+|Annex\s+[IVX]+|art(?:icle)?\.?\s*\d+[a-z]*|"
    r"CO2e?|CO₂e?|CH4|CH₄|N2O|N₂O|GWP[-\s]?\d+|reg(?:ulation)?\.?\s*\d+|\bE[1-5]\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*([kKM])?(?![\w])")


def _fact_numbers(obj: Any) -> List[float]:
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in _fact_numbers(v)]
    if isinstance(obj, (list, tuple)):
        return [n for v in obj for n in _fact_numbers(v)]
    return []


def unverified_numbers(text: str, facts: Dict[str, Any]) -> List[str]:
    """Numbers in ``text`` that match no fact within the precision they are written with."""
    known = _fact_numbers(facts)
    cleaned = _IGNORE.sub(" ", text)
    bad = []
    for m in _NUMBER.finditer(cleaned):
        whole, decimals, suffix = m.group(1), m.group(2) or "", m.group(3) or ""
        value = float(whole.replace(",", "") + decimals)
        if not decimals and not suffix and value <= 10:
            continue          # small counts ("two legs", "3 options") are not data claims
        scale = {"k": 1e3, "K": 1e3, "M": 1e6}.get(suffix, 1.0)
        value *= scale
        tol = 0.5 * 10 ** (-(len(decimals) - 1 if decimals else 0)) * scale
        if not any(abs(value - k) <= max(tol, 0.005 * abs(k)) for k in known):
            bad.append(m.group(0).strip())
    return bad


# ------------------------------------------------------------------ agent

def write_report(title: str, rows: Sequence[Tuple[str, VoyageResult]], client: Optional[Any] = None,
                 model: str = MODEL) -> Report:
    """Ask Claude for the narrative of a compliance report on one or more optimized voyages."""
    facts = build_facts(title, rows)
    client = make_client(client)
    response = client.beta.messages.parse(
        model=model, max_tokens=16000, system=SYSTEM, output_format=ReportNarrative,
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": "Facts:\n" + json.dumps(facts, ensure_ascii=False, indent=1)}],
        **FALLBACK_KWARGS)
    check_refusal(response)
    narrative: ReportNarrative = response.parsed_output
    prose = " ".join([narrative.headline, narrative.executive_summary,
                      *(f.title + " " + f.detail for f in narrative.findings),
                      *(s.explanation for s in narrative.regulatory_status),
                      *narrative.recommendations, *narrative.caveats])
    return Report(title=title, facts=facts, narrative=narrative,
                  unverified_numbers=unverified_numbers(prose, facts),
                  model=getattr(response, "model", model),
                  input_tokens=getattr(getattr(response, "usage", None), "input_tokens", 0) or 0,
                  output_tokens=getattr(getattr(response, "usage", None), "output_tokens", 0) or 0)


# ------------------------------------------------------------------ HTML

_STATUS = {"compliant": ("good", "✓", "Compliant"), "attention": ("warn", "!", "Attention"),
           "non_compliant": ("bad", "✕", "Non-compliant"), "info": ("info", "i", "Reporting metric")}

_REPORT_CSS = """
.status.warn i{background:var(--warn)} .status.info i{background:var(--muted)}
.narrative p{max-width:78ch} .narrative li{margin:4px 0;max-width:78ch}
.regime{display:grid;gap:4px} .regime .label{font-weight:600;color:var(--ink)}
.notice{border-left:3px solid var(--warn);padding:8px 12px;background:var(--surface);margin:12px 0}
.ai-notice{border:1px solid var(--border);border-left:3px solid var(--ink2);border-radius:8px;
padding:10px 14px;background:var(--surface);margin:16px 0;font-size:13px;color:var(--ink2);max-width:100ch}
.ai-notice strong{color:var(--ink)}
"""


def ai_notice_html(model: str) -> str:
    """Disclaimer shown at the top of every AI-written report."""
    return (
        '<aside class="ai-notice" role="note" aria-label="AI-generated content">'
        f"<strong>AI-generated content.</strong> The summary, findings, regulatory status, "
        f"recommendations and caveats in this report were written by an AI model "
        f"({html.escape(model)}) from the optimizer's output. Every figure in the charts and tables "
        "is computed deterministically by maritime-optimizer, and the figures quoted in the text are "
        "checked automatically against them, but the wording, interpretations and recommendations "
        "may still contain errors. Review the report before relying on it. It is not legal, "
        "regulatory or commercial advice.</aside>")


def render_html(report: Report, graph: MaritimeGraph, rows: Sequence[Tuple[str, VoyageResult]]) -> str:
    n = report.narrative
    esc = html.escape
    regimes = "".join(
        f'<div class="tile regime"><div class="label">{esc(s.regime)}</div>'
        f'<span class="status {_STATUS[s.status][0]}"><i aria-hidden="true">{_STATUS[s.status][1]}</i>'
        f'{_STATUS[s.status][2]}</span><div class="note">{esc(s.explanation)}</div></div>'
        for s in n.regulatory_status)
    findings = "".join(f"<li><strong>{esc(f.title)}.</strong> {esc(f.detail)}</li>" for f in n.findings)
    recs = "".join(f"<li>{esc(r)}</li>" for r in n.recommendations)
    caveats = "".join(f"<li>{esc(c)}</li>" for c in n.caveats)
    warning = ""
    if report.unverified_numbers:
        warning = ('<p class="notice">Figures in the narrative that could not be matched to the '
                   f'optimizer output: {esc(", ".join(report.unverified_numbers))}. '
                   'Trust the tables and charts.</p>')
    routes = {label: r for label, r in rows}
    cards = [
        _card("Route", "Optimized route(s) on the maritime graph.", route_map(graph, routes),
              _table(["Alternative", "Route"], [[lbl, " → ".join(graph.ports[c].name for c in r.route_ports
                                                                   if not c.startswith("WP_"))]
                                                 for lbl, r in rows]), "map", wide=True),
        _card("Cost breakdown", "Objective Z by term.", cost_breakdown(list(rows)),
              _table(COST_HEADERS, _cost_rows(list(rows))), "cost", wide=True),
    ]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(report.title)}</title><script src="{PLOTLY_CDN}"></script>
<style>{CSS}{_REPORT_CSS}</style></head>
<body><div class="wrap">
<header><div><h1>{esc(report.title)}</h1><p class="sub">{esc(n.headline)}</p></div>
<nav><button id="theme" type="button" onclick="cycleTheme()">Theme: auto</button></nav></header>
{ai_notice_html(report.model)}
<section class="narrative"><h2>Summary</h2><p>{esc(n.executive_summary)}</p>{warning}</section>
<div class="tiles" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr))">{regimes}</div>
<section class="narrative"><h2>Findings</h2><ul>{findings}</ul></section>
<div class="grid">{''.join(cards)}</div>
<section class="narrative"><h2>Recommendations</h2><ul>{recs}</ul>
<h2>Caveats</h2><ul>{caveats}</ul></section>
<footer><p><strong>AI-generated narrative.</strong> Written by {esc(report.model)} from the optimizer's
output; it may not have been reviewed by a person. Every figure in the charts and tables is
computed deterministically by maritime-optimizer.</p>
<p><strong>Data notice.</strong> {esc(DISCLAIMER)}</p></footer>
</div><script>{THEME_JS}</script></body></html>"""
