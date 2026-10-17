"""Current V4 reconciliation projection, compaction, publication and cold seals."""
from dataclasses import replace
from datetime import date, timedelta
import asyncio

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, publish_compound_v4
from src.trading_runtime.arte_journal_commit_v4 import (
    _load_verified_details_v4, _publish_typed_batch_v4, load_verified_v4_prefix,
)
from src.trading_runtime.arte_journal_writer import (
    _datetime_wire, typed_row, v4_storage_contracts, v4_journal_write_tables,
)
from src.trading_runtime.arte_portfolio_reconciliation_v4 import DIFFERENCE, V4PortfolioReconciliationBatch
from tests.test_arte_journal_commit_v4 import attached_v4_client
from tests.test_backtest_reconciliation_v3 import _record, AT, ATTEMPT


def project(*, cleared=False, decision_delay=timedelta(0)):
    journal = BacktestMemoryJournal(run_id="run-1")
    for source in ((_record(), _record([])) if cleared else (_record(),)):
        journal.append_many([dict(run_id=source.run_id, category=source.category,
            entity_type=source.entity_type, entity_id=source.entity_id,
            account_id=source.account_id, event_time=AT + decision_delay,
            payload=dict(source.payload))])
    try:
        return project_pending_backtest_v4_prefix(journal, attempt_id=ATTEMPT,
            run_month=date(2026, 8, 1), prior_sequence=0,
            through_sequence=journal.latest_sequence(journal.run_id))
    finally:
        journal.close()


def test_current_prefix_publishes_all_normalized_differences_and_recovers():
    unit, = project()
    assert type(unit) is V4PortfolioReconciliationBatch
    assert [r["ticker"] for r in unit.differences] == ["AAA", "BBB"]
    client = attached_v4_client()
    assert _publish_typed_batch_v4(client, unit.base,
        portfolio_reconciliation_differences=unit.differences) == unit.base.batch_id
    assert len(client.tables[DIFFERENCE.name]) == 2
    assert load_verified_v4_prefix(client, "run-1").last_sequence == 1
    assert DIFFERENCE in v4_storage_contracts()
    assert DIFFERENCE.name in v4_journal_write_tables()
    from scripts.clickhouse.provision_backtest_v4_program_runner import desired_plan
    plan = desired_plan()
    assert DIFFERENCE.name in plan.select_arte & plan.insert_arte
    assert plan.insert_arte <= plan.select_arte


def test_compound_rekeys_differences_and_retains_empty_reconciliation_event():
    units = project(cleared=True)
    assert units[1].differences == ()
    compound = coalesce_v4_units(units)
    client = attached_v4_client()
    assert publish_compound_v4(client, compound) == units[-1].base.batch_id
    prefix = load_verified_v4_prefix(client, "run-1")
    assert prefix.last_sequence == 2
    assert len(client.tables["trading_portfolio_reconciliation_event_v1"]) == 2
    assert len(client.tables[DIFFERENCE.name]) == 2
    assert {r["batch_id"] for r in client.tables[DIFFERENCE.name]} == {compound.base.batch_id}


@pytest.mark.parametrize("compound", (False, True))
def test_snapshot_before_decision_preserves_causal_times_and_recovers(compound):
    delay = timedelta(seconds=7)
    units = project(cleared=compound, decision_delay=delay)
    client = attached_v4_client()
    if compound:
        publish_compound_v4(client, coalesce_v4_units(units))
    else:
        unit, = units
        _publish_typed_batch_v4(client, unit.base,
            portfolio_reconciliation_differences=unit.differences)
    events = client.tables["trading_event_v1"]
    parents = client.tables["trading_portfolio_reconciliation_event_v1"]
    assert all(row["event_time"] == _datetime_wire(AT + delay, 9) for row in events)
    assert all(row["source_event_time"] == _datetime_wire(AT, 9) for row in parents)
    assert load_verified_v4_prefix(client, "run-1").last_sequence == len(units)


def test_projection_rejects_snapshot_after_decision():
    with pytest.raises(ValueError, match="future"):
        project(decision_delay=-timedelta(microseconds=1))


