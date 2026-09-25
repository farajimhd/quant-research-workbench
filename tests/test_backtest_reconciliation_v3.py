from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.backend.backtest_reconciliation_v3 import project_reconciliation_v3
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.portfolio import PortfolioReconciliationDifference


AT = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
BATCH = str(uuid4())
ATTEMPT = str(uuid4())


def _record(differences=None):
    if differences is None:
        differences = [asdict(PortfolioReconciliationDifference(
            "primary", ticker, broker, attributed, broker - attributed, AT))
            for ticker, broker, attributed in (("AAA", 5.0, 2.0), ("BBB", 3.0, 0.0))]
    return JournalRecord(str(uuid4()), "run-1", 1, AT, AT,
                         "portfolio_management", "portfolio_reconciliation",
                         "primary", "DU1", {
                             "event": "portfolio_reconciliation_completed",
                             "snapshot_id": "snapshot-1",
                             "snapshot_observed_at": AT,
                             "difference_count": len(differences),
                             "differences": differences,
                             "correlation_id": "run:run-1",
                             "causation_id": "event:reconciliation-1",
                         })


def test_exact_reconciliation_projects_ordered_typed_children_and_seal():
    record = _record()
    result = project_reconciliation_v3(record, attempt_id=ATTEMPT,
                                       batch_id=BATCH, account_key="primary")
    assert result.event["correlation_id"] == record.payload["correlation_id"]
    assert result.event["causation_id"] == record.payload["causation_id"]
    assert result.parent["difference_count"] == 2
    assert [row["ticker"] for row in result.differences] == ["AAA", "BBB"]
    assert [row["ordinal"] for row in result.differences] == [0, 1]
    assert all(row["parent_record_id"] == record.record_id for row in result.differences)
    assert all(type(row["broker_quantity"]) is float for row in result.differences)
    assert len(result.parent["difference_hash"]) == 64
    assert len(result.parent["content_hash"]) == 64
    empty = project_reconciliation_v3(_record([]), attempt_id=ATTEMPT, batch_id=BATCH,
                                      account_key="primary")
    assert empty.parent["difference_count"] == 0 and empty.differences == ()


@pytest.mark.parametrize("change", [
    lambda rows: rows.reverse(),
    lambda rows: rows.append(dict(rows[0])),
    lambda rows: rows[0].update(account_key="other"),
    lambda rows: rows[0].update(extra="opaque"),
    lambda rows: rows[0].update(broker_quantity=float("nan")),
    lambda rows: rows[0].update(unattributed_quantity=4.0),
    lambda rows: rows[0].update(observed_at=AT + timedelta(seconds=1)),
])
def test_reconciliation_rejects_loss_duplicate_order_and_future(change):
    rows = _record().payload["differences"]
    change(rows)
    with pytest.raises(ValueError):
        project_reconciliation_v3(_record(rows), attempt_id=ATTEMPT, batch_id=BATCH,
                                  account_key="primary")


def test_reconciliation_rejects_wrong_payload_and_account():
    record = _record()
    record.payload["unmodeled"] = {"opaque": True}
    with pytest.raises(ValueError):
        project_reconciliation_v3(record, attempt_id=ATTEMPT,
                                  batch_id=BATCH, account_key="primary")
    with pytest.raises(ValueError):
        project_reconciliation_v3(_record(), attempt_id=ATTEMPT,
                                  batch_id=BATCH, account_key="other")


def test_empty_snapshot_requires_embedded_observed_time_anchor():
    record = _record([])
    record.payload.pop("snapshot_observed_at")
    with pytest.raises(ValueError, match="exact completed snapshot"):
        project_reconciliation_v3(record, attempt_id=ATTEMPT,
                                  batch_id=BATCH, account_key="primary")
    record.payload["snapshot_observed_at"] = AT + timedelta(seconds=1)
    with pytest.raises(ValueError, match="future"):
        project_reconciliation_v3(record, attempt_id=ATTEMPT,
                                  batch_id=BATCH, account_key="primary")


def test_actual_backtest_memory_journal_lineage_projects_without_loss():
    source = _record()
    payload = {key: value for key, value in source.payload.items()
               if key not in {"correlation_id", "causation_id"}}
    journal = BacktestMemoryJournal(run_id="run-1")
    actual = journal.append_many([{
        "run_id": "run-1", "category": "portfolio_management",
        "entity_type": "portfolio_reconciliation", "entity_id": "primary",
        "account_id": "DU1", "event_time": AT, "payload": payload,
    }])[0]
    result = project_reconciliation_v3(
        actual, attempt_id=ATTEMPT, batch_id=BATCH,
        account_key="primary")
    assert result.event["correlation_id"] == actual.payload["correlation_id"]
    assert result.event["causation_id"] == actual.payload["causation_id"]
    assert result.parent["difference_count"] == 2
    journal.close()


def test_reconciliation_requires_exact_lineage_pair():
    record = _record()
    record.payload.pop("causation_id")
    with pytest.raises(ValueError, match="exact completed snapshot"):
        project_reconciliation_v3(record, attempt_id=ATTEMPT, batch_id=BATCH,
                                  account_key="primary")
    record.payload["causation_id"] = ""
    with pytest.raises(ValueError, match="exact completed snapshot"):
        project_reconciliation_v3(record, attempt_id=ATTEMPT, batch_id=BATCH,
                                  account_key="primary")
