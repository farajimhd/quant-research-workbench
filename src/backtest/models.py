from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import polars as pl


@dataclass(frozen=True, slots=True)
class DataRequirements:
    event_timeframe: str = "1m"
    feature_groups: tuple[str, ...] = ()
    context_feature_groups: dict[str, tuple[str, ...]] | None = None
    daily_lookback_days: int = 0
    daily_feature_groups: tuple[str, ...] = ()
    required_columns: tuple[str, ...] = ()


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
    fill_requires_green_bar: bool = False
    fill_requires_close_through_stop: bool = False
    expire_on_bar_close: bool = False
    protective_stop_price: float | None = None


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
    fill_fee: float = 0.0
    commission: float = 0.0
    regulatory_fee: float = 0.0
    fee_tax: float = 0.0
    fee_model: str = ""
    tag: str = ""
    fill_requires_green_bar: bool = False
    fill_requires_close_through_stop: bool = False
    expire_on_bar_close: bool = False
    protective_stop_price: float | None = None


@dataclass(slots=True)
class Fill:
    fill_id: int
    order_id: int
    symbol: str
    side: str
    quantity: int
    fill_price: float
    filled_at: datetime
    order_type: str
    reason: str
    slippage_bps: float
    commission: float = 0.0
    regulatory_fee: float = 0.0
    fee_tax: float = 0.0
    total_fee: float = 0.0
    sec_fee: float = 0.0
    finra_taf: float = 0.0
    finra_cat: float = 0.0
    fee_model: str = ""
    bar_time_market: datetime | None = None
    bar_open: float | None = None
    bar_high: float | None = None
    bar_low: float | None = None
    bar_close: float | None = None
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
    entry_fee: float = 0.0
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
    gross_pnl: float
    entry_fee: float
    exit_fee: float
    fees: float
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


@dataclass(slots=True)
class BarContext:
    timestamp: datetime
    updates: pl.DataFrame
    latest: pl.DataFrame
    updates_by_symbol: dict[str, dict]
    latest_by_symbol: dict[str, dict]
    observability: Any | None = None
    recent_orders: list[dict] = field(default_factory=list)
    recent_fills: list[dict] = field(default_factory=list)
