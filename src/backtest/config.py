from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("D:/TradingData/massive_flatfiles/us_stock_sip/minutes_agg_v1")
DEFAULT_OUTPUT_ROOT = Path("D:/TradingData/qq-momentum-trading/runs")


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


@dataclass(slots=True)
class BacktestConfig:
    strategy_name: str
    start_date: date
    end_date: date
    data_root: Path = DEFAULT_DATA_ROOT
    output_root: Path = DEFAULT_OUTPUT_ROOT
    initial_cash: float = 10_000.0
    market_utc_offset_hours: float = -4.0
    session_start_minute: int = 9 * 60 + 30
    session_end_minute: int = 16 * 60
    slippage_bps: float = 2.0
    save_symbol_bars: bool = True
    strategy_params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BacktestConfig":
        data = dict(raw)
        data["start_date"] = parse_date(data["start_date"])
        data["end_date"] = parse_date(data["end_date"])
        data["data_root"] = Path(data.get("data_root", DEFAULT_DATA_ROOT))
        data["output_root"] = Path(data.get("output_root", DEFAULT_OUTPUT_ROOT))
        data["strategy_params"] = dict(data.get("strategy_params", {}))
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "data_root": str(self.data_root),
            "output_root": str(self.output_root),
            "initial_cash": self.initial_cash,
            "market_utc_offset_hours": self.market_utc_offset_hours,
            "session_start_minute": self.session_start_minute,
            "session_end_minute": self.session_end_minute,
            "slippage_bps": self.slippage_bps,
            "save_symbol_bars": self.save_symbol_bars,
            "strategy_params": self.strategy_params,
        }
