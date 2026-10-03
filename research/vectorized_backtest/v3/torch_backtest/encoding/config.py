"""Separate source/search, strategy-clock and broker-model contracts."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_EXCLUDED_TICKERS = ("LGHL",)


@dataclass(frozen=True)
class Session:
    manifest: Path
    ledger: Path
    runtime: Path
    start: datetime
    end: datetime
    strategy_ms: int = 1000
    broker_ms: int = 1000
    fetch_seconds: int = 300
    fetch_tickers: int = 64
    max_prepared_gib: float = 2.0
    warmup_seconds: int = 3600
    excluded_tickers: tuple[str, ...] = DEFAULT_EXCLUDED_TICKERS
    regular_us_exchanges_only: bool = True


@dataclass(frozen=True)
class Funnel:
    """Fixed upstream admission envelope; membership persists to session end.

    Altering these values changes the upstream dataset and its cache identity.
    The compiler does not push arbitrary downstream predicates into this gate.
    """

    min_price: float = 1.0
    max_price: float = 50.0
    impulse_bps: float = 5.0
    signal_ms: int = 100


@dataclass(frozen=True)
class Broker:
    initial_cash: float = 100_000.0
    participation: float = 0.10
    fee_bps: float = 1.0
    initial_stop_return: float = 0.02
    initial_target_return: float = 0.04
    drawdown_weight: float = 0.0
