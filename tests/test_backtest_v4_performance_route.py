"""Saved chart projections must not pay for journal-only entry context."""
from __future__ import annotations

import asyncio

import pytest


RUN_ID = "516d56ae-dd0b-4ba3-bfac-1c3e4a29c5f4"


def test_saved_chart_omits_entry_context_without_changing_journal_default(monkeypatch):
    from src.backend import app as backend_app
    from src.backend import backtest_v4_saved_review, strategy_one_entry_context
    from src.trading_runtime import arte_journal_writer

    calls: list[str] = []

    class Client:
        def close(self):
            calls.append("close")

    def report(_client, run_id):
        assert run_id == RUN_ID
        calls.append("report")
        return {"run_id": run_id, "report": {"episodes": []}}

    def context(_client, run_id):
        assert run_id == RUN_ID
        calls.append("context")
        return {"market_plan_token": "pinned"}

    def attach(_client, page, *, market_plan_token):
        assert market_plan_token == "pinned"
        calls.append("attach")
        return {**page, "entry_context_attached": True}

    monkeypatch.setattr(arte_journal_writer, "backtest_v4_operator_client_from_env", Client)
    monkeypatch.setattr(arte_journal_writer, "load_typed_run_context", context)
    monkeypatch.setattr(backtest_v4_saved_review, "load_cached_v4_performance_report", report)
    monkeypatch.setattr(strategy_one_entry_context, "attach_saved_entry_context", attach)

    chart = asyncio.run(backend_app.trading_backtest_v4_performance(
        RUN_ID, include_entry_context=False))
    assert chart == {"run_id": RUN_ID, "report": {"episodes": []}}
    assert calls == ["report", "close"]

    calls.clear()
    journal = asyncio.run(backend_app.trading_backtest_v4_performance(RUN_ID))
    assert journal["entry_context_attached"] is True
    assert calls == ["report", "context", "attach", "close"]


def test_performance_cache_rechecks_head_and_is_not_mutated_by_journal(monkeypatch):
    from types import SimpleNamespace
    from src.backend import backtest_v4_saved_review as review
    from src.backend.typed_backtest_review_core import AuditedSessionCache

    prefix = SimpleNamespace(last_sequence=4)
    current = {"head": True, "projections": 0}
    cache = AuditedSessionCache(max_sessions=2, max_bytes=4096,
                                max_entry_bytes=2048)
    monkeypatch.setattr(review, "_terminal_attestation", lambda *_args:
                        {"prefix": prefix, "context": {"run_id": RUN_ID}})
    monkeypatch.setattr(review, "_cache_key", lambda *_args:
                        ("operator-scope", RUN_ID, "terminal-head"))
    monkeypatch.setattr(review, "_head_matches", lambda *_args: current["head"])

    def project(_client, _run_id):
        current["projections"] += 1
        return {"run_id": RUN_ID, "verified_sequence": 4,
                "report": {"episodes": [{"episode_id": "one"}]}}

    monkeypatch.setattr(review, "load_v4_performance_report", project)
    first = review.load_cached_v4_performance_report(object(), RUN_ID, cache=cache)
    first["report"]["episodes"].clear()
    second = review.load_cached_v4_performance_report(object(), RUN_ID, cache=cache)
    assert second["report"]["episodes"] == [{"episode_id": "one"}]
    assert current["projections"] == 1

    current["head"] = False
    with pytest.raises(RuntimeError, match="head changed"):
        review.load_cached_v4_performance_report(object(), RUN_ID, cache=cache)
    assert current["projections"] == 2


def test_chart_trades_prunes_other_tickers_and_non_effective_evidence(monkeypatch):
    from src.backend import backtest_v4_saved_review as review

    source = {
        "run_id": RUN_ID, "verified_sequence": 12,
        "report": {"episodes": [{"not_for_chart": "bulky"}]},
        "position_lifecycles": [
            {"episode_id": "one", "instrument": {"symbol": "SLE", "conid": 123},
             "opened_at": "2026-08-18T13:00:00+00:00", "entry_price": "6.1",
             "closed_at": "2026-08-18T13:01:00+00:00", "exit_price": "6.4",
             "side": "LONG", "quantity": "100", "status": "closed",
             "exit_reason": "", "presentation_exit_reason": "target_hit",
             "net_pnl": "30", "unneeded_large_field": "omit",
             "protection_timeline": [
                 {"phase": "effective", "kind": "target", "event_time": "2026-08-18T13:00:01+00:00",
                  "sequence": 4, "order_id": "order-1", "price": "6.4", "active": True,
                  "unneeded_detail": "omit"},
                 {"phase": "proposed", "kind": "stop", "event_time": "2026-08-18T13:00:02+00:00"}]},
            {"episode_id": "two", "instrument": {"symbol": "OTHER"},
             "opened_at": "2026-08-18T13:00:00+00:00", "entry_price": "1",
             "side": "LONG", "quantity": "10", "status": "open",
             "protection_timeline": []},
        ],
    }
    monkeypatch.setattr(review, "load_cached_v4_performance_report", lambda *_: source)
    result = review.load_v4_chart_trades(object(), RUN_ID, "sle")
    assert result["schema_version"] == "strategy-one-v4-chart-trades-v1"
    assert result["verified_sequence"] == 12
    assert len(result["position_lifecycles"]) == 1
    row = result["position_lifecycles"][0]
    assert row["instrument"] == {"symbol": "SLE"}
    assert row["presentation_exit_reason"] == "target_hit"
    assert row["protection_timeline"] == [{
        "phase": "effective", "kind": "target", "event_time": "2026-08-18T13:00:01+00:00",
        "sequence": 4, "order_id": "order-1", "price": "6.4", "active": True}]
    assert "report" not in result and "unneeded_large_field" not in row
