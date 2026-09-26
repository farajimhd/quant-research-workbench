from datetime import date, datetime, timezone
from uuid import UUID

import pytest
from types import SimpleNamespace
from unittest.mock import patch

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_typed_projection import (
    NIL_BATCH_ID, project_pending_backtest_prefix,
)
from src.trading_runtime.arte_journal_writer import _sealed_families
from src.trading_runtime.arte_journal_schema import strategy_signal_decision_upgrade_ddl
from src.trading_runtime.arte_journal_projection import recover_common_signal_decision_metadata
from src.trading_runtime.signals import StrategySignal
from src.trading_runtime.signals import StrategyEvaluation
from src.trading_runtime.runtime import TradingRuntime
from src.trading_runtime.ibkr_schema import OrderRequest


RUN_ID = "00000000-0000-0000-0000-000000000a01"
ATTEMPT_ID = "00000000-0000-0000-0000-000000000a02"
DAY = date(2026, 8, 18)
BOUNDARY = 300_000
AT = market_day_boundary(DAY, BOUNDARY).astimezone(timezone.utc)


def _journal():
    return BacktestMemoryJournal(run_id=RUN_ID)


def _project(journal):
    return project_pending_backtest_prefix(
        journal, attempt_id=ATTEMPT_ID, run_month=DAY.replace(day=1),
        prior_sequence=0, expected_config={"mode": "backtest"},
    )


def test_actual_memory_records_project_into_chained_typed_families():
    journal = _journal()
    journal.append(run_id=RUN_ID, category="lifecycle", entity_type="run",
                   entity_id=RUN_ID, event_time=AT,
                   payload={"status": "running", "config": {"mode": "backtest"}})
    journal.append(run_id=RUN_ID, category="checkpoint",
                   entity_type="market_boundary",
                   entity_id=f"{DAY.isoformat()}:{BOUNDARY}", event_time=AT,
                   payload={"session_date": DAY.isoformat(),
                            "boundary_ms": BOUNDARY, "market_sequence": 7,
                            "frame_as_of": None, "frame_ticker": None,
                            "frame_timeframe": None, "frame_sequence": None})
    projected = _project(journal)
    assert projected.last_sequence == 2
    assert projected.source_cursor == f"{DAY.isoformat()}:{BOUNDARY}"
    first, second = projected.batches
    assert first.run_transitions[0]["status"] == "running"
    assert second.backtest_cursors[0]["market_sequence"] == 7
    assert first.prior_batch_id == NIL_BATCH_ID
    assert second.prior_batch_id == first.batch_id
    assert projected.last_batch_id == second.batch_id
    assert UUID(first.batch_id) != UUID(second.batch_id)
    assert dict(_sealed_families(first))["trading_run_transition_v1"]
    assert dict(_sealed_families(second))["trading_backtest_cursor_v1"]
    assert all("payload_json" not in row for batch in projected.batches
               for row in batch.events)
    assert _project(journal).batches == projected.batches
    assert journal.pending_record_count == 2  # Projection is not a durability fence.


def test_strategy_order_command_projects_from_actual_memory_journal() -> None:
    journal = _journal()
    request = OrderRequest(
        acctId="SIM-01", conid=123, cOID="client-1", ticker="ABCD",
        orderType="LMT", side="BUY", quantity=5, price=12.34)
    record = journal.append(
        run_id=RUN_ID, category="command", entity_type="order",
        entity_id=request.cOID, account_id="SIM-01", event_time=AT,
        payload={**request.to_cpapi(), "strategy_intent_id": "intent-1",
                 "order_group_id": "group-1", "policy_version": "policy-1"})
    projected = project_pending_backtest_prefix(
        journal, attempt_id=ATTEMPT_ID, run_month=DAY.replace(day=1),
        prior_sequence=0,
        expected_config={"strategy_id": "strategy-1", "strategy_revision": 1})
    batch = projected.batches[0]
    assert batch.events[0]["record_id"] == record.record_id
    assert batch.order_commands[0]["client_order_id"] == request.cOID
    assert batch.order_commands[0]["strategy_id"] == "strategy-1"
    assert batch.order_contexts[0]["strategy_intent_id"] == "intent-1"
    assert dict(_sealed_families(batch))["trading_order_command_v1"]


def test_unsupported_record_rejects_whole_pending_prefix():
    journal = _journal()
    journal.append(run_id=RUN_ID, category="lifecycle", entity_type="run",
                   entity_id=RUN_ID, event_time=AT,
                   payload={"status": "running", "config": {"mode": "backtest"}})
    journal.append(run_id=RUN_ID, category="watchlist_membership",
                   entity_type="historical_watchlist_member", entity_id="ABCD",
                   event_time=AT, payload={"event": "added", "ticker": "ABCD"})
    with pytest.raises(ValueError, match="lacks a typed projection"):
        _project(journal)
    assert journal.pending_record_count == 2


