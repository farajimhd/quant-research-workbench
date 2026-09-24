from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from src.trading_runtime.arte_journal_projection import broker_fill_batch, broker_fill_details
from src.trading_runtime.arte_journal_schema import TABLES
from src.trading_runtime.arte_journal_writer import TypedJournalBatch, _sealed_families
from src.trading_runtime.ibkr_client import _execution


AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


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
