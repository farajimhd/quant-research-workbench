"""Inactive, explicit V2 signal/commit profile; no ClickHouse connection."""
from datetime import date, datetime, timezone
import json
import re

import pytest

from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime.arte_journal_schema import (
    BATCH_LOOKUP_INDEX, LEGACY_COMMIT_V1, LEGACY_STRATEGY_SIGNAL_V1,
    MARKET_READ_TABLES, TABLES, VERSIONED_JOURNAL_V2_TABLES,
    versioned_journal_v2_preflight,
)
from src.trading_runtime.arte_journal_projection import strategy_signal_batch
from src.trading_runtime.signals import StrategySignal
from tests.test_arte_journal_writer import MemoryClient


class V2MemoryClient(MemoryClient):
    def execute(self, sql):
        if "groupArray((toString(record_id),toString(content_hash)))" in sql:
            batch_id = sql.split("batch_id=toUUID('", 1)[1].split("'", 1)[0]
            pairs = re.findall(r"FROM arte\.([a-z0-9_]+) WHERE batch_id=.*?\) AS ([a-z0-9_]+)", sql)
            return json.dumps({alias: [
                [row["record_id"], row["content_hash"]]
                for row in self.tables.get(table, []) if row["batch_id"] == batch_id
            ] for table, alias in pairs})
        return super().execute(sql)


class V2Catalog:
    def __init__(self):
        replaced = {"trading_strategy_signal_v1", "trading_commit_v1"}
        self.writable = {table.name for table in TABLES if table.name not in replaced}
        self.writable |= {table.name for table in VERSIONED_JOURNAL_V2_TABLES}
        self.readable = self.writable | replaced | MARKET_READ_TABLES
        self.tables = tuple(table for table in TABLES if table.name not in replaced)
        self.tables += (LEGACY_STRATEGY_SIGNAL_V1, LEGACY_COMMIT_V1)
        self.tables += VERSIONED_JOURNAL_V2_TABLES
        self.bad_disk = False
        self.legacy_insert = False

    def execute(self, sql):
        if sql == "SHOW GRANTS":
            lines = [f"GRANT SELECT, INSERT ON arte.{name} TO journal_writer"
                     for name in sorted(self.writable)]
            lines += [f"GRANT SELECT ON arte.{name} TO journal_writer"
                      for name in sorted(self.readable - self.writable)]
            lines += [f"GRANT SELECT ON system.{name} TO journal_writer"
                      for name in ("storage_policies", "tables", "columns", "parts",
                                   "data_skipping_indices")]
            if self.legacy_insert:
                lines.append("GRANT INSERT ON arte.trading_commit_v1 TO journal_writer")
            return "\n".join(lines)
        if sql == "SELECT currentUser()":
            return "journal_writer\n"
        if sql.startswith("CHECK GRANT "):
            privilege, scope = sql.removeprefix("CHECK GRANT ").split(" ON ")
            table = scope.removeprefix("arte.")
            allowed = ((privilege == "SELECT" and table in self.readable)
                       or (privilege == "INSERT" and table in self.writable)
                       or (self.legacy_insert and privilege == "INSERT"
                           and table == "trading_commit_v1"))
            return "1\n" if allowed else "0\n"
        if "FROM system.storage_policies" in sql:
            rows = [{"disks": ["live_market_ssd"]}]
        elif "FROM system.tables" in sql:
            if "engine,storage_policy" in sql:
                rows = [{"name": table.name, "engine": "MergeTree",
                         "storage_policy": "live_market_ssd",
                         "partition_key": table.partition, "sorting_key": table.order}
                        for table in self.tables]
            else:
                rows = [{"name": name} for name in sorted(self.readable)]
        elif "FROM system.columns" in sql:
            rows = [{"table": table.name, "name": name, "type": kind}
                    for table in sorted(self.tables, key=lambda value: value.name)
                    for name, kind in table.columns]
        elif "FROM system.data_skipping_indices" in sql:
            rows = [{"table": table.name, "name": BATCH_LOOKUP_INDEX,
                     "type": "bloom_filter", "expr": "batch_id", "granularity": 1}
                    for table in self.tables if "batch_id" in dict(table.columns)]
        elif "FROM system.parts" in sql:
            rows = ([{"table": "trading_commit_v2", "disk_name": "default"}]
                    if self.bad_disk and "disk_name" in sql else [])
        else:
            raise AssertionError(sql)
        return "\n".join(json.dumps(row) for row in rows)


def test_v2_preflight_checks_legacy_read_only_exact_layout_and_ssd():
    client = V2Catalog()
    versioned_journal_v2_preflight(client)
    client.bad_disk = True
    with pytest.raises(ValueError, match="outside live_market_ssd"):
        versioned_journal_v2_preflight(client)
    client.bad_disk = False
    client.legacy_insert = True
    with pytest.raises(ValueError, match="unauthorized INSERT"):
        versioned_journal_v2_preflight(client)


def _batch(*, batch_id="00000000-0000-0000-0000-000000000005",
           prior_batch_id="00000000-0000-0000-0000-000000000000",
           sequence=1, signal_id="signal-1"):
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    signal = StrategySignal(
        signal_id, "breakout", "TEST", at, "enter_long", "bullish",
        0.75, 0.9, "entry_ready", ("source-a",), "100ms", 9.5,
    )
    return strategy_signal_batch(
        signal, run_id="backtest:v2:test", run_month=date(2026, 8, 1),
        account_id="DU1", strategy_id="strategy-1", strategy_revision=7,
        attempt_id="00000000-0000-0000-0000-000000000004",
        batch_id=batch_id, prior_batch_id=prior_batch_id,
        sequence=sequence, source_cursor=signal_id, run_status="running",
        recorded_at=at,
    )


