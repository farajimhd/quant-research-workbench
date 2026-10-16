"""Explicit UTC restoration at the verified ClickHouse-to-policy boundary."""
import asyncio
from datetime import datetime, timezone
from uuid import UUID

import pytest

from src.backend import program_policy_recovery as recovery
from src.trading_runtime.arte_journal_reader import TypedJournalEvent, restore_typed_utc_clock
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix


AT = datetime(2026, 7, 31, 13, 0, 0, 123456, tzinfo=timezone.utc)
STORED = "2026-07-31 13:00:00.123456000"


@pytest.mark.parametrize("value", (STORED, "2026-07-31 13:00:00.123456",
    "2026-07-31T13:00:00.123456+00:00", AT))
def test_declared_stored_utc_clock_restores_the_exact_instant(value):
    assert restore_typed_utc_clock("trading_event_v1", "event_time", value) == AT


@pytest.mark.parametrize("family,column,value", (
    ("trading_event_v1", "entity_id", STORED),
    ("unknown", "event_time", STORED),
    ("trading_event_v1", "event_time", AT.replace(tzinfo=None)),
    ("trading_event_v1", "event_time", "2026-07-31T13:00:00.123456"),
    ("trading_event_v1", "event_time", "2026-07-31 13:00:00.123456001"),
))
def test_restoration_rejects_unknown_naive_or_lossy_clocks(family, column, value):
    with pytest.raises(ValueError):
        restore_typed_utc_clock(family, column, value)


def stored_completion():
    return TypedJournalEvent(dict(sequence=1, event_time=STORED,
        category="strategy_decision", entity_type="signal"),
        "trading_strategy_signal_v2",
        dict(source_event_time=STORED, signal_type="program_boundary_completed"))


def test_only_verified_storage_adapter_accepts_known_utc_strings_without_mutating_rows():
    item = stored_completion()
    result = recovery.restore_stored_program_policy_event(item)
    assert result.event["event_time"] == result.detail["source_event_time"] == AT
    assert item.event["event_time"] == item.detail["source_event_time"] == STORED
    with pytest.raises(ValueError, match="timezone-aware"):
        recovery._clock(STORED)
    unrelated = TypedJournalEvent(dict(item.event, category="other", entity_type="other"),
        None, dict(source_event_time=None))
    assert recovery.restore_stored_program_policy_event(unrelated).detail is unrelated.detail


def test_async_cold_policy_reader_restores_storage_clocks_before_replay(monkeypatch):
    batch = str(UUID(int=1))
    prefix = V4CommittedPrefix("run", 1, batch, "final", "completed", (batch,))
    class Reader:
        closed = False
        def execute(self, sql):
            assert sql == "SELECT getSetting('readonly')"
            return "1"
        def close(self):
            self.closed = True
    reader = Reader()
    monkeypatch.setattr(recovery, "load_verified_v4_prefix", lambda *args: prefix)
    monkeypatch.setattr(recovery, "load_typed_event_page", lambda *a, **k: (stored_completion(),))
    sentinel = object()
    def replay(template, events, **scope):
        item, = tuple(events)
        assert item.event["event_time"] == item.detail["source_event_time"] == AT
        return sentinel
    monkeypatch.setattr(recovery, "rebuild_program_policy_events", replay)
    assert asyncio.run(recovery.recover_program_policy_async(lambda: reader,
        prefix, object(), exact_prices=True)) is sentinel
    assert reader.closed
