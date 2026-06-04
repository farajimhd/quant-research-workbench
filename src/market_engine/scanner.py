from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


SortDirection = Literal["asc", "desc"]


@dataclass(frozen=True, slots=True)
class ScannerPreset:
    id: str
    label: str
    limit: int
    sort_column: str
    sort_direction: SortDirection = "desc"
    filters: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class ScannerSnapshot:
    as_of: datetime
    rows: tuple[dict[str, Any], ...]
    source: str
    total_rows: int
    preset: ScannerPreset | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


DEFAULT_SCANNER_PRESETS: tuple[ScannerPreset, ...] = (
    ScannerPreset(id="top_gainers_pct", label="Top Gainers %", limit=200, sort_column="change_pct", sort_direction="desc"),
    ScannerPreset(id="top_gainers_dollar", label="Top Gainers $", limit=200, sort_column="change", sort_direction="desc"),
    ScannerPreset(id="top_volume", label="Top Volume", limit=200, sort_column="volume", sort_direction="desc"),
    ScannerPreset(id="top_trades", label="Top Trades", limit=200, sort_column="trade_count", sort_direction="desc"),
    ScannerPreset(id="tight_spread_liquid", label="Tight Spread Liquid", limit=200, sort_column="dollar_volume", sort_direction="desc"),
)
