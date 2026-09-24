from datetime import date, datetime, timezone

import pytest

from src.backend import typed_backtest_progress as progress
from src.trading_runtime.arte_journal_schema import backtest_progress_upgrade_ddl
from src.trading_runtime.arte_journal_writer import (
    _sealed_families, load_committed_prefix, publish_typed_batch,
)
from src.trading_runtime.arte_journal_projection import backtest_cursor_batch
from src.trading_runtime.journal_contract import JournalRecord
from tests.test_arte_journal_writer import MemoryClient


RUN = "00000000-0000-0000-0000-000000000031"
BATCH = "00000000-0000-0000-0000-000000000032"
ATTEMPT = "00000000-0000-0000-0000-000000000033"
ZERO = "00000000-0000-0000-0000-000000000000"
AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


def _fixture():
    payload = {"session_date": "2026-08-18", "boundary_ms": 300000,
               "market_sequence": 700, "frame_as_of": AT.isoformat(),
               "frame_ticker": "TEST", "frame_timeframe": "100ms",
               "frame_sequence": 699}
    record = JournalRecord(
        "00000000-0000-0000-0000-000000000034", RUN, 1, AT, AT,
        "checkpoint", "market_boundary", "2026-08-18:300000", "", payload)
    state = {"identity": {"run_id": RUN, "mode": "backtest"},
             "controller": {"current_time": AT.isoformat(), "processed_events": 81,
                            "warmup_events": 5, "processed_frames": 86,
                            "source_cursor": {"session_date": "2026-08-18",
                                              "boundary_ms": 300000, "sequence": 700},
                            "frame_cursor": {"as_of": AT.isoformat(), "ticker": "TEST",
                                             "timeframe": "100ms", "sequence": 699}},
             "runtime": {"processed_events": 700,
                         "last_event_time": AT.isoformat()}}
    return record, state


def test_checkpoint_progress_is_typed_and_committed(monkeypatch):
    record, state = _fixture()
    batch = progress.project_backtest_progress(
        record, state, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO)
    assert batch.backtest_progress[0]["controller_processed_events"] == 81
    assert dict(_sealed_families(batch))["trading_backtest_progress_v1"]
    client = MemoryClient()
    publish_typed_batch(client, batch)
    prefix = load_committed_prefix(client, RUN)
    assert prefix is not None
    monkeypatch.setattr(progress, "load_latest_backtest_cursor", lambda *_: {
        **batch.backtest_cursors[0], "content_hash": "unused"})
    recovered = progress.load_committed_backtest_progress(client, prefix, required=True)
    assert recovered["controller_processed_frames"] == 86
    assert recovered["runtime_processed_events"] == 700
    client.tables["trading_backtest_progress_v1"].clear()
    with pytest.raises(RuntimeError, match="differs from committed fence"):
        load_committed_prefix(client, RUN)


def test_checkpoint_progress_rejects_clock_and_counter_drift():
    record, state = _fixture()
    args = dict(run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
                batch_id=BATCH, prior_batch_id=ZERO)
    state["controller"]["processed_events"] = -1
    with pytest.raises(ValueError, match="UInt64"):
        progress.project_backtest_progress(record, state, **args)
    state["controller"]["processed_events"] = 81
    state["runtime"]["last_event_time"] = "2026-08-18T08:05:01+00:00"
    with pytest.raises(ValueError, match="not causal"):
        progress.project_backtest_progress(record, state, **args)


def test_progress_upgrade_defaults_preserve_old_commit_rows():
    ddl = backtest_progress_upgrade_ddl()
    assert "trading_backtest_progress_v1" in ddl[0]
    assert "DEFAULT 0" in ddl[1]
    assert "DEFAULT '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'" in ddl[2]
    record, _ = _fixture()
    old_batch = backtest_cursor_batch(
        record, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO, source_cursor=record.entity_id)
    client = MemoryClient()
    publish_typed_batch(client, old_batch)
    commit = client.tables["trading_commit_v1"][0]
    assert commit["backtest_progress_count"] == 0
    assert commit["backtest_progress_hash"] == ddl[2].split("DEFAULT '", 1)[1].split("'", 1)[0]
    assert load_committed_prefix(client, RUN) is not None
