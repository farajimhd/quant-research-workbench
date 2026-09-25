from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import src.trading_runtime.arte_journal_projection as projection_module

from src.trading_runtime.arte_journal_projection import (
    backtest_cursor_batch, backtest_cursor_record_fields, broker_fill_batch,
    broker_fill_details, commission_revision_batch,
    load_latest_backtest_cursor, order_command_batch, project_journal_record,
    strategy_signal_batch,
)
from src.trading_runtime.arte_journal_schema import TABLES
from src.trading_runtime.arte_journal_writer import (
    TypedJournalBatch, V2CommittedPrefix, _sealed_families, load_committed_prefix,
    publish_typed_batch,
)
from src.trading_runtime.ibkr_client import _execution
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.domain import CommissionEvent
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.runtime import TradingRuntime
from src.trading_runtime.signals import StrategySignal
from tests.test_arte_journal_writer import MemoryClient


AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


def test_shared_record_projection_is_typed_and_rejects_unknown_payloads() -> None:
    record = JournalRecord(
        "00000000-0000-0000-0000-000000000013", "live:DU1", 1,
        AT, AT, "lifecycle", "run", "live:DU1", "",
        {"status": "running", "config": {"mode": "live"}},
    )
    identity = dict(
        run_month=date(2026, 8, 1),
        attempt_id="00000000-0000-0000-0000-000000000011",
        batch_id="00000000-0000-0000-0000-000000000012",
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        source_cursor="start",
    )
    batch = project_journal_record(
        record, **identity, expected_config={"mode": "live"})
    assert batch.events[0]["record_id"] == record.record_id
    assert batch.run_transitions[0]["status"] == "running"
    assert dict(_sealed_families(batch))["trading_run_transition_v1"]
    with pytest.raises(ValueError, match="lacks a typed projection"):
        project_journal_record(replace(
            record, category="configuration", entity_type="opaque",
            payload={"unmodeled": {"nested": True}}), **identity)
    with pytest.raises(ValueError, match="pinned typed configuration"):
        project_journal_record(record, **identity)


def test_backtest_cursor_is_normalized_and_causal(monkeypatch) -> None:
    record = JournalRecord(
        "00000000-0000-0000-0000-000000000023", "backtest-1", 1,
        AT, AT, "checkpoint", "market_boundary", "2026-08-18:300000", "",
        {"session_date": "2026-08-18", "boundary_ms": 300000,
         "market_sequence": 700, "frame_as_of": AT.isoformat(),
         "frame_ticker": "TEST", "frame_timeframe": "100ms",
         "frame_sequence": 33},
    )
    identity = dict(run_month=date(2026, 8, 1),
                    attempt_id="00000000-0000-0000-0000-000000000021",
                    batch_id="00000000-0000-0000-0000-000000000022",
                    prior_batch_id="00000000-0000-0000-0000-000000000000",
                    source_cursor="2026-08-18:300000")
    batch = backtest_cursor_batch(record, **identity)
    assert batch.backtest_cursors[0]["market_sequence"] == 700
    assert dict(_sealed_families(batch))["trading_backtest_cursor_v1"]
    client = MemoryClient()
    assert publish_typed_batch(client, batch) == identity["batch_id"]
    assert client.inserts == ["trading_event_v1", "trading_backtest_cursor_v1",
                              "trading_commit_v1"]
    prefix = load_committed_prefix(client, record.run_id)
    assert prefix is not None and prefix.last_sequence == 1
    def joined(_client, sql):
        assert "INNER JOIN arte.trading_event_v1" in sql
        return [{**client.tables["trading_backtest_cursor_v1"][0],
                 "event_sequence": 1, "event_category": "checkpoint",
                 "event_entity_type": "market_boundary",
                 "event_entity_id": record.entity_id}]
    monkeypatch.setattr(projection_module, "_rows", joined)
    assert load_latest_backtest_cursor(client, prefix)["market_sequence"] == 700
    v2_prefix = V2CommittedPrefix(
        prefix.run_id, prefix.last_sequence, prefix.last_batch_id,
        prefix.source_cursor, prefix.status, prefix.batch_ids,
    )
    def joined_v2(_client, sql):
        assert "AND c.batch_id IN (SELECT batch_id FROM arte.trading_commit_v2 " in sql
        return joined(_client, sql)
    monkeypatch.setattr(projection_module, "_rows", joined_v2)
    assert load_latest_backtest_cursor(client, v2_prefix)["market_sequence"] == 700
    monkeypatch.undo()
    client.tables["trading_backtest_cursor_v1"][0]["market_sequence"] = 701
    with pytest.raises(RuntimeError, match="differs from its hash"):
        load_committed_prefix(client, record.run_id)
    with pytest.raises(ValueError, match="not causal"):
        backtest_cursor_batch(replace(record, payload={
            **record.payload, "frame_as_of": "2026-08-18T08:05:00.100000+00:00",
        }), **identity)
    with pytest.raises(ValueError, match="unmodeled"):
        backtest_cursor_batch(replace(record, payload={
            **record.payload, "checkpoint_json": "{}",
        }), **identity)
    with pytest.raises(ValueError, match="completed boundary"):
        backtest_cursor_batch(replace(record, event_time=AT + timedelta(milliseconds=100)),
                              **identity)


