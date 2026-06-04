"""Shared event-based market engine contracts.

The market engine is intentionally provider-agnostic. Live Massive websocket
events, historical ClickHouse events, and replayed sessions should all be
adapted into these contracts before scanner, chart, backtest, or trading code
consumes them.
"""

from src.market_engine.bars import Bar, BarBuilder, BarSpec, TimeBarBuilder
from src.market_engine.broker import AccountSnapshot, BrokerAdapter, ExecutionFill, OrderSnapshot, PortfolioPosition
from src.market_engine.events import MarketEvent, QuoteEvent, TradeEvent
from src.market_engine.scanner import ScannerPreset, ScannerSnapshot
from src.market_engine.sources import EventCursor, MarketEventSource

__all__ = [
    "AccountSnapshot",
    "Bar",
    "BarBuilder",
    "BarSpec",
    "BrokerAdapter",
    "EventCursor",
    "ExecutionFill",
    "MarketEvent",
    "MarketEventSource",
    "OrderSnapshot",
    "PortfolioPosition",
    "QuoteEvent",
    "ScannerPreset",
    "ScannerSnapshot",
    "TimeBarBuilder",
    "TradeEvent",
]
