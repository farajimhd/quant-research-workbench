"""Controller-level fixed-boundary ordering without an event replay source."""
import asyncio
from datetime import date, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import src.backend.backtest_market_data as market_data
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval
from src.backend.replay_run_service import ReplayRunController


NY = ZoneInfo("America/New_York")
DAY = "2026-08-18"
RUN = "00000000-0000-0000-0000-000000000001"


def _row(ticker, boundary_ms, resolution_ms):
    return dict(session_date=DAY, ticker=ticker, boundary_ms=boundary_ms,
                resolution_ms=resolution_ms, bucket_index=boundary_ms // resolution_ms - 1,
                price_valid=1, quote_valid=1, close_int=100_000,
                execution_vwap=(10.0 if ticker == "AAPL" else 20.0)
                + boundary_ms / 1_000 if resolution_ms == 100 else 0)


def test_fixed_controller_applies_all_liquidity_before_any_strategy_frame(monkeypatch):
    plan = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "definition", (DAY,),
        ("AAPL", "MSFT"), (), (100, 1000), "pinned-token")
    rows = [
        _row("AAPL", 100, 100), _row("MSFT", 100, 100),
        _row("AAPL", 1000, 100), _row("AAPL", 1000, 1000),
        _row("MSFT", 1000, 100), _row("MSFT", 1000, 1000),
    ]
    monkeypatch.setattr(market_data, "MarketDayLedger",
                        lambda: SimpleNamespace(certified_plan=lambda **_kwargs: plan))
    monkeypatch.setattr(market_data, "iter_market_day_rows", lambda _plan: iter(rows))
    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(
        configuration_revision={"payload": {"assignments": []}},
        market_data_plan={"token": "pinned-token", "sessions": [DAY]},
        causal_v7_plan={}, tickers=("AAPL", "MSFT"),
        requested_start=datetime.combine(date(2026, 8, 18), time(4), tzinfo=NY),
        session_end=datetime.combine(date(2026, 8, 18), time(4, 0, 2), tzinfo=NY),
    )
    controller._journal = BacktestMemoryJournal(run_id=RUN)
    controller._resume_state = None
    controller._source_cursor = {}
    controller._fixed_vwap_day = None
    controller._fixed_vwap_by_ticker = {}
    controller._historical_external_signal_events = []
    controller._quotes = {}
    controller._stop_requested = False
    controller.processed_events = 0
    controller.warmup_events = 0
    events = []

    class Runtime:
        async def process_liquidity_bar(self, row, *, at):
            events.append(("liquidity", row["boundary_ms"], row["ticker"]))
            return SimpleNamespace(ts=at)

    controller._runtime = Runtime()
    controller._record_data_authority = lambda *_args: None
    controller._apply_historical_watchlist_membership = lambda _at: None
    controller._remember_strategy_frame = lambda frame: events.append(
        ("auxiliary", int((frame.as_of - controller.definition.requested_start).total_seconds() * 1000),
         frame.ticker))

    async def no_op(*_args, **_kwargs):
        return None

    async def strategy(frame):
        boundary_ms = int((frame.as_of - controller.definition.requested_start).total_seconds() * 1000)
        assert set(controller._quotes) == {"AAPL", "MSFT"}
        expected = (10.0 if frame.ticker == "AAPL" else 20.0) + boundary_ms / 1_000
        assert frame.indicator["execution_vwap"] == expected
        events.append(("strategy", boundary_ms, frame.ticker))
        return True

    async def finish(status):
        events.append(("finish", status, ""))

    controller._prepare_session_relative_volume = no_op
    controller._publish = no_op
    controller._wait_until_active = no_op
    controller._after_event = no_op
    controller._process_strategy_frame = strategy
    controller._finish = finish
    asyncio.run(controller._run_fixed_market_days())
    assert events[:4] == [
        ("liquidity", 100, "AAPL"), ("liquidity", 100, "MSFT"),
        ("strategy", 100, "AAPL"), ("strategy", 100, "MSFT"),
    ]
    assert controller.processed_events == 4
    assert events[-1] == ("finish", "completed", "")
