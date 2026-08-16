from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from threading import Lock, Thread
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from src.backend.query_plans.historical_scanner_materialization_v1 import (
    SCANNER_SCHEMA_VERSION,
    SCANNER_TABLE,
    SCANNER_TECHNICAL_SCHEMA_VERSION,
    SCANNER_TECHNICAL_TABLE,
    scanner_snapshot_materialization,
    source_revision_query,
    technical_snapshot_materialization,
)
from src.backend.query_plans.historical_scanner_cache_v1 import (
    SCANNER_QMD_EVENT_TABLE,
    SCANNER_QMD_META_TABLE,
    SCANNER_QMD_SCHEMA_VERSION,
    SCANNER_QMD_TABLE,
    cached_qmd_rows_query,
    cached_qmd_signal_events_query,
    cached_scanner_rows_query,
    cached_technical_rows_query,
    json_each_row_insert,
    latest_cached_scanner_snapshot_query,
    qmd_snapshot_complete_queries,
    qmd_snapshot_table_schemas,
    snapshot_table_schema,
    technical_snapshot_table_schema,
)
from src.backend.query_plans.reference_scanner_asof_v1 import (
    scanner_reference_projection,
)
from src.backend.query_plans.sec_fundamentals_asof_v1 import scanner_fundamentals
from src.backend.real_live_market_data.startup import logo_asset_url
from src.backend.qmd_gateway_client import (
    normalize_qmd_indicator_scanner_row,
    normalize_qmd_market_signal,
    normalize_qmd_symbol_snapshot,
)
from src.backend.ticker_facts_service import (
    FUNDAMENTAL_TAGS,
    analyze_fundamentals,
    financial_card_and_scores,
    select_fundamentals,
    share_base_card,
    valuation_card_from_facts,
)
from src.backend.trading_runtime_service import historical_scanner_derived_snapshot


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NEW_YORK = ZoneInfo("America/New_York")
EXTENDED_SESSION_START_MINUTE = 4 * 60
EXTENDED_SESSION_END_MINUTE = 20 * 60
SCANNER_TECHNICAL_WINDOWS: dict[str, int | None] = {
    "100ms": 100_000,
    "1s": 1_000_000,
    "5s": 5_000_000,
    "10s": 10_000_000,
    "30s": 30_000_000,
    "1m": 60_000_000,
    "5m": 5 * 60_000_000,
    "15m": 15 * 60_000_000,
    "30m": 30 * 60_000_000,
    "1h": 60 * 60_000_000,
    "1d": None,
    "1w": None,
    "1mo": None,
    "extended_session": None,
    "regular_session": None,
}
SCANNER_TECHNICAL_METRICS = (
    "open",
    "high",
    "low",
    "change_pct",
    "volume",
    "dollar_volume",
    "trade_count",
    "quote_count",
    "vwap",
    "vwap_distance_pct",
    "vwap_trade",
    "vwap_trade_distance_pct",
    "relative_volume",
    "range_pct",
)
SCANNER_REFERENCE_FIELDS = (
    "symbol_id",
    "security_id",
    "issuer_id",
    "listing_id",
    "company_name",
    "exchange",
    "country",
    "sector",
    "industry",
    "market_cap",
    "shares_outstanding",
    "float_shares",
    "float_source",
    "float_quality",
    "short_pressure",
    "short_interest",
    "short_crowding_pct",
    "short_interest_pct",
    "days_to_cover",
    "short_volume",
    "short_volume_pct",
    "fails_to_deliver",
    "ftd_value",
    "reg_sho_threshold",
    "borrow_status",
    "borrow_shares",
    "borrow_fee",
    "ipo_date",
    "ipo_days_to_event",
    "split_execution_date",
    "split_days_to_event",
    "ibkr_conid",
)
SCANNER_FUNDAMENTAL_FIELDS = (
    "xbrl_quality_score", "xbrl_quality_label", "xbrl_quality_coverage_pct",
    "xbrl_profitability_score", "xbrl_growth_score", "xbrl_cash_quality_score",
    "xbrl_balance_sheet_score", "xbrl_capital_discipline_score",
    "financial_trajectory_score", "financial_trajectory_label",
    "financial_profitability_score", "financial_cash_generation_score", "financial_balance_sheet_score",
    "share_base_pressure_pct", "share_base_discipline_score", "valuation_pe", "valuation_label",
    "fundamental_free_cash_flow", "fundamental_gross_margin_pct", "fundamental_operating_margin_pct",
    "fundamental_net_margin_pct", "fundamental_free_cash_flow_margin_pct", "fundamental_return_on_assets_pct",
    "fundamental_return_on_equity_pct", "fundamental_working_capital", "fundamental_current_ratio",
    "fundamental_debt_to_equity", "fundamental_net_debt", "fundamental_interest_coverage",
    "fundamental_revenue_growth_pct", "fundamental_earnings_growth_pct", "fundamental_share_growth_pct",
    "fundamental_dilution_pct", "fundamental_cash_conversion", "fundamental_research_intensity_pct",
    "fundamental_sga_intensity_pct", "fundamental_latest_filing_at",
    "fundamental_revenue", "fundamental_gross_profit", "fundamental_operating_income",
    "fundamental_net_income", "fundamental_diluted_eps", "fundamental_operating_cash_flow",
    "fundamental_capital_expenditure", "fundamental_cash", "fundamental_current_assets",
    "fundamental_current_liabilities", "fundamental_accounts_receivable", "fundamental_accounts_payable",
    "fundamental_inventory", "fundamental_assets", "fundamental_liabilities", "fundamental_stockholders_equity",
    "fundamental_long_term_debt", "fundamental_current_debt", "fundamental_research_development",
    "fundamental_sga_expense", "fundamental_stock_based_compensation", "fundamental_interest_expense",
    "fundamental_income_tax_expense", "fundamental_effective_tax_rate_pct", "fundamental_goodwill",
    "fundamental_intangible_assets", "fundamental_deferred_revenue", "fundamental_debt_issued",
    "fundamental_debt_repaid", "fundamental_common_stock_issuance", "fundamental_common_shares_outstanding",
    "fundamental_weighted_average_basic_shares", "fundamental_weighted_average_diluted_shares",
    "fundamental_sec_public_float_value", "fundamental_dividends_per_share", "fundamental_share_repurchases",
    "fundamental_repurchased_shares",
)

