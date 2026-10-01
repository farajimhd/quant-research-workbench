"""Research reports retain numbered release evidence without relabeling history."""
from datetime import date
from types import SimpleNamespace

import pytest

from scripts.clickhouse import report_strategy_one_trades as report
from src.backend import backtest_strategy_one_configuration as configuration


@pytest.mark.parametrize("number", range(2, 17))
def test_numbered_report_requires_pinned_configuration_and_labels_number(monkeypatch, number):
    monkeypatch.setattr(report, "load_broker_observed_drawdown", lambda *_:
                        {"maximum_drawdown": 12.0, "verified_terminal_sequence": 1})
    context = {"strategy_revision": number, "configuration_hash": "a" * 64, "initial_cash": 10000}
    monkeypatch.setattr(report, "load_v4_terminal_review_page", lambda *_a, **_kw: {"status": "completed"})
    monkeypatch.setattr(report, "certified_saved_run_plan", lambda *_a, **_kw:
                        (date(2026, 8, 18), context, {}, SimpleNamespace(build_id="build", token="token")))
    monkeypatch.setattr(report, "load_v4_performance_report", lambda *_a:
                        {"position_lifecycles": [], "report": {"episodes": []}, "verified_sequence": 1})
    release = SimpleNamespace(payload_hash="a" * 64, token="sealed-token",
                              payload={"strategy": {"numbered_release": {"approved_digest": "b" * 64}}})
    calls = []
    monkeypatch.setattr(configuration, "certify_numbered_configuration", lambda client, number:
                        calls.append(number) or release)
    result = report.build_report(object(), object(), "run")
    assert calls == [number]
    assert result["schema_version"] == "numbered-fixed-research-trades-v4"
    assert result["configuration_release_token"] == "sealed-token"
    assert report.markdown(result).startswith(f"# Strategy {number} positions:")
    release.payload_hash = "c" * 64
    with pytest.raises(RuntimeError, match="sealed run configuration"):
        report.build_report(object(), object(), "run")
