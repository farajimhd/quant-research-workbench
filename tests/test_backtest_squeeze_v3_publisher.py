"""Fake-storage acceptance for the isolated V3 squeeze batch fence."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
import json
import re

import pytest

from src.backend.backtest_squeeze_episode_v3 import project_squeeze_batch_v3
from src.trading_runtime import arte_journal_writer as writer
from tests.test_arte_journal_writer import MemoryClient
from tests.test_backtest_squeeze_episode_projection import BATCH, PLAN, QUERY, _record


ATTEMPT = "00000000-0000-0000-0000-000000000a02"
ZERO = "00000000-0000-0000-0000-000000000000"


class FakeV3Client(MemoryClient):
    def execute(self, sql: str) -> str:
        if "groupArray((toString(record_id),toString(content_hash)))" in sql:
            self.selects.append(sql)
            batch_id = sql.split("batch_id=toUUID('", 1)[1].split("'", 1)[0]
            pairs = re.findall(r"FROM arte\.([a-z0-9_]+) WHERE batch_id=.*?\) AS ([a-z0-9_]+)", sql)
            return json.dumps({alias: [[row["record_id"], row["content_hash"]]
                for row in self.tables.get(table, []) if row["batch_id"] == batch_id]
                for table, alias in pairs})
        if (sql.startswith("SELECT ")
                and "FROM arte.trading_backtest_squeeze_episode_v1" in sql
                and "groupArray(" not in sql):
            self.selects.append(sql)
            batch_id = sql.split("batch_id=toUUID('", 1)[1].split("'", 1)[0]
            return "\n".join(json.dumps(row) for row in self.tables.get(
                "trading_backtest_squeeze_episode_v1", []) if row["batch_id"] == batch_id)
        return super().execute(sql)


def _unit():
    record = _record()
    return project_squeeze_batch_v3(
        record, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO, source_cursor="bar:1",
        expected_market_plan_token=PLAN, expected_query_sha256=QUERY)


def test_v3_batch_writes_child_before_only_v3_commit_and_retries_exactly(monkeypatch):
    client = FakeV3Client()
    unit = _unit()
    monkeypatch.setattr(writer, "_v3_preflight", lambda _: None)
    monkeypatch.setattr(writer, "_verify_run_identity",
                        lambda *_: {"mode": "backtest"})
    assert writer.publish_typed_squeeze_batch_v3(client, unit) == BATCH
    assert client.inserts[-2:] == ["trading_backtest_squeeze_episode_v1", "trading_commit_v3"]
    assert "trading_commit_v2" not in client.inserts
    before = list(client.inserts)
    assert writer.publish_typed_squeeze_batch_v3(client, unit) == BATCH
    assert client.inserts == before


def test_v3_batch_rejects_missing_child_and_mixed_v2_before_insert(monkeypatch):
    unit = _unit()
    client = FakeV3Client()
    monkeypatch.setattr(writer, "_v3_preflight", lambda _: None)
    monkeypatch.setattr(writer, "_verify_run_identity",
                        lambda *_: {"mode": "backtest"})
    with pytest.raises(ValueError, match="no typed contract"):
        writer.publish_typed_squeeze_batch_v3(client, replace(unit, episodes=()))
    assert not client.inserts
    client.tables["trading_commit_v2"] = [{"run_id": unit.base.run_id, "batch_id": BATCH}]
    with pytest.raises(RuntimeError, match="mix V2 and V3"):
        writer.publish_typed_squeeze_batch_v3(client, unit)
    assert not client.inserts


def test_v3_writer_worker_receipt_follows_child_and_commit(monkeypatch):
    client = FakeV3Client()
    unit = _unit()
    monkeypatch.setattr(writer, "_v3_preflight", lambda _: None)
    monkeypatch.setattr(writer, "_verify_run_identity",
                        lambda *_: {"mode": "backtest", "account_ids": ("DU1",)})
    lane = writer.ArteJournalWriter(
        client, run_id=unit.base.run_id, journal_profile="backtest_v3",
        coalesce_batches=False, max_events_per_commit=1)
    try:
        with pytest.raises(RuntimeError, match="explicit squeeze"):
            lane.submit(unit.base)
        assert lane.submit_squeeze_v3(unit).result(timeout=3) == BATCH
        assert client.inserts[-1] == "trading_commit_v3"
    finally:
        lane.close()
