"""Causal ordering, typed lineage, and distinct broker-equity authority."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from src.backend import backtest_v4_performance_evidence as evidence


AT = datetime(2026, 8, 19, 23, 55, 40, tzinfo=timezone.utc)
RUN = "00000000-0000-0000-0000-000000000001"
BATCH = "00000000-0000-0000-0000-000000000002"
COMMAND = "00000000-0000-0000-0000-000000000003"
SOURCE = "00000000-0000-0000-0000-000000000004"


def execution(**kw):
    values = dict(execution_id="fill", broker_order_id="58", client_order_id="exit-coid",
        account_id="SIM", instrument=NS(symbol="GO", conid=123), strategy_id="early-squeeze-strategy",
        strategy_revision=2, source_event_time=AT, journal_sequence=30,
        side="SELL", exit_reason="", quantity=Decimal(94))
    return NS(**{**values, **kw})


def test_protection_ignores_post_fill_deactivation_with_earlier_intrabar_clock():
    events = [dict(phase="effective", order_id="58", sequence=10, active=True, kind="stop",
                   event_time="2026-08-19T23:55:00+00:00"),
              dict(phase="effective", order_id="58", sequence=31, active=False, kind="stop",
                   event_time="2026-08-19T23:55:39.990000+00:00")]
    result = evidence.protection_exit_evidence({"protection_timeline": events}, execution())
    assert result["reason"] == "stop_hit" and result["source_sequence"] == 10
    events[-1]["sequence"] = 29
    assert evidence.protection_exit_evidence({"protection_timeline": events}, execution()) is None


def test_partial_stop_and_session_exit_preserve_components_and_final_reason(monkeypatch):
    first = execution(execution_id="first", broker_order_id="3", journal_sequence=15,
                      quantity=Decimal(100), source_event_time=AT.replace(second=10))
    final = execution()
    lifecycle = {"side": "LONG", "closed_at": AT.isoformat(), "execution_ids": ["first", "fill"],
        "protection_timeline": [dict(phase="effective", order_id="3", sequence=10,
            active=True, kind="stop", event_time="2026-08-19T23:55:00+00:00")]}
    monkeypatch.setattr(evidence, "managed_exit_evidence", lambda _c, _p, fills:
        {"fill": {"reason": "strategy_two_session_exit", "source": "typed_strategy_intent"}})
    evidence.attach_exit_evidence(None, None, [lifecycle], [first, final])
    assert lifecycle["presentation_exit_reason"] == "strategy_two_session_exit"
    assert [row["reason"] for row in lifecycle["exit_components"]] == ["stop_hit", "strategy_two_session_exit"]


def managed_fixture(monkeypatch):
    from src.backend import backtest_v4_saved_review as review
    from src.trading_runtime import arte_journal_writer as writer
    from src.trading_runtime import arte_intent_projection as intents
    command = dict(record_id=COMMAND, run_id=RUN, batch_id=BATCH, account_id="SIM",
        client_order_id="exit-coid", ticker="GO", conid=123, strategy_id="early-squeeze-strategy",
        strategy_revision=2, side="SELL", sequence=20, created_at="2026-08-19 23:55:05.800000000")
    context = writer.typed_row("trading_order_command_context_v1", dict(
        record_id=SOURCE, parent_record_id=COMMAND, run_id=RUN, event_month="2026-08-01",
        batch_id=BATCH, account_id="SIM", strategy_intent_id="intent", order_group_id="group", policy_version="1"))
    source = NS(record_id=SOURCE, sequence=18, account_id="SIM", intent=NS(intent_id="intent",
        action="exit", reason="strategy_two_session_exit", ticker="GO",
        event_time=AT.replace(second=5, microsecond=800000)))
    monkeypatch.setattr(review, "_complete_detail_rows", lambda *_a: (command,))
    monkeypatch.setattr(writer, "_committed_batch_filter", lambda *_a: "")
    monkeypatch.setattr(writer, "_rows", lambda _c, sql: [context] if "command_context" in sql
                        else [dict(record_id=SOURCE, intent_id="intent")])
    monkeypatch.setattr(intents, "load_committed_strategy_intent_page", lambda *_a, **_k: (source,))
    return context, source


def test_exact_typed_exit_lineage_recovers_reason_and_rejects_tampered_context(monkeypatch):
    context, source = managed_fixture(monkeypatch)
    result = evidence.managed_exit_evidence(None, NS(run_id=RUN), [execution()])
    assert result["fill"]["reason"] == "strategy_two_session_exit"
    assert result["fill"]["source_record_id"] == SOURCE
    source.sequence = 21
    with pytest.raises(RuntimeError, match="causal command/intent lineage"):
        evidence.managed_exit_evidence(None, NS(run_id=RUN), [execution()])
    source.sequence = 18
    context["strategy_intent_id"] = "forged"
    with pytest.raises(ValueError, match="hash differs"):
        evidence.managed_exit_evidence(None, NS(run_id=RUN), [execution()])


def test_entry_intent_reason_is_not_relabelled_as_exit(monkeypatch):
    _, source = managed_fixture(monkeypatch)
    source.intent.action = "enter_long"
    assert evidence.managed_exit_evidence(None, NS(run_id=RUN), [execution()]) == {}


def test_broker_drawdown_rejects_incomplete_or_wrong_cursor_and_labels_limits(monkeypatch):
    from src.backend import backtest_v4_saved_review as review, typed_backtest_review_core as core
    from src.trading_runtime import arte_journal_projection as projection
    from src.trading_runtime import strategy_one_broker_match_snapshot as broker
    root = dict(boundary_ms=57600000, session_date="2026-08-19", performance_complete=1,
        maximum_drawdown_f64_bits=broker.float64_bits(662.4, "DD"),
        equity_peak_f64_bits=broker.float64_bits(41.1, "peak"),
        performance_as_of=AT.isoformat(), content_hash="a" * 64)
    monkeypatch.setattr(review, "_terminal_attestation", lambda *_: {"prefix": NS(last_sequence=35)})
    monkeypatch.setattr(projection, "load_latest_backtest_cursor", lambda *_:
        dict(event_sequence=33, boundary_ms=57600000, session_date="2026-08-19"))
    monkeypatch.setattr(broker, "load_unattested_broker_match_snapshot", lambda *_a, **_k: NS(snapshot=root))
    monkeypatch.setattr(core, "_head_matches", lambda *_: True)
    result = evidence.load_broker_observed_drawdown(None, RUN)
    assert result["maximum_drawdown"] == 662.4
    assert "asynchronous" in result["limitations"][0]
    assert "no age limit" in result["limitations"][1]
    root["boundary_ms"] -= 100
    with pytest.raises(RuntimeError, match="complete terminal evidence"):
        evidence.load_broker_observed_drawdown(None, RUN)
    root["boundary_ms"] += 100
    root["performance_complete"] = 0
    with pytest.raises(RuntimeError, match="complete terminal evidence"):
        evidence.load_broker_observed_drawdown(None, RUN)


def test_performance_cache_projection_version_excludes_previous_schema(monkeypatch):
    from src.backend import backtest_v4_saved_review as review
    from src.backend.typed_backtest_review_core import AuditedSessionCache
    prefix = NS(last_sequence=35)
    cache = AuditedSessionCache()
    cache.put(("scope", RUN, "old"), {"report": {"stale": True}})
    monkeypatch.setattr(review, "_terminal_attestation", lambda *_:
        {"prefix": prefix, "context": {"run_id": RUN}})
    monkeypatch.setattr(review, "_cache_key", lambda _c, _r, context, _p:
        ("scope", RUN, context.get("performance_projection", "old")))
    monkeypatch.setattr(review, "_head_matches", lambda *_: True)
    monkeypatch.setattr(review, "load_v4_performance_report", lambda *_:
        {"run_id": RUN, "verified_sequence": 35, "schema_version": "strategy-one-v4-performance-report-v2"})
    assert review.load_cached_v4_performance_report(None, RUN, cache=cache)["schema_version"].endswith("v2")
