from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256
import json

import pytest

from src.backend.backtest_fixed_market_authority import (
    project_fixed_market_authority, recover_fixed_market_authority,
)
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import project_pending_backtest_prefix
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.arte_journal_writer import (
    _sealed_families, load_committed_prefix, publish_typed_batch,
)
from src.trading_runtime.arte_journal_schema import backtest_market_authority_upgrade_ddl
from tests.test_arte_journal_writer import MemoryClient


AT = datetime(2026, 8, 18, 8, tzinfo=timezone.utc)
HASH = "a" * 64


def _plans():
    parent = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(1000), "build-1", HASH,
        ("2026-08-18",), ("ABCD", "EFGH"), (), (100, 1000), "b" * 64,
    )
    selected = ("ABCD",)
    token = sha256(json.dumps({"parent_token": parent.token, "tickers": selected},
                              sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    execution = replace(parent, tickers=selected, token=token)
    return parent, execution


def _record(parent, execution):
    payload = {"source_key": "fixed_market_data", **execution.payload(),
               "parent_market_plan_token": parent.token,
               "scanner_ticker_count": len(parent.tickers),
               "execution_ticker_count": len(execution.tickers),
               "database": "arte",
               "tables": ["bars_v1", "indicators_v1", "liquidity_100ms_v1"],
               "access": "select_only", "frame_spool": False,
               "correlation_id": "run:1", "causation_id": "event:1"}
    return JournalRecord("00000000-0000-0000-0000-000000000101", "run-1", 1,
                         AT, AT, "data_authority", "source_revision",
                         "fixed_market_data", "", payload)


def test_fixed_market_authority_projects_only_pinned_hashes_and_recovers():
    parent, execution = _plans()
    record = _record(parent, execution)
    row = project_fixed_market_authority(
        record, parent_plan=parent, execution_plan=execution,
        expected_event_time=AT)
    assert row.execution_plan_token == execution.token
    assert row.parent_market_plan_token == parent.token
    assert row.record_id == record.record_id
    assert recover_fixed_market_authority(
        row, parent_plan=parent, execution_plan=execution) == {
            key: value for key, value in record.payload.items()
            if key not in {"correlation_id", "causation_id"}}


@pytest.mark.parametrize("change", [
    {"tables": ["bars_v1"]},
    {"source_key": "market_events"},
    {"unit_count": 100},
    {"extra": {"opaque": True}},
])
def test_fixed_market_authority_rejects_drift_or_unmodeled_fields(change):
    parent, execution = _plans()
    record = _record(parent, execution)
    record.payload.update(change)
    with pytest.raises(ValueError, match="differs from its pinned plans"):
        project_fixed_market_authority(record, parent_plan=parent,
                                       execution_plan=execution,
                                       expected_event_time=AT)


def test_fixed_market_authority_rejects_unpinned_projection_hash():
    parent, execution = _plans()
    with pytest.raises(ValueError, match="projection lacks its parent hash"):
        project_fixed_market_authority(
            _record(parent, execution), parent_plan=parent,
            execution_plan=replace(execution, token="c" * 64),
            expected_event_time=AT)


def test_other_source_revision_family_remains_unsupported():
    parent, execution = _plans()
    record = replace(_record(parent, execution), entity_id="market_events")
    with pytest.raises(ValueError, match="envelope"):
        project_fixed_market_authority(record, parent_plan=parent,
                                       execution_plan=execution,
                                       expected_event_time=AT)


def test_fixed_market_authority_typed_fence_and_corruption_with_fake_client():
    parent, execution = _plans()
    record = _record(parent, execution)
    identity = dict(run_month=date(2026, 8, 1),
                    attempt_id="00000000-0000-0000-0000-000000000201",
                    batch_id="00000000-0000-0000-0000-000000000202",
                    prior_batch_id="00000000-0000-0000-0000-000000000000",
                    source_cursor="start")
    with pytest.raises(ValueError, match="requires pinned"):
        project_journal_record(record, **identity)
    batch = project_journal_record(
        record, **identity, fixed_market_parent_plan=parent,
        fixed_market_execution_plan=execution, expected_market_start=AT)
    assert dict(_sealed_families(batch))["trading_backtest_market_authority_v1"]
    assert batch.backtest_market_authorities[0]["record_id"] == record.record_id
    client = MemoryClient()
    assert publish_typed_batch(client, batch) == identity["batch_id"]
    assert client.inserts[-1] == "trading_commit_v1"
    assert load_committed_prefix(client, record.run_id) is not None
    client.tables["trading_backtest_market_authority_v1"][0]["execution_plan_token"] = "c" * 64
    with pytest.raises(RuntimeError, match="row content differs from its hash"):
        load_committed_prefix(client, record.run_id)


def test_fixed_market_authority_operator_upgrade_is_additive():
    statements = backtest_market_authority_upgrade_ddl()
    assert len(statements) == 3
    assert "trading_backtest_market_authority_v1" in statements[0]
    assert "DEFAULT 0" in statements[1]
    assert "DEFAULT '4f53cda" in statements[2]


def test_memory_journal_authority_requires_pinned_plans_before_batch_projection():
    parent, execution = _plans()
    source = _record(parent, execution)
    journal = BacktestMemoryJournal(run_id=source.run_id)
    journal.append(run_id=source.run_id, category=source.category,
                   entity_type=source.entity_type, entity_id=source.entity_id,
                   event_time=source.event_time, payload=source.payload)
    identity = dict(attempt_id="00000000-0000-0000-0000-000000000201",
                    run_month=date(2026, 8, 1), prior_sequence=0)
    with pytest.raises(ValueError, match="requires pinned"):
        project_pending_backtest_prefix(journal, **identity)
    prefix = project_pending_backtest_prefix(
        journal, **identity, fixed_market_parent_plan=parent,
        fixed_market_execution_plan=execution, expected_market_start=AT)
    assert len(prefix.batches) == 1
    assert prefix.batches[0].backtest_market_authorities[0]["execution_plan_token"] == execution.token
    assert journal.pending_record_count == 1
