from __future__ import annotations

from datetime import date
import json
from threading import Event
from uuid import UUID

import pytest

from src.trading_runtime import arte_journal_writer as writer_module
from src.trading_runtime.arte_journal_writer import (
    ArteJournalWriter, JournalQueueFull, TypedJournalBatch, load_committed_prefix,
    publish_typed_batch, publish_typed_run, typed_row,
)


RUN = "live:account:session"
ATTEMPT = "00000000-0000-0000-0000-000000000011"
BATCH = "00000000-0000-0000-0000-000000000012"
RECORD = "00000000-0000-0000-0000-000000000013"
ZERO = "00000000-0000-0000-0000-000000000000"


def test_clickhouse_wire_time_preserves_utc_nanoseconds() -> None:
    assert writer_module._datetime_wire("2026-08-18T04:05:00.123456789-04:00", 9) == (
        "2026-08-18 08:05:00.123456789"
    )
    assert writer_module._datetime_wire("2026-08-18T08:05:00+00:00", 6) == (
        "2026-08-18 08:05:00.000000"
    )
    with pytest.raises(ValueError, match="Submicrosecond"):
        writer_module._datetime_wire("2026-08-18T08:05:00.123456789Z", 6)


def batch() -> TypedJournalBatch:
    event = typed_row({
        "run_id": RUN, "event_month": "2026-08-01", "attempt_id": ATTEMPT,
        "batch_id": BATCH, "record_id": RECORD, "sequence": 1,
        "event_time": "2026-08-18T08:05:00+00:00",
        "recorded_at": "2026-08-18T08:05:01+00:00",
        "category": "execution", "entity_type": "fill", "entity_id": "fill-1",
        "account_id": "DU1", "correlation_id": "c1", "causation_id": "k1",
    })
    return TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                             1, 1, "bucket-1", "running", (event,))


def run_row() -> dict:
    return {
        "run_id": RUN, "run_month": "2026-08-01", "mode": "backtest",
        "evaluation_interval_ms": 100, "session_date": "2026-08-18",
        "configuration_hash": "a" * 64, "code_hash": "b" * 64,
        "market_plan_token": "certified-build", "started_at": "2026-08-18T08:00:00+00:00",
    }


class MemoryClient:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.inserts: list[str] = []

    def execute(self, sql: str) -> str:
        if sql.startswith("INSERT INTO arte."):
            name = sql.split("arte.", 1)[1].split(" ", 1)[0]
            self.inserts.append(name)
            self.tables.setdefault(name, []).extend(json.loads(line) for line in sql.split("\n", 1)[1].splitlines())
            return ""
        assert sql.startswith("SELECT ")
        name = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
        columns = sql.removeprefix("SELECT ").split(" FROM ", 1)[0].split(",")
        if "WHERE run_id=" in sql:
            run_id = sql.split("WHERE run_id='", 1)[1].split("'", 1)[0]
            matching = [row for row in self.tables.get(name, []) if row["run_id"] == run_id]
            descending = "ORDER BY last_sequence DESC" in sql
            if "ORDER BY last_sequence" in sql:
                matching.sort(key=lambda row: row["last_sequence"], reverse=descending)
            return "\n".join(json.dumps({column: row[column] for column in columns})
                             for row in (matching[:1] if "LIMIT 1" in sql else matching))
        batch_id = sql.split("batch_id=toUUID('", 1)[1].split("')", 1)[0]
        return "\n".join(json.dumps({column: row[column] for column in columns})
                         for row in self.tables.get(name, []) if row["batch_id"] == batch_id)


def test_typed_publication_commits_last_and_retry_is_idempotent() -> None:
    client = MemoryClient()
    item = batch()
    assert publish_typed_batch(client, item) == BATCH
    assert client.inserts == ["trading_event_v1", "trading_commit_v1"]
    assert publish_typed_batch(client, item) == BATCH
    assert client.inserts == ["trading_event_v1", "trading_commit_v1"]
    assert len(client.tables["trading_commit_v1"]) == 1
    assert not any("payload_json" in row for rows in client.tables.values() for row in rows)
    prefix = load_committed_prefix(client, RUN)
    assert prefix is not None
    assert (prefix.last_sequence, prefix.source_cursor, prefix.batch_ids) == (1, "bucket-1", (BATCH,))