def test_controller_cursor_projects_without_json_and_rejects_future_frame() -> None:
    market = {"session_date": "2026-08-18", "boundary_ms": 300000, "sequence": 700}
    frame = {"as_of": AT.isoformat(), "ticker": "TEST", "timeframe": "100ms",
             "sequence": 699}
    entity_id, payload = backtest_cursor_record_fields(market, frame, completed_at=AT)
    assert entity_id == "2026-08-18:300000"
    assert payload["market_sequence"] == 700
    assert payload["frame_sequence"] == 699
    assert all(not isinstance(value, (dict, list)) for value in payload.values())
    with pytest.raises(ValueError, match="not causal"):
        backtest_cursor_record_fields(market, {**frame, "sequence": 701}, completed_at=AT)
    with pytest.raises(ValueError, match="completed boundary"):
        backtest_cursor_record_fields(market, frame,
                                      completed_at=AT + timedelta(milliseconds=100))


def test_simple_order_command_preserves_every_broker_instruction() -> None:
    request = OrderRequest(
        acctId="DU1", conid=123, cOID="client-1", ticker="TEST",
        orderType="LMT", side="BUY", quantity=5, price=12.34,
        secType="STK", listingExchange="ARCA", outsideRTH=True,
        isSingleGroup=True, manualIndicator=True, extOperator="operator-1",
        referrer="risk-console", strategy="broker-algo",
    )
    args = dict(run_id="live:DU1", run_month=date(2026, 8, 1),
                attempt_id="00000000-0000-0000-0000-000000000011",
                batch_id="00000000-0000-0000-0000-000000000012",
                prior_batch_id="00000000-0000-0000-0000-000000000000",
                sequence=1, source_cursor="command-1", run_status="running",
                command_id="command-1", created_at=AT, recorded_at=AT,
                strategy_id="strategy-1", strategy_revision=7)
    batch = order_command_batch(request, **args)
    columns = {table.name: {name for name, _ in table.columns} for table in TABLES}
    detail = dict(batch.order_commands[0])
    assert set(detail) == columns["trading_order_command_v1"] - {"content_hash"}
    assert detail["limit_price"] == "12.3400000000"
    assert detail["listing_exchange"] == "ARCA"
    assert detail["manual_indicator"] == 1
    assert detail["broker_strategy"] == "broker-algo"
    assert order_command_batch(replace(request, ticker="Test.a"), **args).order_commands[0]["ticker"] == "Test.a"
    assert dict(_sealed_families(batch))["trading_order_command_v1"]
    assert order_command_batch(request, **args).events[0]["record_id"] == batch.events[0]["record_id"]
    contextual = order_command_batch(
        request, **args, strategy_intent_id="intent-1",
        order_group_id="group-1", policy_version="policy-1",
    )
    context = dict(contextual.order_contexts[0])
    assert set(context) == columns["trading_order_command_context_v1"] - {"content_hash"}
    assert context["parent_record_id"] == contextual.order_commands[0]["record_id"]
    assert dict(_sealed_families(contextual))["trading_order_command_context_v1"]
    client = MemoryClient()
    with pytest.raises(RuntimeError, match="one committed strategy intent"):
        publish_typed_batch(client, contextual)
    assert client.inserts == []
    with pytest.raises(ValueError, match="unique typed command parent"):
        _sealed_families(replace(
            contextual, order_contexts=({**context, "account_id": "wrong"},),
        ))
    with pytest.raises(ValueError, match="context must be complete"):
        order_command_batch(request, **args, strategy_intent_id="intent-1")
    with pytest.raises(ValueError, match="unmodeled nested"):
        order_command_batch(replace(request, raw={"canonical_metadata": {"action": "enter_long"}}),
                            **args)
    with pytest.raises(ValueError, match="unmodeled nested"):
        order_command_batch(replace(request, strategyParameters=({"tag": "x"},)), **args)


