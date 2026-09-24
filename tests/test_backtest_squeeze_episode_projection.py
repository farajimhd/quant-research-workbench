import asyncio
from datetime import date, datetime, timedelta, timezone
from copy import deepcopy
from dataclasses import replace

import pytest

from src.backend.backtest_squeeze_episode_projection import project_fixed_squeeze_episode
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import project_pending_backtest_prefix
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
from src.backend.backtest_squeeze_episode_schema import SQUEEZE_EPISODE, staged_upgrade_ddl
from src.backend.fixed_bar_signal import CONTRACT, load_first_squeeze_occurrences
from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.journal_contract import JournalRecord
from tests.test_fixed_bar_signal import _contract, _plan
from tests.test_backtest_typed_publisher import FakeWriter


AT = datetime(2026, 8, 18, 14, 0, 0, 100000, tzinfo=timezone.utc)
EVENT_ID = "a" * 64
PLAN = "b" * 64
QUERY = "c" * 64
ATTEMPT = "00000000-0000-0000-0000-000000000a11"
BATCH = "00000000-0000-0000-0000-000000000a12"
ZERO = "00000000-0000-0000-0000-000000000000"


def _record():
    clock = AT.isoformat()
    payload = {
        "event_id": EVENT_ID, "signal_stream_id": "price-squeeze-early",
        "ticker": "AAPL", "event_time": clock, "effective_at": clock,
        "available_at": clock, "last_price": 101.5,
        "squeeze_episode_id": EVENT_ID, "squeeze_episode_role": "start",
        "squeeze_episode_started_at": clock,
        "squeeze_expires_at": (AT + timedelta(seconds=300)).isoformat(),
        "squeeze_anchor_price": 100.0, "squeeze_move_pct": 1.5,
        "squeeze_high_water_pct": 1.5, "source_authority": CONTRACT,
        "market_plan_token": PLAN, "query_sha256": QUERY,
        "evidence": {"price_change_1_bar_pct": 1.5,
                     "trade_count_change": 2, "volume_change": 100.0},
    }
    return JournalRecord("00000000-0000-0000-0000-000000000a13",
                         "backtest:squeeze", 1, AT, AT,
                         "market_discovery_signal", "signal_occurrence", EVENT_ID,
                         "", payload)


def test_fixed_squeeze_closed_projection_is_staged_outside_active_live_schema():
    from src.trading_runtime.arte_journal_schema import TABLES

    projected = project_fixed_squeeze_episode(
        _record(), expected_market_plan_token=PLAN, expected_query_sha256=QUERY)
    assert projected["episode_id"] == EVENT_ID
    assert SQUEEZE_EPISODE.name not in {table.name for table in TABLES}
    assert "live_market_ssd" in staged_upgrade_ddl()[0]


def test_actual_completed_bar_loader_payload_projects_without_field_loss():
    plan = replace(_plan(), token=PLAN)
    stream, activation = _contract()

    class Client:
        def iter_json_each_row(self, _query):
            return iter([{
                "session_date": "2026-08-18", "ticker": "ABCD",
                "bucket_index": 147000, "close_int": 100_100,
                "previous_close_int": 100_000, "volume": 200.0,
                "previous_volume": 100.0, "trade_count": 4,
                "previous_trade_count": 2,
            }])

    loaded = load_first_squeeze_occurrences(
        plan, stream=stream, activation=activation,
        through_boundary_ms=19_800_000, client=Client())
    payload = loaded["occurrences"][0]
    assert payload["query_sha256"] == loaded["authority"]["query_sha256"]
    event_time = datetime.fromisoformat(payload["available_at"])
    record = JournalRecord(
        "00000000-0000-0000-0000-000000000a14", "backtest:squeeze", 1,
        event_time, event_time, "market_discovery_signal", "signal_occurrence",
        payload["event_id"], "", payload)
    projected = project_fixed_squeeze_episode(
        record, expected_market_plan_token=PLAN,
        expected_query_sha256=payload["query_sha256"])
    assert projected["ticker"] == "ABCD"
    assert projected["move_pct"] == projected["high_water_pct"]
    memory = BacktestMemoryJournal(run_id="backtest:squeeze")
    emitted = memory.append_once(
        run_id="backtest:squeeze", category="market_discovery_signal",
        entity_type="signal_occurrence", entity_id=payload["event_id"],
        event_time=event_time, payload=payload)[0]
    projected_emitted = project_fixed_squeeze_episode(
        emitted, expected_market_plan_token=PLAN,
        expected_query_sha256=payload["query_sha256"])
    assert projected_emitted["episode_id"] == emitted.entity_id


@pytest.mark.parametrize("change", [
    {"market_plan_token": "d" * 64},
    {"query_sha256": "d" * 64},
    {"signal_stream_id": "other"},
    {"extra": "not typed"},
    {"evidence": {"price_change_1_bar_pct": 1.5,
                  "trade_count_change": 2, "volume_change": 100.0,
                  "unknown": 1}},
])
def test_other_or_unmodeled_occurrence_fails_closed(change):
    source = _record()
    payload = deepcopy(source.payload)
    payload.update(change)
    record = JournalRecord(source.record_id, source.run_id, source.sequence,
                           source.event_time, source.recorded_at,
                           source.category, source.entity_type, source.entity_id,
                           source.account_id, payload)
    with pytest.raises(ValueError):
        project_fixed_squeeze_episode(record, expected_market_plan_token=PLAN,
                                      expected_query_sha256=QUERY)


def test_active_journal_rejects_staged_occurrence():
    with pytest.raises(ValueError, match="operator-provisioned"):
        project_journal_record(
            _record(), run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
            batch_id=BATCH, prior_batch_id=ZERO, source_cursor="bar:1")


def test_pending_prefix_and_publisher_reject_unprovisioned_squeeze():
    memory = BacktestMemoryJournal(run_id="backtest:squeeze")
    source = _record()
    memory.append_once(
        run_id=source.run_id, category=source.category,
        entity_type=source.entity_type, entity_id=source.entity_id,
        event_time=source.event_time, payload=source.payload)
    with pytest.raises(ValueError, match="operator-provisioned"):
        project_pending_backtest_prefix(
            memory, attempt_id=ATTEMPT, run_month=date(2026, 8, 1),
            prior_sequence=0)
    async def exercise():
        writer = FakeWriter()
        writer.run_id = memory.run_id
        publisher = BacktestTypedJournalPublisher(
            memory, writer, attempt_id=ATTEMPT, run_month=date(2026, 8, 1))
        with pytest.raises(ValueError, match="operator-provisioned"):
            await publisher.enqueue_pending()
        assert writer.submitted == []
        assert memory.pending_record_count == 1

    asyncio.run(exercise())
