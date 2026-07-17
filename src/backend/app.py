from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import polars as pl
import websockets
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
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
from src.backend.canvas_preview_service import canvas_preview_payload
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
    live_scanner_base_frame,
    load_live_scanner_signal_search,
    load_momentum_discovery,
    load_artifact_query_sample,
    load_artifact_sample,
    load_scanner_snapshot,
    review_payload,
    resolve_chart_display_items,
    scope_defaults,
    source_scan,
)
from src.backend.news_service import ensure_benzinga_news_cache, news_at_payload
from src.backend.progress_model import build_progress_model
from src.backend.qmd_gateway_client import (
    ENRICHED_QMD_TIMEFRAMES,
    MACRO_QMD_TIMEFRAMES,
    normalize_qmd_family_bar_snapshot,
    normalize_qmd_macro_bar_snapshot,
    qmd_catalogs,
    qmd_chart_bars,
    qmd_indicators,
    qmd_service_status,
    qmd_status,
    qmd_websocket_url,
)
from src.backend.real_live_trading_service import (
    apply_tradable_filter_to_scanner_payload,
    cancel_real_live_order,
    configured_real_live_accounts,
    modify_real_live_order,
    public_account,
    real_live_portfolio,
    real_live_preflight,
    real_live_scanner_snapshot,
    reply_real_live_order,
    submit_real_live_order,
)
from src.backend.real_live_market_data import (
    market_gateway_bars,
    market_gateway_snapshot,
    market_gateway_start,
    market_gateway_status,
    market_gateway_stop,
    market_gateway_universe_preview,
)
from src.backend.real_live_market_data.config import market_gateway_config
from src.backend.trading_runtime_service import (
    SUPPORTED_HISTORICAL_TIMEFRAMES,
    get_strategy_definition,
    historical_bar_history_before,
    historical_bar_chunk,
    historical_latest_coverage,
    historical_gateway_snapshot,
    historical_gateway_websocket_url,
    historical_preflight,
    historical_window_preview,
    list_strategy_definitions,
    save_strategy_definition,
)
from src.backend.ticker_presentation_service import ticker_presentation_payload
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
from research.mlops.clickhouse import default_clickhouse_password, default_clickhouse_url, default_clickhouse_user, quote_ident, sql_string
from research.mlops.env import discover_env_files, load_env_files


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_env_files(discover_env_files(PROJECT_ROOT), verbose=False)
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
CHART_DISPLAY_ITEMS_NONE = "__none__"
EXCHANGE_TIME_ZONE = "America/New_York"
PORTFOLIO_CHART_TIMEFRAMES = ["30m", "1h", "2h", "4h", "1d"]
DEBUG_SESSIONS: dict[str, StepBacktestDebugger] = {}
SERVICE_STATUS_TIMEOUT_SECONDS = 1.8
NEWS_QUERY_TIMEOUT_SECONDS = 12.0
SERVICE_LOG_TAIL_LIMIT = 160
SERVICE_DASHBOARD_LOG_LIMIT = 360

SERVICE_DASHBOARD_LOG_EVENTS = {
    "background_article_enrichment_failed",
    "background_batch_completed",
    "background_batch_failed_uncaught",
    "background_batch_queued",
    "background_batch_started",
    "coverage_bootstrap_completed",
    "coverage_bootstrap_skipped",
    "coverage_gap_provider_probe",
    "coverage_gap_provider_probe_failed",
    "coverage_gap_provider_probe_plan",
    "coverage_gap_provider_probe_started",
    "coverage_gap_snapshot_written",
    "coverage_live_snapshot_written",
    "coverage_manifest_compacted",
    "gap_fill_finished",
    "gap_fill_progress",
    "gap_fill_started",
    "live_url_download_not_downloaded",
    "poll_completed",
    "publish_completed",
    "publish_failed",
    "publish_started",
    "shutdown_background_drained",
    "shutdown_background_timeout",
    "shutdown_waiting_for_background_news",
    "shutdown_waiting_for_publish",
    "shutdown_publish_drained",
}
SERVICE_TABLE_STATE_LIMIT = 32
SERVICE_TABLE_STATE_CACHE_SECONDS = 30.0
SERVICE_NEWS_HISTOGRAM_CACHE_SECONDS = 20.0
SERVICE_NEWS_HISTOGRAM_BIN_SECONDS = 900
SERVICE_SEC_HISTOGRAM_CACHE_SECONDS = 20.0
SERVICE_SEC_HISTOGRAM_BIN_SECONDS = SERVICE_NEWS_HISTOGRAM_BIN_SECONDS
SERVICE_NEWS_TODAY_ROWS_LIMIT = 5000
SERVICE_SEC_TODAY_ROWS_LIMIT = 5000
SERVICE_TABLE_STATE_START_YEAR = 2019
SERVICE_TABLE_TIME_COLUMN_CANDIDATES = (
    "published_at_utc",
    "accepted_at_utc",
    "observed_at_utc",
    "source_timestamp_utc",
    "event_time",
    "sip_timestamp_utc",
    "timestamp_utc",
    "created_at_utc",
    "updated_at_utc",
    "started_at_utc",
    "coverage_start_utc",
    "last_started_at_utc",
    "updated_at",
    "source_archive_date",
    "filing_date",
    "trade_date",
    "universe_date",
    "coverage_start_date",
    "period_end_date",
    "list_date",
)
_SERVICE_TABLE_STATE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_SERVICE_NEWS_HISTOGRAM_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_SERVICE_SEC_HISTOGRAM_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

SERVICE_DATABASE_TABLES: dict[str, list[dict[str, str]]] = {
    "qmd": [
        {"database": "q_live", "table": "events", "role": "live events"},
        {"database": "q_live", "table": "intraday_bars_v1", "role": "canonical rolling intraday bars"},
        {"database": "q_live", "table": "live_symbol_market_event_v1", "role": "market state"},
        {"database": "q_live", "table": "qmd_live_event_coverage_v1", "role": "coverage"},
        {"database": "q_live", "table": "qmd_flatfile_coverage_v2", "role": "flatfile coverage"},
        {"database": "q_live", "table": "qmd_gap_fill_symbol_universe_v1", "role": "gap symbols"},
    ],
    "qmd-history": [],
    "news": [
        {"database": "q_live", "table": "benzinga_news_normalized_v1", "role": "normalized news"},
        {"database": "q_live", "table": "benzinga_news_ticker_v1", "role": "ticker links"},
        {"database": "q_live", "table": "benzinga_news_coverage_manifest_v1", "role": "coverage"},
    ],
    "sec": [
        {"database": "q_live", "table": "sec_filing_v3", "role": "filings"},
        {"database": "q_live", "table": "sec_filing_document_v3", "role": "documents"},
        {"database": "q_live", "table": "sec_filing_text_rendered_v3", "role": "filing text"},
        {"database": "q_live", "table": "sec_xbrl_company_fact_v3", "role": "company facts"},
        {"database": "q_live", "table": "sec_xbrl_frame_observation_v3", "role": "frame facts"},
        {"database": "q_live", "table": "sec_coverage_manifest_v3", "role": "coverage"},
    ],
    "text-embed": [
        {"database": "market_sip_compact", "table": "news_text_tokens", "role": "news tokens"},
        {"database": "market_sip_compact", "table": "news_text_embeddings", "role": "news embeddings"},
        {"database": "q_live", "table": "sec_filing_text_rendered_v3", "role": "sec rendered source"},
        {"database": "market_sip_compact", "table": "sec_filing_text_tokens_v3", "role": "sec tokens"},
        {"database": "market_sip_compact", "table": "sec_filing_text_embeddings_v3", "role": "sec embeddings"},
        {"database": "market_sip_compact", "table": "text_embedding_coverage_v1", "role": "coverage"},
    ],
    "reference": [
        {"database": "q_live", "table": "id_issuer_v1", "role": "issuers"},
        {"database": "q_live", "table": "id_security_v1", "role": "securities"},
        {"database": "q_live", "table": "id_listing_v1", "role": "listings"},
        {"database": "q_live", "table": "id_symbol_v1", "role": "symbols"},
        {"database": "q_live", "table": "id_mapping_issue_v1", "role": "issues"},
        {"database": "q_live", "table": "id_sec_market_bridge_v3", "role": "sec bridge"},
        {"database": "q_live", "table": "feature_tradable_universe_v1", "role": "tradable universe"},
        {"database": "q_live", "table": "market_reference_alert_v1", "role": "alerts"},
        {"database": "q_live", "table": "market_reference_source_schedule_v1", "role": "source schedule"},
        {"database": "q_live", "table": "market_reference_publication_coverage_v1", "role": "publication coverage"},
        {"database": "q_live", "table": "market_security_borrow_v1", "role": "borrow"},
        {"database": "q_live", "table": "market_short_volume_v1", "role": "short volume"},
    ],
    "ibkr": [
        {"database": "q_live", "table": "ibkr_gateway_supervisor_event_v1", "role": "supervisor events"},
    ],
}

SERVICE_REGISTRY: dict[str, dict[str, str]] = {
    "qmd": {
        "id": "qmd",
        "label": "QMD Gateway",
        "kind": "market data",
        "bind_env": "QMD_GATEWAY_BIND",
        "default_bind": "127.0.0.1:8795",
        "description": "Massive quote/trade ingest, recent gap repair, live bars, scanner primitives, and market-state publication.",
        "recent_path": "/snapshot/scanner-primitives?limit=25",
    },
    "qmd-history": {
        "id": "qmd-history",
        "label": "QMD History",
        "kind": "historical market data",
        "bind_env": "QMD_HISTORY_BIND",
        "default_bind": "127.0.0.1:8801",
        "description": "Read-only canonical historical events and event-derived bars for Replay, Backtest, and Backtest Debug.",
        "recent_path": "/health",
    },
    "news": {
        "id": "news",
        "label": "News Gateway",
        "kind": "news",
        "bind_env": "NEWS_GATEWAY_BIND",
        "default_bind": "127.0.0.1:8796",
        "description": "Benzinga polling, raw retention, enrichment, canonical news rows, ticker links, and coverage repair.",
        "recent_path": "/snapshot/news/recent?limit=25",
    },
    "sec": {
        "id": "sec",
        "label": "SEC Gateway",
        "kind": "filings",
        "bind_env": "SEC_GATEWAY_BIND",
        "default_bind": "127.0.0.1:8797",
        "description": "SEC current feed polling, filing text, XBRL companyfacts, coverage, and historical gap handoff.",
        "recent_path": "/snapshot/sec/recent?limit=25",
    },
    "text-embed": {
        "id": "text-embed",
        "label": "Text Embed Gateway",
        "kind": "inference",
        "bind_env": "TEXT_EMBED_GATEWAY_BIND",
        "default_bind": "127.0.0.1:8798",
        "description": "News and SEC text tokenization, embedding extraction, and embedding coverage reconciliation.",
        "recent_path": "/snapshot/text-embeddings/recent?limit=25",
    },
    "reference": {
        "id": "reference",
        "label": "Reference Gateway",
        "kind": "reference",
        "bind_env": "REFERENCE_GATEWAY_BIND",
        "default_bind": "127.0.0.1:8799",
        "description": "Reference graph sync, source publications, issuer/listing integrity, tradable universe, and issue tracking.",
        "recent_path": "/snapshot/reference/recent?limit=25",
    },
    "ibkr": {
        "id": "ibkr",
        "label": "IBKR Supervisor",
        "kind": "broker",
        "bind_env": "IBKR_GATEWAY_SUPERVISOR_BIND",
        "default_bind": "127.0.0.1:8800",
        "description": "Client Portal Gateway process supervision, authentication state, account checks, and keepalive monitoring.",
        "recent_path": "/snapshot/ibkr/recent?limit=25",
    },
}

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
    max_steps: int | None = Field(default=None, ge=1, le=120)


class LiveTradingNewsAtRequest(BaseModel):
    processed_root: str = Field(default=str(DEFAULT_PROCESSED_ROOT))
    session_date: date
    bar_time: str = "04:00"
    tickers: list[str] = Field(default_factory=list)


class RealLiveOrderSubmit(BaseModel):
    account_type: str = "paper"
    account_keys: list[str] = Field(default_factory=list)
    order: dict[str, Any] = Field(default_factory=dict)
    preview: bool = False


class RealLiveOrderReply(BaseModel):
    reply_id: str
    confirmed: bool


class RealLiveOrderModify(BaseModel):
    account_key: str
    order: dict[str, Any] = Field(default_factory=dict)


class StrategyDefinitionSubmit(BaseModel):
    strategy_id: str
    revision: int = Field(default=0, ge=0)
    name: str
    implementation: str
    automatic: bool = True
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class HistoricalWindowPreviewRequest(BaseModel):
    mode: str
    anchor_date: date
    session_count: int = Field(default=1, ge=1, le=260)
    replay_end_date: date | None = None


class HistoricalPreflightRequest(BaseModel):
    mode: str
    anchor_date: date
    session_count: int = Field(default=20, ge=1, le=260)


class HistoricalBarChunkRequest(BaseModel):
    session_date: date
    ticker: str
    timeframe: str = "1m"
    offset_minutes: int = Field(default=0, ge=0, le=959)
    window_minutes: int = Field(default=15, ge=1, le=30)


class CanvasPreviewRequest(BaseModel):
    session_date: date
    preview_time: str = "09:45"
    chart_symbol: str = "AAPL"
    chart_timeframe: str = "1m"


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


def service_base_url(service: dict[str, str]) -> str:
    bind = os.environ.get(service["bind_env"], service["default_bind"]).strip() or service["default_bind"]
    host, port = parse_service_bind(bind)
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def service_websocket_url(service: dict[str, str], path: str) -> str:
    return f"{service_base_url(service).replace('http://', 'ws://', 1)}{path}"


def parse_service_bind(bind: str) -> tuple[str, int]:
    text = bind.strip()
    if text.startswith("[") and "]:" in text:
        host, port_text = text[1:].split("]:", 1)
        return host, int(port_text)
    if ":" not in text:
        return text or "127.0.0.1", 80
    host, port_text = text.rsplit(":", 1)
    return host or "127.0.0.1", int(port_text)


def fetch_service_json(base_url: str, path: str) -> tuple[dict[str, Any] | list[Any] | None, str | None]:
    url = f"{base_url}{path}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=SERVICE_STATUS_TIMEOUT_SECONDS) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        return None, f"HTTP {exc.code}: {body or exc.reason}"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, f"Non-JSON response from {path}"
    if isinstance(payload, (dict, list)):
        return payload, None
    return None, f"Unexpected JSON payload from {path}"