_REPORTED_FUNDAMENTAL_KEYS = {
    "Revenue": "fundamental_revenue", "Gross profit": "fundamental_gross_profit",
    "Operating income": "fundamental_operating_income", "Net income": "fundamental_net_income",
    "Diluted EPS": "fundamental_diluted_eps", "Operating cash flow": "fundamental_operating_cash_flow",
    "Capital expenditure": "fundamental_capital_expenditure", "Cash": "fundamental_cash",
    "Current assets": "fundamental_current_assets", "Current liabilities": "fundamental_current_liabilities",
    "Accounts receivable": "fundamental_accounts_receivable", "Accounts payable": "fundamental_accounts_payable",
    "Inventory": "fundamental_inventory", "Assets": "fundamental_assets", "Liabilities": "fundamental_liabilities",
    "Stockholders' equity": "fundamental_stockholders_equity", "Long-term debt": "fundamental_long_term_debt",
    "Current debt": "fundamental_current_debt", "Research & development": "fundamental_research_development",
    "SG&A expense": "fundamental_sga_expense", "Stock-based compensation": "fundamental_stock_based_compensation",
    "Interest expense": "fundamental_interest_expense", "Income tax expense": "fundamental_income_tax_expense",
    "Effective tax rate": "fundamental_effective_tax_rate_pct", "Goodwill": "fundamental_goodwill",
    "Intangible assets": "fundamental_intangible_assets", "Deferred revenue": "fundamental_deferred_revenue",
    "Debt issued": "fundamental_debt_issued", "Debt repaid": "fundamental_debt_repaid",
    "Common-stock issuance": "fundamental_common_stock_issuance",
    "Common shares outstanding": "fundamental_common_shares_outstanding",
    "Weighted average basic shares": "fundamental_weighted_average_basic_shares",
    "Weighted average diluted shares": "fundamental_weighted_average_diluted_shares",
    "SEC public float value": "fundamental_sec_public_float_value",
    "Dividends per share": "fundamental_dividends_per_share", "Share repurchases": "fundamental_share_repurchases",
    "Repurchased shares": "fundamental_repurchased_shares",
}

_DERIVED_FUNDAMENTAL_KEYS = {
    "free_cash_flow": "fundamental_free_cash_flow", "gross_margin": "fundamental_gross_margin_pct",
    "operating_margin": "fundamental_operating_margin_pct", "net_margin": "fundamental_net_margin_pct",
    "free_cash_flow_margin": "fundamental_free_cash_flow_margin_pct",
    "return_on_assets": "fundamental_return_on_assets_pct", "return_on_equity": "fundamental_return_on_equity_pct",
    "working_capital": "fundamental_working_capital", "current_ratio": "fundamental_current_ratio",
    "debt_to_equity": "fundamental_debt_to_equity", "net_debt": "fundamental_net_debt",
    "interest_coverage": "fundamental_interest_coverage", "revenue_growth": "fundamental_revenue_growth_pct",
    "earnings_growth": "fundamental_earnings_growth_pct", "share_growth": "fundamental_share_growth_pct",
    "dilution": "fundamental_dilution_pct", "cash_conversion": "fundamental_cash_conversion",
    "research_intensity": "fundamental_research_intensity_pct", "sga_intensity": "fundamental_sga_intensity_pct",
}

_QMD_MATERIALIZATION_LOCK = Lock()
_QMD_MATERIALIZATIONS: dict[str, dict[str, Any]] = {}
_SCANNER_MATERIALIZATION_LOCK = Lock()
_SCANNER_MATERIALIZATIONS: dict[str, dict[str, Any]] = {}
MAX_ACTIVE_MATERIALIZATIONS = 4
MAX_TRACKED_MATERIALIZATIONS = 256
MATERIALIZATION_STATE_TTL_SECONDS = 3_600