def test_order_command_and_transition_have_typed_durable_fences() -> None:
    first = dict(batch().events[0])
    first.pop("content_hash")
    first.update(category="order_management", entity_type="order_command")
    second_id = "00000000-0000-0000-0000-000000000014"
    second = {**first, "record_id": second_id, "sequence": 2,
              "entity_type": "order_transition"}
    common = {"run_id": RUN, "event_month": "2026-08-01", "batch_id": BATCH,
              "account_id": "DU1", "command_id": "command-1",
              "client_order_id": "client-1", "conid": 123, "ticker": "TEST"}
    command = {**common, "record_id": RECORD, "side": "BUY", "order_type": "LIMIT",
               "time_in_force": "DAY", "quantity": "5.0000000000", "cash_quantity": None,
               "limit_price": "12.3400000000", "stop_price": None, "outside_rth": 1,
               "parent_command_id": "", "oca_group": "", "strategy_id": "strategy-1",
               "strategy_revision": 2, "created_at": "2026-08-18T08:05:00+00:00"}
    transition = {**common, "record_id": second_id, "broker_order_id": "broker-1",
                  "status": "submitted", "broker_status": "Submitted",
                  "total_quantity": "5.0000000000", "filled_quantity": "0.0000000000",
                  "remaining_quantity": "5.0000000000", "average_fill_price": None,
                  "can_modify": 1, "can_cancel": 1, "terminal": 0,
                  "rejection_code": "", "rejection_reason": "",
                  "source_event_time": "2026-08-18T08:05:01+00:00",
                  "received_at": "2026-08-18T08:05:01+00:00"}
    item = TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                             1, 2, "bucket-2", "completed", (first, second),
                             order_commands=(command,), order_transitions=(transition,))
    client = MemoryClient()
    assert publish_typed_batch(client, item) == BATCH
    assert client.inserts == ["trading_event_v1", "trading_order_command_v1",
                              "trading_order_transition_v1", "trading_commit_v1"]
    assert load_committed_prefix(client, RUN).last_sequence == 2


def test_position_snapshot_requires_account_snapshot_in_same_batch() -> None:
    base = dict(batch().events[0])
    base.pop("content_hash")
    account_id = "00000000-0000-0000-0000-000000000015"
    position_id = "00000000-0000-0000-0000-000000000016"
    events = ({**base, "record_id": account_id, "sequence": 1,
               "category": "snapshot", "entity_type": "portfolio"},
              {**base, "record_id": position_id, "sequence": 2,
               "category": "snapshot", "entity_type": "position"})
    common = {"run_id": RUN, "event_month": "2026-08-01", "batch_id": BATCH,
              "snapshot_id": "snapshot-1", "account_id": "DU1",
              "source_event_time": "2026-08-18T08:05:00+00:00"}
    account = {**common, "record_id": account_id, "currency": "USD",
               "net_liquidation": "1000.0000000000", "total_cash_value": "900.0000000000",
               "buying_power": "900.0000000000", "gross_position_value": "100.0000000000",
               "available_funds": "800.0000000000", "excess_liquidity": "700.0000000000",
               "snapshot_complete": 1}
    position = {**common, "record_id": position_id, "conid": 123,
                "ticker": "TEST", "currency": "USD", "asset_class": "STK",
                "quantity": "10.0000000000", "market_price": "10.0000000000",
                "market_value": "100.0000000000", "average_cost": "9.0000000000",
                "average_price": "9.0000000000", "realized_pnl": "0.0000000000",
                "unrealized_pnl": "10.0000000000"}
    item = TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                             1, 2, "bucket-2", "completed", events,
                             account_snapshots=(account,), position_snapshots=(position,))
    client = MemoryClient()
    assert publish_typed_batch(client, item) == BATCH
    assert load_committed_prefix(client, RUN).last_sequence == 2
    orphan = TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                               1, 2, "bucket-2", "completed", events,
                               position_snapshots=(position,))
    with pytest.raises(ValueError, match="lacks its complete account snapshot"):
        publish_typed_batch(MemoryClient(), orphan)