def service_runtime_logs(*payloads: Any, service_id: str = "", limit: int = SERVICE_LOG_TAIL_LIMIT) -> dict[str, Any]:
    log_path = find_runtime_log_path(*payloads) or latest_service_log_path(service_id)
    if not log_path:
        return {"path": "", "rows": [], "error": ""}
    path = Path(log_path)
    try:
        if not path.exists():
            return {"path": str(path), "rows": [], "error": "log file not found"}
        if not path.is_file():
            return {"path": str(path), "rows": [], "error": "log path is not a file"}
    except OSError as exc:
        return {"path": str(path), "rows": [], "error": f"{type(exc).__name__}: {exc}"}
    rows: deque[dict[str, Any]] = deque(maxlen=max(1, min(limit, 500)))
    dashboard_rows: deque[dict[str, Any]] = deque(maxlen=max(1, min(max(limit, SERVICE_DASHBOARD_LOG_LIMIT), 500)))
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    normalized = normalize_runtime_log_row(
                        {"event": "unparsed_log_line", "message": text, "line_number": line_number},
                        source_path=path,
                        line_number=line_number,
                    )
                    rows.append(normalized)
                    continue
                if isinstance(payload, dict):
                    normalized = normalize_runtime_log_row(payload, source_path=path, line_number=line_number)
                    rows.append(normalized)
                    if str(normalized.get("event") or "") in SERVICE_DASHBOARD_LOG_EVENTS:
                        dashboard_rows.append(normalized)
    except OSError as exc:
        return {"path": str(path), "rows": [], "error": f"{type(exc).__name__}: {exc}"}
    merged: dict[tuple[str, int], dict[str, Any]] = {}
    for row in [*dashboard_rows, *rows]:
        merged[(str(row.get("source") or ""), int(row.get("line") or 0))] = row
    merged_rows = sorted(merged.values(), key=lambda item: (str(item.get("source") or ""), int(item.get("line") or 0)))
    return {"path": str(path), "rows": merged_rows[-500:], "error": ""}


def latest_service_log_path(service_id: str) -> str:
    candidates: list[Path] = []
    for root in service_log_roots(service_id):
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        for pattern in ("*.jsonl", "*.log"):
            try:
                candidates.extend(path for path in root.rglob(pattern) if path.is_file())
            except OSError:
                continue
    if not candidates:
        return ""
    latest = max(candidates, key=safe_mtime)
    return str(latest) if safe_mtime(latest) >= 0 else ""


def safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def service_log_roots(service_id: str) -> list[Path]:
    data_roots = service_data_roots()
    roots_by_service: dict[str, list[Path]] = {
        "qmd": [PROJECT_ROOT / ".tmp" / "qmd-gateway"],
        "qmd-history": [PROJECT_ROOT / ".tmp" / "qmd-history-gateway"],
        "news": env_paths("NEWS_GATEWAY_LOG_ROOT_WIN") + [root / "prepared" / "news_gateway" / "logs" for root in data_roots],
        "sec": env_paths("SEC_GATEWAY_LOG_ROOT_WIN") + [root / "prepared" / "sec_gateway" / "logs" for root in data_roots],
        "text-embed": env_paths("TEXT_EMBED_GATEWAY_LOG_ROOT_WIN") + [root / "prepared" / "text_embed_gateway" / "logs" for root in data_roots],
        "reference": reference_log_roots(data_roots),
        "ibkr": env_paths("IBKR_GATEWAY_LOG_ROOT") + [PROJECT_ROOT / "tmp" / "ibkr_gateway_supervisor"],
    }
    seen: set[str] = set()
    roots: list[Path] = []
    for root in roots_by_service.get(service_id, []):
        normalized = str(root)
        if normalized in seen:
            continue
        seen.add(normalized)
        roots.append(root)
    return roots


def reference_log_roots(data_roots: list[Path]) -> list[Path]:
    roots = env_paths("REFERENCE_GATEWAY_LOG_ROOT_WIN")
    for prepared_root in env_paths("REFERENCE_GATEWAY_PREPARED_ROOT_WIN"):
        roots.append(prepared_root / "reference_gateway" / "logs")
    roots.extend(root / "prepared" / "reference_gateway" / "logs" for root in data_roots)
    return roots


