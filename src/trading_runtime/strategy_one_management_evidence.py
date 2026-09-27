"""Transport-neutral completed-boundary evidence for Strategy 1 management."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from src.trading_runtime.strategy_one_position import ResistanceBreak


@dataclass(frozen=True, slots=True)
class StrategyOneManagementEvidence:
    ticker: str
    boundary_ms: int
    bid: float | None
    ask: float | None
    price_bearing_bar: bool
    low_boundary_ms: int | None
    low_int: int | None
    breaks: tuple[ResistanceBreak, ...]
    overhead_levels: tuple[Mapping, ...]
