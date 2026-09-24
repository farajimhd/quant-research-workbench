from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import json
import unittest
from uuid import UUID

from src.backend.backtest_journal_clickhouse import (
    BacktestJournalWriter, load_fenced_checkpoint,
    prepare_batch, prepare_fence, publish_batch, publish_fence, publish_run,
    schema_ddl, storage_preflight,
)
from src.backend.backtest_journal_clickhouse import _COLUMNS, _LAYOUT
from src.trading_runtime.clickhouse import _journal_row
from src.trading_runtime.journal import JournalRecord
from src.trading_runtime.journal_contract import journal_row


RUN = "00000000-0000-0000-0000-000000000010"
ATTEMPT = "00000000-0000-0000-0000-000000000020"


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


if __name__ == "__main__":
    unittest.main()
