from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.backend.real_live_market_data.models import BarState, SymbolState, TradeEvent


def minute_start(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(second=0, microsecond=0)


def current_bar_row(sym: str, state: SymbolState, source: str = "massive_ws") -> dict:
    start = minute_start(state.last_trade.ts) if state.last_trade else minute_start(datetime.now(timezone.utc))
    bar = state.bar_1m
    return {
        "session_date": start.date().isoformat(),
        "timeframe": "1m",
        "bar_start": start.isoformat(),
        "bar_end": (start + timedelta(minutes=1)).isoformat(),
        "sym": sym,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "dollar_volume": bar.dollar_volume,
        "trade_count": bar.trade_count,
        "vwap": bar.vwap,
        "source": source,
    }


def rotate_minute_bar_if_needed(state: SymbolState, event: TradeEvent) -> dict | None:
    if not state.last_trade:
        return None
    current_start = minute_start(state.last_trade.ts)
    next_start = minute_start(event.ts)
    if next_start <= current_start:
        return None
    finalized = current_bar_row(state.last_trade.sym, state)
    state.bar_1m = BarState()
    return finalized