def source(**changes):
    return {
        "execution_id": "e1", "symbol": "TEST", "side": "B",
        "trade_time_r": 1787040300000, "size": 1, "price": 10.25,
        "order_id": "o1", "order_ref": "c1", "account": "DU1",
        "conid": 123, "currency": "USD", "exchange": "ARCA", **changes,
    }


def project(row, commission_record_id=None):
    return broker_fill_details(
        _execution(row), run_id="live:DU1", event_month="2026-08-01",
        batch_id="00000000-0000-0000-0000-000000000001",
        execution_record_id="00000000-0000-0000-0000-000000000002",
        commission_record_id=commission_record_id, received_at=AT,
    )


def test_broker_execution_projects_only_named_typed_fields() -> None:
    pending = project(source())
    columns = {table.name: {name for name, _ in table.columns} for table in TABLES}
    assert set(pending.execution) == columns["trading_execution_v1"] - {"content_hash"}
    assert pending.execution["price"] == "10.2500000000"
    assert pending.execution["currency"] == "USD"
    assert pending.execution["exchange"] == "ARCA"
    assert pending.commission is None
    final = project(source(commission=0),
                    "00000000-0000-0000-0000-000000000003")
    assert set(final.commission) == columns["trading_commission_v1"] - {"content_hash"}
    assert final.commission["commission"] == "0.0000000000"
    assert final.commission["status"] == "final"
    assert final.commission["time_authority"] == "execution"


def test_broker_execution_rejects_unmodeled_or_conflicting_source() -> None:
    with pytest.raises(ValueError, match="unmodeled source fields"):
        project(source(hidden_broker_field="x"))
    with pytest.raises(ValueError, match="separate typed event"):
        project(source(commission=1.25))
    with pytest.raises(ValueError, match="cannot be published as final"):
        project(source(), "00000000-0000-0000-0000-000000000003")
    altered = replace(_execution(source()), price=11)
    with pytest.raises(ValueError, match="source disagrees on price"):
        broker_fill_details(
            altered, run_id="live:DU1", event_month="2026-08-01",
            batch_id="00000000-0000-0000-0000-000000000001",
            execution_record_id="00000000-0000-0000-0000-000000000002",
            commission_record_id=None, received_at=AT,
        )
    with pytest.raises(ValueError, match="losslessly"):
        project(source(price=1.123456789012))


def test_fill_and_commission_form_a_typed_committable_batch() -> None:
    fill_id = "00000000-0000-0000-0000-000000000002"
    fee_id = "00000000-0000-0000-0000-000000000003"
    batch_id = "00000000-0000-0000-0000-000000000001"
    attempt_id = "00000000-0000-0000-0000-000000000004"
    details = project(source(commission=1.25), fee_id)
    base = {
        "run_id": "live:DU1", "event_month": "2026-08-01",
        "attempt_id": attempt_id, "batch_id": batch_id,
        "event_time": AT.isoformat(), "recorded_at": AT.isoformat(),
        "category": "execution", "account_id": "DU1",
        "correlation_id": "", "causation_id": "",
    }
    events = (
        {**base, "record_id": fill_id, "sequence": 1,
         "entity_type": "fill", "entity_id": "e1"},
        {**base, "record_id": fee_id, "sequence": 2,
         "entity_type": "commission", "entity_id": "e1"},
    )
    batch = TypedJournalBatch(
        "live:DU1", date(2026, 8, 1), attempt_id, batch_id,
        "00000000-0000-0000-0000-000000000000", 1, 2,
        "source-e1", "completed", events,
        executions=(details.execution,), commissions=(details.commission,),
    )
    sealed = dict(_sealed_families(batch))
    assert len(sealed["trading_execution_v1"]) == 1
    assert len(sealed["trading_commission_v1"]) == 1