def test_hot_path_defers_json_validation_to_bounded_projection_worker():
    journal = _journal()
    journal.append(
        run_id=RUN_ID, category="lifecycle", entity_type="run",
        entity_id=RUN_ID, event_time=AT,
        payload={"status": "running", "config": {"mode": "backtest",
                                                  "invalid": float("nan")}},
    )
    # Appending takes a defensive snapshot but does not serialize evidence on
    # the simulated market callback. The entire prefix is rejected before a
    # writer can see it when projection runs on the publisher's worker lane.
    assert journal.pending_record_count == 1
    with pytest.raises(ValueError, match="Out of range float"):
        _project(journal)
    assert journal.pending_record_count == 1


def test_runtime_strategy_signal_record_preserves_identity_without_opaque_evidence():
    journal = _journal()
    signal = StrategySignal(
        "signal-1", "breakout", "ABCD", AT, "enter_long", "bullish",
        0.7, 0.9, "entry_ready", ("source-1",), "100ms",
        metadata={},
    )
    record = journal.append(
        run_id=RUN_ID, category="strategy_decision", entity_type="signal",
        entity_id=signal.signal_id, account_id="SIM-01", event_time=AT,
        payload={**signal.payload(), "strategy_id": "strategy-1",
                 "strategy_revision": 7},
    )
    batch = _project(journal).batches[0]
    assert batch.events[0]["record_id"] == record.record_id
    assert batch.events[0]["correlation_id"] == record.payload["correlation_id"]
    assert batch.signals[0]["record_id"] == record.record_id
    assert batch.signals[0]["signal_id"] == signal.signal_id
    assert batch.signal_sources[0]["source_signal_id"] == "source-1"
    assert not batch.signal_evidence_nodes
    assert dict(_sealed_families(batch))["trading_strategy_signal_v1"]


def test_signal_metadata_fails_closed_until_concrete_catalog_exists():
    journal = _journal()
    signal = StrategySignal(
        "signal-2", "breakout", "ABCD", AT, "wait", "neutral",
        0.0, 0.5, "waiting", metadata={
            "assignment_id": "assignment-1", "reference_price": 12.5,
            "status": "watching", "reason_code": "waiting",
            "reason_detail": "not ready", "correlation_id": "run:1",
            "causation_id": "event:1", "entry_body_trigger": {"passed": True},
        },
    )
    journal.append(run_id=RUN_ID, category="strategy_decision",
                   entity_type="signal", entity_id=signal.signal_id,
                   account_id="SIM-01", event_time=AT,
                   payload={**signal.payload(), "strategy_id": "strategy-1",
                            "strategy_revision": 7})
    with pytest.raises(ValueError, match="concrete typed catalog"):
        _project(journal)


def test_common_strategy_decision_metadata_uses_explicit_scalar_columns():
    journal = _journal()
    metadata = {"assignment_id": "assignment-1", "reference_price": 12.5,
                "status": "watching", "reason_code": "entry_ready",
                "reason_detail": "completed bar confirmed", "correlation_id": "run:1",
                "causation_id": "event:1"}
    signal = StrategySignal("signal-4", "breakout", "ABCD", AT,
                            "enter_long", "bullish", 0.7, 0.9,
                            "entry_ready", metadata=metadata)
    record = journal.append(run_id=RUN_ID, category="strategy_decision",
                            entity_type="signal", entity_id=signal.signal_id,
                            account_id="SIM-01", event_time=AT,
                            payload={**signal.payload(), "strategy_id": "strategy-1",
                                     "strategy_revision": 7,
                                     "correlation_id": metadata["correlation_id"],
                                     "causation_id": metadata["causation_id"]})
    batch = _project(journal).batches[0]
    detail = batch.signals[0]
    assert detail["record_id"] == record.record_id
    assert detail["decision_assignment_id"] == "assignment-1"
    assert detail["decision_reference_price"] == "12.5000000000"
    assert detail["decision_reason_detail"] == "completed bar confirmed"
    assert batch.events[0]["causation_id"] == "event:1"
    assert recover_common_signal_decision_metadata(batch.events[0], detail) == metadata
    assert not batch.signal_evidence_nodes
    assert dict(_sealed_families(batch))["trading_strategy_signal_v1"]


def test_signal_record_rejects_unmodeled_field_and_clock_drift():
    for change, expected in (
        ({"opaque_state": {"broker": "unknown"}}, "unmodeled"),
        ({"event_time": datetime(2026, 8, 18, 15, tzinfo=timezone.utc)}, "identity"),
    ):
        journal = _journal()
        signal = StrategySignal("signal-3", "breakout", "ABCD", AT,
                                "wait", "neutral", 0.0, 0.5, "waiting")
        journal.append(run_id=RUN_ID, category="strategy_decision",
                       entity_type="signal", entity_id=signal.signal_id,
                       account_id="SIM-01", event_time=AT,
                       payload={**signal.payload(), "strategy_id": "strategy-1",
                                "strategy_revision": 7, **change})
        with pytest.raises(ValueError, match=expected):
            _project(journal)


