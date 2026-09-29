"""Campaign checkpoint keeps exact normalized owner authority."""
from dataclasses import replace
from datetime import date

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.strategy_one_campaign_snapshot import (
    TABLES, project_campaign_snapshot, verify_campaign_snapshot,
)


RUN = "backtest:strategy-one"
BATCH = "00000000-0000-0000-0000-000000000042"
DAY = date(2026, 8, 18)


def project(ownership=()):
    return project_campaign_snapshot(
        run_id=RUN, session_date=DAY, checkpoint_sequence=42,
        boundary_ms=1_200_000, journal_batch_id=BATCH,
        ownership=ownership)


def test_empty_and_owned_checkpoints_are_stable_and_normalized():
    empty = project()
    assert empty.snapshot["owner_count"] == 0
    assert empty.owners == ()
    assert verify_campaign_snapshot(empty) is empty
    owners = (
        {"resource_id": "book:AAA", "session_key": "2026-08-18",
         "owner_id": "campaign-a", "state": "confirmed", "epoch": 2,
         "updated_at": "2026-09-28T12:00:00+00:00"},
        {"resource_id": "book:BBB", "session_key": "2026-08-18",
         "owner_id": "campaign-b", "state": "reserved", "epoch": 1},
    )
    rows = project(owners)
    assert rows == project(tuple(reversed(owners)))
    assert rows.snapshot["owner_count"] == 2
    assert all("updated_at" not in row for row in rows.owners)
    assert verify_campaign_snapshot(rows) is rows
    assert all("live_market_ssd" in table.ddl() for table in TABLES)
    assert all("json" not in name and "blob" not in name
               for table in TABLES for name, _ in table.columns)


def test_changed_or_missing_owner_fails_seal():
    rows = project((
        {"resource_id": "book:AAA", "session_key": "2026-08-18",
         "owner_id": "campaign-a", "state": "confirmed", "epoch": 2},
    ))
    with pytest.raises(RuntimeError, match="committed seal"):
        verify_campaign_snapshot(replace(rows, owners=()))
    with pytest.raises(RuntimeError, match="committed seal"):
        verify_campaign_snapshot(replace(rows, owners=(
            {**rows.owners[0], "owner_id": "campaign-b"},)))
    with pytest.raises(ValueError, match="duplicate owner"):
        project((rows.owners[0], rows.owners[0]))


def test_memory_capture_contains_only_active_owner_scalars():
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.acquire_campaign_session_ownership(
        "book:AAA", session_key="2026-08-18", owner_id="campaign-a",
        state="reserved")
    journal.acquire_campaign_session_ownership(
        "book:BBB", session_key="2026-08-18", owner_id="campaign-b",
        state="reserved")
    journal.acquire_campaign_session_ownership(
        "book:AAA", session_key="2026-08-18", owner_id="campaign-a",
        state="confirmed")
    journal.release_campaign_session_reservation(
        "book:BBB", session_key="2026-08-18", owner_id="campaign-b")
    captured = journal.campaign_ownership_snapshot()
    assert captured == ({
        "resource_id": "book:AAA", "session_key": "2026-08-18",
        "owner_id": "campaign-a", "state": "confirmed", "epoch": 2,
    },)
    assert verify_campaign_snapshot(project(captured)).snapshot["owner_count"] == 1
