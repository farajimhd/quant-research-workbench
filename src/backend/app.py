from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import polars as pl
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.backtest.config import (
    DEFAULT_EXCLUDED_SYMBOLS_FILE,
    DEFAULT_OUTPUT_ROOT,
    BacktestConfig,
    generated_run_name,
    submitted_run_name,
)
from src.backtest.debugger import StepBacktestDebugger
from src.backtest.equity_candles import default_portfolio_candle_timeframe
from src.backtest.jobs import cancel_backtest_job, get_backtest_status, list_backtest_jobs, submit_backtest_job
from src.backtest.metrics import portfolio_pnl_breakdown
from src.backtest.results import list_runs, read_run_metadata
from src.backend.json_utils import json_safe, parse_csv_list
from src.backend.market_data_service import (
    apply_chart_volume_convergence_columns,
    artifact_records,
    artifact_schema,
    chart_display_item_options,
    catalog_preview_payload,
    chart_timestamp_seconds,
    chart_payload,
    coverage_rows,
    display_item_settings,
    display_item_markers,
    display_price_zones,
    extended_session_regions,
    feature_groups_for_display_items,
    first_matching_artifact,
    first_ticker_in_range,
    load_momentum_discovery,
    load_artifact_query_sample,
    load_artifact_sample,
    load_scanner_snapshot,
    review_payload,
    resolve_chart_display_items,
    scope_defaults,
    source_scan,
)
from src.backend.progress_model import build_progress_model
from src.data_provider.calendar import market_sessions, scan_market_source
from src.data_provider.catalog import provider_catalog, save_presentation_override
from src.data_provider.config import (
    DEFAULT_PROCESSED_ROOT,
    DEFAULT_RAW_ROOT,
    DEFAULT_SPREAD_ROOT,
    DataProviderConfig,
    FEATURE_GROUPS,
    TIMEFRAMES,
    BuildRequest,
)
from src.data_provider.jobs import cancel_build_job, delete_build_job, get_build_status, list_build_jobs, pause_build_job, resume_build_job, resume_paused_build_job, submit_build_job
from src.data_provider.manifest import read_manifest
from src.data_provider.provider import MarketDataProvider
from src.strategies.registry import (
    available_strategies,
    available_strategy_versions,
    create_strategy,
    default_strategy_params,
    default_strategy_version,
    strategy_description,
    strategy_readme_path,
    strategy_chart_presentation,
    strategy_version_description,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
CHART_DISPLAY_ITEMS_NONE = "__none__"
EXCHANGE_TIME_ZONE = "America/New_York"
PORTFOLIO_CHART_TIMEFRAMES = ["30m", "1h", "2h", "4h", "1d"]
DEBUG_SESSIONS: dict[str, StepBacktestDebugger] = {}

app = FastAPI(title="Quant Research Workbench API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScopeUpdate(BaseModel):
    raw_root: str = Field(default=str(DEFAULT_RAW_ROOT))
    spread_root: str = Field(default=str(DEFAULT_SPREAD_ROOT))
    processed_root: str = Field(default=str(DEFAULT_PROCESSED_ROOT))
    start_date: date
    end_date: date


class BuildSubmit(ScopeUpdate):
    session_workers: int = Field(default=8, ge=1, le=24)
    polars_threads: int = Field(default=10, ge=1, le=24)


def build_start_with_reference_warmup(start_date: date, end_date: date, warmup_sessions: int = 13) -> date:
    search_start = start_date - timedelta(days=max(45, warmup_sessions * 5))
    sessions = market_sessions(search_start, end_date)
    first_output_index = next((index for index, session in enumerate(sessions) if session >= start_date), None)
    if first_output_index is None:
        return start_date
    return sessions[max(0, first_output_index - warmup_sessions)]


class BacktestSubmit(BaseModel):
    strategy_name: str
    strategy_version: str = "v3"
    run_name: str = ""
    start_date: date
    end_date: date
    data_root: str = Field(default=str(DEFAULT_RAW_ROOT))
    processed_data_root: str = Field(default=str(DEFAULT_PROCESSED_ROOT))
    output_root: str = Field(default=str(DEFAULT_OUTPUT_ROOT))
    excluded_symbols_file: str = Field(default=str(DEFAULT_EXCLUDED_SYMBOLS_FILE))
    initial_cash: float = 10_000.0
    slippage_bps: float = 0.0
    max_entry_participation_rate: float = 0.05
    max_entry_trade_multiple: float = 3.0
    enable_partial_fills: bool = True
    max_allowable_entry_fill_size: int = 3_000
    exit_liquidity_slippage_bps_per_excess_multiple: float = 10.0
    fee_model: str = "ibkr_ca_us_stock_fixed"
    fee_tax_rate: float = 0.0
    save_symbol_bars: bool = True
    observability_mode: str = "standard"
    observability_sessions: int = 7
    observability_scanner_top_percent: float = 0.25
    observability_scanner_min_rows: int = 10
    observability_scanner_max_rows: int = 100
    observability_always_trace_trades: bool = True
    strategy_params: dict[str, Any] = Field(default_factory=dict)


class CatalogPresentationUpdate(BaseModel):
    processed_root: str = Field(default=str(DEFAULT_PROCESSED_ROOT))
    item_id: str
    presentation: dict[str, Any] = Field(default_factory=dict)


class LiveTradingPreloadRequest(BaseModel):
    processed_root: str = Field(default=str(DEFAULT_PROCESSED_ROOT))
    session_date: date


class LiveTradingNextSignalRequest(BaseModel):
    processed_root: str = Field(default=str(DEFAULT_PROCESSED_ROOT))
    session_date: date
    start_time: str = "04:00"
    feature_groups: list[str] = Field(default_factory=lambda: ["core", "session", "momentum", "volume_liquidity", "price_action", "shock", "market_structure"])
    columns: list[str] = Field(default_factory=list)
    table_query: dict[str, Any] | None = None
    row_limit: int = Field(default=1000, ge=1, le=5000)


def parse_date_param(value: date | None, fallback: str) -> date:
    return value or date.fromisoformat(fallback)


def parse_live_clock_minute(value: str) -> int | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour * 60 + minute


def read_table(path: Path, limit: int = 1000) -> dict[str, Any]:
    if not path.exists():
        return {"columns": [], "rows": []}
    frame = pl.read_parquet(path)
    if frame.height > limit:
        frame = frame.head(limit)
    return {"columns": frame.columns, "rows": json_safe(frame.to_dicts())}


def empty_backtest_tables() -> dict[str, dict[str, Any]]:
    return {
        "daily": {"columns": [], "rows": []},
        "trades": {"columns": [], "rows": []},
        "orders": {"columns": [], "rows": []},
        "fills": {"columns": [], "rows": []},
        "scanner": {"columns": [], "rows": []},
        "watchlist": {"columns": [], "rows": []},
        "observability_scanner": {"columns": [], "rows": []},
        "observability_trace": {"columns": [], "rows": []},
        "observability_state": {"columns": [], "rows": []},
        "rejections": {"columns": [], "rows": []},
        "positions": {"columns": [], "rows": []},
        "portfolio": {"columns": [], "rows": []},
    }


def backtest_tables_payload(run_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        "daily": read_table(run_dir / "daily_summary.parquet", limit=10_000),
        "trades": read_table(run_dir / "trades.parquet", limit=10_000),
        "orders": read_table(run_dir / "orders.parquet", limit=10_000),
        "fills": read_table(run_dir / "fills.parquet", limit=10_000),
        "scanner": read_table(run_dir / "scanner_snapshots.parquet", limit=10_000),
        "watchlist": read_table(run_dir / "watchlist_snapshots.parquet", limit=50_000),
        "observability_scanner": read_table(run_dir / "observability_scanner.parquet", limit=50_000),
        "observability_trace": read_table(run_dir / "observability_trace.parquet", limit=25_000),
        "observability_state": read_table(run_dir / "observability_state.parquet", limit=25_000),
        "rejections": read_table(run_dir / "rejection_events.parquet", limit=10_000),
        "positions": read_table(run_dir / "positions.parquet", limit=10_000),
        "portfolio": read_table(run_dir / "portfolio.parquet", limit=25_000),
    }


def enriched_backtest_summary(run_dir: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    summary = dict(metadata.get("summary") or {})
    if "avg_daily_pnl" in summary:
        return summary
    portfolio_path = run_dir / "portfolio.parquet"
    daily_path = run_dir / "daily_summary.parquet"
    if not portfolio_path.exists():
        return summary
    config = metadata.get("config") or {}
    initial_cash = float(config.get("initial_cash") or summary.get("initial_cash") or 0.0)
    portfolio_rows = pl.read_parquet(portfolio_path).to_dicts()
    daily_rows = pl.read_parquet(daily_path).to_dicts() if daily_path.exists() else []
    summary.update(portfolio_pnl_breakdown(initial_cash, portfolio_rows, daily_rows))
    return summary


def read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def timestamp_seconds(value: Any, timezone_name: str = EXCHANGE_TIME_ZONE) -> int | None:
    if isinstance(value, datetime):
        dt = value
    elif value is None:
        return None
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
    return int(dt.timestamp())


def portfolio_candle_payload(run_dir: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    path = run_dir / "portfolio_candles.parquet"
    chart_metadata = read_json_file(run_dir / "chart_metadata.json")
    config = metadata.get("config") or {}
    metadata_timeframes = chart_metadata.get("portfolio_candle_timeframes") or PORTFOLIO_CHART_TIMEFRAMES
    available_timeframes = [timeframe for timeframe in metadata_timeframes if timeframe in PORTFOLIO_CHART_TIMEFRAMES]
    if not available_timeframes:
        available_timeframes = PORTFOLIO_CHART_TIMEFRAMES
    default_timeframe = chart_metadata.get("default_portfolio_candle_timeframe")
    if not default_timeframe:
        try:
            default_timeframe = default_portfolio_candle_timeframe(
                date.fromisoformat(str(config.get("start_date"))),
                date.fromisoformat(str(config.get("end_date"))),
            )
        except (TypeError, ValueError):
            default_timeframe = "30m"
    if not path.exists():
        return {"timeframes": available_timeframes, "default_timeframe": default_timeframe, "candles": {}}
    frame = pl.read_parquet(path)
    candles: dict[str, list[dict[str, Any]]] = {}
    for timeframe in available_timeframes:
        if "timeframe" not in frame.columns:
            rows = frame.to_dicts()
        else:
            rows = frame.filter(pl.col("timeframe") == timeframe).sort("timestamp").to_dicts()
        candles[str(timeframe)] = [
            {
                "time": timestamp_seconds(row.get("timestamp"), timezone_name="UTC"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "equity_open": row.get("equity_open"),
                "equity_high": row.get("equity_high"),
                "equity_low": row.get("equity_low"),
                "equity_close": row.get("equity_close"),
                "open_unrealized_open": row.get("open_unrealized_open"),
                "open_unrealized_high": row.get("open_unrealized_high"),
                "open_unrealized_low": row.get("open_unrealized_low"),
                "open_unrealized_close": row.get("open_unrealized_close"),
                "realized_pnl_open": row.get("realized_pnl_open"),
                "realized_pnl_high": row.get("realized_pnl_high"),
                "realized_pnl_low": row.get("realized_pnl_low"),
                "realized_pnl_close": row.get("realized_pnl_close"),
                "drawdown_open": row.get("drawdown_open"),
                "drawdown_high": row.get("drawdown_high"),
                "drawdown_low": row.get("drawdown_low"),
                "drawdown_close": row.get("drawdown_close"),
                "drawdown_pct_close": row.get("drawdown_pct_close"),
                "gross_exposure": row.get("gross_exposure"),
                "cash": row.get("cash"),
                "open_positions": row.get("open_positions"),
            }
            for row in rows
            if timestamp_seconds(row.get("timestamp"), timezone_name="UTC") is not None
        ]
    return {"timeframes": available_timeframes, "default_timeframe": default_timeframe, "candles": candles}


def run_symbol_chart_payload(
    run_dir: Path,
    symbol: str,
    selected_display_items: list[str] | None = None,
    selected_timeframe: str | None = None,
) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    metadata = read_run_metadata(run_dir) or {}
    presentation = run_strategy_chart_presentation(metadata)
    config = metadata.get("config") or {}
    processed_root = Path(config.get("processed_data_root") or DEFAULT_PROCESSED_ROOT)
    start_date, end_date = run_chart_date_range(metadata)
    requested_timeframes = strategy_chart_timeframes(presentation)
    default_timeframe = str(presentation.get("default_timeframe") or (requested_timeframes[0] if requested_timeframes else "1m"))
    if default_timeframe not in requested_timeframes and requested_timeframes:
        default_timeframe = requested_timeframes[0]
    active_timeframe = selected_timeframe if selected_timeframe in requested_timeframes else default_timeframe
    timeframe_payloads = {
        active_timeframe: symbol_timeframe_chart_payload(
            run_dir,
            normalized_symbol,
            active_timeframe,
            presentation,
            processed_root,
            start_date,
            end_date,
            selected_display_items,
        )
    }
    available_timeframes = requested_timeframes or [active_timeframe]
    default_payload = timeframe_payloads.get(active_timeframe, empty_symbol_timeframe_payload())
    trades = run_symbol_trades(run_dir, normalized_symbol)
    catalog = provider_catalog(processed_root)
    return {
        "symbol": normalized_symbol,
        "timeframes": available_timeframes,
        "default_timeframe": active_timeframe,
        "timeframe_payloads": timeframe_payloads,
        "presentation": presentation,
        "catalog_columns": catalog.get("columns", []),
        "selected_display_items": selected_display_items,
        "trades": trades,
        **default_payload,
    }


def symbol_timeframe_chart_payload(
    run_dir: Path,
    normalized_symbol: str,
    timeframe: str,
    presentation: dict[str, Any],
    processed_root: Path,
    start_date: date | None,
    end_date: date | None,
    selected_display_items: list[str] | None,
) -> dict[str, Any]:
    display_options, selected_items, requested_feature_groups = symbol_chart_display_contracts(processed_root, timeframe, start_date, end_date, presentation, selected_display_items)
    frame = provider_symbol_frame(normalized_symbol, timeframe, presentation, processed_root, start_date, end_date, requested_feature_groups)
    if frame is None or frame.is_empty():
        frame = saved_symbol_frame(run_dir, normalized_symbol, timeframe)
    if frame is None or frame.is_empty():
        return empty_symbol_timeframe_payload()
    required_columns = {"ticker", "bar_time_market", "open", "high", "low", "close"}
    if not required_columns.issubset(set(frame.columns)):
        return empty_symbol_timeframe_payload()
    frame = apply_chart_volume_convergence_columns(frame.sort("bar_time_market"))
    rows = frame.to_dicts()
    timed_rows = [(chart_timestamp_seconds(row, timeframe), row) for row in rows]
    timed_rows = [(timestamp, row) for timestamp, row in timed_rows if timestamp is not None]
    candles, volume = symbol_candles_and_volume(timed_rows)
    return {
        "candles": candles,
        "volume": volume,
        "overlay_series": symbol_overlay_series(timed_rows, selected_items, timeframe),
        "oscillator_series": symbol_oscillator_series(timed_rows, selected_items, timeframe),
        "markers": display_item_markers([row for _, row in timed_rows], timeframe, selected_items, marker_limit=500),
        "price_zones": display_price_zones([row for _, row in timed_rows], timeframe, selected_items),
        "regions": extended_session_regions([row for _, row in timed_rows], timeframe),
        "options": {
            "display_items": display_options,
            "feature_columns": [],
            "feature_groups": requested_feature_groups,
            "standard_indicators": [],
            "supervision_groups": [],
        },
    }


def symbol_chart_display_contracts(
    processed_root: Path,
    timeframe: str,
    start_date: date | None,
    end_date: date | None,
    presentation: dict[str, Any],
    selected_display_items: list[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    if start_date is None or end_date is None:
        if selected_display_items is None:
            selected_items = strategy_display_items(processed_root, presentation, timeframe)
        else:
            catalog = provider_catalog(processed_root)
            by_id = {str(item.get("id")): item for item in catalog.get("displayItems", [])}
            selected_items = [by_id[item_id] for item_id in selected_display_items if item_id in by_id]
        requested_feature_groups = feature_groups_for_display_items(selected_items)
        if selected_display_items is None and not requested_feature_groups:
            requested_feature_groups = strategy_timeframe_feature_groups(presentation, timeframe)
        return [], selected_items, requested_feature_groups
    catalog = provider_catalog(processed_root)
    display_options = chart_display_item_options(artifact_records(processed_root), timeframe, start_date, end_date, catalog)
    selected_ids = selected_display_items if selected_display_items is not None else strategy_display_item_ids(presentation, timeframe)
    selected_items = resolve_chart_display_items(catalog, display_options, selected_ids, [])
    if selected_display_items is None and not selected_items:
        selected_items = strategy_display_items(processed_root, presentation, timeframe)
    requested_feature_groups = feature_groups_for_display_items(selected_items)
    if selected_display_items is None and not requested_feature_groups:
        requested_feature_groups = strategy_timeframe_feature_groups(presentation, timeframe)
    return display_options, selected_items, requested_feature_groups


def saved_symbol_frame(run_dir: Path, normalized_symbol: str, timeframe: str) -> pl.DataFrame | None:
    path = run_dir / ("symbol_bars.parquet" if timeframe == "1m" else f"symbol_bars_{timeframe}.parquet")
    if not path.exists():
        return None
    frame = pl.read_parquet(path)
    if "ticker" not in frame.columns:
        return None
    return frame.filter(pl.col("ticker").cast(pl.Utf8).str.to_uppercase() == normalized_symbol)


def provider_symbol_frame(
    normalized_symbol: str,
    timeframe: str,
    presentation: dict[str, Any],
    processed_root: Path,
    start_date: date | None,
    end_date: date | None,
    feature_groups: list[str] | None = None,
) -> pl.DataFrame | None:
    if start_date is None or end_date is None:
        return None
    try:
        provider = MarketDataProvider(DataProviderConfig(processed_root=processed_root))
        frame = provider.load_bars(
            start_date=start_date,
            end_date=end_date,
            timeframe=timeframe,
            tickers=[normalized_symbol],
            feature_groups=feature_groups if feature_groups is not None else strategy_timeframe_feature_groups(presentation, timeframe),
        )
    except (FileNotFoundError, OSError, ValueError, pl.exceptions.PolarsError):
        return None
    return frame if not frame.is_empty() else None


def run_chart_date_range(metadata: dict[str, Any]) -> tuple[date | None, date | None]:
    config = metadata.get("config") or {}
    start = config.get("start_date") or metadata.get("requested_start_date")
    end = config.get("end_date") or metadata.get("requested_end_date") or start
    try:
        return (date.fromisoformat(str(start)), date.fromisoformat(str(end)))
    except (TypeError, ValueError):
        return None, None


def strategy_timeframe_feature_groups(presentation: dict[str, Any], timeframe: str) -> list[str]:
    groups = presentation.get("feature_groups")
    if isinstance(groups, dict):
        values = groups.get(timeframe) or groups.get("*") or []
    else:
        values = groups or []
    return [str(group) for group in values if str(group).strip()]


def strategy_display_items(processed_root: Path, presentation: dict[str, Any], timeframe: str) -> list[dict[str, Any]]:
    ids = strategy_display_item_ids(presentation, timeframe)
    if not ids:
        return []
    catalog = provider_catalog(processed_root)
    by_id = {str(item.get("id")): item for item in catalog.get("displayItems", [])}
    return [by_id[item_id] for item_id in ids if item_id in by_id]


def strategy_display_item_ids(presentation: dict[str, Any], timeframe: str) -> list[str]:
    items = presentation.get("display_items")
    if isinstance(items, dict):
        values = items.get(timeframe) or items.get("*") or []
    else:
        values = items or []
    return [str(item) for item in values if str(item).strip()]


def symbol_candles_and_volume(timed_rows: list[tuple[int, dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candles = [
        {
            "time": timestamp,
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
        }
        for timestamp, row in timed_rows
    ]
    volume = [
        {
            "time": timestamp,
            "value": row.get("volume") or 0,
            "color": "#16a34a" if float(row.get("close") or 0) >= float(row.get("open") or 0) else "#dc2626",
        }
        for timestamp, row in timed_rows
        if "volume" in row
    ]
    return candles, volume


def run_symbol_trades(run_dir: Path, symbol: str) -> list[dict[str, Any]]:
    path = run_dir / "trades.parquet"
    if not path.exists():
        return []
    frame = pl.read_parquet(path)
    if "symbol" not in frame.columns:
        return []
    rows = frame.filter(pl.col("symbol").cast(pl.Utf8).str.to_uppercase() == symbol).sort("entry_time").to_dicts()
    orders_by_id = run_orders_by_id(run_dir)
    fills = run_symbol_fills(run_dir, symbol)
    return json_safe([enrich_trade_with_order_context(row, orders_by_id, fills) for row in rows])


def empty_symbol_timeframe_payload() -> dict[str, Any]:
    return {"candles": [], "volume": [], "overlay_series": [], "oscillator_series": [], "markers": [], "price_zones": [], "regions": []}


def run_strategy_chart_presentation(metadata: dict[str, Any]) -> dict[str, Any]:
    snapshot = metadata.get("strategy_chart_presentation")
    strategy_name = str(metadata.get("strategy_name") or (metadata.get("config") or {}).get("strategy_name") or "").strip()
    strategy_version = str(metadata.get("strategy_version") or (metadata.get("config") or {}).get("strategy_version") or "").strip()
    if strategy_name:
        try:
            current = strategy_chart_presentation(strategy_name, strategy_version or None)
            if isinstance(snapshot, dict):
                return {**snapshot, **current}
            return current
        except KeyError:
            pass
    if isinstance(snapshot, dict) and snapshot.get("display_items"):
        return snapshot
    return {}


def strategy_chart_timeframes(presentation: dict[str, Any]) -> list[str]:
    values = presentation.get("timeframes")
    if isinstance(values, list):
        timeframes = [str(value) for value in values if str(value).strip()]
        if timeframes:
            return timeframes
    return ["1m"]


def run_orders_by_id(run_dir: Path) -> dict[int, dict[str, Any]]:
    path = run_dir / "orders.parquet"
    if not path.exists():
        return {}
    frame = pl.read_parquet(path)
    if "order_id" not in frame.columns:
        return {}
    rows = frame.to_dicts()
    return {int(row["order_id"]): row for row in rows if row.get("order_id") is not None}


def run_symbol_fills(run_dir: Path, symbol: str) -> list[dict[str, Any]]:
    path = run_dir / "fills.parquet"
    if not path.exists():
        return []
    frame = pl.read_parquet(path)
    if "symbol" not in frame.columns:
        return []
    return frame.filter(pl.col("symbol").cast(pl.Utf8).str.to_uppercase() == symbol).to_dicts()


def enrich_trade_with_order_context(trade: dict[str, Any], orders_by_id: dict[int, dict[str, Any]], fills: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    enriched = dict(trade)
    entry_order_id = trade.get("entry_order_id")
    entry_order = orders_by_id.get(int(entry_order_id)) if entry_order_id is not None else None
    if entry_order:
        tag = str(entry_order.get("tag") or "")
        enriched["entry_order_tag"] = tag
        enriched["entry_reason"] = entry_order.get("reason") or "Entry"
        for key, value in parse_pipe_tag(tag).items():
            if key in {"trigger", "stop", "box_high", "box_mid", "box_low"}:
                enriched[f"entry_{key}"] = value
        if "entry_stop" in enriched:
            enriched["stop_price"] = enriched["entry_stop"]
    fills = fills or []
    entry_fill = matching_trade_fill(trade, fills, "BUY", "entry_time", "entry_price")
    exit_fill = matching_trade_fill(trade, fills, "SELL", "exit_time", "exit_price")
    enriched["entry_fills"] = matching_trade_fills(trade, fills, "BUY")
    enriched["exit_fills"] = matching_trade_fills(trade, fills, "SELL")
    if entry_fill and entry_fill.get("bar_time_market"):
        enriched["entry_bar_time"] = entry_fill.get("bar_time_market")
    if exit_fill and exit_fill.get("bar_time_market"):
        enriched["exit_bar_time"] = exit_fill.get("bar_time_market")
    return enriched


def matching_trade_fills(trade: dict[str, Any], fills: list[dict[str, Any]], side: str) -> list[dict[str, Any]]:
    entry_time = str(trade.get("entry_time") or "")
    exit_time = str(trade.get("exit_time") or "")
    if not entry_time or not exit_time:
        return []
    selected = [
        fill
        for fill in fills
        if str(fill.get("side") or "").upper() == side
        and entry_time <= str(fill.get("filled_at") or "") <= exit_time
    ]
    return sorted(selected, key=lambda fill: str(fill.get("filled_at") or ""))


def matching_trade_fill(trade: dict[str, Any], fills: list[dict[str, Any]], side: str, time_key: str, price_key: str) -> dict[str, Any] | None:
    trade_time = str(trade.get(time_key) or "")
    trade_price = float(trade.get(price_key) or 0.0)
    candidates = [
        fill
        for fill in fills
        if str(fill.get("side") or "").upper() == side and str(fill.get("filled_at") or "") == trade_time
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda fill: abs(float(fill.get("fill_price") or 0.0) - trade_price))


def parse_pipe_tag(tag: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for part in tag.split("|"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if not key:
            continue
        values[key] = float(value) if re.fullmatch(r"-?\d+(?:\.\d+)?", value) else value
    return values


def symbol_overlay_series(timed_rows: list[tuple[int, dict[str, Any]]], selected_items: list[dict[str, Any]], timeframe: str = "1m") -> list[dict[str, Any]]:
    configured = catalog_symbol_series(timed_rows, selected_items, "price")
    if configured or selected_items:
        return configured
    series_config = [
        ("tema9", "TEMA 9", "#2563eb"),
        ("tema20", "TEMA 20", "#7c3aed"),
    ]
    return [
        {
            "color": color,
            "column": column,
            "data": [{"time": timestamp, "value": row.get(column)} for timestamp, row in timed_rows if row.get(column) is not None],
            "displayItemId": column,
            "label": label,
            "lineStyle": "solid",
            "lineWidth": 2,
            "style": "line",
        }
        for column, label, color in series_config
        if column in (timed_rows[0][1].keys() if timed_rows else set())
    ]


def symbol_oscillator_series(timed_rows: list[tuple[int, dict[str, Any]]], selected_items: list[dict[str, Any]], timeframe: str = "1m") -> list[dict[str, Any]]:
    configured = catalog_symbol_series(timed_rows, selected_items, "oscillator")
    if configured or selected_items:
        return configured
    series_config = [
        ("macd_line", "MACD", "#2563eb"),
        ("macd_signal", "Signal", "#f97316"),
        ("macd_hist", "Histogram", "#64748b"),
    ]
    return [
        {
            "color": color,
            "column": column,
            "data": [{"time": timestamp, "value": row.get(column)} for timestamp, row in timed_rows if row.get(column) is not None],
            "displayItemId": column,
            "label": label,
            "lineStyle": "solid",
            "lineWidth": 2,
            "paneKey": "macd",
            "style": "histogram" if column == "macd_hist" else "line",
        }
        for column, label, color in series_config
        if column in (timed_rows[0][1].keys() if timed_rows else set())
    ]


def catalog_symbol_series(timed_rows: list[tuple[int, dict[str, Any]]], selected_items: list[dict[str, Any]], pane: str) -> list[dict[str, Any]]:
    if not timed_rows or not selected_items:
        return []
    columns = timed_rows[0][1].keys()
    settings = display_item_settings(selected_items)
    series = []
    for _, option in settings.items():
        option_pane = str(option.get("pane") or "price")
        if option_pane != pane:
            continue
        column = str(option.get("column") or "")
        if not column or column not in columns:
            continue
        points = []
        for timestamp, row in timed_rows:
            value = row.get(column)
            if value is None:
                continue
            point = {"time": timestamp, "value": value}
            if option.get("dynamicColor"):
                point["color"] = "#33E42A" if float(value) >= 0 else "#FD0E50"
            points.append(point)
        item = {
            "bandFillColor": option.get("bandFillColor"),
            "bandFillOpacity": option.get("bandFillOpacity"),
            "chartRole": option.get("chartRole"),
            "color": str(option.get("color") or "#2563eb"),
            "column": column,
            "data": points,
            "displayItemId": option.get("displayItemId"),
            "label": str(option.get("label") or column),
            "legend": option.get("legend", True),
            "lineStyle": str(option.get("lineStyle") or "solid"),
            "lineWidth": int(option.get("lineWidth") or 2),
            "opacity": option.get("opacity", 1.0),
            "style": str(option.get("style") or "line"),
        }
        pane_key = option.get("paneKey")
        if pane_key and pane_key != "price":
            item["paneKey"] = pane_key
        series.append(item)
    return series


def resolve_chart_range(start_date: date | None, end_date: date | None, session_date: date | None) -> tuple[date, date]:
    range_start = start_date or session_date
    range_end = end_date or range_start
    if range_start is None or range_end is None:
        raise HTTPException(status_code=400, detail="start_date and end_date are required")
    if range_end < range_start:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")
    return range_start, range_end


def parse_table_query(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid table query JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Table query must be an object")
    return payload


def parse_derived_columns(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid derived columns JSON") from exc
    if not isinstance(payload, list):
        raise HTTPException(status_code=400, detail="Derived columns must be a list")
    return [item for item in payload if isinstance(item, dict)]


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "app": "quant-research-workbench"}


@app.get("/api/config/defaults")
def config_defaults() -> dict[str, Any]:
    return {
        "raw_root": str(DEFAULT_RAW_ROOT),
        "processed_root": str(DEFAULT_PROCESSED_ROOT),
        "output_root": str(DEFAULT_OUTPUT_ROOT),
        "timeframes": list(TIMEFRAMES),
        "feature_groups": list(FEATURE_GROUPS),
        "supervision_groups": [],
    }


@app.get("/api/strategies")
def strategies() -> dict[str, Any]:
    return {
        "strategies": [
            {
                "name": name,
                "display_name": name.replace("_", " ").title(),
                "description": strategy_description(name),
                "version_descriptions": {version: strategy_version_description(name, version) for version in available_strategy_versions(name)},
                "versions": available_strategy_versions(name),
                "default_version": default_strategy_version(name),
            }
            for name in available_strategies()
        ]
    }


@app.get("/api/strategies/{strategy_name}/readme")
def strategy_readme(strategy_name: str, version: str | None = None) -> dict[str, str]:
    try:
        path = strategy_readme_path(strategy_name, version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not path.exists():
        return {"content": "No README exists for this strategy."}
    selected_version = version or default_strategy_version(strategy_name)
    overview = (
        "## Strategy Summary\n\n"
        f"{strategy_description(strategy_name)}\n\n"
        f"**{selected_version}**: {strategy_version_description(strategy_name, selected_version)}\n\n"
        "---\n\n"
    )
    return {"content": overview + path.read_text(encoding="utf-8"), "version": selected_version}


@app.get("/api/strategies/{strategy_name}/default-config")
def strategy_default_config(strategy_name: str, version: str | None = None) -> dict[str, Any]:
    if strategy_name not in available_strategies():
        raise HTTPException(status_code=404, detail="Unknown strategy")
    selected_version = version or default_strategy_version(strategy_name)
    try:
        strategy_params = default_strategy_params(strategy_name, selected_version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "strategy_name": strategy_name,
        "strategy_version": selected_version,
        "strategy_description": strategy_description(strategy_name),
        "strategy_version_description": strategy_version_description(strategy_name, selected_version),
        "chart_presentation": strategy_chart_presentation(strategy_name, selected_version),
        "run_name": generated_run_name(strategy_name, selected_version),
        "start_date": "2024-05-01",
        "end_date": "2024-05-09",
        "data_root": str(DEFAULT_RAW_ROOT),
        "processed_data_root": str(DEFAULT_PROCESSED_ROOT),
        "output_root": str(DEFAULT_OUTPUT_ROOT),
        "excluded_symbols_file": str(DEFAULT_EXCLUDED_SYMBOLS_FILE),
        "initial_cash": 10_000.0,
        "slippage_bps": 0.0,
        "max_entry_participation_rate": 0.05,
        "max_entry_trade_multiple": 3.0,
        "enable_partial_fills": True,
        "max_allowable_entry_fill_size": 3_000,
        "exit_liquidity_slippage_bps_per_excess_multiple": 10.0,
        "fee_model": "ibkr_ca_us_stock_fixed",
        "fee_tax_rate": 0.0,
        "save_symbol_bars": True,
        "strategy_params": strategy_params,
    }


@app.get("/api/backtests/runs")
def backtest_runs(
    output_root: str = str(DEFAULT_OUTPUT_ROOT),
    strategy_name: str | None = None,
    strategy_version: str | None = None,
) -> dict[str, Any]:
    rows = []
    for path in list_runs(Path(output_root), strategy_name):
        metadata = read_run_metadata(path) or {}
        summary = metadata.get("summary") or {}
        config = metadata.get("config") or {}
        run_strategy_version = metadata.get("strategy_version", config.get("strategy_version", "v1"))
        if strategy_version and run_strategy_version != strategy_version:
            continue
        rows.append(
            {
                "run_id": path.name,
                "run_dir": str(path),
                "run_name": metadata.get("run_name", path.name),
                "strategy_name": metadata.get("strategy_name", config.get("strategy_name")),
                "strategy_version": run_strategy_version,
                "status": metadata.get("status", "unknown"),
                "created_at": metadata.get("created_at"),
                "date_range": f"{config.get('start_date', '')} to {config.get('end_date', '')}",
                "return_pct": summary.get("return_pct", 0.0),
                "total_pnl": summary.get("total_pnl", 0.0),
                "trade_count": summary.get("trade_count", 0),
            }
        )
    return {"runs": json_safe(rows)}


@app.post("/api/backtests/jobs")
def start_backtest(payload: BacktestSubmit) -> dict[str, Any]:
    raw = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    raw["run_name"] = submitted_run_name(raw["strategy_name"], raw.get("strategy_version") or "v3", raw.get("run_name"))
    config = BacktestConfig.from_dict({**raw, "created_by_app": True})
    try:
        strategy = create_strategy(config.strategy_name, config.strategy_params, config.strategy_version)
        from src.backtest.data.minute_bars import available_session_dates

        available_session_dates(config, strategy.data_requirements())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return submit_backtest_job(config)


@app.post("/api/backtests/debug/sessions")
def create_backtest_debug_session(payload: BacktestSubmit) -> dict[str, Any]:
    raw = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    raw["run_name"] = submitted_run_name(raw["strategy_name"], raw.get("strategy_version") or "v3", raw.get("run_name"))
    raw["observability_mode"] = "standard"
    raw["observability_sessions"] = max(999, int(raw.get("observability_sessions") or 0))
    raw["observability_scanner_top_percent"] = 1.0
    raw["observability_scanner_min_rows"] = max(100, int(raw.get("observability_scanner_min_rows") or 0))
    raw["observability_scanner_max_rows"] = max(500, int(raw.get("observability_scanner_max_rows") or 0))
    raw["observability_always_trace_trades"] = True
    raw["save_symbol_bars"] = True
    config = BacktestConfig.from_dict({**raw, "created_by_app": True})
    try:
        strategy = create_strategy(config.strategy_name, config.strategy_params, config.strategy_version)
        debugger = StepBacktestDebugger(config, strategy)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    DEBUG_SESSIONS[debugger.debug_session_id] = debugger
    return json_safe(debugger.payload())


@app.get("/api/backtests/debug/sessions/{session_id}")
def get_backtest_debug_session(session_id: str) -> dict[str, Any]:
    debugger = DEBUG_SESSIONS.get(session_id)
    if debugger is None:
        raise HTTPException(status_code=404, detail="Debug session not found")
    return json_safe(debugger.payload())


@app.post("/api/backtests/debug/sessions/{session_id}/next")
def next_backtest_debug_step(session_id: str) -> dict[str, Any]:
    debugger = DEBUG_SESSIONS.get(session_id)
    if debugger is None:
        raise HTTPException(status_code=404, detail="Debug session not found")
    return json_safe(debugger.next_step())


@app.post("/api/backtests/debug/sessions/{session_id}/run-until-action")
def run_backtest_debug_until_action(session_id: str, max_steps: int = Query(100, ge=1, le=5000)) -> dict[str, Any]:
    debugger = DEBUG_SESSIONS.get(session_id)
    if debugger is None:
        raise HTTPException(status_code=404, detail="Debug session not found")
    return json_safe(debugger.run_until_action(max_steps=max_steps))


@app.post("/api/backtests/debug/sessions/{session_id}/previous")
def previous_backtest_debug_step(session_id: str) -> dict[str, Any]:
    debugger = DEBUG_SESSIONS.get(session_id)
    if debugger is None:
        raise HTTPException(status_code=404, detail="Debug session not found")
    return json_safe(debugger.previous_step())


@app.delete("/api/backtests/debug/sessions/{session_id}")
def delete_backtest_debug_session(session_id: str) -> dict[str, Any]:
    removed = DEBUG_SESSIONS.pop(session_id, None)
    if removed is None:
        raise HTTPException(status_code=404, detail="Debug session not found")
    return {"deleted": True, "session_id": session_id}


@app.get("/api/backtests/jobs")
def get_backtest_jobs(output_root: str = str(DEFAULT_OUTPUT_ROOT)) -> dict[str, Any]:
    return {"jobs": list_backtest_jobs(Path(output_root))}


@app.get("/api/backtests/jobs/{job_id}")
def backtest_job_status(job_id: str, output_root: str = str(DEFAULT_OUTPUT_ROOT)) -> dict[str, Any]:
    status = get_backtest_status(Path(output_root), job_id)
    if not status.get("job_id"):
        raise HTTPException(status_code=404, detail="Backtest job not found")
    return status


@app.post("/api/backtests/jobs/{job_id}/cancel")
def stop_backtest(job_id: str, output_root: str = str(DEFAULT_OUTPUT_ROOT)) -> dict[str, Any]:
    status = cancel_backtest_job(Path(output_root), job_id)
    if not status.get("job_id"):
        raise HTTPException(status_code=404, detail="Backtest job not found")
    return status


@app.get("/api/backtests/runs/{run_id}")
def backtest_run_detail(
    run_id: str,
    output_root: str = str(DEFAULT_OUTPUT_ROOT),
    include_logs: bool = True,
    include_tables: bool = True,
) -> dict[str, Any]:
    root = Path(output_root).resolve()
    run_dir = (root / run_id).resolve()
    if root != run_dir and root not in run_dir.parents:
        raise HTTPException(status_code=400, detail="Invalid run path")
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    metadata = read_run_metadata(run_dir) or {}
    summary = enriched_backtest_summary(run_dir, metadata)
    metadata = {**metadata, "summary": summary}
    return {
        "metadata": json_safe(metadata),
        "summary": json_safe(summary),
        "tables": backtest_tables_payload(run_dir) if include_tables else empty_backtest_tables(),
        "portfolio_candles": json_safe(portfolio_candle_payload(run_dir, metadata)),
        "logs": (run_dir / "logs.txt").read_text(encoding="utf-8") if include_logs and (run_dir / "logs.txt").exists() else "",
    }


@app.get("/api/backtests/runs/{run_id}/symbols/{symbol}/chart")
def backtest_run_symbol_chart(
    run_id: str,
    symbol: str,
    output_root: str = str(DEFAULT_OUTPUT_ROOT),
    display_items: str | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    root = Path(output_root).resolve()
    run_dir = (root / run_id).resolve()
    if root != run_dir and root not in run_dir.parents:
        raise HTTPException(status_code=400, detail="Invalid run path")
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    return json_safe(run_symbol_chart_payload(run_dir, symbol, parse_chart_display_items(display_items), timeframe))


@app.delete("/api/backtests/runs/{run_id}")
def delete_backtest_run(run_id: str, output_root: str = str(DEFAULT_OUTPUT_ROOT)) -> dict[str, Any]:
    root = Path(output_root).resolve()
    run_dir = (root / run_id).resolve()
    if root != run_dir and root not in run_dir.parents:
        raise HTTPException(status_code=400, detail="Refusing to delete outside output root")
    metadata = read_run_metadata(run_dir)
    if not metadata or not metadata.get("created_by_app"):
        raise HTTPException(status_code=400, detail="Refusing to delete a run not created by this app")
    shutil.rmtree(run_dir)
    return {"status": "deleted", "run_id": run_id}


@app.get("/api/market-data/scope")
def market_scope(
    raw_root: str = str(DEFAULT_RAW_ROOT),
    processed_root: str = str(DEFAULT_PROCESSED_ROOT),
    spread_root: str = str(DEFAULT_SPREAD_ROOT),
) -> dict[str, Any]:
    return scope_defaults(Path(raw_root), Path(processed_root), Path(spread_root))


@app.get("/api/market-data/source")
def market_source(raw_root: str, start_date: date, end_date: date) -> dict[str, Any]:
    return {"rows": source_scan(Path(raw_root), start_date, end_date)}


@app.post("/api/market-data/build/jobs")
def start_build(payload: BuildSubmit) -> dict[str, Any]:
    request = BuildRequest(
        raw_root=Path(payload.raw_root),
        spread_root=Path(payload.spread_root),
        processed_root=Path(payload.processed_root),
        start_date=payload.start_date,
        end_date=payload.end_date,
        timeframes=list(TIMEFRAMES),
        feature_groups=list(FEATURE_GROUPS),
        supervision_groups=[],
        rebuild_mode="force_rebuild",
        tickers=None,
    )
    return submit_build_job(
        request,
        session_workers=payload.session_workers,
        polars_threads=payload.polars_threads,
    )


def submit_long_momentum_v9_feature_build(payload: BuildSubmit) -> dict[str, Any]:
    build_start = build_start_with_reference_warmup(payload.start_date, payload.end_date)
    request = BuildRequest(
        raw_root=Path(payload.raw_root),
        spread_root=Path(payload.spread_root),
        processed_root=Path(payload.processed_root),
        start_date=build_start,
        end_date=payload.end_date,
        timeframes=["1m"],
        feature_groups=["core", "momentum", "session", "volatility", "volume_liquidity"],
        supervision_groups=[],
        rebuild_mode="force_rebuild",
        tickers=None,
        resume_stage="force_stateful_features",
    )
    request.build_name = f"long_momentum_v9_features_{payload.start_date.isoformat()}_{payload.end_date.isoformat()}"
    return submit_build_job(
        request,
        session_workers=payload.session_workers,
        polars_threads=payload.polars_threads,
    )


@app.post("/api/market-data/build/long-momentum-v9/jobs")
def start_long_momentum_v9_build(payload: BuildSubmit) -> dict[str, Any]:
    return submit_long_momentum_v9_feature_build(payload)


@app.post("/api/market-data/build/long-momentum-v4/jobs")
def start_long_momentum_v4_build(payload: BuildSubmit) -> dict[str, Any]:
    return submit_long_momentum_v9_feature_build(payload)


@app.post("/api/market-data/build/oracle-supervision/jobs")
def start_oracle_supervision_build(payload: BuildSubmit) -> dict[str, Any]:
    request = BuildRequest(
        raw_root=Path(payload.raw_root),
        spread_root=Path(payload.spread_root),
        processed_root=Path(payload.processed_root),
        start_date=payload.start_date,
        end_date=payload.end_date,
        timeframes=["1m"],
        feature_groups=[],
        supervision_groups=["oracle"],
        rebuild_mode="force_rebuild",
        tickers=None,
    )
    request.build_name = f"oracle_supervision_{payload.start_date.isoformat()}_{payload.end_date.isoformat()}"
    return submit_build_job(
        request,
        session_workers=payload.session_workers,
        polars_threads=payload.polars_threads,
    )


@app.post("/api/market-data/build/spread-backfill/jobs")
def start_spread_backfill(payload: BuildSubmit) -> dict[str, Any]:
    request = BuildRequest(
        raw_root=Path(payload.raw_root),
        spread_root=Path(payload.spread_root),
        processed_root=Path(payload.processed_root),
        start_date=payload.start_date,
        end_date=payload.end_date,
        timeframes=list(TIMEFRAMES),
        feature_groups=list(FEATURE_GROUPS),
        supervision_groups=[],
        rebuild_mode="force_rebuild",
        tickers=None,
        resume_stage="spread_backfill",
    )
    request.build_name = f"spread_backfill_{payload.start_date.isoformat()}_{payload.end_date.isoformat()}"
    return submit_build_job(
        request,
        session_workers=payload.session_workers,
        polars_threads=payload.polars_threads,
    )


@app.get("/api/market-data/build/jobs")
def build_jobs(processed_root: str = str(DEFAULT_PROCESSED_ROOT)) -> dict[str, Any]:
    return {"jobs": list_build_jobs(Path(processed_root))}


@app.get("/api/market-data/build/jobs/{job_id}")
def build_job_status(job_id: str, processed_root: str = str(DEFAULT_PROCESSED_ROOT), raw_root: str = str(DEFAULT_RAW_ROOT)) -> dict[str, Any]:
    status = get_build_status(Path(processed_root), job_id)
    if not status.get("job_id"):
        raise HTTPException(status_code=404, detail="Build job not found")
    request = status.get("request") or {}
    start = date.fromisoformat(request.get("start_date"))
    end = date.fromisoformat(request.get("end_date"))
    source_rows = [asdict(row) for row in scan_market_source(Path(request.get("raw_root") or raw_root), start, end)]
    status["progress"] = build_progress_model(source_rows=source_rows, events=status.get("events", []), job_status=status)
    return json_safe(status)


@app.post("/api/market-data/build/jobs/{job_id}/cancel")
def stop_build(job_id: str, processed_root: str = str(DEFAULT_PROCESSED_ROOT)) -> dict[str, Any]:
    return cancel_build_job(Path(processed_root), job_id)


@app.post("/api/market-data/build/jobs/{job_id}/pause")
def pause_build(job_id: str, processed_root: str = str(DEFAULT_PROCESSED_ROOT)) -> dict[str, Any]:
    try:
        return pause_build_job(Path(processed_root), job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/market-data/build/jobs/{job_id}/resume")
def resume_paused_build(job_id: str, processed_root: str = str(DEFAULT_PROCESSED_ROOT)) -> dict[str, Any]:
    try:
        return resume_paused_build_job(Path(processed_root), job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/market-data/build/jobs/{job_id}/resume-stateful")
def resume_build_stateful(job_id: str, processed_root: str = str(DEFAULT_PROCESSED_ROOT)) -> dict[str, Any]:
    try:
        return resume_build_job(Path(processed_root), job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/market-data/build/jobs/{job_id}")
def delete_market_data_build(job_id: str, processed_root: str = str(DEFAULT_PROCESSED_ROOT)) -> dict[str, Any]:
    try:
        return delete_build_job(Path(processed_root), job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/market-data/review")
def market_review(
    processed_root: str = str(DEFAULT_PROCESSED_ROOT),
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    return json_safe(review_payload(Path(processed_root), start_date, end_date))


@app.get("/api/market-data/coverage")
def market_coverage(processed_root: str, group: str, start_date: date, end_date: date) -> dict[str, Any]:
    return {"rows": coverage_rows(artifact_records(Path(processed_root)), start_date, end_date, group)}


@app.get("/api/market-data/manifest")
def market_manifest(processed_root: str = str(DEFAULT_PROCESSED_ROOT)) -> dict[str, Any]:
    manifest = read_manifest(Path(processed_root))
    return {
        "card": {
            "updated_at": manifest.get("updated_at"),
            "schema_version": manifest.get("schema_version"),
            "feature_version": manifest.get("feature_version"),
            "supervision_version": manifest.get("supervision_version"),
            "artifact_count": len(manifest.get("artifacts", {})),
            "processed_root": processed_root,
        }
    }


@app.get("/api/market-data/preview")
def market_preview(
    processed_root: str,
    group: str,
    timeframe: str,
    session_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    all_rows: bool = False,
    columns: str | None = None,
    tickers: str | None = None,
    table_query: str | None = None,
    row_limit: int = Query(default=1000, ge=1, le=5000),
    row_offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    records = artifact_records(Path(processed_root))
    if start_date or end_date:
        range_start = start_date or end_date
        range_end = end_date or start_date
        if range_start is None or range_end is None:
            raise HTTPException(status_code=400, detail="Both start_date and end_date are required for range preview")
        if range_start > range_end:
            range_start, range_end = range_end, range_start
        selected_columns = parse_csv_list(columns)
        selected_tickers = parse_csv_list(tickers)
        return {
            "record": {
                "key": f"{group}|{timeframe}|{range_start.isoformat()}..{range_end.isoformat()}",
                "group": group,
                "timeframe": timeframe,
                "session_date": range_start.isoformat(),
                "path": "",
            },
            "sample": load_artifact_query_sample(
                records,
                group=group,
                timeframe=timeframe,
                start_date=range_start,
                end_date=range_end,
                columns=selected_columns,
                row_limit=row_limit,
                tickers=selected_tickers,
                row_offset=row_offset,
                table_query=parse_table_query(table_query),
            ),
        }
    if session_date is None:
        raise HTTPException(status_code=400, detail="session_date or start_date/end_date is required")
    record = first_matching_artifact(records, group, timeframe, session_date.isoformat())
    if not record:
        raise HTTPException(status_code=404, detail="Artifact not found")
    selected_columns = parse_csv_list(columns)
    selected_tickers = parse_csv_list(tickers)
    return {
        "record": record,
        "sample": load_artifact_sample(
            record,
            selected_columns,
            row_limit,
            selected_tickers,
            row_offset if all_rows else 0,
            parse_table_query(table_query),
        ),
    }


@app.get("/api/market-data/schema")
def market_schema(processed_root: str, group: str, timeframe: str, session_date: date) -> dict[str, Any]:
    record = first_matching_artifact(artifact_records(Path(processed_root)), group, timeframe, session_date.isoformat())
    if not record:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"record": record, "schema": artifact_schema(record)}


@app.get("/api/market-data/scanner-snapshot")
def market_scanner_snapshot(
    processed_root: str,
    session_date: date,
    timeframe: str,
    bar_time: str,
    feature_groups: str | None = None,
    columns: str | None = None,
    table_query: str | None = None,
    derived_columns: str | None = None,
    row_limit: int = Query(default=2000, ge=1, le=5000),
    row_offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    selected_feature_groups = parse_csv_list(feature_groups) or ["core", "session", "momentum", "volume_liquidity", "price_action"]
    try:
        snapshot = load_scanner_snapshot(
            artifact_records(Path(processed_root)),
            session_date=session_date,
            timeframe=timeframe,
            bar_time=bar_time,
            feature_groups=selected_feature_groups,
            columns=parse_csv_list(columns),
            row_limit=row_limit,
            row_offset=row_offset,
            table_query=parse_table_query(table_query),
            derived_columns=parse_derived_columns(derived_columns),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"snapshot": snapshot}


@app.post("/api/live-trading/preload")
def live_trading_preload(payload: LiveTradingPreloadRequest) -> dict[str, Any]:
    records = artifact_records(Path(payload.processed_root))
    session_text = payload.session_date.isoformat()
    required = [
        {
            "label": "Day 1m bars",
            "group": "bars",
            "timeframe": "1m",
            "sessions": [session_text],
        },
        {
            "label": "Recent daily bars",
            "group": "bars",
            "timeframe": "1d",
            "sessions": [session.isoformat() for session in market_sessions(payload.session_date - timedelta(days=45), payload.session_date)][-30:],
        },
        {
            "label": "Recent 5m bars",
            "group": "bars",
            "timeframe": "5m",
            "sessions": [session.isoformat() for session in market_sessions(payload.session_date - timedelta(days=10), payload.session_date)][-7:],
        },
    ]
    artifact_index = {(str(record.get("group")), str(record.get("timeframe")), str(record.get("session_date"))): record for record in records}
    checks = []
    for item in required:
        sessions = item["sessions"]
        matched = [artifact_index.get((item["group"], item["timeframe"], session)) for session in sessions]
        ready = [record for record in matched if record and record.get("exists")]
        checks.append(
            {
                "label": item["label"],
                "group": item["group"],
                "timeframe": item["timeframe"],
                "expected_sessions": len(sessions),
                "ready_sessions": len(ready),
                "rows": sum(int(record.get("rows") or 0) for record in ready),
                "status": "ready" if sessions and len(ready) == len(sessions) else "missing",
                "missing_sessions": [session for session, record in zip(sessions, matched) if not record or not record.get("exists")][:10],
            }
        )
    ready_count = sum(1 for check in checks if check["status"] == "ready")
    return {
        "session_date": session_text,
        "status": "ready" if ready_count == len(checks) else "missing",
        "progress": round(ready_count / max(1, len(checks)), 4),
        "checks": checks,
    }


@app.post("/api/live-trading/next-signal")
def live_trading_next_signal(payload: LiveTradingNextSignalRequest) -> dict[str, Any]:
    records = artifact_records(Path(payload.processed_root))
    start_minute = parse_live_clock_minute(payload.start_time)
    if start_minute is None:
        raise HTTPException(status_code=400, detail="Invalid start_time")
    end_minute = 20 * 60
    for minute in range(start_minute, end_minute + 1):
        bar_time = f"{minute // 60:02d}:{minute % 60:02d}"
        snapshot = load_scanner_snapshot(
            records,
            session_date=payload.session_date,
            timeframe="1m",
            bar_time=bar_time,
            feature_groups=payload.feature_groups,
            columns=payload.columns,
            row_limit=payload.row_limit,
            row_offset=0,
            table_query=payload.table_query,
            derived_columns=None,
        )
        if snapshot.get("rows"):
            return {"found": True, "snapshot": snapshot, "steps": minute - start_minute + 1}
        if snapshot.get("reason"):
            return {"found": False, "snapshot": snapshot, "steps": minute - start_minute + 1}
    return {
        "found": False,
        "snapshot": {
            "bar_time": f"{end_minute // 60:02d}:{end_minute % 60:02d}",
            "columns": [],
            "feature_groups": [],
            "reason": "No scanner signal found before the session cutoff.",
            "row_count": 0,
            "rows": [],
            "session_date": payload.session_date.isoformat(),
            "timeframe": "1m",
        },
        "steps": max(0, end_minute - start_minute + 1),
    }


@app.get("/api/market-data/momentum-discovery")
def market_momentum_discovery(
    processed_root: str,
    start_date: date,
    end_date: date,
    feature_groups: str | None = None,
    columns: str | None = None,
    table_query: str | None = None,
    min_day_high_move_pct: Annotated[float, Query(ge=0.0, le=10.0)] = 0.10,
    start_move_pct: Annotated[float, Query(ge=0.0, le=10.0)] = 0.05,
    row_limit: Annotated[int, Query(ge=1, le=5000)] = 2000,
    row_offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    range_start, range_end = (start_date, end_date) if start_date <= end_date else (end_date, start_date)
    selected_feature_groups = parse_csv_list(feature_groups) or ["core", "session", "momentum", "volume_liquidity", "price_action", "volatility"]
    try:
        discovery = load_momentum_discovery(
            artifact_records(Path(processed_root)),
            start_date=range_start,
            end_date=range_end,
            feature_groups=selected_feature_groups,
            columns=parse_csv_list(columns),
            min_day_high_move_pct=min_day_high_move_pct,
            start_move_pct=start_move_pct,
            row_limit=row_limit,
            row_offset=row_offset,
            table_query=parse_table_query(table_query),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"discovery": discovery}


@app.get("/api/market-data/catalog")
def market_catalog(processed_root: str = str(DEFAULT_PROCESSED_ROOT)) -> dict[str, Any]:
    return json_safe(provider_catalog(Path(processed_root)))


@app.get("/api/market-data/catalog/preview")
def market_catalog_preview(processed_root: str, item_id: str, timeframe: str | None = None) -> dict[str, Any]:
    return json_safe(catalog_preview_payload(Path(processed_root), item_id, timeframe))


@app.patch("/api/market-data/catalog/presentation")
def update_market_catalog_presentation(payload: CatalogPresentationUpdate) -> dict[str, Any]:
    return {"catalog": json_safe(save_presentation_override(Path(payload.processed_root), payload.item_id, payload.presentation))}


def default_catalog_chart_columns(processed_root: Path) -> list[str]:
    columns = []
    for item in provider_catalog(processed_root).get("columns", []):
        presentation = item.get("presentation", {})
        role = str(presentation.get("chartRole") or "")
        column = item.get("column")
        if column and presentation.get("defaultVisible") and presentation.get("selectable") and role not in {"marker", "text_label", "background_state", "anchored_zone", "data_only", "table_only"}:
            columns.append(str(column))
    return columns


def default_catalog_display_items(processed_root: Path) -> list[str]:
    item_ids = []
    for item in provider_catalog(processed_root).get("displayItems", []):
        presentation = item.get("presentation", {})
        if item.get("id") and presentation.get("defaultVisible") and presentation.get("selectable", True):
            item_ids.append(str(item["id"]))
    return item_ids


def parse_chart_display_items(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = parse_csv_list(value)
    if not items or CHART_DISPLAY_ITEMS_NONE in items:
        return []
    return items


@app.get("/api/market-data/chart")
def market_chart(
    processed_root: str,
    timeframe: str,
    ticker: str,
    start_date: date | None = None,
    end_date: date | None = None,
    session_date: date | None = None,
    feature_groups: str | None = None,
    columns: str | None = None,
    display_items: str | None = None,
    supervision_groups: str | None = None,
    marker_limit: int = Query(default=100, ge=0, le=500),
    min_confidence: float = Query(default=0.7, ge=0.0, le=1.0),
) -> dict[str, Any]:
    processed_root_path = Path(processed_root)
    selected_display_items = parse_chart_display_items(display_items)
    selected_feature_groups = parse_csv_list(feature_groups) or []
    selected_columns = parse_csv_list(columns) if columns is not None else []
    if selected_display_items is None and not selected_columns:
        selected_display_items = default_catalog_display_items(processed_root_path)
    selected_supervision = parse_csv_list(supervision_groups)
    range_start, range_end = resolve_chart_range(start_date, end_date, session_date)
    return json_safe(
        chart_payload(
            processed_root_path,
            start_date=range_start,
            end_date=range_end,
            timeframe=timeframe,
            ticker=ticker,
            feature_groups_selected=selected_feature_groups,
            selected_columns=selected_columns,
            selected_display_items=selected_display_items,
            supervision_groups_selected=selected_supervision,
            marker_limit=marker_limit,
            min_confidence=min_confidence,
        )
    )


@app.get("/api/market-data/chart/default-ticker")
def chart_default_ticker(
    processed_root: str,
    timeframe: str,
    start_date: date | None = None,
    end_date: date | None = None,
    session_date: date | None = None,
) -> dict[str, str]:
    range_start, range_end = resolve_chart_range(start_date, end_date, session_date)
    return {"ticker": first_ticker_in_range(artifact_records(Path(processed_root)), timeframe, range_start, range_end) or "AAPL"}


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")


@app.get("/{path:path}")
def frontend(path: str) -> FileResponse:
    if path == "api" or path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API route not found. Restart the backend if this route was just added.")
    index = FRONTEND_DIST / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="React build not found. Run `npm --prefix frontend run build`.")
    return FileResponse(index)