def test_explicit_v2_profile_seals_signal_and_commit_then_cold_reads(monkeypatch):
    calls = []
    monkeypatch.setattr(writer, "versioned_journal_v2_preflight",
                        lambda client: calls.append(client))
    monkeypatch.setattr(writer, "_verify_run_identity",
                        lambda client, run_id: {"mode": "backtest"})
    client = V2MemoryClient()
    batch = _batch()
    assert writer.publish_typed_batch(client, batch,
                                      journal_profile="backtest_v2") == batch.batch_id
    assert "trading_strategy_signal_v2" in client.inserts
    assert "trading_commit_v2" == client.inserts[-1]
    assert "trading_strategy_signal_v1" not in client.inserts
    assert "trading_commit_v1" not in client.inserts
    assert writer.publish_typed_batch(client, batch,
                                      journal_profile="backtest_v2") == batch.batch_id
    assert client.inserts.count("trading_commit_v2") == 1
    prefix = writer.load_committed_prefix(client, batch.run_id,
                                           journal_profile="backtest_v2")
    assert isinstance(prefix, writer.V2CommittedPrefix)
    assert not isinstance(prefix, writer.CommittedPrefix)
    assert prefix.last_sequence == 1
    with pytest.raises(ValueError, match="verified committed prefix"):
        writer.load_committed_execution_page(client, prefix)
    assert len(calls) == 3
    client.tables["trading_commit_v1"] = [{"run_id": batch.run_id,
                                           "batch_id": batch.batch_id}]
    with pytest.raises(RuntimeError, match="cannot mix"):
        writer.load_committed_prefix(client, batch.run_id,
                                     journal_profile="backtest_v2")
    client.tables.pop("trading_commit_v1")
    client.tables["trading_strategy_signal_v2"][0]["reason"] = "tampered"
    with pytest.raises(RuntimeError, match="row content differs"):
        writer.load_committed_prefix(client, batch.run_id,
                                     journal_profile="backtest_v2")


def test_v2_profile_cannot_use_terminal_batch_or_unknown_profile(monkeypatch):
    client = MemoryClient()
    batch = _batch()
    with pytest.raises(ValueError, match="Unknown typed journal profile"):
        writer.publish_typed_batch(client, batch, journal_profile="v3")
    from dataclasses import replace
    with pytest.raises(ValueError, match="Terminal Backtest"):
        writer.publish_typed_batch(client, replace(batch, status="completed"),
                                   journal_profile="backtest_v2")
    assert not client.inserts


def test_v2_profile_rejects_non_backtest_or_legacy_run_before_write(monkeypatch):
    monkeypatch.setattr(writer, "versioned_journal_v2_preflight", lambda client: None)
    client = V2MemoryClient()
    batch = _batch()
    monkeypatch.setattr(writer, "_verify_run_identity",
                        lambda client, run_id: {"mode": "live"})
    with pytest.raises(RuntimeError, match="verified Backtest"):
        writer.publish_typed_batch(client, batch, journal_profile="backtest_v2")
    monkeypatch.setattr(writer, "_verify_run_identity",
                        lambda client, run_id: {"mode": "backtest"})
    client.tables["trading_commit_v1"] = [{"run_id": batch.run_id,
                                           "batch_id": batch.batch_id}]
    with pytest.raises(RuntimeError, match="cannot mix"):
        writer.publish_typed_batch(client, batch, journal_profile="backtest_v2")
    assert not client.inserts


def test_v2_writer_opt_in_uses_bounded_worker_and_never_v1_fence(monkeypatch):
    checked = []
    monkeypatch.setattr(writer, "versioned_journal_v2_preflight",
                        lambda client: checked.append(client))
    monkeypatch.setattr(writer, "_verify_run_identity",
                        lambda client, run_id: {"mode": "backtest",
                                                "account_ids": ("DU1",)})
    client = V2MemoryClient()
    batch = _batch()
    next_batch = _batch(batch_id="00000000-0000-0000-0000-000000000006",
                        prior_batch_id=batch.batch_id, sequence=2,
                        signal_id="signal-2")
    lane = writer.ArteJournalWriter(
        client, run_id=batch.run_id, journal_profile="backtest_v2", capacity=2,
        coalesce_batches=False)
    try:
        assert lane.journal_profile == "backtest_v2"
        for submit, message in (
            (lane.submit_portfolio_snapshot, "terminal suffix fence"),
            (lane.submit_captured_portfolio_snapshot, "terminal suffix fence"),
            (lambda value: lane.submit_admission(value, None), "live admission"),
            (lambda value: lane.submit_portfolio_sync(value, None), "live portfolio sync"),
        ):
            with pytest.raises(RuntimeError, match=message):
                submit(batch)
        assert lane.metrics()["queue_depth"] == 0
        assert lane.submit(batch).result(timeout=3) == batch.batch_id
        assert lane.submit(next_batch).result(timeout=3) == next_batch.batch_id
        assert checked == [client]
        assert client.inserts[-1] == "trading_commit_v2"
        assert "trading_commit_v1" not in client.inserts
        with pytest.raises(RuntimeError, match="separate fence"):
            lane.submit_terminal_backtest(batch, ())
    finally:
        lane.close()
