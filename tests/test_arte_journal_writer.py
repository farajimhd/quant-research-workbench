from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
import re
from threading import Event
from uuid import UUID

import pytest

from src.trading_runtime import arte_journal_writer as writer_module
from src.trading_runtime.arte_journal_projection import commission_revision_batch
from src.trading_runtime.domain import CommissionEvent
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


def test_typed_hash_uses_stored_utc_representation() -> None:
    source = {key: value for key, value in batch().events[0].items()
              if key != "content_hash"}
    source["event_time"] = "2026-08-18T04:05:00-04:00"
    source["recorded_at"] = "2026-08-18T04:05:01-04:00"
    row = typed_row("trading_event_v1", source)
    utc = {**source, "event_time": "2026-08-18T08:05:00Z",
           "recorded_at": "2026-08-18T08:05:01Z"}
    assert typed_row("trading_event_v1", utc)["content_hash"] == row["content_hash"]
    stored = writer_module._wire_row("trading_event_v1", row)
    assert writer_module._canonical_typed_content(
        "trading_event_v1", source,
    ) == writer_module._canonical_typed_content(
        "trading_event_v1", {key: value for key, value in stored.items()
                             if key != "content_hash"}, stored_utc=True,
    )


def test_event_partition_must_match_utc_event_month() -> None:
    source = dict(batch().events[0])
    source["event_month"] = "2026-07-01"
    source.pop("content_hash")
    item = TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                             1, 1, "bucket-1", "running", (source,))
    with pytest.raises(ValueError, match="partition differs"):
        publish_typed_batch(MemoryClient(), item)


def test_typed_journal_client_requires_a_separate_complete_identity(monkeypatch) -> None:
    for key in ("TRADING_JOURNAL_CLICKHOUSE_URL", "TRADING_JOURNAL_CLICKHOUSE_USER",
                "TRADING_JOURNAL_CLICKHOUSE_PASSWORD", "BACKTEST_CLICKHOUSE_USER"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="dedicated ClickHouse"):
        writer_module.journal_client_from_env()
    monkeypatch.setenv("TRADING_JOURNAL_CLICKHOUSE_URL", "http://localhost:8123")
    monkeypatch.setenv("TRADING_JOURNAL_CLICKHOUSE_USER", "journal-only")
    monkeypatch.setenv("TRADING_JOURNAL_CLICKHOUSE_PASSWORD", "test-only")
    monkeypatch.setenv("BACKTEST_CLICKHOUSE_USER", "journal-only")
    with pytest.raises(ValueError, match="differ from market-data readers"):
        writer_module.journal_client_from_env()
    monkeypatch.setenv("BACKTEST_CLICKHOUSE_USER", "market-reader")
    client = writer_module.journal_client_from_env()
    try:
        assert client.user == "journal-only"
        assert client.persistent
    finally:
        client.close()