def test_background_fill_batch_has_stable_ids_and_contiguous_sequences() -> None:
    args = dict(
        run_id="live:DU1", run_month=date(2026, 8, 1),
        attempt_id="00000000-0000-0000-0000-000000000004",
        batch_id="00000000-0000-0000-0000-000000000001",
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        first_sequence=7, source_cursor="broker-execution:e1",
        status="running", received_at=AT,
    )
    first = broker_fill_batch(_execution(source(commission=1.25)), **args)
    retried = broker_fill_batch(_execution(source(commission=1.25)), **args)
    assert (first.first_sequence, first.last_sequence) == (7, 8)
    assert [row["record_id"] for row in first.events] == [
        row["record_id"] for row in retried.events
    ]
    assert _sealed_families(first) == _sealed_families(retried)
    pending = broker_fill_batch(_execution(source()), **args)
    assert (pending.first_sequence, pending.last_sequence) == (7, 7)
    assert len(pending.commissions) == 0


def test_shared_fill_record_projects_without_fee_or_information_loss() -> None:
    record = JournalRecord(
        "00000000-0000-0000-0000-000000000083", "live:DU1", 1,
        AT, AT, "execution", "fill", "e1", "DU1",
        {**source(), "correlation_id": "corr-1", "causation_id": "cause-1"},
    )
    identity = dict(
        run_month=date(2026, 8, 1),
        attempt_id="00000000-0000-0000-0000-000000000004",
        batch_id="00000000-0000-0000-0000-000000000001",
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        source_cursor="broker-execution:e1",
    )
    projected = project_journal_record(record, **identity)
    assert projected.first_sequence == projected.last_sequence == 1
    assert projected.events[0]["record_id"] == record.record_id
    assert projected.events[0]["correlation_id"] == "corr-1"
    assert projected.executions[0]["execution_id"] == "e1"
    assert not projected.commissions
    assert dict(_sealed_families(projected))["trading_execution_v1"]
    client = MemoryClient()
    publish_typed_batch(client, projected)
    prefix = load_committed_prefix(client, record.run_id)
    assert prefix is not None and prefix.last_sequence == 1
    with pytest.raises(ValueError, match="separate commission event"):
        project_journal_record(replace(
            record, payload={**record.payload, "commission": 1.25}), **identity)
    with pytest.raises(ValueError, match="unmodeled source fields"):
        project_journal_record(replace(
            record, payload={**record.payload, "hidden": "source"}), **identity)


def test_later_commission_is_a_separate_typed_revision() -> None:
    report = CommissionEvent(
        "e1", "DU1", Decimal("1.25"), "USD", Decimal("0.50"),
        source_event_time=AT, received_at=AT,
    )
    args = dict(
        run_id="live:DU1", run_month=date(2026, 8, 1),
        attempt_id="00000000-0000-0000-0000-000000000004",
        batch_id="00000000-0000-0000-0000-000000000005",
        prior_batch_id="00000000-0000-0000-0000-000000000001",
        sequence=8, source_cursor="fee:e1", run_status="running",
        time_authority="observation",
    )
    revision = commission_revision_batch(report, **args)
    assert revision.first_sequence == revision.last_sequence == 8
    assert len(revision.events) == len(revision.commissions) == 1
    assert not revision.executions
    assert revision.commissions[0]["time_authority"] == "observation"
    assert revision.commissions[0]["realized_pnl"] == "0.5000000000"
    assert _sealed_families(revision) == _sealed_families(
        commission_revision_batch(report, **args)
    )
    with pytest.raises(ValueError, match="must equal receipt"):
        commission_revision_batch(
            replace(report, source_event_time=AT.replace(hour=7)), **args
        )
    with pytest.raises(ValueError, match="unmodeled source fields"):
        commission_revision_batch(replace(report, raw={"unknown": 1}), **args)