def _prune_materialization_states(
    states: dict[str, dict[str, Any]],
    *,
    now: float,
) -> None:
    """Bound terminal coordination state without ever evicting active work."""
    terminal = [
        (key, float(state.get("finished_monotonic") or 0))
        for key, state in states.items()
        if state.get("status") in {"error", "ready"}
    ]
    for key, finished in terminal:
        if finished and now - finished >= MATERIALIZATION_STATE_TTL_SECONDS:
            states.pop(key, None)
    excess = max(0, len(states) - MAX_TRACKED_MATERIALIZATIONS)
    if not excess:
        return
    terminal = sorted(
        (
            (key, float(state.get("finished_monotonic") or 0))
            for key, state in states.items()
            if state.get("status") in {"error", "ready"}
        ),
        key=lambda item: item[1],
    )
    for key, _finished in terminal[:excess]:
        states.pop(key, None)


def historical_scanner_snapshot(as_of: datetime, *, lookback_minutes: int = 15) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return a causal full-universe scanner snapshot, materializing it once per source revision."""
    if as_of.tzinfo is None:
        raise ValueError("Historical scanner clock must be timezone-aware.")
    lookback_minutes = max(5, min(int(lookback_minutes), 120))
    snapshot_at = as_of.astimezone(UTC).replace(second=0, microsecond=0)
    window_start = snapshot_at - timedelta(minutes=lookback_minutes)
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    source_database = os.environ.get("QMD_HISTORY_CLICKHOUSE_DATABASE", "market_sip_compact")
    table_prefix = os.environ.get("QMD_HISTORY_TABLE_PREFIX", "events_")
    if not IDENTIFIER.fullmatch(source_database) or not IDENTIFIER.fullmatch(table_prefix):
        raise ValueError("Historical scanner source identifiers are invalid.")
    source_revision = _source_revision(client, source_database, snapshot_at)
    _ensure_snapshot_table(client)
    rows = _cached_rows(client, snapshot_at, lookback_minutes, source_revision)
    effective_snapshot_at = snapshot_at
    status = "ready"
    if not rows:
        rows, fallback_snapshot_at = _latest_cached_rows(
            client,
            snapshot_at,
            lookback_minutes,
            source_revision,
        )
        if fallback_snapshot_at is not None:
            effective_snapshot_at = fallback_snapshot_at
        status = _schedule_scanner_materialization(
            source_database=source_database,
            table_prefix=table_prefix,
            snapshot_at=snapshot_at,
            window_start=window_start,
            lookback_minutes=lookback_minutes,
            source_revision=source_revision,
        )
    return rows, {
        "complete_universe": bool(rows),
        "lookback_minutes": lookback_minutes,
        "materialized": status == "ready",
        "requested_snapshot_at_utc": snapshot_at.isoformat(),
        "row_count": len(rows),
        "schema_version": SCANNER_SCHEMA_VERSION,
        "snapshot_at_utc": effective_snapshot_at.isoformat(),
        "source_revision": source_revision,
        "refresh_status": status,
        "status": status if effective_snapshot_at == snapshot_at else "refreshing",
        "window_start_utc": (effective_snapshot_at - timedelta(minutes=lookback_minutes)).isoformat(),
    }


def historical_scanner_technical_projection(
    as_of: datetime,
    *,
    calculation_windows: list[str] | tuple[str, ...],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Project cached cross-sectional technical fields for each requested window.

    Interval metrics use exchange-session buckets. Anchored metrics use explicit
    session windows and do not acquire a synthetic bar timeframe. Each distinct
    calculation window is computed once for the full market and stored by source
    revision, avoiding per-symbol QMD calls on the interactive scanner path.
    """
    if as_of.tzinfo is None:
        raise ValueError("Historical scanner clock must be timezone-aware.")
    requested = list(dict.fromkeys(str(value).strip() for value in calculation_windows if str(value).strip()))
    invalid = [value for value in requested if value not in SCANNER_TECHNICAL_WINDOWS]
    if invalid:
        raise ValueError(
            "Unsupported scanner technical calculation window(s): "
            f"{', '.join(invalid)}. Expected one of {', '.join(SCANNER_TECHNICAL_WINDOWS)}."
        )
    if not requested:
        return {}, {"technical_calculation_windows": [], "technical_materialized": []}
    cutoff = as_of.astimezone(UTC)
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    source_database = os.environ.get("QMD_HISTORY_CLICKHOUSE_DATABASE", "market_sip_compact")
    table_prefix = os.environ.get("QMD_HISTORY_TABLE_PREFIX", "events_")
    if not IDENTIFIER.fullmatch(source_database) or not IDENTIFIER.fullmatch(table_prefix):
        raise ValueError("Historical scanner source identifiers are invalid.")
    source_revision = _source_revision(client, source_database, cutoff)
    _ensure_technical_snapshot_table(client)
    projection: dict[str, dict[str, Any]] = defaultdict(dict)
    materialized: list[str] = []
    windows: dict[str, dict[str, str]] = {}
    for calculation_window in requested:
        window_start, window_end = scanner_technical_window(cutoff, calculation_window)
        windows[calculation_window] = {
            "window_start_utc": window_start.isoformat(),
            "window_end_utc": window_end.isoformat(),
        }
        rows = _cached_technical_rows(client, window_end, calculation_window, source_revision)
        if not rows:
            _materialize_technical_snapshot(
                client,
                source_database=source_database,
                table_prefix=table_prefix,
                snapshot_at=window_end,
                window_start=window_start,
                calculation_window=calculation_window,
                source_revision=source_revision,
            )
            rows = _cached_technical_rows(client, window_end, calculation_window, source_revision)
            materialized.append(calculation_window)
        for row in rows:
            ticker = str(row.pop("symbol", "")).upper()
            if not ticker:
                continue
            for metric in SCANNER_TECHNICAL_METRICS:
                value = row.get(metric)
                if value in (None, ""):
                    continue
                if metric == "vwap":
                    projection[ticker][_technical_field_key("vwap", calculation_window, "hlc3")] = value
                elif metric == "vwap_distance_pct":
                    projection[ticker][_technical_field_key("vwap_distance_pct", calculation_window, "hlc3")] = value
                elif metric == "vwap_trade":
                    projection[ticker][_technical_field_key("vwap", calculation_window, "trade_price")] = value
                elif metric == "vwap_trade_distance_pct":
                    projection[ticker][_technical_field_key("vwap_distance_pct", calculation_window, "trade_price")] = value
                else:
                    projection[ticker][_technical_field_key(metric, calculation_window)] = value
    return dict(projection), {
        "technical_materialized": materialized,
        "technical_schema_version": SCANNER_TECHNICAL_SCHEMA_VERSION,
        "source_revision": source_revision,
        "technical_calculation_windows": requested,
        "technical_windows": windows,
    }


