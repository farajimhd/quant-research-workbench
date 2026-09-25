"""Writer-only V3 reconciliation handoff and timestamp contract."""
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
import re

import pytest

from src.backend.backtest_reconciliation_v3 import project_reconciliation_v3
from src.trading_runtime.arte_journal_writer import (
    TypedJournalBatch, V3SqueezeBatch, _sealed_families,
    publish_typed_squeeze_batch_v3,
)
from src.trading_runtime import arte_journal_writer as writer_module
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.portfolio import PortfolioReconciliationDifference
from tests.test_arte_journal_writer import ATTEMPT, BATCH, RECORD, RUN, ZERO, MemoryClient


AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


class V3Client(MemoryClient):
    def execute(self, sql):
        if "groupArray((toString(record_id),toString(content_hash)))" in sql:
            batch_id = re.search(r"batch_id=toUUID\('([0-9a-f-]+)'\)", sql).group(1)
            names = re.findall(
                r"FROM arte\.([a-z0-9_]+) WHERE batch_id=.*?\) AS ([a-z0-9_]+)", sql)
            return json.dumps({alias: [[row["record_id"], row["content_hash"]]
                               for row in self.tables.get(table, [])
                               if row["batch_id"] == batch_id]
                               for table, alias in names})
        return super().execute(sql)


def _client(monkeypatch):
    monkeypatch.setattr(writer_module, "_v3_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run: {
        "mode": "backtest"})
    return V3Client()


def _unit(*, observed=AT):
    difference = PortfolioReconciliationDifference(
        "primary", "AAA", 5.0, 2.0, 3.0, observed)
    record = JournalRecord(RECORD, RUN, 1, AT + timedelta(seconds=1),
                           AT + timedelta(seconds=1),
                           "portfolio_management", "portfolio_reconciliation",
                           "primary", "DU1", {
                               "event": "portfolio_reconciliation_completed",
                               "snapshot_id": "snapshot-1",
                               "snapshot_observed_at": observed,
                               "difference_count": 1,
                               "differences": [asdict(difference)],
                               "correlation_id": "run:1",
                               "causation_id": "event:1",
                           })
    projected = project_reconciliation_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH, account_key="primary")
    base = TypedJournalBatch(
        RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
        1, 1, "boundary-1", "running", (projected.event,),
        portfolio_reconciliation_events=(projected.parent,))
    return V3SqueezeBatch(base, (), reconciliation_differences=projected.differences)


def test_v3_reconciliation_accepts_causal_snapshot_time_only_in_v3():
    unit = _unit()
    sealed = dict(_sealed_families(unit.base, v3_reconciliation=True))
    assert len(sealed["trading_portfolio_reconciliation_event_v1"]) == 1
    assert sealed["trading_portfolio_reconciliation_event_v1"][0][
        "source_event_time"] == AT.isoformat(timespec="microseconds")
    with pytest.raises(ValueError, match="differs from its journal event"):
        _sealed_families(unit.base)
    assert len(unit.reconciliation_differences) == 1


def test_v3_reconciliation_rejects_future_snapshot_before_insert():
    unit = _unit()
    parent = dict(unit.base.portfolio_reconciliation_events[0])
    parent["source_event_time"] = (AT + timedelta(seconds=2)).isoformat()
    bad = TypedJournalBatch(
        RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
        1, 1, "boundary-1", "running", unit.base.events,
        portfolio_reconciliation_events=(parent,))
    with pytest.raises(ValueError, match="differs from its journal event"):
        _sealed_families(bad, v3_reconciliation=True)


def test_v3_reconciliation_child_commits_last_and_retry_is_idempotent(monkeypatch):
    client = _client(monkeypatch)
    unit = _unit()
    publish_typed_squeeze_batch_v3(client, unit)
    assert client.inserts == ["trading_event_v1",
                              "trading_portfolio_reconciliation_event_v1",
                              "trading_portfolio_reconciliation_difference_v3",
                              "trading_commit_v3"]
    commit = client.tables["trading_commit_v3"][0]
    assert commit["portfolio_reconciliation_difference_count"] == 1
    writer_module._verify_recovery_chunk(client, [commit], journal_profile="backtest_v3")
    publish_typed_squeeze_batch_v3(client, unit)
    assert len(client.tables["trading_commit_v3"]) == 1
    assert len(client.tables["trading_portfolio_reconciliation_difference_v3"]) == 1
    client.tables["trading_portfolio_reconciliation_difference_v3"][0][
        "broker_quantity"] = 999.0
    with pytest.raises(RuntimeError, match="durable readback"):
        publish_typed_squeeze_batch_v3(client, unit)
    assert len(client.tables["trading_commit_v3"]) == 1


def test_v3_reconciliation_missing_child_fails_before_insert(monkeypatch):
    client = _client(monkeypatch)
    unit = _unit()
    with pytest.raises(ValueError):
        publish_typed_squeeze_batch_v3(client, V3SqueezeBatch(unit.base, ()))
    assert client.inserts == []


def test_v3_reconciliation_tampered_readback_never_commits(monkeypatch):
    class TamperClient(V3Client):
        def execute(self, sql):
            result = super().execute(sql)
            if sql.startswith("INSERT INTO arte.trading_portfolio_reconciliation_difference_v3"):
                self.tables["trading_portfolio_reconciliation_difference_v3"][0][
                    "broker_quantity"] = 999.0
            return result

    _client(monkeypatch)
    client = TamperClient()
    with pytest.raises(RuntimeError, match="durable readback"):
        publish_typed_squeeze_batch_v3(client, _unit())
    assert "trading_commit_v3" not in client.inserts