def service_data_roots() -> list[Path]:
    roots = env_paths(
        "NEWS_GATEWAY_DATA_ROOT_WIN",
        "SEC_DATA_ROOT_WIN",
        "TEXT_EMBED_GATEWAY_DATA_ROOT_WIN",
        "REFERENCE_GATEWAY_DATA_ROOT_WIN",
    )
    roots.extend([Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data"), Path("D:/market-data")])
    return roots


def env_paths(*names: str) -> list[Path]:
    paths: list[Path] = []
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            paths.append(Path(value.strip()))
    return paths


def find_runtime_log_path(*payloads: Any) -> str:
    keys = {
        "run_log_path",
        "runtime_log_path",
        "log_path",
        "event_log_path",
        "events_log_path",
    }
    for payload in payloads:
        found = find_first_string_by_key(payload, keys)
        if found:
            return found
    return ""


def find_first_string_by_key(value: Any, keys: set[str]) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in keys and isinstance(item, str) and item.strip():
                return item.strip()
        for item in value.values():
            found = find_first_string_by_key(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_first_string_by_key(item, keys)
            if found:
                return found
    return ""


def normalize_runtime_log_row(row: dict[str, Any], *, source_path: Path, line_number: int) -> dict[str, Any]:
    event = str(row.get("event") or row.get("type") or row.get("name") or "log")
    ts_utc = str(row.get("ts_utc") or row.get("timestamp_utc") or row.get("updated_at_utc") or row.get("created_at_utc") or "")
    level = str(row.get("level") or row.get("status") or infer_runtime_log_level(event, row)).lower()
    title = str(row.get("title") or row.get("message") or row.get("phase") or event)
    detail = runtime_log_detail(row)
    return {
        "ts_utc": ts_utc,
        "level": level,
        "event": event,
        "title": redact_log_text(title),
        "detail": redact_log_text(detail),
        "source": source_path.name,
        "line": line_number,
        "fields": runtime_log_public_fields(event, row),
    }


def runtime_log_public_fields(event: str, row: dict[str, Any]) -> dict[str, Any]:
    poll_allowed = {
        "coverage_mode",
        "duplicate_news_rows",
        "failed_rows",
        "input_duplicate_ids_total",
        "normalized_rows_inserted",
        "pages",
        "poll_id",
        "processed_rows",
        "provider_rows",
        "saturated",
        "skipped_existing",
        "start_utc",
        "status",
        "ticker_rows_inserted",
        "unique_news_rows",
        "wall_seconds",
    }
    publish_allowed = {
        "active_jobs",
        "article_count",
        "article_failures",
        "canonical_news_id",
        "canonical_news_id_sample",
        "coverage_mode",
        "domain_sample",
        "enriched_count",
        "enriched_urls",
        "enrichment_canonical_news_id_sample",
        "enrichment_domain_sample",
        "enrichment_provider_article_id_sample",
        "enrichment_title_sample",
        "enrichment_url_sample",
        "error_type",
        "fetch_task_count",
        "http_status",
        "input_duplicate_ids_total",
        "items",
        "items_logged",
        "items_total",
        "normalized_rows_inserted",
        "pdf_count",
        "pending_rows",
        "poll_id",
        "processed_rows",
        "provider_article_id",
        "provider_article_id_sample",
        "published_at_end_utc",
        "published_at_start_utc",
        "queue_size",
        "requires_enrichment_count",
        "saturated",
        "skipped_existing",
        "status",
        "status_reason",
        "ticker_count",
        "ticker_rows_inserted",
        "ticker_sample",
        "title_sample",
        "url_hash",
        "url_sample",
        "wall_seconds",
        "worker_index",
    }
    coverage_allowed = {
        "chunk_minutes",
        "chunks",
        "coverage_id",
        "decision",
        "deferred_reason",
        "empty_count",
        "end_utc",
        "failed_rows",
        "first_start_utc",
        "flushed",
        "gap_count",
        "gaps",
        "has_news",
        "in_flight",
        "last_end_utc",
        "manifest",
        "message",
        "metadata",
        "pages",
        "poll_runs",
        "positive_count",
        "probe_index",
        "probe_total",
        "processed_rows",
        "provider_rows",
        "rows_seen",
        "script",
        "skipped_existing",
        "start_utc",
        "status",
        "submitted",
        "summary",
        "total_chunks",
        "total_gap_seconds",
        "unique_gap_days",
        "wall_seconds",
        "workers",
        "written_rows",
    }
    allowed_by_event = {
        "poll_completed": poll_allowed,
        "background_article_enrichment_failed": publish_allowed,
        "background_batch_completed": publish_allowed,
        "background_batch_failed_uncaught": publish_allowed,
        "background_batch_queued": publish_allowed,
        "background_batch_started": publish_allowed,
        "coverage_bootstrap_completed": coverage_allowed,
        "coverage_bootstrap_skipped": coverage_allowed,
        "coverage_gap_provider_probe": coverage_allowed,
        "coverage_gap_provider_probe_failed": coverage_allowed,
        "coverage_gap_provider_probe_plan": coverage_allowed,
        "coverage_gap_provider_probe_started": coverage_allowed,
        "coverage_gap_snapshot_written": coverage_allowed,
        "coverage_live_snapshot_written": coverage_allowed,
        "coverage_manifest_compacted": coverage_allowed,
        "gap_fill_finished": coverage_allowed,
        "gap_fill_progress": coverage_allowed,
        "gap_fill_started": coverage_allowed,
        "live_url_download_not_downloaded": publish_allowed,
        "publish_started": publish_allowed,
        "publish_completed": publish_allowed,
        "publish_failed": publish_allowed,
        "shutdown_background_drained": publish_allowed,
        "shutdown_background_timeout": publish_allowed,
        "shutdown_publish_drained": publish_allowed,
        "shutdown_waiting_for_background_news": publish_allowed,
        "shutdown_waiting_for_publish": publish_allowed,
    }
    allowed = allowed_by_event.get(event)
    if not allowed:
        return {}
    return {key: value for key, value in row.items() if key in allowed and value not in (None, "")}


def infer_runtime_log_level(event: str, row: dict[str, Any]) -> str:
    text = " ".join(str(value) for value in [event, row.get("status", ""), row.get("error_type", ""), row.get("error_message", "")]).lower()
    if any(token in text for token in ("critical", "exception", "failed", "failure", "error", "traceback")):
        return "error"
    if any(token in text for token in ("warning", "warn", "retry", "timeout", "degraded")):
        return "warning"
    if any(token in text for token in ("resolved", "completed", "success", "succeeded", "ok")):
        return "resolved"
    return "info"


def runtime_log_detail(row: dict[str, Any]) -> str:
    preferred = ["error_message", "detail", "details", "message", "reason", "status", "phase", "rows", "elapsed_seconds"]
    parts: list[str] = []
    for key in preferred:
        value = row.get(key)
        if value is None or value == "":
            continue
        parts.append(f"{key}={compact_runtime_log_value(value)}")
    if parts:
        return "; ".join(parts)
    compact = {key: value for key, value in row.items() if key not in {"ts_utc", "event", "run_id"}}
    return compact_runtime_log_value(compact) if compact else "-"


def compact_runtime_log_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        except TypeError:
            text = str(value)
    else:
        text = str(value)
    return text if len(text) <= 800 else text[:800] + "...<truncated>"


def redact_log_text(value: str) -> str:
    text = str(value)
    text = re.sub(r"([?&](?:apiKey|apikey|api_key|token|key|password)=)[^&'\"\s)]+", r"\1redacted", text, flags=re.IGNORECASE)
    return re.sub(r"((?:apiKey|apikey|api_key|token|key|password)['\"]?\s*[:=]\s*['\"]?)[^'\"&\s,)]+", r"\1redacted", text, flags=re.IGNORECASE)


def service_unreachable_error(error_text: str | None) -> bool:
    if not error_text:
        return False
    normalized = error_text.lower()
    return any(
        token in normalized
        for token in (
            "urlerror",
            "timed out",
            "timeout",
            "connection refused",
            "actively refused",
            "no connection could be made",
            "connection reset",
            "failed to establish",
            "winerror 10061",
            "winerror 10060",
        )
    )


def service_database_table_state(service_id: str) -> dict[str, Any]:
    targets = SERVICE_DATABASE_TABLES.get(service_id, [])
    if not targets:
        return {"rows": [], "error": ""}
    cached_at, cached_payload = _SERVICE_TABLE_STATE_CACHE.get(service_id, (0.0, {}))
    if cached_payload and time.monotonic() - cached_at < SERVICE_TABLE_STATE_CACHE_SECONDS:
        return cached_payload
    try:
        stats = clickhouse_table_stats(targets)
    except Exception as exc:
        payload = {
            "rows": [
                {
                    "database": "-",
                    "table": "-",
                    "role": "database check",
                    "status": "error",
                    "rows": "-",
                    "bytes": "-",
                    "latest_update": "-",
                    "detail": redact_log_text(f"{type(exc).__name__}: {exc}"),
                }
            ],
            "error": redact_log_text(f"{type(exc).__name__}: {exc}"),
        }
        _SERVICE_TABLE_STATE_CACHE[service_id] = (time.monotonic(), payload)
        return payload

    rows: list[dict[str, Any]] = []
    for target in targets[:SERVICE_TABLE_STATE_LIMIT]:
        key = (target["database"], target["table"])
        stat = stats.get(key)
        rows.append(
            {
                "database": target["database"],
                "table": target["table"],
                "role": target.get("role", ""),
                "status": table_state_status(stat),
                "rows": format_int(stat["rows"]) if stat else "-",
                "bytes": format_bytes(stat["bytes_on_disk"]) if stat else "-",
                "latest_update": stat["latest_update"] if stat else "-",
                "engine": stat["engine"] if stat else "-",
                "time_column": stat.get("time_column", "-") if stat else "-",
                "rows_today": format_optional_int(stat.get("rows_today")) if stat else "-",
                "rows_last_week": format_optional_int(stat.get("rows_last_week")) if stat else "-",
                "rows_last_month": format_optional_int(stat.get("rows_last_month")) if stat else "-",
                **{
                    f"rows_{year}": format_optional_int(stat.get(f"rows_{year}")) if stat else "-"
                    for year in service_table_state_years()
                },
            }
        )
    payload = {"rows": rows, "error": ""}
    _SERVICE_TABLE_STATE_CACHE[service_id] = (time.monotonic(), payload)
    return payload


def service_database_table_preview(service_id: str, database: str, table: str, limit: int = 20) -> dict[str, Any]:
    target = service_database_table_target(service_id, database, table)
    columns = clickhouse_table_columns([target])
    time_column = table_time_column(columns.get((database, table), set()))
    order_clause = f"\n        ORDER BY {quote_ident(time_column)} DESC" if time_column else ""
    safe_limit = max(1, min(limit, 100))
    query = f"""
        SELECT *
        FROM {quote_ident(database)}.{quote_ident(table)}
        {order_clause}
        LIMIT {safe_limit}
        FORMAT JSONEachRow
    """
    rows: list[dict[str, Any]] = []
    for line in clickhouse_status_query(query).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows.append({key: preview_cell_value(value) for key, value in row.items()})
    return {
        "database": database,
        "limit": safe_limit,
        "order_by": time_column or "",
        "rows": rows,
        "table": table,
    }


def service_news_histogram() -> dict[str, Any]:
    safe_bin_seconds = SERVICE_NEWS_HISTOGRAM_BIN_SECONDS

    database = "q_live"
    normalized_table = "benzinga_news_normalized_v1"
    ticker_table = "benzinga_news_ticker_v1"
    market_now = datetime.now(UTC).astimezone(ZoneInfo(EXCHANGE_TIME_ZONE))
    window_start_et = market_now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end_et = window_start_et + timedelta(days=1)
    window_start_utc = window_start_et.astimezone(UTC)
    window_end_utc = window_end_et.astimezone(UTC)
    cache_key = f"{window_start_et.date().isoformat()}:{safe_bin_seconds}"
    cached_at, cached_payload = _SERVICE_NEWS_HISTOGRAM_CACHE.get(cache_key, (0.0, {}))
    if cached_payload and time.monotonic() - cached_at < SERVICE_NEWS_HISTOGRAM_CACHE_SECONDS:
        return cached_payload

    bin_count = int(((window_end_utc - window_start_utc).total_seconds() + safe_bin_seconds - 1) // safe_bin_seconds)
    window_start_sql = f"toDateTime64({sql_string(window_start_utc.strftime('%Y-%m-%d %H:%M:%S.%f'))}, 6, 'UTC')"
    window_end_sql = f"toDateTime64({sql_string(window_end_utc.strftime('%Y-%m-%d %H:%M:%S.%f'))}, 6, 'UTC')"
    query = f"""
        WITH
            {window_start_sql} AS window_start,
            {window_end_sql} AS window_end,
            ticker_counts AS
            (
                SELECT
                    canonical_news_id,
                    toUInt64(countDistinct(nullIf(ticker, ''))) AS ticker_count
                FROM {quote_ident(database)}.{quote_ident(ticker_table)} FINAL
                WHERE published_at_utc >= window_start
                  AND published_at_utc < window_end
                GROUP BY canonical_news_id
            ),
            news_counts AS
            (
                SELECT
                    toUInt64(intDiv(dateDiff('second', window_start, n.published_at_utc) + {safe_bin_seconds // 2}, {safe_bin_seconds})) AS bucket_index,
                    toUInt64(countIf(ifNull(t.ticker_count, toUInt64(0)) = 1)) AS single_ticker_rows,
                    toUInt64(countIf(ifNull(t.ticker_count, toUInt64(0)) != 1)) AS broad_or_none_rows,
                    toUInt64(count()) AS total_rows
                FROM {quote_ident(database)}.{quote_ident(normalized_table)} AS n FINAL
                LEFT JOIN ticker_counts AS t
                    ON t.canonical_news_id = n.canonical_news_id
                WHERE n.published_at_utc >= window_start
                  AND n.published_at_utc < window_end
                GROUP BY bucket_index
            )
        SELECT
            formatDateTime(
                window_start + toIntervalSecond(toInt64(b.bucket_index) * {safe_bin_seconds}),
                '%Y-%m-%dT%H:%i:%S.000Z',
                'UTC'
            ) AS bucket_utc,
            toUInt64(ifNull(c.single_ticker_rows, 0)) AS single_ticker_rows,
            toUInt64(ifNull(c.broad_or_none_rows, 0)) AS broad_or_none_rows,
            toUInt64(ifNull(c.total_rows, 0)) AS total_rows
        FROM
        (
            SELECT toUInt64(number) AS bucket_index
            FROM numbers({bin_count + 1})
        ) AS b
        LEFT JOIN news_counts AS c
            ON c.bucket_index = b.bucket_index
        ORDER BY b.bucket_index
        FORMAT JSONEachRow
    """
    rows: list[dict[str, Any]] = []
    for line in clickhouse_status_query(query).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows.append(
            {
                "bucket_utc": str(row.get("bucket_utc") or ""),
                "single_ticker_rows": int(row.get("single_ticker_rows") or 0),
                "broad_or_none_rows": int(row.get("broad_or_none_rows") or 0),
                "total_rows": int(row.get("total_rows") or 0),
            }
        )
    payload = {
        "bin_seconds": safe_bin_seconds,
        "database": database,
        "market_timezone": EXCHANGE_TIME_ZONE,
        "normalized_table": normalized_table,
        "ticker_table": ticker_table,
        "rows": rows,
        "source": "clickhouse",
        "window_end_et": window_end_et.isoformat(),
        "window_end_utc": window_end_utc.isoformat().replace("+00:00", "Z"),
        "window_start_et": window_start_et.isoformat(),
        "window_start_utc": window_start_utc.isoformat().replace("+00:00", "Z"),
    }
    _SERVICE_NEWS_HISTOGRAM_CACHE[cache_key] = (time.monotonic(), payload)
    return payload


def service_news_today_rows(limit: int = 250, sort: str = "desc") -> dict[str, Any]:
    safe_limit = max(1, min(limit, SERVICE_NEWS_TODAY_ROWS_LIMIT))
    sort_direction = "ASC" if sort.strip().lower() == "asc" else "DESC"
    database = "q_live"
    normalized_table = "benzinga_news_normalized_v1"
    ticker_table = "benzinga_news_ticker_v1"
    market_now = datetime.now(UTC).astimezone(ZoneInfo(EXCHANGE_TIME_ZONE))
    window_start_et = market_now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end_et = window_start_et + timedelta(days=1)
    window_start_utc = window_start_et.astimezone(UTC)
    window_end_utc = window_end_et.astimezone(UTC)
    window_start_sql = f"toDateTime64({sql_string(window_start_utc.strftime('%Y-%m-%d %H:%M:%S.%f'))}, 6, 'UTC')"
    window_end_sql = f"toDateTime64({sql_string(window_end_utc.strftime('%Y-%m-%d %H:%M:%S.%f'))}, 6, 'UTC')"
    summary_query = f"""
        WITH
            {window_start_sql} AS window_start,
            {window_end_sql} AS window_end,
            ticker_counts AS
            (
                SELECT
                    canonical_news_id,
                    toUInt64(countDistinct(nullIf(ticker, ''))) AS ticker_link_count
                FROM {quote_ident(database)}.{quote_ident(ticker_table)} FINAL
                WHERE published_at_utc >= window_start
                  AND published_at_utc < window_end
                GROUP BY canonical_news_id
            )
        SELECT
            toUInt64(count()) AS total_rows,
            toUInt64(countIf(ifNull(t.ticker_link_count, toUInt64(0)) = 1)) AS one_ticker_rows,
            toUInt64(countIf(ifNull(t.ticker_link_count, toUInt64(0)) > 1)) AS multi_ticker_rows,
            toUInt64(countIf(ifNull(t.ticker_link_count, toUInt64(0)) = 0)) AS no_ticker_rows,
            toUInt64(countIf(ifNull(t.ticker_link_count, toUInt64(0)) > 0)) AS with_ticker_rows,
            toUInt64(countIf(n.has_external_text)) AS external_text_rows,
            toUInt64(countIf(n.has_pdf)) AS pdf_rows,
            formatDateTime(max(n.published_at_utc), '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS latest_published_at_utc
        FROM {quote_ident(database)}.{quote_ident(normalized_table)} AS n FINAL
        LEFT JOIN ticker_counts AS t
            ON t.canonical_news_id = n.canonical_news_id
        WHERE n.published_at_utc >= window_start
          AND n.published_at_utc < window_end
        FORMAT JSONEachRow
    """
    summary_rows = [json.loads(line) for line in clickhouse_status_query(summary_query).splitlines() if line.strip()]
    summary = summary_rows[0] if summary_rows else {}
    query = f"""
        WITH
            {window_start_sql} AS window_start,
            {window_end_sql} AS window_end,
            ticker_counts AS
            (
                SELECT
                    canonical_news_id,
                    toUInt64(countDistinct(nullIf(ticker, ''))) AS ticker_link_count,
                    arraySort(groupUniqArray(nullIf(ticker, ''))) AS ticker_link_sample
                FROM {quote_ident(database)}.{quote_ident(ticker_table)} FINAL
                WHERE published_at_utc >= window_start
                  AND published_at_utc < window_end
                GROUP BY canonical_news_id
            )
        SELECT
            n.canonical_news_id,
            n.provider_article_id,
            formatDateTime(n.published_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS published_at_utc,
            formatDateTime(n.downloaded_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS downloaded_at_utc,
            n.title,
            n.normalized_title,
            n.article_url,
            n.url_domain,
            n.author,
            n.tickers,
            n.channels,
            n.provider_tags,
            ifNull(t.ticker_link_count, toUInt64(0)) AS ticker_link_count,
            ifNull(t.ticker_link_sample, []) AS ticker_link_sample,
            n.has_body,
            n.is_title_only,
            n.has_external_text,
            n.has_pdf,
            n.external_fetch_status,
            n.pdf_extract_status,
            n.content_quality_flags,
            lengthUTF8(n.body_text) AS body_chars,
            lengthUTF8(n.external_text) AS external_chars,
            lengthUTF8(n.pdf_text) AS pdf_chars,
            lengthUTF8(n.normalized_full_text) AS full_text_chars,
            substring(n.normalized_full_text, 1, 240) AS text_preview
        FROM {quote_ident(database)}.{quote_ident(normalized_table)} AS n FINAL
        LEFT JOIN ticker_counts AS t
            ON t.canonical_news_id = n.canonical_news_id
        WHERE n.published_at_utc >= window_start
          AND n.published_at_utc < window_end
        ORDER BY n.published_at_utc {sort_direction}, n.provider_article_id {sort_direction}
        LIMIT {safe_limit}
        FORMAT JSONEachRow
    """
    rows = [json.loads(line) for line in clickhouse_status_query(query).splitlines() if line.strip()]
    return {
        "database": database,
        "limit": safe_limit,
        "market_timezone": EXCHANGE_TIME_ZONE,
        "normalized_table": normalized_table,
        "rows": rows,
        "source": "clickhouse",
        "sort": sort_direction.lower(),
        "summary": {
            "external_text_rows": int(summary.get("external_text_rows") or 0),
            "latest_published_at_utc": str(summary.get("latest_published_at_utc") or ""),
            "loaded_rows": len(rows),
            "multi_ticker_rows": int(summary.get("multi_ticker_rows") or 0),
            "no_ticker_rows": int(summary.get("no_ticker_rows") or 0),
            "one_ticker_rows": int(summary.get("one_ticker_rows") or 0),
            "pdf_rows": int(summary.get("pdf_rows") or 0),
            "total_rows": int(summary.get("total_rows") or 0),
            "with_ticker_rows": int(summary.get("with_ticker_rows") or 0),
        },
        "ticker_table": ticker_table,
        "window_end_et": window_end_et.isoformat(),
        "window_end_utc": window_end_utc.isoformat().replace("+00:00", "Z"),
        "window_start_et": window_start_et.isoformat(),
        "window_start_utc": window_start_utc.isoformat().replace("+00:00", "Z"),
    }


def service_news_detail(canonical_news_id: str) -> dict[str, Any]:
    news_id = canonical_news_id.strip()
    if not news_id:
        raise HTTPException(status_code=400, detail="canonical_news_id is required")
    database = "q_live"
    normalized_table = "benzinga_news_normalized_v1"
    ticker_table = "benzinga_news_ticker_v1"
    news_id_sql = sql_string(news_id)
    row_query = f"""
        SELECT * REPLACE
        (
            formatDateTime(published_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS published_at_utc,
            formatDateTime(downloaded_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS downloaded_at_utc,
            if(
                isNull(last_updated_at_utc),
                NULL,
                formatDateTime(assumeNotNull(last_updated_at_utc), '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC')
            ) AS last_updated_at_utc,
            formatDateTime(updated_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS updated_at_utc
        )
        FROM {quote_ident(database)}.{quote_ident(normalized_table)} FINAL
        WHERE canonical_news_id = {news_id_sql}
        LIMIT 1
        FORMAT JSONEachRow
    """
    rows = [json.loads(line) for line in clickhouse_status_query(row_query).splitlines() if line.strip()]
    if not rows:
        raise HTTPException(status_code=404, detail="News row not found")
    ticker_query = f"""
        SELECT * REPLACE
        (
            formatDateTime(published_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS published_at_utc
        )
        FROM {quote_ident(database)}.{quote_ident(ticker_table)} FINAL
        WHERE canonical_news_id = {news_id_sql}
        ORDER BY ticker ASC
        FORMAT JSONEachRow
    """
    ticker_rows = [json.loads(line) for line in clickhouse_status_query(ticker_query).splitlines() if line.strip()]
    rows[0]["news_kind"] = classify_news_kind(rows[0], len(ticker_rows))
    return {
        "canonical_news_id": news_id,
        "database": database,
        "normalized_table": normalized_table,
        "row": rows[0],
        "ticker_rows": ticker_rows,
        "ticker_table": ticker_table,
    }


def trading_news_rows(
    as_of: str = "",
    lookback_hours: int = 6,
    limit: int = 100,
    search: str = "",
    ticker: str = "",
    content: str = "all",
    kind: str = "all",
    before: str = "",
    before_id: str = "",
) -> dict[str, Any]:
    """Return a bounded point-in-time news page for Canvas news containers."""
    safe_limit = max(1, min(limit, 250))
    safe_hours = max(1, min(lookback_hours, 24 * 365 * 5))
    try:
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of.strip() else datetime.now(UTC)
        cutoff = cutoff.replace(tzinfo=UTC) if cutoff.tzinfo is None else cutoff.astimezone(UTC)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="as_of must be an ISO-8601 timestamp") from exc
    try:
        cursor = datetime.fromisoformat(before.replace("Z", "+00:00")) if before.strip() else cutoff
        cursor = cursor.replace(tzinfo=UTC) if cursor.tzinfo is None else cursor.astimezone(UTC)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="before must be an ISO-8601 timestamp") from exc
    safe_ticker = ticker.strip().upper()
    if safe_ticker and (len(safe_ticker) > 16 or not all(char.isalnum() or char in ".-" for char in safe_ticker)):
        raise HTTPException(status_code=400, detail="ticker is invalid")
    safe_content = content.strip().lower()
    if safe_content not in {"all", "full", "title"}:
        raise HTTPException(status_code=400, detail="content must be all, full, or title")
    safe_kind = kind.strip().lower()
    if safe_kind not in {"all", "ai", "analyst", "company", "market", "multi"}:
        raise HTTPException(status_code=400, detail="kind must be all, ai, analyst, company, market, or multi")

    database = "q_live"
    normalized_table = "benzinga_news_normalized_v1"
    ticker_table = "benzinga_news_ticker_v1"
    window_start = cutoff - timedelta(hours=safe_hours)
    start_sql = f"toDateTime64({sql_string(window_start.strftime('%Y-%m-%d %H:%M:%S.%f'))}, 6, 'UTC')"
    end_sql = f"toDateTime64({sql_string(cutoff.strftime('%Y-%m-%d %H:%M:%S.%f'))}, 6, 'UTC')"
    cursor_sql = f"toDateTime64({sql_string(min(cursor, cutoff).strftime('%Y-%m-%d %H:%M:%S.%f'))}, 6, 'UTC')"
    cursor_id = before_id.strip()
    cursor_filter = "n.published_at_utc < page_before"
    if before.strip() and cursor_id:
        cursor_filter = f"(n.published_at_utc < page_before OR (n.published_at_utc = page_before AND n.canonical_news_id < {sql_string(cursor_id)}))"
    filters = [
        "n.published_date >= toDate(window_start)",
        "n.published_date <= toDate(window_end)",
        "n.published_at_utc >= window_start",
        "n.published_at_utc <= window_end",
        cursor_filter,
    ]
    if safe_ticker:
        filters.append(
            "n.canonical_news_id IN ("
            f"SELECT canonical_news_id FROM {quote_ident(database)}.{quote_ident(ticker_table)} FINAL "
            f"WHERE ticker = {sql_string(safe_ticker)} "
            "AND published_at_utc >= window_start AND published_at_utc <= window_end"
            ")"
        )
    search_term = search.strip()
    if search_term:
        escaped = sql_string(search_term)
        filters.append(
            "positionCaseInsensitiveUTF8(concat("
            "ifNull(n.title, ''), ' ', ifNull(n.normalized_full_text, ''), ' ', "
            f"ifNull(n.author, ''), ' ', ifNull(n.url_domain, '')), {escaped}) > 0"
        )
    if safe_content == "full":
        filters.append("NOT n.is_title_only")
    elif safe_content == "title":
        filters.append("n.is_title_only")
    where_sql = " AND ".join(filters)
    ticker_links_sql = (
        "arraySort(arrayDistinct(arrayFilter(value -> notEmpty(value), "
        "arrayMap(value -> upperUTF8(trimBoth(value)), n.tickers))))"
    )
    news_kind_sql = news_kind_sql_expression(ticker_links_sql)
    if safe_kind != "all":
        filters.append(f"({news_kind_sql}) = {sql_string(safe_kind)}")
        where_sql = " AND ".join(filters)
    query = f"""
        WITH
            {start_sql} AS window_start,
            {end_sql} AS window_end,
            {cursor_sql} AS page_before
        SELECT
            n.canonical_news_id,
            formatDateTime(n.published_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS published_at_utc,
            n.title, n.article_url, n.url_domain, n.author, n.channels, n.provider_tags,
            {ticker_links_sql} AS ticker_link_sample,
            length(ticker_link_sample) AS ticker_link_count,
            {news_kind_sql} AS news_kind,
            n.has_external_text, n.has_pdf, n.is_title_only,
            lengthUTF8(n.normalized_full_text) AS full_text_chars,
            substring(n.normalized_full_text, 1, 320) AS text_preview
        FROM {quote_ident(database)}.{quote_ident(normalized_table)} AS n FINAL
        WHERE {where_sql}
        ORDER BY n.published_at_utc DESC, n.canonical_news_id DESC
        LIMIT {safe_limit + 1}
        FORMAT JSONEachRow
    """
    try:
        rows = clickhouse_json_each_row(query)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="News query timed out while reading ClickHouse") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=503, detail=f"News database unavailable: {exc.reason}") from exc
    has_more = len(rows) > safe_limit
    rows = rows[:safe_limit]
    return {
        "as_of": cutoff.isoformat().replace("+00:00", "Z"),
        "has_more": has_more,
        "limit": safe_limit,
        "lookback_hours": safe_hours,
        "next_before": str(rows[-1].get("published_at_utc") or "") if has_more and rows else "",
        "next_before_id": str(rows[-1].get("canonical_news_id") or "") if has_more and rows else "",
        "rows": rows,
        "source": f"{database}.{normalized_table}",
        "window_start": window_start.isoformat().replace("+00:00", "Z"),
    }