def scanner_technical_window(as_of: datetime, calculation_window: str) -> tuple[datetime, datetime]:
    """Return the causal calculation window ending no later than ``as_of``.

    Interval calculations use the latest bucket on the 04:00-20:00 New York
    grid. Anchored calculations use either the complete extended session or
    regular trading session to date; an anchor is not a bar timeframe.
    """
    if calculation_window not in SCANNER_TECHNICAL_WINDOWS:
        raise ValueError(f"Unsupported scanner technical calculation window: {calculation_window}")
    if as_of.tzinfo is None:
        raise ValueError("Historical scanner clock must be timezone-aware.")
    local = as_of.astimezone(NEW_YORK)
    anchored = calculation_window in {"1d", "1w", "1mo", "extended_session", "regular_session"}
    session_start_minute = 9 * 60 + 30 if calculation_window == "regular_session" else EXTENDED_SESSION_START_MINUTE
    session_end_minute = 16 * 60 if calculation_window == "regular_session" else EXTENDED_SESSION_END_MINUTE
    minute_of_day = local.hour * 60 + local.minute
    if minute_of_day < session_start_minute:
        session_date = _previous_weekday(local.date())
        local_end = datetime.combine(
            session_date,
            datetime.min.time(),
            NEW_YORK,
        ) + timedelta(minutes=session_end_minute)
    else:
        session_date = local.date()
        session_close = datetime.combine(session_date, datetime.min.time(), NEW_YORK) + timedelta(
            minutes=session_end_minute
        )
        local_end = min(local, session_close)
    session_open = datetime.combine(session_date, datetime.min.time(), NEW_YORK) + timedelta(minutes=session_start_minute)
    if calculation_window == "1w":
        period_date = session_date - timedelta(days=session_date.weekday())
        local_start = datetime.combine(period_date, datetime.min.time(), NEW_YORK) + timedelta(
            minutes=EXTENDED_SESSION_START_MINUTE
        )
    elif calculation_window == "1mo":
        period_date = session_date.replace(day=1)
        local_start = datetime.combine(period_date, datetime.min.time(), NEW_YORK) + timedelta(
            minutes=EXTENDED_SESSION_START_MINUTE
        )
    elif anchored:
        local_start = session_open
    else:
        resolution_us = int(SCANNER_TECHNICAL_WINDOWS[calculation_window] or 0)
        elapsed_us = max(1, int((local_end - session_open).total_seconds() * 1_000_000))
        bucket_index = max(0, (elapsed_us - 1) // resolution_us)
        local_start = session_open + timedelta(microseconds=bucket_index * resolution_us)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def historical_scanner_reference_projection(
    as_of: datetime,
    *,
    client: ClickHouseHttpClient | None = None,
) -> dict[str, dict[str, Any]]:
    """Batch-project point-in-time identity, supply, market, and short facts for the scanner universe."""
    if as_of.tzinfo is None:
        raise ValueError("Historical scanner clock must be timezone-aware.")
    cutoff = as_of.astimezone(UTC)
    active_client = client or ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
    )
    rows = _json_rows(
        active_client.execute(scanner_reference_projection(cutoff, "q_live"))
    )
    projection: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        values = {
            field: row.get(field)
            for field in SCANNER_REFERENCE_FIELDS
            if row.get(field) not in (None, "")
        }
        logo_url = logo_asset_url(str(row.get("logo_relative_path") or ""))
        if logo_url:
            values["logo_url"] = logo_url
        projection[ticker] = values
    return projection


