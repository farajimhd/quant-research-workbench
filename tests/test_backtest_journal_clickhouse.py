from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import json
import os
import unittest
from unittest.mock import patch
from uuid import UUID

import pytest

from src.backend.backtest_journal_clickhouse import (
    BacktestJournalWriter, backtest_code_hash, journal_clickhouse_client,
    load_fenced_checkpoint,
    prepare_batch, prepare_fence, publish_batch, publish_fence, publish_run,
    schema_ddl, storage_preflight,
)
from src.backend.backtest_journal_clickhouse import _COLUMNS, _LAYOUT, _insert
from src.backend.backtest_journal_memory import BacktestJournalPublisher, BacktestMemoryJournal
from src.trading_runtime.clickhouse import _journal_row
from src.trading_runtime.journal import JournalRecord
from src.trading_runtime.journal_contract import journal_row


RUN = "00000000-0000-0000-0000-000000000010"
ATTEMPT = "00000000-0000-0000-0000-000000000020"


def test_source_identity_changes_with_deployed_python(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "research").mkdir()
    source = tmp_path / "src" / "runtime.py"
    source.write_text("VERSION = 1\n", encoding="utf-8")
    (tmp_path / "research" / "helper.py").write_text("x = 1\n", encoding="utf-8")
    first = backtest_code_hash(tmp_path)
    assert first == backtest_code_hash(tmp_path)
    source.write_text("VERSION = 2\n", encoding="utf-8")
    assert first != backtest_code_hash(tmp_path)


def test_journal_insert_cannot_target_market_products():
    with pytest.raises(ValueError, match="outside its four journal tables"):
        _insert(_Client(), "arte.bars_v1", ({"ticker": "TEST"},), "token")



def record(sequence: int) -> JournalRecord:
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    return JournalRecord(
        str(UUID(int=sequence)), RUN, sequence, at, at,
        "strategy_decision", "signal", f"signal-{sequence}", "paper",
        {"reason": "accepted", "intent_id": f"intent-{sequence}",
         "decision_levels": [{"level": 1.25, "side": 1}]},
    )


class _Client:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.calls: list[str] = []

    def execute(self, sql: str) -> str:
        self.calls.append(sql)
        if sql.startswith("INSERT INTO "):
            table = sql.split()[2]
            body = sql.split("FORMAT JSONEachRow\n", 1)[1]
            self.tables.setdefault(table, []).extend(json.loads(line) for line in body.splitlines())
            return ""
        if "FROM arte.bt_event_v1" in sql:
            rows = self.tables.get("arte.bt_event_v1", [])
            if "AND sequence BETWEEN " in sql:
                bounds = sql.split("AND sequence BETWEEN ", 1)[1].split(" ORDER BY", 1)[0]
                start, end = (int(value) for value in bounds.split(" AND "))
                rows = [row for row in rows if start <= row["sequence"] <= end]
            if "AND batch_id=toUUID('" in sql:
                selected = sql.split("AND batch_id=toUUID('", 1)[1].split("'", 1)[0]
                rows = [row for row in rows if row["batch_id"] == selected]
            if sql.startswith("SELECT toString(batch_id)"):
                return "\n".join(json.dumps({key: row[key] for key in
                    ("batch_id", "sequence", "record_id", "payload_hash")}) for row in rows)
            return "\n".join(json.dumps({key: row[key] for key in
                ("sequence", "record_id", "payload_hash")}) for row in rows)
        if "FROM arte.bt_commit_v1" in sql:
            rows = self.tables.get("arte.bt_commit_v1", [])
            if sql.startswith("SELECT run_id,"):
                return "\n".join(json.dumps(row) for row in rows)
            if sql.startswith("SELECT last_sequence"):
                selected = sql.split("AND fence_id=toUUID('", 1)[1].split("'", 1)[0]
                rows = [row for row in rows if row["fence_id"] == selected]
                return "\n".join(json.dumps({"last_sequence": row["last_sequence"]})
                                 for row in rows)
            selected = sql.split("AND fence_id=toUUID('", 1)[1].split("'", 1)[0]
            rows = [row for row in rows if row["fence_id"] == selected]
            return "\n".join(json.dumps({key: row[key] for key in
                ("batch_hash", "checkpoint_hash", "first_sequence",
                 "last_sequence", "event_count")}) for row in rows)
        if "FROM arte.bt_run_v1" in sql:
            rows = self.tables.get("arte.bt_run_v1", [])
            return "\n".join(json.dumps({key: row[key] for key in
                ("definition_hash", "configuration_hash", "market_plan_token",
                 "v7_plan_token", "code_hash", "contract_version")}) for row in rows)
        if "FROM arte.bt_blob_v1" in sql:
            digest = sql.split("WHERE sha256='", 1)[1].split("'", 1)[0]
            return "\n".join(json.dumps({"payload_json": row["payload_json"]})
                             for row in self.tables.get("arte.bt_blob_v1", [])
                             if row["sha256"] == digest)
        raise AssertionError(sql)