def news_kind_sql_expression(ticker_links_sql: str) -> str:
    return f"""multiIf(
        arrayExists(value -> lowerUTF8(value) IN ('benzai', 'ai generated', 'ai-generated'), n.provider_tags), 'ai',
        arrayExists(value -> lowerUTF8(value) IN ('analyst ratings', 'price target', 'analyst color', 'initiation', 'reiteration', 'upgrades', 'downgrades'), n.channels), 'analyst',
        length({ticker_links_sql}) > 1, 'multi',
        length({ticker_links_sql}) = 1, 'company',
        'market'
    )"""


def classify_news_kind(row: dict[str, Any], ticker_count: int) -> str:
    provider_tags = {str(value).strip().lower() for value in row.get("provider_tags") or []}
    channels = {str(value).strip().lower() for value in row.get("channels") or []}
    if provider_tags.intersection({"benzai", "ai generated", "ai-generated"}):
        return "ai"
    if channels.intersection({"analyst ratings", "price target", "analyst color", "initiation", "reiteration", "upgrades", "downgrades"}):
        return "analyst"
    if ticker_count > 1:
        return "multi"
    if ticker_count == 1:
        return "company"
    return "market"


def clickhouse_json_each_row(query: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in clickhouse_status_query(query, timeout_seconds=NEWS_QUERY_TIMEOUT_SECONDS).splitlines()
        if line.strip()
    ]


def service_market_day_window() -> tuple[datetime, datetime, datetime, datetime]:
    market_now = datetime.now(UTC).astimezone(ZoneInfo(EXCHANGE_TIME_ZONE))
    window_start_et = market_now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end_et = window_start_et + timedelta(days=1)
    return window_start_et, window_end_et, window_start_et.astimezone(UTC), window_end_et.astimezone(UTC)


def service_datetime64_sql(value: datetime) -> str:
    return f"toDateTime64({sql_string(value.strftime('%Y-%m-%d %H:%M:%S.%f'))}, 6, 'UTC')"


def parse_service_timestamp_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_service_timestamp_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def service_sec_feed_company_name(title: Any, *, cik: str, form_type: str) -> str:
    text = str(title or "").strip()
    if not text:
        return ""
    prefix = f"{form_type} - " if form_type else ""
    if prefix and text.startswith(prefix):
        text = text[len(prefix):].strip()
    cik_marker = f"({cik})" if cik else ""
    if cik_marker and cik_marker in text:
        text = text.split(cik_marker, 1)[0].strip()
    return text.rstrip("- ").strip()


def service_sec_recent_feed_rows(window_start_utc: datetime, window_end_utc: datetime, limit: int) -> tuple[list[dict[str, Any]], str]:
    service = SERVICE_REGISTRY.get("sec")
    if not service:
        return [], "SEC service registry entry is missing"
    payload, error = fetch_service_json(service_base_url(service), f"/snapshot/sec/recent?limit={max(1, limit)}")
    if error:
        return [], error
    if not isinstance(payload, dict):
        return [], "Unexpected SEC recent feed payload"
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        return [], ""
    rows: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            continue
        updated_at = parse_service_timestamp_utc(raw_row.get("updated_at_utc"))
        if updated_at is None or updated_at < window_start_utc or updated_at >= window_end_utc:
            continue
        cik = str(raw_row.get("cik") or "").strip()
        accession = str(raw_row.get("accession_number") or "").strip()
        if not cik or not accession:
            continue
        row = dict(raw_row)
        row["cik"] = cik
        row["accession_number"] = accession
        row["updated_at_utc"] = format_service_timestamp_utc(updated_at)
        rows.append(row)
    return rows, ""


