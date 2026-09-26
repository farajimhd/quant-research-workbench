"""V4 terminal cannot substitute portfolio recovery for broker snapshots."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_terminal_broker_snapshot_v4 import (
    project_v4_terminal_broker_batch,
    project_v4_terminal_broker_snapshots,
)
from src.backend.backtest_terminal_snapshot_v2 import (
    ACCOUNT_METRICS, position_set_sha256,
)


AT = datetime(2026, 8, 18, 13, 30, tzinfo=timezone.utc)


def _records():
    journal = BacktestMemoryJournal(run_id="run-v4")
    snapshot_id = str(uuid4())
    payload = {name: {"amount": 1000.0, "currency": "USD",
                      "timestamp": 123} for name, _ in ACCOUNT_METRICS}
    journal.append(
        run_id="run-v4", category="snapshot", entity_type="portfolio",
        entity_id="DU1", account_id="DU1", event_time=AT,
        payload={**payload, "snapshot_id": snapshot_id,
                 "expected_position_count": 0,
                 "position_set_sha256": position_set_sha256(())})
    journal.append(
        run_id="run-v4", category="lifecycle", entity_type="run",
        entity_id="run-v4", event_time=AT,
        payload={"status": "completed", "processed_events": 2})
    return tuple(journal.unfenced_records())


def test_terminal_broker_snapshot_group_is_normalized_and_complete():
    rows = project_v4_terminal_broker_snapshots(
        _records(), run_id="run-v4", account_ids=("DU1",),
        batch_id=str(uuid4()))
    assert len(rows.accounts) == 1 and rows.positions == ()
    assert rows.accounts[0]["net_liquidation"] == 1000.0
    assert rows.first_sequence == 1 and rows.last_sequence == 2


def test_terminal_broker_batch_keeps_full_event_span_and_float64_families():
    records = _records()
    kwargs = dict(run_id="run-v4", account_ids=("DU1",),
                  attempt_id=str(uuid4()), run_month=date(2026, 8, 1),
                  prior_batch_id=str(uuid4()), source_cursor="2026-08-18:34200000")
    unit = project_v4_terminal_broker_batch(records, **kwargs)
    assert unit.base.first_sequence == 1
    assert unit.base.last_sequence == 2
    assert [row["record_id"] for row in unit.base.events] == [
        record.record_id for record in records]
    assert len(unit.base.run_transitions) == 1
    assert unit.base.account_snapshots == ()
    assert unit.broker_snapshots.accounts[0]["batch_id"] == unit.base.batch_id
    assert unit.broker_snapshots.accounts[0]["net_liquidation"] == 1000.0
    assert project_v4_terminal_broker_batch(records, **kwargs) == unit


def test_terminal_broker_batch_rejects_foreign_month_before_projection():
    with pytest.raises(ValueError, match="clock or month"):
        project_v4_terminal_broker_batch(
            _records(), run_id="run-v4", account_ids=("DU1",),
            attempt_id=str(uuid4()), run_month=date(2026, 9, 1),
            prior_batch_id=str(uuid4()), source_cursor="2026-08-18:34200000")


def test_terminal_broker_snapshot_rejects_missing_account_and_clock():
    source = _records()
    with pytest.raises(ValueError, match="population differs"):
        project_v4_terminal_broker_snapshots(
            source, run_id="run-v4", account_ids=("DU2",),
            batch_id=str(uuid4()))
    with pytest.raises(ValueError, match="population differs"):
        project_v4_terminal_broker_snapshots(
            (source[-1],), run_id="run-v4", account_ids=("DU1",),
            batch_id=str(uuid4()))
    with pytest.raises(ValueError, match="population differs"):
        project_v4_terminal_broker_snapshots(
            (replace(source[0], event_time=AT - timedelta(seconds=1)),
             source[1]), run_id="run-v4", account_ids=("DU1",),
            batch_id=str(uuid4()))
