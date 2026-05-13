from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
import re


DEFAULT_DATA_ROOT = Path("D:/TradingData/massive_flatfiles/us_stock_sip/minutes_agg_v1")
DEFAULT_OUTPUT_ROOT = Path("D:/TradingData/quant-research-workbench/runs")
DEFAULT_PROCESSED_DATA_ROOT = Path("D:/TradingData/quant-research-workbench/market_data")
DEFAULT_RUN_NAME_PLACEHOLDERS = {"", "react app run", "untitled run"}


def slugify_run_token(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(value)).strip("_").lower()
    return slug or "run"


def generated_run_name(strategy_name: str, strategy_version: str, created_at: datetime | None = None, suffix: str | None = None) -> str:
    moment = created_at or datetime.now()
    timestamp = moment.strftime("%Y%m%d_%H%M%S_%f")[:-3]
    parts = [slugify_run_token(strategy_name), slugify_run_token(strategy_version), timestamp]
    if suffix:
        parts.append(slugify_run_token(suffix))
    return "_".join(parts)


def is_generated_run_name(value: str, strategy_name: str, strategy_version: str) -> bool:
    name = slugify_run_token(value)
    prefix = f"{slugify_run_token(strategy_name)}_{slugify_run_token(strategy_version)}_"
    timestamp_pattern = r"\d{8}_\d{6}_\d{3}(?:_.+)?$"
    return bool(name.startswith(prefix) and re.match(f"^{re.escape(prefix)}{timestamp_pattern}", name))


def submitted_run_name(strategy_name: str, strategy_version: str, current_name: str | None, created_at: datetime | None = None) -> str:
    value = str(current_name or "").strip()
    normalized = value.lower()
    if normalized in DEFAULT_RUN_NAME_PLACEHOLDERS:
        return generated_run_name(strategy_name, strategy_version, created_at)
    if is_generated_run_name(value, strategy_name, strategy_version):
        return slugify_run_token(value)
    return generated_run_name(strategy_name, strategy_version, created_at, suffix=value)


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


@dataclass(slots=True)
class BacktestConfig:
    strategy_name: str
    start_date: date
    end_date: date
    strategy_version: str = "v3"
    run_name: str = "Untitled run"
    data_root: Path = DEFAULT_DATA_ROOT
    processed_data_root: Path = DEFAULT_PROCESSED_DATA_ROOT
    output_root: Path = DEFAULT_OUTPUT_ROOT
    initial_cash: float = 10_000.0
    market_utc_offset_hours: float = -4.0
    session_start_minute: int = 9 * 60 + 30
    session_end_minute: int = 16 * 60
    slippage_bps: float = 2.0
    fee_model: str = "ibkr_ca_us_stock_fixed"
    fee_tax_rate: float = 0.0
    save_symbol_bars: bool = True
    observability_mode: str = "standard"
    observability_sessions: int = 7
    observability_scanner_top_percent: float = 0.25
    observability_scanner_min_rows: int = 10
    observability_scanner_max_rows: int = 100
    observability_always_trace_trades: bool = True
    created_by_app: bool = False
    strategy_params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BacktestConfig":
        data = dict(raw)
        data["start_date"] = parse_date(data["start_date"])
        data["end_date"] = parse_date(data["end_date"])
        data["data_root"] = Path(data.get("data_root", DEFAULT_DATA_ROOT))
        data["processed_data_root"] = Path(data.get("processed_data_root", DEFAULT_PROCESSED_DATA_ROOT))
        data["output_root"] = Path(data.get("output_root", DEFAULT_OUTPUT_ROOT))
        data["strategy_params"] = dict(data.get("strategy_params", {}))
        data["strategy_version"] = str(data.get("strategy_version") or "v3").strip()
        data["run_name"] = str(data.get("run_name") or "Untitled run").strip()
        data["observability_mode"] = str(data.get("observability_mode") or "standard").strip().lower()
        data["observability_sessions"] = int(data.get("observability_sessions", 7))
        data["observability_scanner_top_percent"] = float(data.get("observability_scanner_top_percent", 0.25))
        data["observability_scanner_min_rows"] = int(data.get("observability_scanner_min_rows", 10))
        data["observability_scanner_max_rows"] = int(data.get("observability_scanner_max_rows", 100))
        data["observability_always_trace_trades"] = bool(data.get("observability_always_trace_trades", True))
        data["created_by_app"] = bool(data.get("created_by_app", False))
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "run_name": self.run_name,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "data_root": str(self.data_root),
            "processed_data_root": str(self.processed_data_root),
            "output_root": str(self.output_root),
            "initial_cash": self.initial_cash,
            "market_utc_offset_hours": self.market_utc_offset_hours,
            "session_start_minute": self.session_start_minute,
            "session_end_minute": self.session_end_minute,
            "slippage_bps": self.slippage_bps,
            "fee_model": self.fee_model,
            "fee_tax_rate": self.fee_tax_rate,
            "save_symbol_bars": self.save_symbol_bars,
            "observability_mode": self.observability_mode,
            "observability_sessions": self.observability_sessions,
            "observability_scanner_top_percent": self.observability_scanner_top_percent,
            "observability_scanner_min_rows": self.observability_scanner_min_rows,
            "observability_scanner_max_rows": self.observability_scanner_max_rows,
            "observability_always_trace_trades": self.observability_always_trace_trades,
            "created_by_app": self.created_by_app,
            "strategy_params": self.strategy_params,
        }

    @property
    def run_slug(self) -> str:
        prefix = f"{slugify_run_token(self.strategy_name)}_{slugify_run_token(self.strategy_version)}"
        run_name_slug = slugify_run_token(self.run_name)
        if run_name_slug.startswith(f"{prefix}_"):
            return run_name_slug
        return slugify_run_token(f"{prefix}_{self.run_name}")