def test_runtime_splits_known_fee_and_shared_projection_preserves_it() -> None:
    class CapturingJournal:
        def __init__(self) -> None:
            self.entries = []

        def append_many(self, entries):
            self.entries.extend(entries)

    runtime = object.__new__(TradingRuntime)
    runtime.run_id = "live:DU1"
    runtime.journal = CapturingJournal()
    execution = replace(_execution({**source(), "commission": 1.25}),
                        raw={**source(), "commission": 1.25})
    runtime._record_executions((execution,))
    fill, fee = runtime.journal.entries
    assert [fill["entity_type"], fee["entity_type"]] == ["fill", "commission"]
    assert "commission" not in fill["payload"]
    assert fee["payload"]["commission"] == 1.25
    identity = dict(
        run_month=date(2026, 8, 1),
        attempt_id="00000000-0000-0000-0000-000000000004",
        batch_id="00000000-0000-0000-0000-000000000001",
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        source_cursor="broker-execution:e1",
    )
    fee_record = JournalRecord(
        "00000000-0000-0000-0000-000000000084", runtime.run_id, 2,
        AT, AT, "execution", "commission", fee["entity_id"], fee["account_id"],
        fee["payload"],
    )
    projected = project_journal_record(fee_record, **identity)
    assert projected.commissions[0]["commission"] == "1.2500000000"
    assert projected.commissions[0]["execution_id"] == execution.execution_id
    assert not projected.executions
    assert dict(_sealed_families(projected))["trading_commission_v1"]
    with pytest.raises(ValueError, match="invalid"):
        project_journal_record(replace(
            fee_record, payload={**fee_record.payload, "unmodeled": 1}), **identity)


def test_signal_sources_are_normalized_without_dropping_metadata() -> None:
    signal = StrategySignal(
        "signal-1", "breakout", "TEST", AT, "enter_long", "bullish",
        0.75, 0.9, "entry_ready", ("source-a", "source-b"), "100ms",
        9.5,
    )
    args = dict(
        run_id="live:DU1", run_month=date(2026, 8, 1), account_id="DU1",
        strategy_id="strategy-1", strategy_revision=7,
        attempt_id="00000000-0000-0000-0000-000000000004",
        batch_id="00000000-0000-0000-0000-000000000005",
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        sequence=1, source_cursor="signal-1", run_status="running",
        recorded_at=AT,
    )
    batch = strategy_signal_batch(signal, **args)
    sealed = dict(_sealed_families(batch))
    assert len(sealed["trading_strategy_signal_v1"]) == 1
    assert [row["source_signal_id"] for row in sealed["trading_signal_source_v1"]] == [
        "source-a", "source-b"
    ]
    assert batch.signals[0]["score"] == "0.750000000000000000"
    evidence = {"unmapped": 1, "nested": {"prices": [1.25, None, True]},
                "sources": ("one", "two")}
    with pytest.raises(ValueError, match="concrete typed catalog"):
        strategy_signal_batch(replace(signal, metadata=evidence), **args)
    with pytest.raises(ValueError, match="retired for new writes"):
        strategy_signal_batch(replace(signal, metadata=evidence),
                              persist_metadata_nodes=True, **args)
    from src.trading_runtime.arte_journal_projection import project_signal_evidence_nodes
    with pytest.raises(ValueError, match="byte bound"):
        project_signal_evidence_nodes(
            {str(index): "x" * 1_000_000 for index in range(9)},
            run_id=args["run_id"], event_month="2026-08-01",
            batch_id=args["batch_id"],
            parent_record_id=batch.signals[0]["record_id"],
        )
    with pytest.raises(ValueError, match="out of range"):
        strategy_signal_batch(replace(signal, confidence=1.1), **args)
    invalid = TypedJournalBatch(
        batch.run_id, batch.run_month, batch.attempt_id, batch.batch_id,
        batch.prior_batch_id, 1, 1, batch.source_cursor, batch.status,
        batch.events, signals=batch.signals,
        signal_sources=(batch.signal_sources[0],),
    )
    with pytest.raises(ValueError, match="do not match"):
        _sealed_families(invalid)


def test_generic_signal_evidence_cannot_be_published() -> None:
    metadata = {"decision": {"passes": [True, False], "price": 12.5,
                              "reason": None}, "source_ids": ("a", "b")}
    signal = StrategySignal(
        "signal-tree", "breakout", "TEST", AT, "enter_long", "bullish",
        0.75, 0.9, "entry_ready", (), "100ms", metadata=metadata)
    args = dict(run_id="live:DU1", run_month=date(2026, 8, 1), account_id="DU1",
                strategy_id="strategy-1", strategy_revision=7,
                attempt_id="00000000-0000-0000-0000-000000000014",
                batch_id="00000000-0000-0000-0000-000000000015",
                prior_batch_id="00000000-0000-0000-0000-000000000000",
                sequence=1, source_cursor="signal-tree", run_status="completed",
                recorded_at=AT)
    client = MemoryClient()
    with pytest.raises(ValueError, match="concrete typed catalog"):
        strategy_signal_batch(signal, **args)
    assert not client.inserts