def historical_scanner_fundamental_projection(
    as_of: datetime,
    *,
    client: ClickHouseHttpClient | None = None,
    prices_by_ticker: dict[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    """Calculate the Stock Facts and XBRL financial fields in one causal, set-based read."""
    if as_of.tzinfo is None:
        raise ValueError("Historical scanner clock must be timezone-aware.")
    cutoff = as_of.astimezone(UTC)
    tags = sorted({tag for _, alternatives in FUNDAMENTAL_TAGS for tag in alternatives})
    active_client = client or ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
    )
    rows = _json_rows(
        active_client.execute(scanner_fundamentals(tags, cutoff, "q_live"))
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker:
            grouped[ticker].append(row)

    prices = prices_by_ticker or {}
    projection: dict[str, dict[str, Any]] = {}
    for ticker, fundamental_rows in grouped.items():
        analysis = analyze_fundamentals(fundamental_rows)
        financial_card, financial_scores = financial_card_and_scores(fundamental_rows)
        share_card, share_score = share_base_card(fundamental_rows, [], [])
        valuation_card = valuation_card_from_facts(fundamental_rows, prices.get(ticker), None)
        facets = {str(facet.get("id") or ""): facet for facet in analysis.get("facets", [])}
        values: dict[str, Any] = {
            "xbrl_quality_score": analysis.get("score"),
            "xbrl_quality_label": analysis.get("label"),
            "xbrl_quality_coverage_pct": analysis.get("coverage_percent"),
            "financial_trajectory_score": financial_card.get("value"),
            "financial_trajectory_label": financial_card.get("label"),
            "financial_profitability_score": financial_scores.get("profitability"),
            "financial_cash_generation_score": financial_scores.get("cash_generation"),
            "financial_balance_sheet_score": financial_scores.get("balance_sheet"),
            "share_base_pressure_pct": share_card.get("value"),
            "share_base_discipline_score": share_score,
            "valuation_pe": valuation_card.get("value"),
            "valuation_label": valuation_card.get("label"),
            "fundamental_latest_filing_at": _utc_iso(max(
                (str(row.get("filed_at_utc") or "") for row in fundamental_rows), default="",
            )),
        }
        for facet_id, field in {
            "profitability": "xbrl_profitability_score", "growth": "xbrl_growth_score",
            "cash_quality": "xbrl_cash_quality_score", "balance_sheet": "xbrl_balance_sheet_score",
            "capital_discipline": "xbrl_capital_discipline_score",
        }.items():
            values[field] = facets.get(facet_id, {}).get("score")
        for metric in analysis.get("metrics", []):
            field = _DERIVED_FUNDAMENTAL_KEYS.get(str(metric.get("id") or ""))
            if field:
                values[field] = metric.get("value")
        for fact in select_fundamentals(fundamental_rows, cutoff):
            field = _REPORTED_FUNDAMENTAL_KEYS.get(str(fact.get("label") or ""))
            if field:
                values[field] = fact.get("value")
        projection[ticker] = {key: value for key, value in values.items() if value not in (None, "")}
    return projection


def historical_scanner_qmd_projection(
    as_of: datetime,
    *,
    source_revision: str,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return the durable, canonical QMD cross-sectional replay at ``as_of``.

    QMD History owns calculation with the shared Rust engines. This service
    owns only durable Canvas materialization and the row-shaped application
    projection, so repeated UI requests never replay the market session.
    """
    if as_of.tzinfo is None:
        raise ValueError("Historical Scanner QMD clock must be timezone-aware.")
    snapshot_at = as_of.astimezone(UTC)
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    _ensure_qmd_snapshot_tables(client)
    if not _qmd_snapshot_complete(client, snapshot_at, source_revision):
        payload = historical_scanner_derived_snapshot(snapshot_at)
        payload_revision = str((payload.get("source_revision") or {}).get("token") or "")
        if payload_revision != source_revision:
            raise RuntimeError(
                "QMD History source revision changed while the Scanner snapshot was being built; "
                f"expected {source_revision!r}, received {payload_revision!r}."
            )
        if str(payload.get("schema_version") or "") != SCANNER_QMD_SCHEMA_VERSION:
            raise RuntimeError(
                "QMD History Scanner schema mismatch: "
                f"expected {SCANNER_QMD_SCHEMA_VERSION}, received {payload.get('schema_version')!r}."
            )
        _materialize_qmd_snapshot(
            client,
            snapshot_at=snapshot_at,
            source_revision=source_revision,
            payload=payload,
        )
    cached_rows = _cached_qmd_rows(client, snapshot_at, source_revision)
    projection: dict[str, dict[str, Any]] = {}
    active_signal_count = 0
    market_row_count = 0
    indicator_row_count = 0
    for cached in cached_rows:
        ticker = str(cached.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        market = json.loads(str(cached.get("market_json") or "{}"))
        indicator = json.loads(str(cached.get("indicator_json") or "{}"))
        market_row_count += bool(market)
        indicator_row_count += bool(indicator)
        active_signals = json.loads(str(cached.get("active_signals_json") or "[]"))
        normalized_signals = [
            normalize_qmd_market_signal(row)
            for row in active_signals
            if isinstance(row, dict)
        ]
        strongest = max(
            normalized_signals,
            key=lambda row: (
                float(row.get("signal_rank_score") or 0),
                float(row.get("signal_confidence") or 0),
            ),
            default={},
        )
        active_signal_count += len(normalized_signals)
        projection[ticker] = {
            **normalize_qmd_symbol_snapshot(market),
            **normalize_qmd_indicator_scanner_row(indicator),
            **strongest,
            "active_signal_count": len(normalized_signals),
        }
    signal_rows = [
        normalize_qmd_market_signal(row)
        for row in _cached_qmd_signal_events(client, snapshot_at, source_revision)
        if isinstance(row, dict)
    ]
    return projection, signal_rows, {
        "qmd_active_signal_count": active_signal_count,
        "qmd_derived_materialized": True,
        "qmd_derived_schema_version": SCANNER_QMD_SCHEMA_VERSION,
        "qmd_market_row_count": market_row_count,
        "qmd_indicator_row_count": indicator_row_count,
        "qmd_signal_event_count": len(signal_rows),
    }


def historical_scanner_qmd_projection_or_schedule(
    as_of: datetime,
    *,
    source_revision: str,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Read a durable QMD projection or start one bounded background build."""
    if as_of.tzinfo is None:
        raise ValueError("Historical Scanner QMD clock must be timezone-aware.")
    snapshot_at = as_of.astimezone(UTC)
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    _ensure_qmd_snapshot_tables(client)
    if _qmd_snapshot_complete(client, snapshot_at, source_revision):
        projection, signals, meta = historical_scanner_qmd_projection(
            snapshot_at,
            source_revision=source_revision,
        )
        return projection, signals, {**meta, "qmd_derived_status": "ready"}
    key = f"{_clock(snapshot_at)}|{source_revision}|{SCANNER_QMD_SCHEMA_VERSION}"
    now = monotonic()
    with _QMD_MATERIALIZATION_LOCK:
        _prune_materialization_states(_QMD_MATERIALIZATIONS, now=now)
        state = _QMD_MATERIALIZATIONS.get(key)
        retryable = state is None or (
            state.get("status") == "error"
            and now - float(state.get("finished_monotonic") or 0) >= 60
        )
        if retryable:
            active = sum(
                row.get("status") == "building"
                for row in _QMD_MATERIALIZATIONS.values()
            )
            if active >= MAX_ACTIVE_MATERIALIZATIONS:
                state = {
                    "error": "Historical QMD materialization capacity is in use; retry shortly.",
                    "status": "capacity_limited",
                }
            else:
                state = {"error": "", "started_monotonic": now, "status": "building"}
                _QMD_MATERIALIZATIONS[key] = state
                Thread(
                    target=_run_qmd_materialization,
                    args=(key, snapshot_at, source_revision),
                    daemon=True,
                    name=f"canvas-qmd-{snapshot_at:%Y%m%d-%H%M}",
                ).start()
        status = str(state.get("status") or "building")
        error = str(state.get("error") or "")
    return {}, [], {
        "qmd_derived_error": error,
        "qmd_derived_materialized": False,
        "qmd_derived_schema_version": SCANNER_QMD_SCHEMA_VERSION,
        "qmd_derived_status": status,
        "qmd_market_row_count": 0,
        "qmd_indicator_row_count": 0,
        "qmd_signal_event_count": 0,
    }


def _run_qmd_materialization(
    key: str,
    snapshot_at: datetime,
    source_revision: str,
) -> None:
    try:
        historical_scanner_qmd_projection(
            snapshot_at,
            source_revision=source_revision,
        )
    except Exception as exc:
        with _QMD_MATERIALIZATION_LOCK:
            _QMD_MATERIALIZATIONS[key] = {
                "error": str(exc),
                "finished_monotonic": monotonic(),
                "status": "error",
            }
        return
    with _QMD_MATERIALIZATION_LOCK:
        _QMD_MATERIALIZATIONS[key] = {
            "error": "",
            "finished_monotonic": monotonic(),
            "status": "ready",
        }


def _ensure_snapshot_table(client: ClickHouseHttpClient) -> None:
    for query in snapshot_table_schema():
        client.execute(query)


def _ensure_qmd_snapshot_tables(client: ClickHouseHttpClient) -> None:
    for query in qmd_snapshot_table_schemas():
        client.execute(query)


def _qmd_snapshot_complete(
    client: ClickHouseHttpClient,
    snapshot_at: datetime,
    source_revision: str,
) -> bool:
    meta_query, count_query = qmd_snapshot_complete_queries(
        snapshot_at=snapshot_at,
        source_revision=source_revision,
    )
    rows = _json_rows(
        client.execute(meta_query)
    )
    if not rows or int(rows[0].get("complete") or 0) != 1:
        return False
    expected_rows = int(rows[0].get("row_count") or 0)
    if expected_rows <= 0:
        return False
    stored = _json_rows(
        client.execute(count_query)
    )
    return bool(
        stored
        and int(stored[0].get("row_count") or 0) == expected_rows
    )


def _cached_qmd_rows(
    client: ClickHouseHttpClient,
    snapshot_at: datetime,
    source_revision: str,
) -> list[dict[str, Any]]:
    return _json_rows(
        client.execute(
            cached_qmd_rows_query(
                snapshot_at=snapshot_at,
                source_revision=source_revision,
            )
        )
    )


def _cached_qmd_signal_events(
    client: ClickHouseHttpClient,
    snapshot_at: datetime,
    source_revision: str,
) -> list[dict[str, Any]]:
    rows = _json_rows(
        client.execute(
            cached_qmd_signal_events_query(
                snapshot_at=snapshot_at,
                source_revision=source_revision,
            )
        )
    )
    return [json.loads(str(row.get("event_json") or "{}")) for row in rows]


def _materialize_qmd_snapshot(
    client: ClickHouseHttpClient,
    *,
    snapshot_at: datetime,
    source_revision: str,
    payload: dict[str, Any],
) -> None:
    active_by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for signal in payload.get("active_signals") or []:
        if not isinstance(signal, dict):
            continue
        ticker = str(signal.get("ticker") or "").strip().upper()
        if ticker:
            active_by_ticker[ticker].append(signal)
    indicators_by_ticker = {
        str(indicator.get("sym") or "").strip().upper(): indicator
        for indicator in payload.get("indicators") or []
        if isinstance(indicator, dict) and str(indicator.get("sym") or "").strip()
    }
    market_by_ticker = {
        str(market.get("ticker") or "").strip().upper(): market
        for market in payload.get("market_rows") or []
        if isinstance(market, dict) and str(market.get("ticker") or "").strip()
    }
    snapshot_rows = [
        {
            "snapshot_at_utc": _clock(snapshot_at),
            "schema_version": SCANNER_QMD_SCHEMA_VERSION,
            "source_revision": source_revision,
            "ticker": ticker,
            "market_json": json.dumps(
                market_by_ticker.get(ticker, {}), separators=(",", ":"), sort_keys=True
            ),
            "indicator_json": json.dumps(
                indicators_by_ticker.get(ticker, {}), separators=(",", ":"), sort_keys=True
            ),
            "active_signals_json": json.dumps(
                active_by_ticker.get(ticker, []),
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
        for ticker in sorted(set(indicators_by_ticker) | set(market_by_ticker))
    ]
    event_rows = [
        {
            "snapshot_at_utc": _clock(snapshot_at),
            "schema_version": SCANNER_QMD_SCHEMA_VERSION,
            "source_revision": source_revision,
            "event_id": str(event.get("event_id") or ""),
            "event_json": json.dumps(event, separators=(",", ":"), sort_keys=True),
        }
        for event in payload.get("recent_signal_events") or []
        if isinstance(event, dict) and event.get("event_id")
    ]
    _insert_json_rows(client, SCANNER_QMD_TABLE, snapshot_rows)
    _insert_json_rows(client, SCANNER_QMD_EVENT_TABLE, event_rows)
    active_signal_count = sum(len(rows) for rows in active_by_ticker.values())
    _insert_json_rows(
        client,
        SCANNER_QMD_META_TABLE,
        [
            {
                "snapshot_at_utc": _clock(snapshot_at),
                "schema_version": SCANNER_QMD_SCHEMA_VERSION,
                "source_revision": source_revision,
                "engine_version": str(payload.get("engine_version") or ""),
                "event_count": int(payload.get("event_count") or 0),
                "market_count": len(market_by_ticker),
                "indicator_count": len(indicators_by_ticker),
                "row_count": len(snapshot_rows),
                "active_signal_count": active_signal_count,
                "signal_event_count": len(event_rows),
                "complete": 1,
            }
        ],
    )


def _insert_json_rows(
    client: ClickHouseHttpClient,
    table: str,
    rows: list[dict[str, Any]],
    *,
    batch_size: int = 1_000,
) -> None:
    for start in range(0, len(rows), batch_size):
        body = "\n".join(
            json.dumps(row, separators=(",", ":"), sort_keys=True)
            for row in rows[start : start + batch_size]
        )
        if body:
            client.execute(f"{json_each_row_insert(table)}\n{body}")


def _ensure_technical_snapshot_table(client: ClickHouseHttpClient) -> None:
    client.execute(technical_snapshot_table_schema())


def _cached_technical_rows(
    client: ClickHouseHttpClient,
    snapshot_at: datetime,
    calculation_window: str,
    source_revision: str,
) -> list[dict[str, Any]]:
    return _json_rows(
        client.execute(
            cached_technical_rows_query(
                snapshot_at=snapshot_at,
                calculation_window=calculation_window,
                source_revision=source_revision,
            )
        )
    )


def _materialize_technical_snapshot(
    client: ClickHouseHttpClient,
    *,
    source_database: str,
    table_prefix: str,
    snapshot_at: datetime,
    window_start: datetime,
    calculation_window: str,
    source_revision: str,
) -> None:
    client.execute(
        technical_snapshot_materialization(
            source_database=source_database,
            table_prefix=table_prefix,
            snapshot_at=snapshot_at,
            window_start=window_start,
            calculation_window=calculation_window,
            source_revision=source_revision,
        )
    )


def _source_revision(client: ClickHouseHttpClient, database: str, snapshot_at: datetime) -> str:
    rows = _json_rows(
        client.execute(
            source_revision_query(database=database, snapshot_at=snapshot_at)
        )
    )
    row = rows[0] if rows else {}
    return f"{int(row.get('build_step') or 0)}:{int(row.get('event_count') or 0)}:{row.get('updated_at') or ''}"


def _cached_rows(client: ClickHouseHttpClient, snapshot_at: datetime, lookback_minutes: int, source_revision: str) -> list[dict[str, Any]]:
    rows = _json_rows(
        client.execute(
            cached_scanner_rows_query(
                snapshot_at=snapshot_at,
                lookback_minutes=lookback_minutes,
                source_revision=source_revision,
            )
        )
    )
    return [{**row, "ticker": str(row.get("symbol") or "")} for row in rows]


def _latest_cached_rows(
    client: ClickHouseHttpClient,
    snapshot_at: datetime,
    lookback_minutes: int,
    source_revision: str,
) -> tuple[list[dict[str, Any]], datetime | None]:
    candidates = _json_rows(
        client.execute(
            latest_cached_scanner_snapshot_query(
                snapshot_at=snapshot_at,
                lookback_minutes=lookback_minutes,
                source_revision=source_revision,
            )
        )
    )
    raw = str((candidates[0] if candidates else {}).get("latest_snapshot_at_utc") or "").strip()
    if not raw:
        return [], None
    fallback_snapshot_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if fallback_snapshot_at.tzinfo is None:
        fallback_snapshot_at = fallback_snapshot_at.replace(tzinfo=UTC)
    fallback_snapshot_at = fallback_snapshot_at.astimezone(UTC)
    return (
        _cached_rows(client, fallback_snapshot_at, lookback_minutes, source_revision),
        fallback_snapshot_at,
    )


def _schedule_scanner_materialization(
    *,
    source_database: str,
    table_prefix: str,
    snapshot_at: datetime,
    window_start: datetime,
    lookback_minutes: int,
    source_revision: str,
) -> str:
    key = f"{_clock(snapshot_at)}|{lookback_minutes}|{source_revision}|{SCANNER_SCHEMA_VERSION}"
    now = monotonic()
    with _SCANNER_MATERIALIZATION_LOCK:
        _prune_materialization_states(_SCANNER_MATERIALIZATIONS, now=now)
        state = _SCANNER_MATERIALIZATIONS.get(key)
        retryable = state is None or (
            state.get("status") == "error"
            and now - float(state.get("finished_monotonic") or 0) >= 60
        )
        if retryable:
            active = sum(
                row.get("status") == "building"
                for row in _SCANNER_MATERIALIZATIONS.values()
            )
            if active >= MAX_ACTIVE_MATERIALIZATIONS:
                state = {
                    "error": "Historical Scanner materialization capacity is in use; retry shortly.",
                    "status": "capacity_limited",
                }
            else:
                state = {"error": "", "started_monotonic": now, "status": "building"}
                _SCANNER_MATERIALIZATIONS[key] = state
                Thread(
                    target=_run_scanner_materialization,
                    kwargs={
                        "key": key,
                        "lookback_minutes": lookback_minutes,
                        "snapshot_at": snapshot_at,
                        "source_database": source_database,
                        "source_revision": source_revision,
                        "table_prefix": table_prefix,
                        "window_start": window_start,
                    },
                    daemon=True,
                    name=f"canvas-scanner-{snapshot_at:%Y%m%d-%H%M}",
                ).start()
        return str(state.get("status") or "building")


def _run_scanner_materialization(
    *,
    key: str,
    source_database: str,
    table_prefix: str,
    snapshot_at: datetime,
    window_start: datetime,
    lookback_minutes: int,
    source_revision: str,
) -> None:
    try:
        client = ClickHouseHttpClient(
            default_clickhouse_url(),
            default_clickhouse_user(),
            default_clickhouse_password(),
        )
        _materialize_snapshot(
            client,
            source_database=source_database,
            table_prefix=table_prefix,
            snapshot_at=snapshot_at,
            window_start=window_start,
            lookback_minutes=lookback_minutes,
            source_revision=source_revision,
        )
    except Exception as exc:
        with _SCANNER_MATERIALIZATION_LOCK:
            _SCANNER_MATERIALIZATIONS[key] = {
                "error": str(exc),
                "finished_monotonic": monotonic(),
                "status": "error",
            }
        return
    with _SCANNER_MATERIALIZATION_LOCK:
        _SCANNER_MATERIALIZATIONS[key] = {
            "error": "",
            "finished_monotonic": monotonic(),
            "status": "ready",
        }


def _materialize_snapshot(
    client: ClickHouseHttpClient,
    *,
    source_database: str,
    table_prefix: str,
    snapshot_at: datetime,
    window_start: datetime,
    lookback_minutes: int,
    source_revision: str,
) -> None:
    client.execute(
        scanner_snapshot_materialization(
            source_database=source_database,
            table_prefix=table_prefix,
            snapshot_at=snapshot_at,
            window_start=window_start,
            lookback_minutes=lookback_minutes,
            source_revision=source_revision,
        )
    )


def _clock(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _technical_field_key(
    metric: str,
    calculation_window: str,
    source: str | None = None,
) -> str:
    source_suffix = f"__{source}" if source else ""
    return f"technical__{metric}__{calculation_window}{source_suffix}"


def _previous_weekday(value: date) -> date:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _utc_iso(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _json_rows(payload: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in payload.splitlines() if line.strip()]