def service_sec_histogram(
    *,
    company_fact_table: str,
    database: str,
    document_table: str,
    filing_table: str,
    frame_table: str,
    text_table: str,
    window_end_et: datetime,
    window_end_utc: datetime,
    window_start_et: datetime,
    window_start_utc: datetime,
) -> dict[str, Any]:
    safe_bin_seconds = SERVICE_SEC_HISTOGRAM_BIN_SECONDS
    cache_key = f"{window_start_et.date().isoformat()}:{database}:{safe_bin_seconds}"
    cached_at, cached_payload = _SERVICE_SEC_HISTOGRAM_CACHE.get(cache_key, (0.0, {}))
    if cached_payload and time.monotonic() - cached_at < SERVICE_SEC_HISTOGRAM_CACHE_SECONDS:
        return cached_payload

    bin_count = int(((window_end_utc - window_start_utc).total_seconds() + safe_bin_seconds - 1) // safe_bin_seconds)
    window_start_sql = service_datetime64_sql(window_start_utc)
    window_end_sql = service_datetime64_sql(window_end_utc)
    query = f"""
        WITH
            {window_start_sql} AS window_start,
            {window_end_sql} AS window_end,
            filing_buckets AS
            (
                SELECT
                    toString(cik) AS cik,
                    accession_number,
                    toUInt64(intDiv(dateDiff('second', window_start, accepted_at_utc) + {safe_bin_seconds // 2}, {safe_bin_seconds})) AS bucket_index
                FROM {quote_ident(database)}.{quote_ident(filing_table)}
                WHERE accepted_at_utc >= window_start
                  AND accepted_at_utc < window_end
            ),
            document_counts AS
            (
                SELECT
                    toString(cik) AS cik,
                    accession_number,
                    toUInt64(count()) AS document_rows
                FROM {quote_ident(database)}.{quote_ident(document_table)} FINAL
                WHERE (toString(cik), accession_number) IN (SELECT cik, accession_number FROM filing_buckets)
                GROUP BY
                    cik,
                    accession_number
            ),
            text_counts AS
            (
                SELECT
                    toString(cik) AS cik,
                    accession_number,
                    toUInt64(count()) AS text_rows
                FROM {quote_ident(database)}.{quote_ident(text_table)} FINAL
                WHERE (toString(cik), accession_number) IN (SELECT cik, accession_number FROM filing_buckets)
                GROUP BY
                    cik,
                    accession_number
            ),
            fact_counts AS
            (
                SELECT
                    toString(cik) AS cik,
                    accession_number,
                    toUInt64(count()) AS xbrl_fact_rows
                FROM {quote_ident(database)}.{quote_ident(company_fact_table)}
                WHERE (toString(cik), accession_number) IN (SELECT cik, accession_number FROM filing_buckets)
                GROUP BY
                    cik,
                    accession_number
            ),
            frame_counts AS
            (
                SELECT
                    toString(cik) AS cik,
                    accession_number,
                    toUInt64(count()) AS xbrl_frame_rows
                FROM {quote_ident(database)}.{quote_ident(frame_table)}
                WHERE (toString(cik), accession_number) IN (SELECT cik, accession_number FROM filing_buckets)
                GROUP BY
                    cik,
                    accession_number
            ),
            classified_filings AS
            (
                SELECT
                    f.bucket_index AS bucket_index,
                    toUInt64(ifNull(d.document_rows, 0)) AS related_document_rows,
                    toUInt64(ifNull(t.text_rows, 0)) AS related_text_rows,
                    toUInt64(ifNull(cf.xbrl_fact_rows, 0) + ifNull(fr.xbrl_frame_rows, 0)) AS related_xbrl_rows
                FROM filing_buckets AS f
                LEFT JOIN document_counts AS d
                    ON d.cik = f.cik AND d.accession_number = f.accession_number
                LEFT JOIN text_counts AS t
                    ON t.cik = f.cik AND t.accession_number = f.accession_number
                LEFT JOIN fact_counts AS cf
                    ON cf.cik = f.cik AND cf.accession_number = f.accession_number
                LEFT JOIN frame_counts AS fr
                    ON fr.cik = f.cik AND fr.accession_number = f.accession_number
            ),
            bucket_counts AS
            (
                SELECT
                    bucket_index,
                    toUInt64(count()) AS total_rows,
                    toUInt64(countIf(related_xbrl_rows > 0)) AS xbrl_rows,
                    toUInt64(countIf(related_xbrl_rows = 0 AND related_text_rows > 0)) AS text_rows,
                    toUInt64(countIf(related_xbrl_rows = 0 AND related_text_rows = 0 AND related_document_rows > 0)) AS document_rows,
                    toUInt64(countIf(related_xbrl_rows = 0 AND related_text_rows = 0 AND related_document_rows = 0)) AS filing_only_rows
                FROM classified_filings
                GROUP BY bucket_index
            )
        SELECT
            formatDateTime(
                window_start + toIntervalSecond(toInt64(b.bucket_index) * {safe_bin_seconds}),
                '%Y-%m-%dT%H:%i:%S.000Z',
                'UTC'
            ) AS bucket_utc,
            toUInt64(ifNull(c.filing_only_rows, 0)) AS filing_only_rows,
            toUInt64(ifNull(c.document_rows, 0)) AS document_rows,
            toUInt64(ifNull(c.text_rows, 0)) AS text_rows,
            toUInt64(ifNull(c.xbrl_rows, 0)) AS xbrl_rows,
            toUInt64(ifNull(c.total_rows, 0)) AS total_rows
        FROM
        (
            SELECT toUInt64(number) AS bucket_index
            FROM numbers({bin_count + 1})
        ) AS b
        LEFT JOIN bucket_counts AS c
            ON c.bucket_index = b.bucket_index
        ORDER BY b.bucket_index
        FORMAT JSONEachRow
    """
    rows: list[dict[str, Any]] = []
    for line in clickhouse_status_query(query).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows.append(
            {
                "bucket_utc": str(row.get("bucket_utc") or ""),
                "document_rows": int(row.get("document_rows") or 0),
                "filing_only_rows": int(row.get("filing_only_rows") or 0),
                "text_rows": int(row.get("text_rows") or 0),
                "total_rows": int(row.get("total_rows") or 0),
                "xbrl_rows": int(row.get("xbrl_rows") or 0),
            }
        )
    payload = {
        "bin_seconds": safe_bin_seconds,
        "company_fact_table": company_fact_table,
        "database": database,
        "document_table": document_table,
        "filing_table": filing_table,
        "frame_table": frame_table,
        "market_timezone": EXCHANGE_TIME_ZONE,
        "rows": rows,
        "source": "clickhouse",
        "text_table": text_table,
        "window_end_et": window_end_et.isoformat(),
        "window_end_utc": window_end_utc.isoformat().replace("+00:00", "Z"),
        "window_start_et": window_start_et.isoformat(),
        "window_start_utc": window_start_utc.isoformat().replace("+00:00", "Z"),
    }
    _SERVICE_SEC_HISTOGRAM_CACHE[cache_key] = (time.monotonic(), payload)
    return payload


def service_sec_today_rows(limit: int = 250, sort: str = "desc") -> dict[str, Any]:
    safe_limit = max(1, min(limit, SERVICE_SEC_TODAY_ROWS_LIMIT))
    sort_direction = "ASC" if sort.strip().lower() == "asc" else "DESC"
    database = "q_live"
    filing_table = "sec_filing_v3"
    document_table = "sec_filing_document_v3"
    text_table = "sec_filing_text_rendered_v3"
    company_fact_table = "sec_xbrl_company_fact_v3"
    frame_table = "sec_xbrl_frame_observation_v3"
    window_start_et, window_end_et, window_start_utc, window_end_utc = service_market_day_window()
    window_start_sql = service_datetime64_sql(window_start_utc)
    window_end_sql = service_datetime64_sql(window_end_utc)
    try:
        histogram = service_sec_histogram(
            company_fact_table=company_fact_table,
            database=database,
            document_table=document_table,
            filing_table=filing_table,
            frame_table=frame_table,
            text_table=text_table,
            window_end_et=window_end_et,
            window_end_utc=window_end_utc,
            window_start_et=window_start_et,
            window_start_utc=window_start_utc,
        )
    except Exception as exc:
        histogram = {
            "bin_seconds": SERVICE_SEC_HISTOGRAM_BIN_SECONDS,
            "error": str(exc),
            "rows": [],
            "source": "clickhouse",
            "window_end_et": window_end_et.isoformat(),
            "window_end_utc": window_end_utc.isoformat().replace("+00:00", "Z"),
            "window_start_et": window_start_et.isoformat(),
            "window_start_utc": window_start_utc.isoformat().replace("+00:00", "Z"),
        }
    count_query = f"""
        WITH
            {window_start_sql} AS window_start,
            {window_end_sql} AS window_end
        SELECT
            toUInt64(count()) AS total_filings,
            formatDateTime(max(accepted_at_utc), '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS latest_accepted_at_utc
        FROM {quote_ident(database)}.{quote_ident(filing_table)}
        WHERE accepted_at_utc >= window_start
          AND accepted_at_utc < window_end
        FORMAT JSONEachRow
    """
    summary_rows = clickhouse_json_each_row(count_query)
    count_summary = summary_rows[0] if summary_rows else {}
    query = f"""
        WITH
            {window_start_sql} AS window_start,
            {window_end_sql} AS window_end
        SELECT
            f.filing_id,
            f.accession_number,
            f.accession_number_compact,
            toString(f.cik) AS cik,
            f.issuer_id,
            f.company_name,
            f.form_type,
            toString(f.filing_date) AS filing_date,
            toString(f.report_date) AS report_date,
            formatDateTime(f.accepted_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS accepted_at_utc,
            f.acceptance_datetime_raw,
            f.accepted_at_source,
            f.primary_document,
            f.primary_document_url,
            f.filing_detail_url,
            f.source_file_name,
            f.filing_size,
            f.items,
            f.text_status,
            'parent' AS activity_status
        FROM {quote_ident(database)}.{quote_ident(filing_table)} AS f
        WHERE f.accepted_at_utc >= window_start
          AND f.accepted_at_utc < window_end
        ORDER BY
            f.accepted_at_utc {sort_direction},
            f.accession_number {sort_direction}
        LIMIT {safe_limit}
        FORMAT JSONEachRow
    """
    rows = clickhouse_json_each_row(query)
    identity_rows_by_cik = service_sec_identity_rows_by_cik(
        database,
        sorted({str(row.get("cik") or "") for row in rows if str(row.get("cik") or "")}),
    )
    key_pairs = [(str(row.get("cik") or ""), str(row.get("accession_number") or "")) for row in rows]
    key_pairs = [(cik_value, accession_value) for cik_value, accession_value in key_pairs if cik_value and accession_value]
    key_clause = ", ".join(f"({sql_string(cik_value)}, {sql_string(accession_value)})" for cik_value, accession_value in key_pairs)

    def keyed_rows(query_sql: str) -> dict[tuple[str, str], dict[str, Any]]:
        if not key_clause:
            return {}
        return {
            (str(row.get("cik") or ""), str(row.get("accession_number") or "")): row
            for row in clickhouse_json_each_row(query_sql)
        }

    document_counts = keyed_rows(
        f"""
        SELECT
            toString(cik) AS cik,
            accession_number,
            toUInt64(count()) AS document_rows,
            toUInt64(countIf(document_role = 'primary')) AS primary_document_rows,
            toUInt64(countIf(has_normalized_text)) AS document_text_ready_rows,
            toUInt64(countIf(extraction_status NOT IN ('', 'ok', 'complete', 'completed', 'extracted'))) AS document_issue_rows,
            arraySort(arraySlice(groupUniqArray(nullIf(document_type, '')), 1, 8)) AS document_type_sample,
            arraySort(arraySlice(groupUniqArray(nullIf(file_extension, '')), 1, 8)) AS file_extension_sample
        FROM {quote_ident(database)}.{quote_ident(document_table)} FINAL
        WHERE (toString(cik), accession_number) IN ({key_clause})
        GROUP BY
            cik,
            accession_number
        FORMAT JSONEachRow
        """
    )
    text_counts = keyed_rows(
        f"""
        SELECT
            toString(cik) AS cik,
            accession_number,
            toUInt64(count()) AS text_rows,
            toUInt64(sum(text_char_count)) AS text_chars,
            arraySort(arraySlice(groupUniqArray(nullIf(text_kind, '')), 1, 8)) AS text_kind_sample,
            arraySort(arraySlice(arrayDistinct(arrayFlatten(groupArray(quality_flags))), 1, 10)) AS quality_flag_sample
        FROM {quote_ident(database)}.{quote_ident(text_table)} FINAL
        WHERE (toString(cik), accession_number) IN ({key_clause})
        GROUP BY
            cik,
            accession_number
        FORMAT JSONEachRow
        """
    )
    company_fact_counts = keyed_rows(
        f"""
        SELECT
            toString(cik) AS cik,
            accession_number,
            toUInt64(count()) AS xbrl_fact_rows,
            toUInt64(uniqExact(tag)) AS xbrl_fact_tags,
            arraySort(arraySlice(groupUniqArray(nullIf(tag, '')), 1, 12)) AS xbrl_fact_tag_sample
        FROM {quote_ident(database)}.{quote_ident(company_fact_table)}
        WHERE (toString(cik), accession_number) IN ({key_clause})
        GROUP BY
            cik,
            accession_number
        FORMAT JSONEachRow
        """
    )
    frame_counts = keyed_rows(
        f"""
        SELECT
            toString(cik) AS cik,
            accession_number,
            toUInt64(count()) AS xbrl_frame_rows,
            toUInt64(uniqExact(tag)) AS xbrl_frame_tags,
            arraySort(arraySlice(groupUniqArray(nullIf(tag, '')), 1, 12)) AS xbrl_frame_tag_sample
        FROM {quote_ident(database)}.{quote_ident(frame_table)}
        WHERE (toString(cik), accession_number) IN ({key_clause})
        GROUP BY
            cik,
            accession_number
        FORMAT JSONEachRow
        """
    )
    related_defaults = {
        "document_rows": 0,
        "primary_document_rows": 0,
        "document_text_ready_rows": 0,
        "document_issue_rows": 0,
        "document_type_sample": [],
        "file_extension_sample": [],
        "text_rows": 0,
        "text_chars": 0,
        "text_kind_sample": [],
        "quality_flag_sample": [],
        "xbrl_fact_rows": 0,
        "xbrl_fact_tags": 0,
        "xbrl_fact_tag_sample": [],
        "xbrl_frame_rows": 0,
        "xbrl_frame_tags": 0,
        "xbrl_frame_tag_sample": [],
    }
    for row in rows:
        key = (str(row.get("cik") or ""), str(row.get("accession_number") or ""))
        row["filing_parent_cik"] = str(row.get("cik") or "")
        row["row_origin"] = "canonical_parent"
        row.update(related_defaults)
        row.update(document_counts.get(key, {}))
        row.update(text_counts.get(key, {}))
        row.update(company_fact_counts.get(key, {}))
        row.update(frame_counts.get(key, {}))
        row.update(
            service_sec_identity_summary(
                identity_rows_by_cik.get(str(row.get("cik") or ""), []),
                accession_number=str(row.get("accession_number") or ""),
            )
        )
        text_rows = int(row.get("text_rows") or 0)
        xbrl_rows = int(row.get("xbrl_fact_rows") or 0) + int(row.get("xbrl_frame_rows") or 0)
        document_rows = int(row.get("document_rows") or 0)
        if xbrl_rows and text_rows:
            row["activity_status"] = "xbrl_and_text"
        elif xbrl_rows:
            row["activity_status"] = "xbrl"
        elif text_rows:
            row["activity_status"] = "text"
        elif document_rows:
            row["activity_status"] = "filing"
        else:
            row["activity_status"] = "parent"

    rows_by_key = {
        (str(row.get("cik") or ""), str(row.get("accession_number") or "")): row
        for row in rows
    }
    parent_by_accession: dict[str, dict[str, Any]] = {}
    for row in rows:
        accession = str(row.get("accession_number") or "")
        if accession:
            parent_by_accession.setdefault(accession, row)

    recent_feed_rows, recent_feed_error = service_sec_recent_feed_rows(
        window_start_utc=window_start_utc,
        window_end_utc=window_end_utc,
        limit=SERVICE_SEC_TODAY_ROWS_LIMIT,
    )
    feed_participant_rows = 0
    for feed_row in recent_feed_rows:
        cik = str(feed_row.get("cik") or "")
        accession = str(feed_row.get("accession_number") or "")
        key = (cik, accession)
        target = rows_by_key.get(key)
        if target is None:
            parent = parent_by_accession.get(accession)
            if parent is None:
                continue
            target = dict(parent)
            target["row_origin"] = "sec_gateway_feed_participant"
            target["filing_parent_cik"] = str(parent.get("filing_parent_cik") or parent.get("cik") or "")
            target["cik"] = cik
            target["company_name"] = service_sec_feed_company_name(
                feed_row.get("title"),
                cik=cik,
                form_type=str(feed_row.get("form_type") or parent.get("form_type") or ""),
            ) or str(feed_row.get("title") or "") or str(parent.get("company_name") or "")
            target["form_type"] = str(feed_row.get("form_type") or parent.get("form_type") or "")
            rows.append(target)
            rows_by_key[key] = target
            feed_participant_rows += 1
        target["feed_status"] = str(feed_row.get("status") or "")
        target["feed_title"] = str(feed_row.get("title") or "")
        target["feed_updated_at_utc"] = str(feed_row.get("updated_at_utc") or "")
        target["feed_documents"] = int(feed_row.get("documents") or 0)
        target["feed_texts"] = int(feed_row.get("texts") or 0)
        target["feed_skips"] = int(feed_row.get("skips") or 0)
        target["feed_xbrl_facts"] = int(feed_row.get("xbrl_facts") or 0)

    reverse_sort = sort_direction == "DESC"
    rows.sort(
        key=lambda row: parse_service_timestamp_utc(row.get("feed_updated_at_utc") or row.get("accepted_at_utc")) or datetime.min.replace(tzinfo=UTC),
        reverse=reverse_sort,
    )

    loaded_summary = {
        "document_rows": sum(int(row.get("document_rows") or 0) for row in rows),
        "text_rows": sum(int(row.get("text_rows") or 0) for row in rows),
        "with_documents": sum(1 for row in rows if int(row.get("document_rows") or 0) > 0),
        "with_text": sum(1 for row in rows if int(row.get("text_rows") or 0) > 0),
        "with_xbrl": sum(
            1
            for row in rows
            if int(row.get("xbrl_fact_rows") or 0) + int(row.get("xbrl_frame_rows") or 0) > 0
        ),
        "xbrl_fact_rows": sum(int(row.get("xbrl_fact_rows") or 0) for row in rows),
        "xbrl_frame_rows": sum(int(row.get("xbrl_frame_rows") or 0) for row in rows),
    }
    return {
        "database": database,
        "document_table": document_table,
        "filing_table": filing_table,
        "histogram": histogram,
        "limit": safe_limit,
        "market_timezone": EXCHANGE_TIME_ZONE,
        "rows": rows,
        "sort": sort_direction.lower(),
        "source": "clickhouse",
        "summary": {
            "document_rows": loaded_summary["document_rows"],
            "feed_participant_rows": feed_participant_rows,
            "feed_recent_error": recent_feed_error,
            "feed_recent_rows": len(recent_feed_rows),
            "latest_accepted_at_utc": str(count_summary.get("latest_accepted_at_utc") or ""),
            "loaded_rows": len(rows),
            "text_rows": loaded_summary["text_rows"],
            "total_filings": int(count_summary.get("total_filings") or 0),
            "with_documents": loaded_summary["with_documents"],
            "with_text": loaded_summary["with_text"],
            "with_xbrl": loaded_summary["with_xbrl"],
            "xbrl_fact_rows": loaded_summary["xbrl_fact_rows"],
            "xbrl_frame_rows": loaded_summary["xbrl_frame_rows"],
        },
        "text_table": text_table,
        "company_fact_table": company_fact_table,
        "frame_table": frame_table,
        "window_end_et": window_end_et.isoformat(),
        "window_end_utc": window_end_utc.isoformat().replace("+00:00", "Z"),
        "window_start_et": window_start_et.isoformat(),
        "window_start_utc": window_start_utc.isoformat().replace("+00:00", "Z"),
    }


def service_sec_detail(cik: str, accession_number: str) -> dict[str, Any]:
    normalized_cik = cik.strip()
    accession = accession_number.strip()
    if not normalized_cik or not accession:
        raise HTTPException(status_code=400, detail="cik and accession_number are required")
    database = "q_live"
    filing_table = "sec_filing_v3"
    document_table = "sec_filing_document_v3"
    text_table = "sec_filing_text_rendered_v3"
    company_fact_table = "sec_xbrl_company_fact_v3"
    frame_table = "sec_xbrl_frame_observation_v3"
    cik_sql = sql_string(normalized_cik)
    accession_sql = sql_string(accession)
    where_key = f"cik = {cik_sql} AND accession_number = {accession_sql}"
    try:
        filing_rows = clickhouse_json_each_row(
            f"""
            SELECT *
            FROM {quote_ident(database)}.{quote_ident(filing_table)}
            WHERE {where_key}
            ORDER BY inserted_at DESC
            LIMIT 1
            FORMAT JSONEachRow
            """
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="SEC filing parent lookup timed out") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SEC filing parent lookup failed: {exc}") from exc
    if not filing_rows:
        raise HTTPException(status_code=404, detail="SEC filing row not found")

    detail_errors: list[dict[str, str]] = []

    def optional_detail_rows(part: str, query: str) -> list[dict[str, Any]]:
        try:
            return clickhouse_json_each_row(query)
        except Exception as exc:
            detail_errors.append({"part": part, "message": str(exc)})
            return []

    document_rows = optional_detail_rows(
        "document_rows",
        f"""
        SELECT *
        FROM {quote_ident(database)}.{quote_ident(document_table)} FINAL
        WHERE {where_key}
        ORDER BY sequence_number ASC, inserted_at DESC, document_name ASC
        FORMAT JSONEachRow
        """,
    )
    text_rows = optional_detail_rows(
        "text_rows",
        f"""
        SELECT
            document_id,
            filing_id,
            accession_number,
            accession_number_compact,
            toString(cik) AS cik,
            text_kind,
            text,
            text_char_count,
            text_byte_count,
            text_sha256,
            extraction_method,
            normalizer_version,
            quality_flags,
            source_archive_date,
            source_archive_member,
            formatDateTime(extracted_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS extracted_at_utc,
            source_run_id,
            inserted_at,
            false AS text_truncated
        FROM {quote_ident(database)}.{quote_ident(text_table)} FINAL
        WHERE {where_key}
        ORDER BY text_kind ASC, document_id ASC, inserted_at DESC
        FORMAT JSONEachRow
        """,
    )
    company_fact_rows = optional_detail_rows(
        "company_fact_rows",
        f"""
        SELECT *
        FROM {quote_ident(database)}.{quote_ident(company_fact_table)}
        WHERE {where_key}
        ORDER BY taxonomy ASC, tag ASC, period_end_date DESC, unit_code ASC
        LIMIT 300
        FORMAT JSONEachRow
        """,
    )
    frame_rows = optional_detail_rows(
        "frame_rows",
        f"""
        SELECT *
        FROM {quote_ident(database)}.{quote_ident(frame_table)}
        WHERE {where_key}
        ORDER BY taxonomy ASC, tag ASC, period_end_date DESC, unit_code ASC
        LIMIT 300
        FORMAT JSONEachRow
        """,
    )
    try:
        identity_rows = service_sec_identity_rows_by_cik(database, [normalized_cik]).get(normalized_cik, [])
    except Exception as exc:
        detail_errors.append({"part": "identity_rows", "message": str(exc)})
        identity_rows = []
    return {
        "accession_number": accession,
        "cik": normalized_cik,
        "company_fact_rows": company_fact_rows,
        "company_fact_table": company_fact_table,
        "database": database,
        "detail_errors": detail_errors,
        "document_rows": document_rows,
        "document_table": document_table,
        "filing_row": filing_rows[0],
        "filing_table": filing_table,
        "frame_rows": frame_rows,
        "frame_table": frame_table,
        "identity_rows": identity_rows,
        "identity_summary": service_sec_identity_summary(identity_rows, accession_number=accession),
        "text_rows": text_rows,
        "text_table": text_table,
    }


def service_sec_identity_rows_by_cik(database: str, ciks: list[str]) -> dict[str, list[dict[str, Any]]]:
    normalized_ciks = sorted({str(cik).strip() for cik in ciks if str(cik).strip()})
    if not normalized_ciks:
        return {}
    cik_clause = ", ".join(sql_string(cik) for cik in normalized_ciks)
    rows = clickhouse_json_each_row(
        f"""
        SELECT
            b.bridge_id,
            b.cik,
            b.issuer_id AS bridge_issuer_id,
            ifNull(b.security_id, '') AS bridge_security_id,
            ifNull(b.listing_id, '') AS bridge_listing_id,
            ifNull(b.symbol_id, '') AS bridge_symbol_id,
            ifNull(b.ticker, '') AS ticker,
            ifNull(b.accession_number, '') AS bridge_accession_number,
            toString(b.valid_from_date) AS bridge_valid_from_date,
            toString(b.valid_to_date_exclusive) AS bridge_valid_to_date_exclusive,
            b.mapping_method,
            b.mapping_status,
            b.confidence_score AS mapping_confidence_score,
            b.ambiguity_status,
            issuer.issuer_id,
            issuer.issuer_name,
            issuer.issuer_name_normalized,
            ifNull(issuer.legal_name, '') AS issuer_legal_name,
            ifNull(issuer.branding_name, '') AS issuer_branding_name,
            ifNull(issuer.entity_type, '') AS issuer_entity_type,
            ifNull(issuer.domicile_country_code, '') AS issuer_domicile_country_code,
            ifNull(issuer.state_of_incorporation, '') AS issuer_state_of_incorporation,
            ifNull(issuer.sic_code, '') AS issuer_sic_code,
            ifNull(issuer.sic_description, '') AS issuer_sic_description,
            ifNull(issuer.sector, '') AS issuer_sector,
            ifNull(issuer.industry, '') AS issuer_industry,
            ifNull(issuer.industry_group, '') AS issuer_industry_group,
            ifNull(issuer.website_url, '') AS issuer_website_url,
            ifNull(issuer.investor_website_url, '') AS issuer_investor_website_url,
            issuer.status AS issuer_status,
            sec.security_id,
            sec.security_name,
            sec.product_type AS security_product_type,
            ifNull(sec.asset_class, '') AS security_asset_class,
            ifNull(sec.instrument_type, '') AS security_instrument_type,
            ifNull(sec.security_type, '') AS security_type,
            ifNull(toString(sec.has_options), '') AS security_has_options,
            sec.status AS security_status,
            listing.listing_id,
            listing.exchange_code,
            listing.currency_code,
            ifNull(listing.ibkr_conid, '') AS ibkr_conid,
            ifNull(listing.board_code, '') AS listing_board_code,
            ifNull(listing.segment_name, '') AS listing_segment_name,
            listing.listing_status,
            listing.is_primary_listing,
            toString(listing.list_date) AS listing_list_date,
            toString(listing.delisted_date) AS listing_delisted_date,
            sym.symbol_id,
            sym.source_system AS symbol_source_system,
            sym.ticker_normalized,
            sym.display_name AS symbol_display_name,
            ifNull(sym.ticker_root, '') AS ticker_root,
            ifNull(sym.ticker_suffix, '') AS ticker_suffix,
            ifNull(sym.ticker_type_id, '') AS ticker_type_id,
            sym.asset_type AS symbol_asset_type,
            sym.instrument_type AS symbol_instrument_type,
            ifNull(sym.security_type, '') AS symbol_security_type,
            sym.status AS symbol_status,
            sym.primary_symbol_flag
        FROM {quote_ident(database)}.id_sec_market_bridge_v3 AS b FINAL
        LEFT JOIN {quote_ident(database)}.id_issuer_v1 AS issuer FINAL
            ON issuer.issuer_id = b.issuer_id
        LEFT JOIN {quote_ident(database)}.id_security_v1 AS sec FINAL
            ON sec.security_id = ifNull(b.security_id, '')
        LEFT JOIN {quote_ident(database)}.id_listing_v1 AS listing FINAL
            ON listing.listing_id = ifNull(b.listing_id, '')
        LEFT JOIN {quote_ident(database)}.id_symbol_v1 AS sym FINAL
            ON sym.symbol_id = ifNull(b.symbol_id, '')
        WHERE b.cik IN ({cik_clause})
        ORDER BY
            b.cik ASC,
            sym.primary_symbol_flag DESC,
            listing.is_primary_listing DESC,
            b.confidence_score DESC,
            ifNull(b.ticker, '') ASC
        FORMAT JSONEachRow
        """
    )
    rows_by_cik: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_cik.setdefault(str(row.get("cik") or ""), []).append(row)
    return rows_by_cik


def service_sec_identity_summary(
    identity_rows: list[dict[str, Any]],
    *,
    accession_number: str = "",
) -> dict[str, Any]:
    def value(row: dict[str, Any], key: str) -> str:
        return str(row.get(key) or "").strip()

    def primary_flag(row: dict[str, Any]) -> int:
        raw_value = row.get("primary_symbol_flag")
        try:
            return int(raw_value or 0)
        except (TypeError, ValueError):
            return 1 if str(raw_value).strip().lower() in {"true", "yes"} else 0

    def first_value(row: dict[str, Any], *keys: str) -> str:
        for key in keys:
            row_value = value(row, key)
            if row_value:
                return row_value
        return ""

    def unique_values(key: str, *, limit: int = 12) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for item in identity_rows:
            item_value = value(item, key)
            if not item_value or item_value in seen:
                continue
            seen.add(item_value)
            values.append(item_value)
            if len(values) >= limit:
                break
        return values

    accession = accession_number.strip()
    ticker_rows = [item for item in identity_rows if value(item, "ticker")]
    accession_rows = [item for item in ticker_rows if accession and value(item, "bridge_accession_number") == accession]
    primary_candidates = accession_rows or ticker_rows or identity_rows
    primary = next((item for item in primary_candidates if value(item, "ticker") and primary_flag(item) > 0), None)
    if primary is None:
        primary = next((item for item in primary_candidates if value(item, "ticker")), identity_rows[0] if identity_rows else {})
    tickers = unique_values("ticker", limit=24)
    return {
        "identity_bridge_count": len(identity_rows),
        "identity_tickers": tickers,
        "issuer_id": first_value(primary, "issuer_id", "bridge_issuer_id"),
        "security_id": first_value(primary, "security_id", "bridge_security_id"),
        "listing_id": first_value(primary, "listing_id", "bridge_listing_id"),
        "symbol_id": first_value(primary, "symbol_id", "bridge_symbol_id"),
        "primary_ticker": value(primary, "ticker"),
        "primary_exchange_code": value(primary, "exchange_code"),
        "primary_currency_code": value(primary, "currency_code"),
        "primary_ibkr_conid": value(primary, "ibkr_conid"),
        "bridge_id_sample": unique_values("bridge_id", limit=8),
        "security_id_sample": unique_values("security_id", limit=8),
        "listing_id_sample": unique_values("listing_id", limit=8),
        "symbol_id_sample": unique_values("symbol_id", limit=8),
        "exchange_code_sample": unique_values("exchange_code", limit=8),
        "listing_status_sample": unique_values("listing_status", limit=8),
        "symbol_source_sample": unique_values("symbol_source_system", limit=8),
        "mapping_status_sample": unique_values("mapping_status", limit=8),
        "ambiguity_status_sample": unique_values("ambiguity_status", limit=8),
        "max_mapping_confidence": max((float(item.get("mapping_confidence_score") or 0.0) for item in identity_rows), default=0.0),
        "issuer_name": value(primary, "issuer_name"),
        "issuer_legal_name": value(primary, "issuer_legal_name"),
        "issuer_branding_name": value(primary, "issuer_branding_name"),
        "issuer_entity_type": value(primary, "issuer_entity_type"),
        "issuer_domicile_country_code": value(primary, "issuer_domicile_country_code"),
        "issuer_state_of_incorporation": value(primary, "issuer_state_of_incorporation"),
        "issuer_sic_code": value(primary, "issuer_sic_code"),
        "issuer_sic_description": value(primary, "issuer_sic_description"),
        "issuer_sector": value(primary, "issuer_sector"),
        "issuer_industry": value(primary, "issuer_industry"),
        "issuer_industry_group": value(primary, "issuer_industry_group"),
        "issuer_website_url": value(primary, "issuer_website_url"),
        "issuer_investor_website_url": value(primary, "issuer_investor_website_url"),
        "issuer_status": value(primary, "issuer_status"),
        "security_name": value(primary, "security_name"),
        "security_product_type": value(primary, "security_product_type"),
        "security_asset_class": value(primary, "security_asset_class"),
        "security_instrument_type": value(primary, "security_instrument_type"),
        "security_type": value(primary, "security_type"),
        "security_status": value(primary, "security_status"),
    }


def service_database_table_target(service_id: str, database: str, table: str) -> dict[str, str]:
    for target in SERVICE_DATABASE_TABLES.get(service_id, []):
        if target["database"] == database and target["table"] == table:
            return target
    raise HTTPException(status_code=404, detail="Table is not configured for this service")


def preview_cell_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def clickhouse_table_stats(targets: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    pairs = ", ".join(f"({sql_string(target['database'])}, {sql_string(target['table'])})" for target in targets)
    if not pairs:
        return {}
    query = f"""
        SELECT
            t.database,
            t.name AS table,
            t.engine,
            toUInt64(ifNull(sum(p.rows), 0)) AS rows,
            toUInt64(ifNull(sum(p.bytes_on_disk), 0)) AS bytes_on_disk,
            ifNull(toString(max(p.modification_time)), '') AS latest_update
        FROM system.tables AS t
        LEFT JOIN system.parts AS p
            ON p.database = t.database
           AND p.table = t.name
           AND p.active
        WHERE (t.database, t.name) IN ({pairs})
        GROUP BY
            t.database,
            t.name,
            t.engine
        FORMAT TSV
    """
    stats: dict[tuple[str, str], dict[str, Any]] = {}
    for line in clickhouse_status_query(query).splitlines():
        database, table, engine, rows, bytes_on_disk, latest_update = (line.split("\t") + ["", "", "", "", "", ""])[:6]
        stats[(database, table)] = {
            "database": database,
            "table": table,
            "engine": engine,
            "rows": int(rows or "0"),
            "bytes_on_disk": int(bytes_on_disk or "0"),
            "latest_update": latest_update or "-",
        }
    columns = clickhouse_table_columns(targets)
    buckets = clickhouse_table_count_buckets(targets, columns)
    for key, values in buckets.items():
        if key in stats:
            stats[key].update(values)
    return stats


def clickhouse_table_columns(targets: list[dict[str, str]]) -> dict[tuple[str, str], set[str]]:
    pairs = ", ".join(f"({sql_string(target['database'])}, {sql_string(target['table'])})" for target in targets)
    if not pairs:
        return {}
    query = f"""
        SELECT
            database,
            table,
            groupArray(name) AS names
        FROM system.columns
        WHERE (database, table) IN ({pairs})
        GROUP BY
            database,
            table
        FORMAT TSV
    """
    columns: dict[tuple[str, str], set[str]] = {}
    for line in clickhouse_status_query(query).splitlines():
        database, table, raw_names = (line.split("\t") + ["", "", ""])[:3]
        names = {name.strip().strip("'") for name in raw_names.strip("[]").split(",") if name.strip()}
        columns[(database, table)] = names
    return columns


def clickhouse_table_count_buckets(
    targets: list[dict[str, str]],
    columns: dict[tuple[str, str], set[str]],
) -> dict[tuple[str, str], dict[str, Any]]:
    selects: list[str] = []
    years = service_table_state_years()
    for target in targets:
        key = (target["database"], target["table"])
        time_column = table_time_column(columns.get(key, set()))
        if not time_column:
            continue
        date_expr = f"toDate({quote_ident(time_column)})"
        year_exprs = ",\n                ".join(
            f"toUInt64(countIf(toYear({date_expr}) = {year})) AS rows_{year}" for year in years
        )
        selects.append(
            f"""
            SELECT
                {sql_string(target["database"])} AS database,
                {sql_string(target["table"])} AS table,
                {sql_string(time_column)} AS time_column,
                toUInt64(countIf({date_expr} = today())) AS rows_today,
                toUInt64(countIf({date_expr} >= today() - 7)) AS rows_last_week,
                toUInt64(countIf({date_expr} >= today() - 30)) AS rows_last_month,
                {year_exprs}
            FROM {quote_ident(target["database"])}.{quote_ident(target["table"])}
            """
        )
    if not selects:
        return {}
    query = "\nUNION ALL\n".join(selects) + "\nFORMAT TSV"
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        lines = clickhouse_status_query(query).splitlines()
    except Exception:
        return buckets
    for line in lines:
        fields = line.split("\t")
        if len(fields) < 6 + len(years):
            continue
        database, table, time_column = fields[:3]
        values: dict[str, Any] = {
            "time_column": time_column,
            "rows_today": int(fields[3] or "0"),
            "rows_last_week": int(fields[4] or "0"),
            "rows_last_month": int(fields[5] or "0"),
        }
        for offset, year in enumerate(years, start=6):
            values[f"rows_{year}"] = int(fields[offset] or "0")
        buckets[(database, table)] = values
    return buckets


def table_time_column(columns: set[str]) -> str:
    for candidate in SERVICE_TABLE_TIME_COLUMN_CANDIDATES:
        if candidate in columns:
            return candidate
    return ""


def service_table_state_years() -> list[int]:
    return list(range(date.today().year, SERVICE_TABLE_STATE_START_YEAR - 1, -1))


def clickhouse_status_query(sql: str, *, timeout_seconds: float = SERVICE_STATUS_TIMEOUT_SECONDS) -> str:
    req = urllib.request.Request(default_clickhouse_url().rstrip("/") + "/", data=sql.encode("utf-8"), method="POST")
    user = default_clickhouse_user()
    password = default_clickhouse_password()
    if user:
        req.add_header("X-ClickHouse-User", user)
    if password:
        req.add_header("X-ClickHouse-Key", password)
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ClickHouse HTTP {exc.code} {exc.reason}: {body}") from exc


def table_state_status(stat: dict[str, Any] | None) -> str:
    if stat is None:
        return "missing"
    if int(stat.get("rows") or 0) <= 0:
        return "empty"
    return "ok"


def format_int(value: int) -> str:
    return f"{int(value):,}"


def format_optional_int(value: Any) -> str:
    if value is None:
        return "-"
    return format_int(int(value))


def format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


def service_status_payload(service_id: str, *, include_database_tables: bool = True, include_logs: bool = True, include_recent: bool = True) -> dict[str, Any]:
    service = SERVICE_REGISTRY.get(service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Unknown service")
    base_url = service_base_url(service)
    snapshot, snapshot_error = fetch_service_json(base_url, "/snapshot/status")
    health_payload: dict[str, Any] | list[Any] | None = None
    health_error: str | None = None
    metrics_payload: dict[str, Any] | list[Any] | None = None
    metrics_error: str | None = None
    if snapshot_error is not None:
        health_payload, health_error = fetch_service_json(base_url, "/health")
        if health_error is None:
            metrics_payload, metrics_error = fetch_service_json(base_url, "/metrics")
    recent_payload: dict[str, Any] | list[Any] | None = None
    recent_error: str | None = None
    if include_recent and snapshot_error is None and service.get("recent_path"):
        recent_payload, recent_error = fetch_service_json(base_url, service["recent_path"])
    unreachable = service_unreachable_error(snapshot_error) or (
        snapshot_error is not None and service_unreachable_error(health_error) and service_unreachable_error(metrics_error)
    )
    online = not unreachable and (snapshot_error is None or health_error is None or metrics_error is None)
    normalized_snapshot = snapshot if isinstance(snapshot, dict) else {}
    header = normalized_snapshot.get("header") if isinstance(normalized_snapshot.get("header"), dict) else {}
    current_operation = normalized_snapshot.get("current_operation") if isinstance(normalized_snapshot.get("current_operation"), dict) else {}
    metrics = metrics_payload if isinstance(metrics_payload, dict) else normalized_snapshot.get("service_specific", {})
    health_status = health_payload.get("service_status") if isinstance(health_payload, dict) else ""
    status = str(header.get("status") or health_status or "")
    if not status:
        status = "ONLINE" if online else "NOT_STARTED"
    elif not online:
        status = "NOT_STARTED"
    runtime_logs = service_runtime_logs(normalized_snapshot, metrics, recent_payload, health_payload, service_id=service_id) if include_logs else {"path": "", "rows": [], "error": ""}
    database_tables = service_database_table_state(service_id) if include_database_tables else {"rows": [], "error": ""}
    return {
        "registry": {
            "id": service["id"],
            "label": service["label"],
            "kind": service["kind"],
            "description": service["description"],
            "base_url": base_url,
        },
        "online": online,
        "status": status,
        "header": header,
        "current_operation": current_operation,
        "snapshot": normalized_snapshot,
        "health": health_payload if isinstance(health_payload, dict) else {},
        "metrics": metrics if isinstance(metrics, dict) else {},
        "recent": recent_payload if recent_payload is not None else {},
        "logs": runtime_logs,
        "database_tables": database_tables,
        "errors": {
            "snapshot": snapshot_error,
            "health": health_error,
            "metrics": metrics_error,
            "recent": recent_error,
        },
        "checked_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def safe_service_status_payload(service_id: str, *, include_database_tables: bool = True, include_logs: bool = True, include_recent: bool = True) -> dict[str, Any]:
    try:
        return service_status_payload(
            service_id,
            include_database_tables=include_database_tables,
            include_logs=include_logs,
            include_recent=include_recent,
        )
    except HTTPException:
        raise
    except Exception as exc:
        return service_status_error_payload(service_id, exc)


def service_status_error_payload(service_id: str, exc: Exception) -> dict[str, Any]:
    service = SERVICE_REGISTRY.get(service_id, {})
    try:
        base_url = service_base_url(service) if service else ""
    except Exception:
        base_url = ""
    detail = redact_log_text(f"{type(exc).__name__}: {exc}")
    return {
        "registry": {
            "id": service.get("id", service_id),
            "label": service.get("label", service_id),
            "kind": service.get("kind", "service"),
            "description": service.get("description", "Service status collection failed."),
            "base_url": base_url,
        },
        "online": False,
        "status": "DEGRADED",
        "header": {},
        "current_operation": {
            "phase": "status_collection",
            "status": "FAILED",
            "message": detail,
        },
        "snapshot": {},
        "health": {},
        "metrics": {},
        "recent": {},
        "logs": {"path": "", "rows": [], "error": ""},
        "database_tables": {"rows": [], "error": ""},
        "errors": {
            "collection": detail,
            "snapshot": None,
            "health": None,
            "metrics": None,
            "recent": None,
        },
        "checked_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "app": "quant-research-workbench"}


@app.get("/api/services/status")
def services_status(include_recent: bool = False, include_database_tables: bool = False, include_logs: bool = False) -> dict[str, Any]:
    service_ids = list(SERVICE_REGISTRY)
    with ThreadPoolExecutor(max_workers=max(1, min(len(service_ids), 8))) as executor:
        services = list(
            executor.map(
                lambda service_id: safe_service_status_payload(
                    service_id,
                    include_database_tables=include_database_tables,
                    include_logs=include_logs,
                    include_recent=include_recent,
                ),
                service_ids,
            )
        )
    return {
        "checked_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "services": services,
    }


@app.get("/api/services/{service_id}/status")
def service_status(service_id: str, include_database_tables: bool = True, include_logs: bool = True, include_recent: bool = True) -> dict[str, Any]:
    if service_id not in SERVICE_REGISTRY:
        raise HTTPException(status_code=404, detail="Unknown service")
    return safe_service_status_payload(service_id, include_database_tables=include_database_tables, include_logs=include_logs, include_recent=include_recent)


@app.get("/api/services/{service_id}/tables/{database}/{table}/preview")
def service_table_preview(service_id: str, database: str, table: str, limit: int = 20) -> dict[str, Any]:
    return service_database_table_preview(service_id, database, table, limit)


@app.get("/api/services/news/histogram")
def news_service_histogram() -> dict[str, Any]:
    return service_news_histogram()


@app.get("/api/services/news/today")
def news_service_today(limit: int = 250, sort: str = "desc") -> dict[str, Any]:
    return service_news_today_rows(limit, sort)


@app.get("/api/services/news/detail/{canonical_news_id}")
def news_service_detail(canonical_news_id: str) -> dict[str, Any]:
    return service_news_detail(canonical_news_id)


@app.get("/api/trading/news")
def trading_news(
    as_of: str = "",
    lookback_hours: int = 6,
    limit: int = 100,
    search: str = "",
    ticker: str = "",
    content: str = "all",
    kind: str = "all",
    before: str = "",
    before_id: str = "",
) -> dict[str, Any]:
    return trading_news_rows(as_of, lookback_hours, limit, search, ticker, content, kind, before, before_id)


@app.websocket("/api/trading/news/stream")
async def trading_news_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    ticker = websocket.query_params.get("ticker", "").strip().upper()
    if ticker and not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}", ticker):
        await websocket.send_json({"error": "Invalid news ticker stream request."})
        await websocket.close(code=1008)
        return
    upstream_path = f"/stream/news/ticker/{ticker}" if ticker else "/stream/news"
    upstream_url = service_websocket_url(SERVICE_REGISTRY["news"], upstream_path)
    try:
        async with websockets.connect(upstream_url, ping_interval=20, ping_timeout=20, max_size=8 * 1024 * 1024) as upstream:
            async for message in upstream:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        try:
            await websocket.send_json({"error": f"News Gateway stream unavailable: {exc}"})
            await websocket.close(code=1011)
        except Exception:
            return


@app.get("/api/trading/ticker-presentations")
def trading_ticker_presentations(tickers: str = "") -> dict[str, Any]:
    return ticker_presentation_payload(parse_csv_list(tickers))


@app.get("/api/trading/news/detail/{canonical_news_id}")
def trading_news_detail(canonical_news_id: str) -> dict[str, Any]:
    return service_news_detail(canonical_news_id)


@app.get("/api/services/sec/today")
def sec_service_today(limit: int = 250, sort: str = "desc") -> dict[str, Any]:
    return service_sec_today_rows(limit, sort)


@app.get("/api/services/sec/detail/{cik}/{accession_number}")
def sec_service_detail(cik: str, accession_number: str) -> dict[str, Any]:
    return service_sec_detail(cik, accession_number)


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
    return live_trading_preload_payload(Path(payload.processed_root), payload.session_date)


@app.get("/api/live-trading/preload")
def live_trading_preload_get(
    processed_root: str = str(DEFAULT_PROCESSED_ROOT),
    session_date: date = Query(...),
) -> dict[str, Any]:
    return live_trading_preload_payload(Path(processed_root), session_date)


def live_trading_preload_payload(processed_root: Path, session_date: date) -> dict[str, Any]:
    records = artifact_records(processed_root)
    session_text = session_date.isoformat()
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
            "sessions": [session.isoformat() for session in market_sessions(session_date - timedelta(days=45), session_date)][-30:],
        },
        {
            "label": "Recent 5m bars",
            "group": "bars",
            "timeframe": "5m",
            "sessions": [session.isoformat() for session in market_sessions(session_date - timedelta(days=10), session_date)][-7:],
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
    checks.append(ensure_benzinga_news_cache(processed_root, session_date))
    ready_count = sum(1 for check in checks if check["status"] == "ready")
    if ready_count == len(checks):
        try:
            live_scanner_base_frame(processed_root, session_date, "1m", list(LIVE_CHART_FEATURE_GROUPS))
        except Exception:
            pass
    return {
        "session_date": session_text,
        "status": "ready" if ready_count == len(checks) else "missing",
        "progress": round(ready_count / max(1, len(checks)), 4),
        "checks": checks,
    }


@app.post("/api/live-trading/news-at")
def live_trading_news_at(payload: LiveTradingNewsAtRequest) -> dict[str, Any]:
    return news_at_payload(Path(payload.processed_root), payload.session_date, payload.bar_time, payload.tickers)


@app.get("/api/live-trading/news-at")
def live_trading_news_at_get(
    processed_root: str = str(DEFAULT_PROCESSED_ROOT),
    session_date: date = Query(...),
    bar_time: str = "04:00",
    tickers: str | None = None,
) -> dict[str, Any]:
    return news_at_payload(Path(processed_root), session_date, bar_time, parse_csv_list(tickers))


@app.post("/api/live-trading/next-signal")
def live_trading_next_signal(payload: LiveTradingNextSignalRequest) -> dict[str, Any]:
    return live_trading_next_signal_payload(
        processed_root=Path(payload.processed_root),
        session_date=payload.session_date,
        start_time=payload.start_time,
        feature_groups=payload.feature_groups,
        columns=payload.columns,
        table_query=payload.table_query,
        row_limit=payload.row_limit,
        max_steps=payload.max_steps,
    )


@app.get("/api/live-trading/next-signal")
def live_trading_next_signal_get(
    processed_root: str = str(DEFAULT_PROCESSED_ROOT),
    session_date: date = Query(...),
    start_time: str = "04:00",
    feature_groups: str | None = None,
    columns: str | None = None,
    table_query: str | None = None,
    row_limit: int = Query(default=1000, ge=1, le=5000),
    max_steps: int | None = Query(default=None, ge=1, le=120),
) -> dict[str, Any]:
    return live_trading_next_signal_payload(
        processed_root=Path(processed_root),
        session_date=session_date,
        start_time=start_time,
        feature_groups=parse_csv_list(feature_groups) or ["core", "session", "momentum", "volume_liquidity", "price_action", "shock", "market_structure"],
        columns=parse_csv_list(columns),
        table_query=parse_table_query(table_query),
        row_limit=row_limit,
        max_steps=max_steps,
    )


def live_trading_next_signal_payload(
    *,
    processed_root: Path,
    session_date: date,
    start_time: str,
    feature_groups: list[str],
    columns: list[str],
    table_query: dict[str, Any] | None,
    row_limit: int,
    max_steps: int | None,
) -> dict[str, Any]:
    start_minute = parse_live_clock_minute(start_time)
    if start_minute is None:
        raise HTTPException(status_code=400, detail="Invalid start_time")
    end_minute = 20 * 60
    loop_end_minute = min(end_minute, start_minute + max_steps - 1) if max_steps else end_minute
    search = load_live_scanner_signal_search(
        processed_root=processed_root,
        session_date=session_date,
        timeframe="1m",
        start_minute=start_minute,
        end_minute=loop_end_minute,
        feature_groups=feature_groups,
        columns=columns,
        row_limit=row_limit,
        table_query=table_query,
    )
    snapshot = search["snapshot"]
    if snapshot.get("reason"):
        return {
            "complete": True,
            "found": False,
            "last_checked_time": snapshot.get("bar_time") or f"{loop_end_minute // 60:02d}:{loop_end_minute % 60:02d}",
            "next_start_time": None,
            "snapshot": snapshot,
            "steps": max(0, loop_end_minute - start_minute + 1),
        }
    if search.get("found"):
        bar_time = str(snapshot.get("bar_time") or f"{loop_end_minute // 60:02d}:{loop_end_minute % 60:02d}")
        found_minute = parse_live_clock_minute(bar_time) or loop_end_minute
        return {
            "complete": True,
            "found": True,
            "last_checked_time": bar_time,
            "next_start_time": None,
            "snapshot": snapshot,
            "steps": max(1, found_minute - start_minute + 1),
        }
    if loop_end_minute < end_minute:
        checked_time = f"{loop_end_minute // 60:02d}:{loop_end_minute % 60:02d}"
        next_minute = loop_end_minute + 1
        return {
            "complete": False,
            "found": False,
            "last_checked_time": checked_time,
            "next_start_time": f"{next_minute // 60:02d}:{next_minute % 60:02d}",
            "snapshot": {
                **snapshot,
                "bar_time": checked_time,
            },
            "steps": max(0, loop_end_minute - start_minute + 1),
        }
    return {
        "complete": True,
        "found": False,
        "last_checked_time": f"{end_minute // 60:02d}:{end_minute % 60:02d}",
        "next_start_time": None,
        "snapshot": {
            **snapshot,
            "bar_time": f"{end_minute // 60:02d}:{end_minute % 60:02d}",
            "reason": "No scanner signal found before the session cutoff.",
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


LIVE_LOWER_CHART_DISPLAY_ITEMS = ("vwap", "tema9", "tema20")
LIVE_CHART_FEATURE_GROUPS = ("core", "session", "momentum", "volume_liquidity", "price_action", "shock", "market_structure")


@lru_cache(maxsize=256)
def cached_chart_payload(
    processed_root: str,
    start_date_text: str,
    end_date_text: str,
    timeframe: str,
    ticker: str,
    feature_groups: tuple[str, ...],
    selected_columns: tuple[str, ...],
    selected_display_items: tuple[str, ...] | None,
    supervision_groups: tuple[str, ...],
    marker_limit: int,
    min_confidence: float,
) -> dict[str, Any]:
    return chart_payload(
        Path(processed_root),
        start_date=date.fromisoformat(start_date_text),
        end_date=date.fromisoformat(end_date_text),
        timeframe=timeframe,
        ticker=ticker,
        feature_groups_selected=list(feature_groups),
        selected_columns=list(selected_columns),
        selected_display_items=list(selected_display_items) if selected_display_items is not None else None,
        supervision_groups_selected=list(supervision_groups),
        marker_limit=marker_limit,
        min_confidence=min_confidence,
    )


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
        cached_chart_payload(
            str(processed_root_path),
            range_start.isoformat(),
            range_end.isoformat(),
            timeframe,
            ticker.upper(),
            tuple(selected_feature_groups),
            tuple(selected_columns),
            tuple(selected_display_items) if selected_display_items is not None else None,
            tuple(selected_supervision),
            marker_limit,
            min_confidence,
        )
    )


@app.get("/api/real-live-trading/warm-charts")
@app.get("/api/live-trading/warm-charts")
def live_trading_warm_charts(
    processed_root: str = str(DEFAULT_PROCESSED_ROOT),
    session_date: date = Query(...),
    tickers: str | None = None,
    max_tickers: int = Query(default=24, ge=1, le=100),
) -> dict[str, Any]:
    ticker_list = [ticker.upper() for ticker in parse_csv_list(tickers) if ticker][:max_tickers]
    if not ticker_list:
        return {"warmed": 0, "tickers": [], "cache": cached_chart_payload.cache_info()._asdict()}
    daily_start = (session_date - timedelta(days=60)).isoformat()
    five_sessions = market_sessions(session_date - timedelta(days=10), session_date)
    five_start = (five_sessions[-3] if len(five_sessions) >= 3 else five_sessions[0]).isoformat() if five_sessions else session_date.isoformat()
    warmed = 0
    for ticker in ticker_list:
        for timeframe, start_text in (("1d", daily_start), ("5m", five_start)):
            try:
                cached_chart_payload(
                    str(Path(processed_root)),
                    start_text,
                    session_date.isoformat(),
                    timeframe,
                    ticker,
                    LIVE_CHART_FEATURE_GROUPS,
                    (),
                    LIVE_LOWER_CHART_DISPLAY_ITEMS,
                    (),
                    100,
                    0.4,
                )
                warmed += 1
            except Exception:
                continue
    return {"warmed": warmed, "tickers": ticker_list, "cache": cached_chart_payload.cache_info()._asdict()}


@app.get("/api/real-live-trading/preflight")
def real_live_trading_preflight(account_type: str = "paper", account_keys: str = "") -> dict[str, Any]:
    try:
        return real_live_preflight(account_type, account_keys=account_keys)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/real-live-trading/accounts")
def real_live_trading_accounts() -> dict[str, Any]:
    try:
        return {"accounts": [public_account(account) for account in configured_real_live_accounts()]}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/real-live-trading/scanner")
def real_live_trading_scanner(row_limit: int = Query(default=250, ge=1, le=1000)) -> dict[str, Any]:
    try:
        return real_live_scanner_snapshot(row_limit=row_limit)
    except Exception as scanner_exc:
        scanner_error = str(scanner_exc)
    try:
        return apply_tradable_filter_to_scanner_payload(market_gateway_snapshot(row_limit=row_limit))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Filtered live scanner failed: {scanner_error}; Python gateway failed: {exc}") from exc


@app.get("/api/real-live-trading/market-gateway/status")
def real_live_market_gateway_status() -> dict[str, Any]:
    payload = market_gateway_status()
    try:
        payload["qmd_gateway"] = qmd_status()
    except Exception as exc:
        payload["qmd_gateway"] = {"provider": "qmd-gateway", "status": "blocked", "message": str(exc)}
    try:
        payload["qmd_service_core"] = qmd_service_status()
    except Exception as exc:
        payload["qmd_service_core"] = {"error": str(exc)}
    return payload


@app.get("/api/real-live-trading/qmd-gateway/status")
def real_live_qmd_gateway_status() -> dict[str, Any]:
    try:
        return qmd_status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/real-live-trading/qmd-gateway/catalogs")
def real_live_qmd_gateway_catalogs() -> dict[str, Any]:
    try:
        return qmd_catalogs()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/real-live-trading/market-gateway/universe-preview")
def real_live_market_gateway_universe_preview(
    row_limit: int = Query(default=0, ge=0, le=100000),
    refresh_enrichment: bool = False,
    snapshot_row_limit: int = Query(default=0, ge=0, le=100000),
    snapshot_sort_column: str = "",
    snapshot_sort_direction: str = "desc",
) -> dict[str, Any]:
    return market_gateway_universe_preview(
        row_limit=row_limit,
        refresh_enrichment=refresh_enrichment,
        snapshot_row_limit=snapshot_row_limit,
        snapshot_sort_column=snapshot_sort_column,
        snapshot_sort_direction=snapshot_sort_direction,
    )


@app.get("/api/real-live-trading/logo")
def real_live_trading_logo(path: str = Query(default="")) -> FileResponse:
    root = Path(market_gateway_config().logo_artifact_root)
    relative = path.replace("\\", "/").lstrip("/")
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Logo path is outside the configured artifact root.") from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Logo asset not found.")
    return FileResponse(target)


@app.get("/api/real-live-trading/market-gateway/bars")
def real_live_market_gateway_bars(symbol: str = "", timeframe: str = "1m", row_limit: int = Query(default=500, ge=1, le=5000)) -> dict[str, Any]:
    if symbol:
        try:
            return qmd_bars(symbol, timeframe=timeframe, row_limit=row_limit)
        except Exception:
            pass
    try:
        return market_gateway_bars(symbol=symbol or None, row_limit=row_limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/real-live-trading/qmd-gateway/indicators")
def real_live_qmd_gateway_indicators(symbol: str, timeframe: str = "1m", row_limit: int = Query(default=500, ge=1, le=5000)) -> dict[str, Any]:
    try:
        return qmd_indicators(symbol, timeframe=timeframe, row_limit=row_limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/real-live-trading/market-gateway/start")
async def real_live_market_gateway_start() -> dict[str, Any]:
    try:
        return await market_gateway_start()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/real-live-trading/market-gateway/stop")
async def real_live_market_gateway_stop() -> dict[str, Any]:
    try:
        return await market_gateway_stop()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/real-live-trading/portfolio")
def real_live_trading_portfolio(account_type: str = "paper", account_keys: str = "") -> dict[str, Any]:
    try:
        return real_live_portfolio(account_type, account_keys=account_keys)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/real-live-trading/orders")
def real_live_trading_orders(payload: RealLiveOrderSubmit) -> dict[str, Any]:
    try:
        return submit_real_live_order(payload.account_type, payload.order, preview=payload.preview, account_keys=payload.account_keys)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/trading/strategies")
def trading_strategies(latest_only: bool = True) -> dict[str, Any]:
    rows = list_strategy_definitions(latest_only=latest_only)
    return {"rows": rows, "row_count": len(rows)}


@app.get("/api/trading/historical-gateway")
def trading_historical_gateway() -> dict[str, Any]:
    return historical_gateway_snapshot()


@app.post("/api/trading/historical-window")
def trading_historical_window(payload: HistoricalWindowPreviewRequest) -> dict[str, Any]:
    if payload.mode not in {"replay", "backtest", "backtest_debug"}:
        raise HTTPException(status_code=400, detail="mode must be replay, backtest, or backtest_debug")
    try:
        return historical_window_preview(
            mode=payload.mode,
            anchor_date=payload.anchor_date,
            session_count=payload.session_count,
            replay_end_date=payload.replay_end_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/trading/historical-preflight")
def trading_historical_preflight(payload: HistoricalPreflightRequest) -> dict[str, Any]:
    if payload.mode not in {"replay", "backtest"}:
        raise HTTPException(status_code=400, detail="mode must be replay or backtest")
    try:
        return historical_preflight(
            mode=payload.mode,
            anchor_date=payload.anchor_date,
            session_count=1 if payload.mode == "replay" else payload.session_count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/trading/historical-bars")
def trading_historical_bars(payload: HistoricalBarChunkRequest) -> dict[str, Any]:
    try:
        return historical_bar_chunk(
            anchor_date=payload.session_date,
            ticker=payload.ticker,
            timeframe=payload.timeframe,
            offset_minutes=payload.offset_minutes,
            window_minutes=payload.window_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/trading/canvas-preview")
def trading_canvas_preview(payload: CanvasPreviewRequest) -> dict[str, Any]:
    try:
        return canvas_preview_payload(
            session_date=payload.session_date,
            preview_time=payload.preview_time,
            chart_symbol=payload.chart_symbol,
            chart_timeframe=payload.chart_timeframe,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/trading/canvas-live-chart")
def trading_canvas_live_chart(symbol: str, timeframe: str = "1m", row_limit: int = Query(default=500, ge=1, le=5000)) -> dict[str, Any]:
    ticker = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker):
        raise HTTPException(status_code=400, detail="symbol must be a valid ticker")
    if timeframe not in SUPPORTED_HISTORICAL_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of {', '.join(sorted(SUPPORTED_HISTORICAL_TIMEFRAMES))}")
    with ThreadPoolExecutor(max_workers=2) as executor:
        bars_future = executor.submit(qmd_chart_bars, ticker, timeframe=timeframe, row_limit=row_limit)
        indicators_future = (
            executor.submit(qmd_indicators, ticker, timeframe=timeframe, row_limit=row_limit)
            if timeframe in ENRICHED_QMD_TIMEFRAMES
            else None
        )
        try:
            bars = bars_future.result()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        errors: dict[str, str] = {}
        try:
            indicators = (
                indicators_future.result()
                if indicators_future is not None
                else {"ticker": ticker, "timeframe": timeframe, "history": [], "current": None, "tick": None}
            )
        except Exception as exc:
            indicators = {"ticker": ticker, "timeframe": timeframe, "history": [], "current": None, "tick": None}
            errors["indicators"] = str(exc)
    return {
        "bars": bars,
        "errors": errors,
        "indicators": indicators,
        "source": "qmd-gateway",
        "stream_interval_ms": 250,
    }


@app.get("/api/trading/canvas-live-chart/history")
def trading_canvas_live_chart_history(
    symbol: str,
    timeframe: str = "1m",
    before: str | None = None,
    session_date: str | None = None,
    as_of: str | None = None,
    before_bar: str | None = None,
    days: int = Query(default=1, ge=1, le=1),
    row_limit: int = Query(default=20_000, ge=1, le=50_000),
) -> dict[str, Any]:
    ticker = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker):
        raise HTTPException(status_code=400, detail="symbol must be a valid ticker")
    if timeframe not in SUPPORTED_HISTORICAL_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of {', '.join(sorted(SUPPORTED_HISTORICAL_TIMEFRAMES))}")
    if before is not None:
        try:
            date.fromisoformat(before)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="before must be an ISO date") from exc
    try:
        return _canvas_live_chart_history(
            ticker=ticker,
            timeframe=timeframe,
            before=before,
            session_date=session_date,
            as_of=as_of,
            before_bar=before_bar,
            row_limit=row_limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _canvas_live_chart_history(
    *,
    ticker: str,
    timeframe: str,
    before: str | None,
    session_date: str | None,
    as_of: str | None,
    before_bar: str | None,
    row_limit: int,
) -> dict[str, Any]:
    before_date = date.fromisoformat(before) if before else datetime.now(ZoneInfo(EXCHANGE_TIME_ZONE)).date()
    return historical_bar_history_before(
        before=before_date,
        ticker=ticker,
        timeframe=timeframe,
        row_limit=row_limit,
        session_date=date.fromisoformat(session_date) if session_date else None,
        as_of=as_of,
        before_bar=before_bar,
    )


@app.websocket("/api/trading/canvas-live-chart/stream/{stream}/{symbol}")
async def trading_canvas_live_chart_stream(websocket: WebSocket, stream: str, symbol: str) -> None:
    await websocket.accept()
    ticker = symbol.strip().upper()
    timeframe = websocket.query_params.get("timeframe", "1m")
    row_limit_text = websocket.query_params.get("limit", "500")
    if stream not in {"bars", "indicators"} or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker) or timeframe not in SUPPORTED_HISTORICAL_TIMEFRAMES:
        await websocket.send_json({"error": "Invalid live chart stream request."})
        await websocket.close(code=1008)
        return
    try:
        row_limit = max(1, min(int(row_limit_text), 5000))
    except ValueError:
        await websocket.send_json({"error": "limit must be an integer."})
        await websocket.close(code=1008)
        return
    try:
        macro_bars = stream == "bars" and timeframe in MACRO_QMD_TIMEFRAMES
        family_bars = stream == "bars" and timeframe not in ENRICHED_QMD_TIMEFRAMES and not macro_bars
        if stream == "indicators" and timeframe not in ENRICHED_QMD_TIMEFRAMES:
            await websocket.close(code=1000)
            return
        upstream_path = f"/stream/macro-bars/{ticker}" if macro_bars else f"/stream/family-bars/{ticker}" if family_bars else f"/stream/{stream}/{ticker}"
        upstream_params = (
            {"emit": "full_then_updates", "limit": row_limit, "timeframe": timeframe}
            if macro_bars
            else
            {"emit": "full_then_updates", "family": "trade", "limit": row_limit, "resolution": timeframe}
            if family_bars
            else {"timeframe": timeframe, "limit": row_limit}
        )
        upstream_url = qmd_websocket_url(upstream_path, upstream_params)
        async with websockets.connect(upstream_url, ping_interval=20, ping_timeout=20, max_size=8 * 1024 * 1024) as upstream:
            async for message in upstream:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                elif macro_bars:
                    payload = json.loads(message)
                    await websocket.send_json(
                        normalize_qmd_macro_bar_snapshot(payload, symbol=ticker, timeframe=timeframe)
                    )
                elif family_bars:
                    payload = json.loads(message)
                    await websocket.send_json(
                        normalize_qmd_family_bar_snapshot(payload, symbol=ticker, timeframe=timeframe)
                    )
                else:
                    await websocket.send_text(message)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        try:
            await websocket.send_json({"error": f"QMD live {stream} stream unavailable: {exc}"})
            await websocket.close(code=1011)
        except Exception:
            return


@app.websocket("/api/trading/historical-stream/{symbol}")
async def trading_historical_stream(websocket: WebSocket, symbol: str) -> None:
    await websocket.accept()
    ticker = symbol.strip().upper()
    timeframe = websocket.query_params.get("timeframe", "1m")
    session_date_text = websocket.query_params.get("session_date", "")
    after_sequence_text = websocket.query_params.get("after_sequence", "0")
    updates_per_second_text = websocket.query_params.get("updates_per_second", "0")
    max_updates_text = websocket.query_params.get("max_updates", "")
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker):
        await websocket.send_json({"error": "Invalid historical ticker."})
        await websocket.close(code=1008)
        return
    if timeframe not in {"1s", "10s", "30s", "1m", "5m", "1h"}:
        await websocket.send_json({"error": "Invalid historical timeframe."})
        await websocket.close(code=1008)
        return
    try:
        session_date = date.fromisoformat(session_date_text)
        after_sequence = max(0, int(after_sequence_text))
        updates_per_second = float(updates_per_second_text)
        max_updates = int(max_updates_text) if max_updates_text else None
    except ValueError:
        await websocket.send_json({"error": "Invalid historical stream controls."})
        await websocket.close(code=1008)
        return
    if not 0 <= updates_per_second <= 10_000:
        await websocket.send_json({"error": "updates_per_second must be between 0 and 10000."})
        await websocket.close(code=1008)
        return
    if max_updates is not None and not 1 <= max_updates <= 100_000:
        await websocket.send_json({"error": "max_updates must be between 1 and 100000."})
        await websocket.close(code=1008)
        return
    window = historical_window_preview(
        mode="replay",
        anchor_date=session_date,
        session_count=1,
        replay_end_date=session_date,
    )
    try:
        upstream_url = historical_gateway_websocket_url(
            f"/stream/derived/{ticker}",
            {
                "after_sequence": after_sequence,
                "emit": "updates",
                "end": window["end"],
                "start": window["start"],
                "timeframe": timeframe,
                "max_updates": max_updates,
                "updates_per_second": updates_per_second,
            },
        )
        async with websockets.connect(
            upstream_url,
            ping_interval=20,
            ping_timeout=20,
            max_size=16 * 1024 * 1024,
        ) as upstream:
            async for message in upstream:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)
        await websocket.close(code=1000)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        try:
            await websocket.send_json({"error": f"QMD History stream unavailable: {exc}"})
            await websocket.close(code=1011)
        except Exception:
            return


@app.get("/api/trading/canvas-context")
def trading_canvas_context() -> dict[str, Any]:
    try:
        coverage = historical_latest_coverage()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "preview_time": "09:45",
        "session_date": coverage.get("session_date"),
        "coverage": coverage,
    }


@app.get("/api/trading/strategies/{strategy_id}")
def trading_strategy(strategy_id: str, revision: int | None = None) -> dict[str, Any]:
    try:
        return get_strategy_definition(strategy_id, revision)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown strategy: {strategy_id}") from exc


@app.post("/api/trading/strategies")
def trading_strategy_save(payload: StrategyDefinitionSubmit) -> dict[str, Any]:
    try:
        return save_strategy_definition(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/real-live-trading/orders/reply")
def real_live_trading_order_reply(payload: RealLiveOrderReply) -> dict[str, Any]:
    try:
        return reply_real_live_order(payload.reply_id, payload.confirmed)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/real-live-trading/orders/{order_id}")
def real_live_trading_order_modify(order_id: str, payload: RealLiveOrderModify) -> dict[str, Any]:
    try:
        return modify_real_live_order(payload.account_key, order_id, payload.order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.delete("/api/real-live-trading/orders/{order_id}")
def real_live_trading_order_cancel(order_id: str, account_key: str) -> dict[str, Any]:
    try:
        return cancel_real_live_order(account_key, order_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