def test_run_identity_is_immutable_and_uses_typed_rows() -> None:
    client = MemoryClient()
    row = run_row()
    assert publish_typed_run(client, row) == RUN
    assert publish_typed_run(client, row) == RUN
    assert client.inserts == ["trading_run_v1"]
    with pytest.raises(RuntimeError, match="conflicts"):
        publish_typed_run(client, {**row, "code_hash": "c" * 64})


def test_typed_publication_detects_conflicting_readback() -> None:
    client = MemoryClient()
    item = batch()
    publish_typed_batch(client, item)
    client.tables["trading_event_v1"][0]["content_hash"] = "0" * 64
    with pytest.raises(RuntimeError, match="conflicting"):
        publish_typed_batch(client, item)
    with pytest.raises(RuntimeError, match="differs from committed fence"):
        load_committed_prefix(client, RUN)


def test_typed_publication_rejects_orphan_rows_in_empty_family() -> None:
    client = MemoryClient()
    client.tables["trading_execution_v1"] = [{
        "batch_id": BATCH, "record_id": RECORD, "content_hash": "0" * 64,
    }]
    with pytest.raises(RuntimeError, match="trading_execution_v1 has a conflicting"):
        publish_typed_batch(client, batch())
    assert "trading_commit_v1" not in client.inserts


def test_typed_publication_rejects_out_of_order_prefix() -> None:
    client = MemoryClient()
    item = batch()
    altered = TypedJournalBatch(item.run_id, item.run_month, item.attempt_id,
                                item.batch_id, item.prior_batch_id, 2, 2,
                                item.source_cursor, item.status,
                                (typed_row({**{key: value for key, value in item.events[0].items()
                                               if key != "content_hash"}, "sequence": 2}),))
    with pytest.raises(RuntimeError, match="committed prefix"):
        publish_typed_batch(client, altered)
    assert not client.inserts


def test_submission_never_waits_for_network_or_queue_space(monkeypatch) -> None:
    entered, release = Event(), Event()

    def stalled(_client, item):
        entered.set()
        assert release.wait(5)
        return item.batch_id

    monkeypatch.setattr(writer_module, "publish_typed_batch", stalled)
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: None)
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=1)
    try:
        first = journal.submit(batch())
        assert entered.wait(5)
        second = journal.submit(batch())
        with pytest.raises(JournalQueueFull):
            journal.submit(batch())
        assert not first.done() and not second.done()
        release.set()
        assert UUID(first.result(timeout=5)) == UUID(BATCH)
        assert UUID(second.result(timeout=5)) == UUID(BATCH)
    finally:
        release.set()
        journal.close()


def test_writer_failure_poisoning_is_visible_to_all_receipts(monkeypatch) -> None:
    def rejected(_client, _batch):
        raise OSError("ClickHouse unavailable")

    monkeypatch.setattr(writer_module, "publish_typed_batch", rejected)
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: None)
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=2)
    try:
        first = journal.submit(batch())
        with pytest.raises(OSError, match="unavailable"):
            first.result(timeout=5)
        with pytest.raises(RuntimeError, match="failed"):
            journal.submit(batch())
    finally:
        journal.close()


def test_invalid_family_row_is_rejected_before_publication() -> None:
    item = batch()
    altered = dict(item.events[0])
    altered["payload_json"] = "{}"
    invalid = TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                                1, 1, "bucket-1", "running", (altered,))
    with pytest.raises(ValueError, match="typed columns"):
        publish_typed_batch(MemoryClient(), invalid)


def test_submission_snapshot_is_immutable_and_hashing_stays_off_caller(monkeypatch) -> None:
    source = dict(batch().events[0])
    source.pop("content_hash")
    with monkeypatch.context() as patch:
        patch.setattr(writer_module, "canonical_json", lambda _value: (_ for _ in ()).throw(
            AssertionError("serialization occurred on caller")))
        pending = TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                                    1, 1, "bucket-1", "running", (source,))
    source["entity_id"] = "changed-after-submit"
    assert pending.events[0]["entity_id"] == "fill-1"
    assert publish_typed_batch(MemoryClient(), pending) == BATCH
