"""Streamlit app: voyage tab, and the bring-your-own-key AI features (no real API calls)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")


def run_app(**state):
    at = AppTest.from_file(APP, default_timeout=180)
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def texts(elements):
    return " ".join(str(e.value) for e in elements)


def test_voyage_tab_renders_without_key(graph, monkeypatch):
    monkeypatch.delenv("MARITIME_OPT_ALLOW_SERVER_KEY", raising=False)
    at = run_app()
    assert not at.exception
    assert [m.label for m in at.metric][:2] == ["Cost", "Speed"]
    assert "Enter your Anthropic API key" in texts(at.info)
    assert at.chat_input[0].disabled


def test_server_key_is_ignored_without_opt_in(graph, monkeypatch):
    """A deployer's ANTHROPIC_API_KEY must never be spent on visitors by default."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-deployer-test")
    monkeypatch.delenv("MARITIME_OPT_ALLOW_SERVER_KEY", raising=False)
    at = run_app()
    assert "Enter your Anthropic API key" in texts(at.info) and at.chat_input[0].disabled
    monkeypatch.setenv("MARITIME_OPT_ALLOW_SERVER_KEY", "1")
    at = run_app()
    assert not at.chat_input[0].disabled


def test_report_and_chat_use_the_session_key(graph, monkeypatch):
    import maritime_optimizer.agents.assistant as assistant
    import maritime_optimizer.agents.report as report_mod
    from maritime_optimizer.agents.report import Report, ReportNarrative, RegimeStatus

    seen = []

    def fake_report(title, rows, client=None):
        seen.append(client.api_key)
        narrative = ReportNarrative(
            headline="Valencia to Rotterdam is compliant", executive_summary="Summary.",
            findings=[], recommendations=["Keep the speed."], caveats=["Generic vessel."],
            regulatory_status=[RegimeStatus(regime="CII", status="compliant", explanation="Rating C.")])
        return Report(title=title, facts={}, narrative=narrative, input_tokens=1000, output_tokens=200)

    def fake_ask(question, client=None, history=None, context=None):
        seen.append(client.api_key)
        assert '"origin": "ESVLC"' in context
        return SimpleNamespace(text=f"Answer to: {question}", tool_calls=["optimize_route"],
                               input_tokens=10, output_tokens=5)

    monkeypatch.setattr(report_mod, "write_report", fake_report)
    monkeypatch.setattr(assistant, "ask", fake_ask)
    at = run_app(anthropic_key="sk-ant-user-test")
    next(b for b in at.button if b.label == "Generate AI report").click().run()
    assert not at.exception
    assert "Valencia to Rotterdam is compliant" in texts(at.markdown)
    assert "AI-generated content" in texts(at.warning)
    at.chat_input[0].set_value("Is Suez cheaper?").run()
    assert "Answer to: Is Suez cheaper?" in texts(at.markdown)
    assert seen == ["sk-ant-user-test", "sk-ant-user-test"]


def test_forget_key_clears_it(graph, monkeypatch):
    monkeypatch.delenv("MARITIME_OPT_ALLOW_SERVER_KEY", raising=False)
    at = run_app(anthropic_key="sk-ant-user-test")
    assert not at.chat_input[0].disabled
    next(b for b in at.button if b.label == "Forget key").click().run()
    assert not at.exception
    assert at.session_state["anthropic_key"] == "" and at.chat_input[0].disabled



def test_fueleu_deficit_note_and_out_of_scope_voyage(graph, monkeypatch):
    at = run_app()                                   # Valencia → Rotterdam on HFO: deficit
    note = texts(at.warning)
    assert "estimated penalty" in note and "Pooling, banking and borrowing" in note
    at = run_app(origin="AEJEA", destination="BRSSZ")  # no EU port: FuelEU does not apply
    fueleu = next(m.value for m in at.markdown if "mo-kpi" in m.value and ">FuelEU<" in m.value)
    assert "Not applicable" in fueleu and "estimated penalty" not in texts(at.warning)


def test_headline_and_status_badges(graph):
    at = run_app()                                   # Valencia → Rotterdam on HFO
    markdown = [m.value for m in at.markdown]
    headline = next(m for m in markdown if m.startswith('<div class="mo-headline"'))
    assert "Optimal route" in headline and "EUR" in headline
    assert any("mo-badge" in m and "Deficit" in m for m in markdown)       # FuelEU deficit badge
    assert any("mo-badge" in m and "· " in m and ">CII rating<" in m for m in markdown)
    at = run_app(origin="ESVLC", destination="SGSIN")
    at.sidebar.toggle[0].set_value(True).run()       # Suez vs Cape: headline states the saving
    headline = next(m.value for m in at.markdown if m.value.startswith('<div class="mo-headline"'))
    assert "saves" in headline and " days vs " in headline and "%" in headline


def test_query_params_preset_the_scenario(graph):
    at = AppTest.from_file(APP, default_timeout=180)
    at.query_params.update(origin="ESVLC", destination="SGSIN", compare="1", hours="2500",
                           fuel="LNG", vessel="not-a-vessel")
    at.run()
    assert not at.exception
    hero = next(m.value for m in at.markdown if m.value.startswith('<div class="mo-hero"'))
    assert "Singapore" in hero and "LNG" in hero and "2 500 h" in hero and "104.2 days" in hero
    assert at.sidebar.toggle[0].value is True                     # Suez vs Cape comparison on
    assert at.sidebar.selectbox[2].value == "panamax_bulker"            # invalid → default vessel

