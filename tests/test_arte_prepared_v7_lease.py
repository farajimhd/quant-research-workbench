"""Typed fixed-Backtest prepared V7 ownership evidence."""
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.arte_journal_schema import prepared_v7_lease_upgrade_ddl
from src.trading_runtime.arte_journal_writer import load_committed_prefix, publish_typed_batch
from src.trading_runtime.journal_contract import JournalRecord
from tests.test_arte_journal_writer import MemoryClient


RUN = "00000000-0000-0000-0000-000000000041"
ATTEMPT = "00000000-0000-0000-0000-000000000042"
BATCH = "00000000-0000-0000-0000-000000000043"
ZERO = "00000000-0000-0000-0000-000000000000"
AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


def _record():
    return JournalRecord(
        "00000000-0000-0000-0000-000000000044", RUN, 1, AT, AT,
        "resource_lease", "prepared_v7_stream", "stream-1", "",
        {"stream_id": "stream-1", "owner_run_id": RUN, "owner_pid": 1200,
         "phase": "acquiring", "error_type": None,
         "recorded_at": AT.isoformat()})


def _project(record):
    return project_journal_record(
        record, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO, source_cursor="boundary-1")


def test_prepared_v7_lease_commits_and_recovery_verifies_typed_row():
    client = MemoryClient()
    batch = _project(_record())
    assert batch.prepared_v7_leases[0]["phase"] == "acquiring"
    publish_typed_batch(client, batch)
    assert client.inserts == ["trading_event_v1", "trading_prepared_v7_lease_v1",
                              "trading_commit_v1"]
    assert load_committed_prefix(client, RUN).last_sequence == 1
    client.tables["trading_prepared_v7_lease_v1"][0]["phase"] = "released"
    with pytest.raises(RuntimeError, match="row content differs from its hash"):
        load_committed_prefix(client, RUN)


def test_prepared_v7_lease_rejects_unmodeled_or_inconsistent_state():
    record = _record()
    with pytest.raises(ValueError, match="unmodeled"):
        _project(replace(record, payload={**record.payload, "checkpoint": {}}))
    with pytest.raises(ValueError, match="identity or phase"):
        _project(replace(record, payload={**record.payload, "owner_run_id": "other"}))
    with pytest.raises(ValueError, match="identity or phase"):
        _project(replace(record, payload={**record.payload, "phase": "release_failed"}))
    failed = replace(record, payload={**record.payload, "phase": "release_failed",
                                      "error_type": "TimeoutError"})
    assert _project(failed).prepared_v7_leases[0]["error_type"] == "TimeoutError"


def test_prepared_v7_upgrade_defaults_old_batches_to_empty_family():
    ddl = prepared_v7_lease_upgrade_ddl()
    assert "trading_prepared_v7_lease_v1" in ddl[0]
    assert "DEFAULT 0" in ddl[1]
    assert "DEFAULT '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'" in ddl[2]
