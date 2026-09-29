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