class BacktestJournalClickHouseTests(unittest.TestCase):
    def test_journal_client_requires_separate_credentials(self) -> None:
        settings = {
            "BACKTEST_CLICKHOUSE_USER": "market_reader",
            "BACKTEST_JOURNAL_CLICKHOUSE_URL": "http://localhost:8123",
            "BACKTEST_JOURNAL_CLICKHOUSE_USER": "market_reader",
            "BACKTEST_JOURNAL_CLICKHOUSE_PASSWORD": "test-only",
        }
        with patch.dict(os.environ, settings, clear=True):
            with self.assertRaisesRegex(ValueError, "must not share"):
                journal_clickhouse_client()
            os.environ["BACKTEST_JOURNAL_CLICKHOUSE_USER"] = "journal_writer"
            with patch("research.mlops.clickhouse.ClickHouseHttpClient") as factory:
                journal_clickhouse_client()
                args, kwargs = factory.call_args
                self.assertEqual(args[:2], ("http://localhost:8123", "journal_writer"))
                self.assertNotIn("readonly", kwargs["default_query_params"])

    def test_storage_policy_and_part_placement_fail_closed(self) -> None:
        class Catalog:
            def execute(self, sql: str) -> str:
                if "system.storage_policies" in sql:
                    return json.dumps({"disks": ["live_market_ssd"]})
                if "system.tables" in sql:
                    return "\n".join(json.dumps({"name": name,
                        "storage_policy": "live_market_ssd", "engine": "MergeTree",
                        "partition_key": _LAYOUT[name][0], "sorting_key": _LAYOUT[name][1]})
                        for name in _COLUMNS)
                if "system.columns" in sql:
                    typed = {
                        ("bt_event_v1", "event_time"): "DateTime64(9, 'UTC')",
                        ("bt_event_v1", "sequence"): "UInt64",
                        ("bt_event_v1", "payload_hash"): "FixedString(64)",
                        ("bt_blob_v1", "sha256"): "FixedString(64)",
                        ("bt_commit_v1", "batch_ids"): "Array(UUID)",
                        ("bt_commit_v1", "checkpoint_hash"): "FixedString(64)",
                    }
                    return "\n".join(json.dumps({"table": table, "name": name,
                        "type": typed.get((table, name), "String")})
                        for table, names in _COLUMNS.items() for name in names)
                if "system.parts" in sql:
                    return ""
                raise AssertionError(sql)
        storage_preflight(Catalog())
        class Misplaced(Catalog):
            def execute(self, sql: str) -> str:
                if "system.parts" in sql:
                    return json.dumps({"table": "bt_event_v1", "disk_name": "default"})
                return super().execute(sql)
        with self.assertRaisesRegex(ValueError, "outside"):
            storage_preflight(Misplaced())
        class WrongLayout(Catalog):
            def execute(self, sql: str) -> str:
                if "system.tables" in sql:
                    rows = [json.loads(line) for line in super().execute(sql).splitlines()]
                    rows[0]["sorting_key"] = "run_month"
                    return "\n".join(json.dumps(row) for row in rows)
                return super().execute(sql)
        with self.assertRaisesRegex(ValueError, "partition/order"):
            storage_preflight(WrongLayout())

    def test_live_and_backtest_share_logical_envelope(self) -> None:
        value = record(1)
        self.assertEqual(_journal_row(value), journal_row(value))

    def test_checkpoint_fence_is_published_last(self) -> None:
        batch = prepare_batch(
            records=[record(1), record(2)], attempt_id=ATTEMPT,
            run_date=date(2026, 8, 18),
        )
        self.assertEqual(batch.first_sequence, 1)
        self.assertEqual(batch.last_sequence, 2)
        self.assertEqual(len(batch.events), 2)
        self.assertGreaterEqual(len(batch.blobs), 1)
        client = _Client()
        self.assertEqual(publish_batch(client, batch), batch.batch_id)
        self.assertNotIn("arte.bt_commit_v1", client.tables)
        fence = prepare_fence(batches=[batch], checkpoint={"broker": {"cash": 100}},
                              source_cursor="2026-08-18:04:05:00")
        self.assertEqual(publish_fence(client, fence), fence.fence_id)
        self.assertEqual([call.split()[2] for call in client.calls if call.startswith("INSERT INTO ")],
                         ["arte.bt_blob_v1", "arte.bt_event_v1",
                          "arte.bt_blob_v1", "arte.bt_commit_v1"])
        restored = load_fenced_checkpoint(client, RUN)
        self.assertEqual(restored["batch_ids"], (batch.batch_id,))
        self.assertEqual(restored["sequence"], 2)
        self.assertEqual(restored["state"], {"broker": {"cash": 100}})
        self.assertTrue(all("storage_policy='live_market_ssd'" in sql for sql in schema_ddl()))
        next_batch = prepare_batch(records=[record(3)], attempt_id=ATTEMPT,
                                   run_date=date(2026, 8, 18))
        with self.assertRaisesRegex(ValueError, "predecessor"):
            prepare_fence(batches=[next_batch], checkpoint={"cash": 99},
                          source_cursor="next")
        publish_batch(client, next_batch)
        next_fence = prepare_fence(batches=[next_batch], checkpoint={"cash": 99},
                                   source_cursor="next", prior_last_sequence=2,
                                   prior_fence_id=fence.fence_id)
        publish_fence(client, next_fence)
        self.assertEqual(load_fenced_checkpoint(client, RUN)["sequence"], 3)
        # The latest range remains sound, but an older committed event is
        # corrupt. Recovery must reject the whole chain.
        client.tables["arte.bt_event_v1"][0]["payload_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "recovery event range"):
            load_fenced_checkpoint(client, RUN)

    def test_run_definition_is_content_addressed_and_idempotent(self) -> None:
        client = _Client()
        values = dict(run_id=RUN, run_date=date(2026, 8, 18),
                      definition={"mode": "backtest", "execution_interval": "100ms"},
                      configuration_hash="a" * 64, market_plan_token="market",
                      v7_plan_token="v7", code_hash="b" * 64)
        first = publish_run(client, **values)
        second = publish_run(client, **values)
        self.assertEqual(first, second)
        self.assertEqual(len(client.tables["arte.bt_run_v1"]), 1)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            publish_run(client, **{**values, "code_hash": "c" * 64})

    def test_noncontiguous_sequence_and_changed_readback_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "contiguous"):
            prepare_batch(records=[record(1), record(3)], attempt_id=ATTEMPT,
                          run_date=date(2026, 8, 18))
        batch = prepare_batch(records=[record(1)], attempt_id=ATTEMPT,
                              run_date=date(2026, 8, 18))
        class Corrupt(_Client):
            def execute(self, sql: str) -> str:
                response = super().execute(sql)
                if "FROM arte.bt_event_v1" in sql:
                    row = json.loads(response)
                    row["payload_hash"] = "0" * 64
                    return json.dumps(row)
                return response
        with self.assertRaisesRegex(ValueError, "not durably published"):
            publish_batch(Corrupt(), batch)

    def test_bounded_worker_fences_after_staged_batch(self) -> None:
        async def exercise() -> None:
            client = _Client()
            writer = BacktestJournalWriter(client, pending_batches=1)
            try:
                batch = prepare_batch(records=[record(1)], attempt_id=ATTEMPT,
                                      run_date=date(2026, 8, 18))
                staged = await writer.stage(batch)
                fence = prepare_fence(batches=[batch], checkpoint={"broker": {"cash": 100}},
                                      source_cursor="cursor")
                self.assertEqual(await writer.fence(fence), fence.fence_id)
                self.assertEqual(staged.result(), batch.batch_id)
            finally:
                await writer.close()
        asyncio.run(exercise())

    def test_unfenced_tail_is_not_recoverable(self) -> None:
        client = _Client()
        batch = prepare_batch(records=[record(1)], attempt_id=ATTEMPT,
                              run_date=date(2026, 8, 18))
        publish_batch(client, batch)
        self.assertIsNone(load_fenced_checkpoint(client, RUN))

    def test_memory_journal_publishes_and_releases_capacity_only_after_fence(self) -> None:
        async def exercise() -> None:
            client = _Client()
            writer = BacktestJournalWriter(client, pending_batches=1)
            journal = BacktestMemoryJournal(run_id=RUN, max_pending_records=2)
            publisher = BacktestJournalPublisher(journal, writer, attempt_id=ATTEMPT,
                                                 run_date=date(2026, 8, 18), batch_size=1)
            at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
            def append(number: int) -> None:
                journal.append(run_id=RUN, category="strategy_decision",
                               entity_type="signal", entity_id=f"signal-{number}",
                               payload={"intent_id": f"intent-{number}"}, event_time=at)
            try:
                append(1)
                await publisher.stage_pending()
                self.assertIsNone(load_fenced_checkpoint(client, RUN))
                append(2)
                with self.assertRaisesRegex(RuntimeError, "buffer is full"):
                    append(3)
                authority = journal.reference_json({"source": "arte", "v": 1})
                await publisher.fence_checkpoint(state={"broker": {"cash": 100},
                                                         "authority": authority},
                                                 source_cursor="bucket-2")
                self.assertEqual(publisher.fenced_sequence, 2)
                self.assertEqual(len(publisher.committed_batch_ids), 2)
                restored_first = load_fenced_checkpoint(client, RUN)
                self.assertEqual(restored_first["sequence"], 2)
                self.assertEqual(restored_first["state"]["authority"],
                                 {"source": "arte", "v": 1})
                append(3)
                await publisher.fence_checkpoint(state={"broker": {"cash": 99}},
                                                 source_cursor="bucket-3")
                restored = load_fenced_checkpoint(client, RUN)
                self.assertEqual((restored["sequence"], restored["source_cursor"]),
                                 (3, "bucket-3"))
            finally:
                await writer.close()
        asyncio.run(exercise())

    def test_failed_fence_does_not_release_memory_journal_capacity(self) -> None:
        class RejectCommit(_Client):
            def execute(self, sql: str) -> str:
                if sql.startswith("INSERT INTO arte.bt_commit_v1"):
                    raise OSError("commit unavailable")
                return super().execute(sql)

        async def exercise() -> None:
            writer = BacktestJournalWriter(RejectCommit())
            journal = BacktestMemoryJournal(run_id=RUN, max_pending_records=1)
            publisher = BacktestJournalPublisher(journal, writer, attempt_id=ATTEMPT,
                                                 run_date=date(2026, 8, 18), batch_size=1)
            at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
            journal.append(run_id=RUN, category="strategy", entity_type="signal",
                           entity_id="one", payload={}, event_time=at)
            try:
                with self.assertRaisesRegex(OSError, "commit unavailable"):
                    await publisher.fence_checkpoint(state={"broker": {"cash": 100}},
                                                     source_cursor="bucket-1")
                self.assertEqual([row.sequence for row in journal.unfenced_records()], [1])
                with self.assertRaisesRegex(RuntimeError, "buffer is full"):
                    journal.append(run_id=RUN, category="strategy", entity_type="signal",
                                   entity_id="two", payload={}, event_time=at)
            finally:
                await writer.close()
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
