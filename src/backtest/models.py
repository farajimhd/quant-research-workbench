from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class OrderRequest:
    symbol: str
    side: str
    quantity: int
    order_type: str
    reason: str
    stop_price: float | None = None
    limit_price: float | None = None
    tag: str = ""
    allow_same_bar_fill: bool = False


@dataclass(slots=True)
class Order:
    order_id: int
    symbol: str
    side: str
    quantity: int
    order_type: str
    reason: str
    created_at: datetime
    stop_price: float | None = None
    limit_price: float | None = None
    status: str = "OPEN"
    filled_at: datetime | None = None
    fill_price: float | None = None
    tag: str = ""


@dataclass(slots=True)
class Position:
    symbol: str
    quantity: int
    entry_time: datetime
    entry_price: float
    stop_price: float
    entry_order_id: int
    setup_rank: int
    live_rank: int
    setup_score: float
    live_score: float
    max_price: float
    min_price: float
    max_unrealized_profit: float = 0.0
    max_r_multiple: float = 0.0
    max_adverse_excursion: float = 0.0


@dataclass(slots=True)
class Trade:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    quantity: int
    entry_price: float
    exit_price: float
    pnl: float
    return_pct: float
    exit_reason: str
    max_unrealized_profit: float
    max_r_multiple: float
    mae: float
    mfe: float
    end_trade_drawdown: float


@dataclass(slots=True)
class MinuteContext:
    timestamp: datetime
    bars_by_symbol: dict[str, dict]