def test_prefix_requires_verified_prior_chain_identity():
    journal = _journal()
    with pytest.raises(ValueError, match="prefix identity"):
        project_pending_backtest_prefix(
            journal, attempt_id=ATTEMPT_ID, run_month=DAY.replace(day=1),
            prior_sequence=1, source_cursor="start")


def test_strategy_signal_scalar_upgrade_is_operator_only_and_nullable():
    class Reader:
        def __init__(self, count):
            self.count = count
            self.queries = []

        def execute(self, sql):
            self.queries.append(sql)
            return f'{{"row_count":{self.count}}}\n'

    reader = Reader(0)
    statements = strategy_signal_decision_upgrade_ddl(reader)
    assert len(statements) == 4
    assert all(statement.startswith(
        "ALTER TABLE arte.trading_strategy_signal_v1 ADD COLUMN IF NOT EXISTS "
    ) for statement in statements)
    assert all("DEFAULT NULL" in statement for statement in statements)
    assert len(reader.queries) == 1
    assert reader.queries[0].startswith("SELECT count()")
    with pytest.raises(ValueError, match="nonempty history needs a versioned schema"):
        strategy_signal_decision_upgrade_ddl(Reader(1))

    class UnknownReader:
        def execute(self, sql):
            return ""

    with pytest.raises(ValueError, match="empty, quiesced table"):
        strategy_signal_decision_upgrade_ddl(UnknownReader())


def test_commit_family_additions_use_empty_fence_defaults_not_row_rehashes():
    from src.trading_runtime.arte_journal_schema import TABLES, backtest_market_authority_upgrade_ddl
    from src.trading_runtime.arte_journal_writer import _FAMILIES

    commit = next(table for table in TABLES if table.name == "trading_commit_v1")
    assert "content_hash" not in dict(commit.columns)
    family = next(row for row in _FAMILIES
                  if row[0] == "trading_backtest_market_authority_v1")
    assert family[2:] == ("backtest_market_authority_count",
                          "backtest_market_authority_hash")
    upgrade = backtest_market_authority_upgrade_ddl()
    assert "DEFAULT 0" in upgrade[1]
    assert "DEFAULT '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'" in upgrade[2]


def test_runtime_copies_signal_lineage_to_envelope_for_both_journals():
    metadata = {"correlation_id": "run:signal", "causation_id": "event:signal"}
    signal = StrategySignal("signal-lineage", "breakout", "ABCD", AT,
                            "enter_long", "bullish", 0.7, 0.9,
                            "entry_ready", metadata=metadata)

    class FakeLiveJournal:
        def __init__(self):
            self.entries = []

        def append(self, **entry):
            self.entries.append(entry)

    for journal in (FakeLiveJournal(), _journal()):
        runtime = object.__new__(TradingRuntime)
        runtime.journal = journal
        runtime.run_id = RUN_ID
        runtime.config = SimpleNamespace(strategy_id="strategy-1", strategy_revision=7)
        runtime._last_wait_decision_signatures = {}
        runtime._record_strategy_signals(StrategyEvaluation(signals=(signal,)), "SIM-01")
        payload = (journal.entries[0]["payload"] if isinstance(journal, FakeLiveJournal)
                   else journal.unfenced_records()[0].payload)
        assert payload["correlation_id"] == metadata["correlation_id"]
        assert payload["causation_id"] == metadata["causation_id"]
    with patch.object(StrategySignal, "payload", return_value={
        **signal.payload(), "correlation_id": "conflict",
    }):
        with pytest.raises(ValueError, match="lineage conflicts"):
            runtime._record_strategy_signals(
                StrategyEvaluation(signals=(signal,)), "SIM-01")


def test_common_signal_metadata_rejects_envelope_lineage_mismatch():
    journal = _journal()
    metadata = {"assignment_id": "assignment-1", "reference_price": 12.5,
                "status": "watching", "reason_code": "waiting",
                "reason_detail": "not ready", "correlation_id": "run:signal",
                "causation_id": "event:signal"}
    signal = StrategySignal("signal-mismatch", "breakout", "ABCD", AT,
                            "wait", "neutral", 0.0, 0.5, "waiting",
                            metadata=metadata)
    journal.append(run_id=RUN_ID, category="strategy_decision", entity_type="signal",
                   entity_id=signal.signal_id, account_id="SIM-01", event_time=AT,
                   payload={**signal.payload(), "strategy_id": "strategy-1",
                            "strategy_revision": 7,
                            "correlation_id": "run:other",
                            "causation_id": "event:signal"})
    with pytest.raises(ValueError, match="decision metadata is invalid"):
        _project(journal)