def batch() -> TypedJournalBatch:
    event = typed_row("trading_event_v1", {
        "run_id": RUN, "event_month": "2026-08-01", "attempt_id": ATTEMPT,
        "batch_id": BATCH, "record_id": RECORD, "sequence": 1,
        "event_time": "2026-08-18T08:05:00+00:00",
        "recorded_at": "2026-08-18T08:05:01+00:00",
        "category": "run_state", "entity_type": "lifecycle", "entity_id": "run-1",
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
        self.selects: list[str] = []

    def execute(self, sql: str) -> str:
        if sql.startswith("INSERT INTO arte."):
            name = sql.split("arte.", 1)[1].split(" ", 1)[0]
            self.inserts.append(name)
            self.tables.setdefault(name, []).extend(json.loads(line) for line in sql.split("\n", 1)[1].splitlines())
            return ""
        assert sql.startswith("SELECT ")
        self.selects.append(sql)
        if "groupArray((toString(batch_id),toString(record_id)," in sql:
            ids = set(re.findall(r"toUUID\('([0-9a-f-]+)'\)", sql))
            names = re.findall(r"FROM arte\.([a-z0-9_]+) WHERE batch_id IN", sql)
            return json.dumps({name: [
                [row["batch_id"], row["record_id"], row["content_hash"]]
                for row in self.tables.get(name, []) if row["batch_id"] in ids
            ] for name in names})
        if "groupArray((toString(record_id),toString(content_hash)))" in sql:
            batch_id = sql.split("batch_id=toUUID('", 1)[1].split("'", 1)[0]
            names = re.findall(r"FROM arte\.([a-z0-9_]+) WHERE batch_id=", sql)
            return json.dumps({name: [
                [row["record_id"], row["content_hash"]]
                for row in self.tables.get(name, []) if row["batch_id"] == batch_id
            ] for name in names})
        if "WHERE batch_id IN (" in sql:
            ids = set(re.findall(r"toUUID\('([0-9a-f-]+)'\)", sql))
            name = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
            columns = sql.removeprefix("SELECT ").split(" FROM ", 1)[0].split(",")
            return "\n".join(json.dumps({column: row[column] for column in columns})
                             for row in self.tables.get(name, []) if row["batch_id"] in ids)
        name = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
        columns = sql.removeprefix("SELECT ").split(" FROM ", 1)[0].split(",")
        if "WHERE run_id=" in sql:
            run_id = sql.split("WHERE run_id='", 1)[1].split("'", 1)[0]
            matching = [row for row in self.tables.get(name, []) if row["run_id"] == run_id]
            for field in ("account_id", "execution_id"):
                marker = f"AND {field}='"
                if marker in sql:
                    value = sql.split(marker, 1)[1].split("'", 1)[0]
                    matching = [row for row in matching if row[field] == value]
            if "AND batch_id=toUUID('" in sql:
                value = sql.split("AND batch_id=toUUID('", 1)[1].split("'", 1)[0]
                matching = [row for row in matching if row["batch_id"] == value]
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
    assert len(client.selects) == 5
    assert publish_typed_batch(client, item) == BATCH
    assert client.inserts == ["trading_event_v1", "trading_commit_v1"]
    assert len(client.selects) == 7
    assert len(client.tables["trading_commit_v1"]) == 1
    assert not any("payload_json" in row for rows in client.tables.values() for row in rows)
    prefix = load_committed_prefix(client, RUN)
    assert len(client.selects) == 17
    assert prefix is not None
    assert (prefix.last_sequence, prefix.source_cursor, prefix.batch_ids) == (1, "bucket-1", (BATCH,))


def test_recovery_groups_batches_but_verifies_each_fence() -> None:
    client = MemoryClient()
    ids = ["00000000-0000-0000-0000-000000000031",
           "00000000-0000-0000-0000-000000000032",
           "00000000-0000-0000-0000-000000000033"]
    for index, batch_id in enumerate(ids):
        event = dict(batch().events[0])
        event.pop("content_hash")
        event.update(batch_id=batch_id, sequence=index + 1,
                     record_id=f"00000000-0000-0000-0000-{index + 31:012d}")
        item = TypedJournalBatch(
            RUN, date(2026, 8, 1), ATTEMPT, batch_id,
            ids[index - 1] if index else ZERO,
            index + 1, index + 1, f"bucket-{index + 1}",
            "completed" if index == 2 else "running", (event,),
        )
        publish_typed_batch(client, item)
    client.selects.clear()
    prefix = load_committed_prefix(client, RUN)
    assert prefix is not None and prefix.last_sequence == 3
    assert len(client.selects) == 10
    client.tables["trading_commit_v1"][0]["event_count"] = 0
    with pytest.raises(RuntimeError, match="not contiguous"):
        load_committed_prefix(client, RUN)
    client.tables["trading_commit_v1"][0]["event_count"] = 1
    client.tables["trading_event_v1"][1]["content_hash"] = "0" * 64
    with pytest.raises(RuntimeError, match="row content differs from its hash"):
        load_committed_prefix(client, RUN)


def test_recovery_detects_content_change_even_when_hash_column_is_unchanged() -> None:
    client = MemoryClient()
    publish_typed_batch(client, batch())
    client.tables["trading_event_v1"][0]["entity_id"] = "tampered"
    with pytest.raises(RuntimeError, match="row content differs from its hash"):
        load_committed_prefix(client, RUN)


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


def test_typed_detail_cannot_change_parent_account_or_partition() -> None:
    original = batch()
    event = {**dict(original.events[0]), "category": "snapshot",
             "entity_type": "portfolio", "account_id": "DU1"}
    event.pop("content_hash")
    detail = {
        "record_id": RECORD, "run_id": RUN, "event_month": "2026-08-01",
        "batch_id": BATCH, "snapshot_id": "s1", "account_id": "DU1",
        "currency": "USD", "net_liquidation": "1", "total_cash_value": "1",
        "buying_power": "1", "gross_position_value": "0", "available_funds": "1",
        "excess_liquidity": "1", "snapshot_complete": 1,
        "source_event_time": "2026-08-18T08:05:00+00:00",
    }
    for changed in ({"account_id": "OTHER"}, {"event_month": "2026-09-01"}):
        item = TypedJournalBatch(RUN, original.run_month, ATTEMPT, BATCH, ZERO,
                                 1, 1, "bucket-1", "running", (event,),
                                 account_snapshots=({**detail, **changed},))
        with pytest.raises(ValueError, match="differs from its parent"):
            publish_typed_batch(MemoryClient(), item)


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
    with pytest.raises(RuntimeError, match="row content differs from its hash"):
        load_committed_prefix(client, RUN)


def test_typed_publication_rejects_orphan_rows_in_empty_family() -> None:
    client = MemoryClient()
    client.tables["trading_execution_v1"] = [{
        "batch_id": BATCH, "record_id": RECORD, "content_hash": "0" * 64,
    }]
    with pytest.raises(RuntimeError, match="trading_execution_v1 has a conflicting"):
        publish_typed_batch(client, batch())
    assert "trading_commit_v1" not in client.inserts


def test_late_commission_requires_a_committed_execution() -> None:
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    fee = CommissionEvent("execution-1", "DU1", Decimal("1.25"), "USD",
                          source_event_time=at, received_at=at)
    item = commission_revision_batch(
        fee, run_id=RUN, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO, sequence=1,
        source_cursor="fee-1", run_status="running",
        time_authority="observation",
    )
    client = MemoryClient()
    with pytest.raises(RuntimeError, match="requires one committed execution"):
        publish_typed_batch(client, item)
    source_batch = "00000000-0000-0000-0000-000000000099"
    client.tables["trading_execution_v1"] = [{
        "record_id": RECORD, "batch_id": source_batch, "run_id": RUN,
        "account_id": "DU1", "execution_id": "execution-1",
    }]
    with pytest.raises(RuntimeError, match="requires one committed execution"):
        publish_typed_batch(client, item)
    client.tables["trading_commit_v1"] = [{
        "batch_id": source_batch, "run_id": RUN, "last_sequence": 1,
    }]
    continued = commission_revision_batch(
        fee, run_id=RUN, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=source_batch, sequence=2,
        source_cursor="fee-1", run_status="running",
        time_authority="observation",
    )
    assert publish_typed_batch(client, continued) == BATCH
    assert "trading_execution_v1" not in client.inserts


def test_event_details_are_required_and_unmapped_events_fail_closed() -> None:
    original = batch()
    base = dict(original.events[0])
    base.pop("content_hash")
    for category, entity_type in (("execution", "fill"),
                                  ("order_management", "order_command"),
                                  ("strategy_decision", "signal")):
        event = {**base, "category": category, "entity_type": entity_type}
        item = TypedJournalBatch(RUN, original.run_month, ATTEMPT, BATCH, ZERO,
                                 1, 1, "bucket-1", "running", (event,))
        with pytest.raises(ValueError, match="required typed detail|no typed contract"):
            publish_typed_batch(MemoryClient(), item)


def test_typed_publication_rejects_out_of_order_prefix() -> None:
    client = MemoryClient()
    item = batch()
    altered = TypedJournalBatch(item.run_id, item.run_month, item.attempt_id,
                                item.batch_id, item.prior_batch_id, 2, 2,
                                item.source_cursor, item.status,
                                (typed_row("trading_event_v1", {
                                    **{key: value for key, value in item.events[0].items()
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
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
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


def test_writer_coalesces_only_contiguous_unpublished_batches(monkeypatch) -> None:
    entered, release = Event(), Event()
    published = []
    ids = ["00000000-0000-0000-0000-000000000021",
           "00000000-0000-0000-0000-000000000022",
           "00000000-0000-0000-0000-000000000023"]

    def micro(sequence: int) -> TypedJournalBatch:
        event = dict(batch().events[0])
        event.pop("content_hash")
        event.update(batch_id=ids[sequence - 1], sequence=sequence,
                     record_id=f"00000000-0000-0000-0000-{sequence:012d}")
        return TypedJournalBatch(
            RUN, date(2026, 8, 1), ATTEMPT, ids[sequence - 1],
            ids[sequence - 2] if sequence > 1 else ZERO,
            sequence, sequence, f"bucket-{sequence}",
            "completed" if sequence == 3 else "running", (event,),
        )

    def record(_client, item):
        if item.first_sequence == 1:
            entered.set()
            assert release.wait(5)
        writer_module._sealed_families(item)
        published.append(item)
        return item.batch_id

    monkeypatch.setattr(writer_module, "publish_typed_batch", record)
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: None)
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=3)
    try:
        first = journal.submit(micro(1))
        assert entered.wait(5)
        second = journal.submit(micro(2))
        third = journal.submit(micro(3))
        assert not second.done() and not third.done()
        release.set()
        assert first.result(timeout=5) == ids[0]
        assert second.result(timeout=5) == third.result(timeout=5) == ids[2]
        assert [(row.first_sequence, row.last_sequence, row.batch_id)
                for row in published] == [(1, 1, ids[0]), (2, 3, ids[2])]
        assert [row["batch_id"] for row in published[1].events] == [ids[2], ids[2]]
    finally:
        release.set()
        journal.close()


def test_writer_failure_poisoning_is_visible_to_all_receipts(monkeypatch) -> None:
    def rejected(_client, _batch):
        raise OSError("ClickHouse unavailable")

    monkeypatch.setattr(writer_module, "publish_typed_batch", rejected)
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: None)
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=2)
    try:
        first = journal.submit(batch())
        with pytest.raises(OSError, match="unavailable"):
            first.result(timeout=5)
        with pytest.raises(RuntimeError, match="failed"):
            journal.submit(batch())
    finally:
        with pytest.raises(RuntimeError, match="did not drain durably"):
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
    assert pending.events[0]["entity_id"] == "run-1"
    assert publish_typed_batch(MemoryClient(), pending) == BATCH


def test_submission_rejects_nested_mutable_data_before_async_handoff() -> None:
    source = dict(batch().events[0])
    source["entity_id"] = {"mutable": "value"}
    with pytest.raises(ValueError, match="mutable or opaque"):
        TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                          1, 1, "bucket-1", "running", (source,))