@pytest.mark.parametrize("field,value,message", (
    ("source_event_time", AT + timedelta(seconds=8), "Portfolio reconciliation differs"),
    ("account_key", "other", "Portfolio reconciliation differs"),
    ("account_id", "other", "differs from its parent event identity"),
    ("snapshot_id", "", "Portfolio reconciliation differs"),
    ("difference_hash", "invalid", "must have exactly 64 UTF-8 bytes"),
))
def test_delayed_reconciliation_still_rejects_invalid_parent_before_insert(field, value, message):
    unit, = project(decision_delay=timedelta(seconds=7))
    parent, = unit.base.portfolio_reconciliation_events
    batch = replace(unit.base, portfolio_reconciliation_events=({**parent, field: value},))
    client = attached_v4_client()
    with pytest.raises(ValueError, match=message):
        _publish_typed_batch_v4(client, batch,
            portfolio_reconciliation_differences=unit.differences)
    assert client.inserts == []


def test_positive_reconciliation_cannot_publish_without_its_children():
    unit, = project()
    client = attached_v4_client()
    with pytest.raises(ValueError):
        _publish_typed_batch_v4(client, unit.base)
    assert client.inserts == []
    with pytest.raises(ValueError):
        replace(unit, differences=unit.differences[:1])


def test_unrelated_batches_do_not_hash_empty_reconciliation_families(monkeypatch):
    from src.trading_runtime import arte_portfolio_reconciliation_v4 as module
    def unexpected(*args, **kwargs):
        raise AssertionError("Unrelated batch computed a reconciliation seal")
    monkeypatch.setattr(module, "seal_reconciliation_difference_family_v3", unexpected)
    module.seal_portfolio_reconciliation_v4((),
        ({"category": "risk", "entity_type": "continuous_risk_state"},), ())


def test_cold_read_rejects_rehashed_difference_with_changed_financial_values():
    unit, = project()
    client = attached_v4_client()
    _publish_typed_batch_v4(client, unit.base,
        portfolio_reconciliation_differences=unit.differences)
    row = client.tables[DIFFERENCE.name][0]
    client.tables[DIFFERENCE.name][0] = typed_row(DIFFERENCE.name,
        {**{k: v for k, v in row.items() if k != "content_hash"},
         "broker_quantity": row["broker_quantity"] + 1,
         "unattributed_quantity": row["unattributed_quantity"] + 1})
    with pytest.raises(RuntimeError, match="Portfolio reconciliation differs"):
        _load_verified_details_v4(client, run_id="run-1", batch_id=unit.base.batch_id,
            family_rows=client.tables["trading_commit_family_v4"], max_rows_per_family=100)


@pytest.mark.parametrize("events", (1, 2))
def test_actual_async_publisher_and_writer_fence_reconciliation_without_losing_rows(monkeypatch, events):
    from src.trading_runtime import arte_journal_writer as module
    from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher

    monkeypatch.setattr(module, "storage_preflight", lambda *a, **k: None)
    monkeypatch.setattr(module, "journal_permission_preflight", lambda *a, **k: None)
    monkeypatch.setattr(module, "_verify_run_identity",
        lambda *a, **k: dict(mode="backtest", account_ids=("DU1",)))
    client = attached_v4_client()
    journal = BacktestMemoryJournal(run_id="run-1")
    writer = module.ArteJournalWriter(client, run_id="run-1",
        journal_profile="backtest_v4", coalesce_batches=False)
    try:
        for i in range(events):
            source = _record([] if i else None)
            journal.append_many([dict(run_id=source.run_id, category=source.category,
                entity_type=source.entity_type, entity_id=source.entity_id,
                account_id=source.account_id, event_time=AT + timedelta(seconds=7),
                payload=dict(source.payload))])
        publisher = BacktestTypedJournalPublisher(journal, writer,
            attempt_id=ATTEMPT, run_month=date(2026, 8, 1))
        async def fence():
            task = publisher.enqueue_pending()
            assert not task.done()
            result = await publisher.await_fence()
            assert result.last_sequence == events
        asyncio.run(fence())
        assert journal.pending_record_count == 0
        assert load_verified_v4_prefix(client, "run-1").last_sequence == events
        assert len(client.tables[DIFFERENCE.name]) == 2
    finally:
        writer.close()
        journal.close()
