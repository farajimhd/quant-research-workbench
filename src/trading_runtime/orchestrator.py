from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.data_provider.calendar import market_sessions
from src.market_engine.historical_source import QmdHistoricalEventSource
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.runtime import AutomaticStrategy, RunConfig, RunMode, TradingRuntime
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig


NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class HistoricalRunWindow:
    start: datetime
    end: datetime
    sessions: tuple[date, ...]


def historical_run_window(
    mode: RunMode,
    anchor_date: date,
    *,
    session_count: int = 1,
    replay_end_date: date | None = None,
) -> HistoricalRunWindow:
    if mode not in {RunMode.REPLAY, RunMode.BACKTEST, RunMode.BACKTEST_DEBUG}:
        raise ValueError("Historical run windows are only valid for replay/backtest modes")
    if session_count <= 0:
        raise ValueError("session_count must be positive")
    if mode in {RunMode.BACKTEST, RunMode.BACKTEST_DEBUG}:
        candidates = market_sessions(anchor_date - timedelta(days=max(14, session_count * 4)), anchor_date - timedelta(days=1))
        sessions = tuple(candidates[-session_count:])
    else:
        requested_end = replay_end_date or anchor_date
        if requested_end < anchor_date:
            raise ValueError("Replay end date cannot precede anchor date")
        sessions = tuple(market_sessions(anchor_date, requested_end))
    if not sessions:
        raise ValueError("No exchange sessions resolved for historical run")
    start = datetime.combine(sessions[0], time(4, 0), tzinfo=NEW_YORK)
    end = datetime.combine(sessions[-1], time(20, 0), tzinfo=NEW_YORK)
    return HistoricalRunWindow(start=start, end=end, sessions=sessions)


class HistoricalTradingRunner:
    """Runs replay/backtest/debug from the canonical historical event source."""

    def __init__(self, gateway_base_url: str, journal: TradingJournal, *, batch_size: int = 10_000) -> None:
        if not 1 <= batch_size <= 100_000:
            raise ValueError("batch_size must be between 1 and 100000")
        self.gateway_base_url = gateway_base_url.rstrip("/")
        self.journal = journal
        self.batch_size = batch_size

    async def run(
        self,
        *,
        config: RunConfig,
        strategy: AutomaticStrategy,
        tickers: list[str] | None = None,
        session_count: int = 1,
        replay_end_date: date | None = None,
        simulation: SimulationConfig | None = None,
    ) -> TradingRuntime:
        window = historical_run_window(config.mode, config.anchor_date, session_count=session_count, replay_end_date=replay_end_date)
        broker = SimulatedBrokerAdapter(list(config.account_ids), simulation)
        runtime = TradingRuntime(config, broker, strategy, self.journal)
        await runtime.initialize()
        source = QmdHistoricalEventSource(
            self.gateway_base_url,
            start=window.start,
            end=window.end,
            tickers=tickers,
            batch_size=self.batch_size,
        )
        await source.health()
        async for batch in source.stream():
            for event in batch.events:
                await runtime.process_event(event)
        await runtime.finish()
        return runtime
