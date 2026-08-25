from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, date, datetime
from threading import Lock
from time import monotonic
from typing import Any, Callable
from zoneinfo import ZoneInfo

from src.request_context import ContextThreadPoolExecutor as ThreadPoolExecutor

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from src.backend.news_synthesis import ENGINE_VERSION, SYNTHESIS_TABLE
from src.backend.news_ai_review_service import load_news_ai_state
from src.backend.trading_runtime_service import (
    SUPPORTED_HISTORICAL_TIMEFRAMES,
    historical_day_coverage,
    strategy_canvas_payload,
)
from src.backend.canonical_trading_service import trading_state_payload
from src.trading_runtime.portfolio import default_policy_for_account
from src.backend.historical_scanner_service import (
    SCANNER_FUNDAMENTAL_FIELDS,
    historical_scanner_fundamental_projection,
    historical_scanner_reference_projection,
    historical_scanner_snapshot,
    historical_scanner_technical_projection_or_schedule,
    historical_scanner_qmd_projection_or_schedule,
)
from src.backend.feature_projection import compact_feature_projection
from src.backend.query_plans import canvas_context_v1
from src.trading_runtime.domain import BrokerAccount, BrokerEventEnvelope, BrokerEventType, BrokerProvider, TradingMode
from src.trading_runtime.ibkr_normalizer import normalize_account_values, normalize_execution, normalize_ledger, normalize_order, normalize_position_snapshot
from src.trading_runtime.projector import TradingStateProjector
from src.trading_runtime.round_trips import derive_round_trip_trades


NEW_YORK = ZoneInfo("America/New_York")
_SCANNER_RESPONSE_CACHE_TTL_SECONDS = 30.0
_SCANNER_RESPONSE_CACHE_LOCK = Lock()
_SCANNER_RESPONSE_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_SCANNER_RESPONSE_KEY_LOCKS: dict[tuple[Any, ...], Lock] = {}
_NEWS_INTELLIGENCE_CACHE_TTL_SECONDS = 15.0
_NEWS_INTELLIGENCE_CACHE_LOCK = Lock()
_NEWS_INTELLIGENCE_CACHE: tuple[float, list[dict[str, Any]]] | None = None


def clear_scanner_snapshot_cache() -> None:
    global _NEWS_INTELLIGENCE_CACHE
    with _SCANNER_RESPONSE_CACHE_LOCK:
        _SCANNER_RESPONSE_CACHE.clear()
        _SCANNER_RESPONSE_KEY_LOCKS.clear()
    with _NEWS_INTELLIGENCE_CACHE_LOCK:
        _NEWS_INTELLIGENCE_CACHE = None


def canvas_preview_payload(
    *,
    session_date: date,
    preview_time: str = "09:45",
    chart_symbol: str = "AAPL",
    chart_timeframe: str = "1m",
    include_domains: list[str] | tuple[str, ...] = ("coverage", "news", "scanner", "sec"),
) -> dict[str, Any]:
    as_of = _as_of(session_date, preview_time)
    symbol = chart_symbol.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
        raise ValueError("chart_symbol must be a valid ticker")
    if chart_timeframe not in SUPPORTED_HISTORICAL_TIMEFRAMES:
        raise ValueError(f"chart_timeframe must be one of {', '.join(sorted(SUPPORTED_HISTORICAL_TIMEFRAMES))}")

    cutoff = as_of.astimezone(UTC)

    requested_domains = {str(value).strip().lower() for value in include_domains}
    supported_domains = {"coverage", "news", "scanner", "sec"}
    invalid_domains = sorted(requested_domains - supported_domains)
    if invalid_domains:
        raise ValueError(f"Unsupported Canvas preview domain(s): {', '.join(invalid_domains)}")
    jobs: dict[str, Callable[[], Any]] = {}
    if "coverage" in requested_domains:
        jobs["coverage"] = lambda: historical_day_coverage(session_date)
    if "news" in requested_domains:
        jobs["news"] = lambda: _query_news(cutoff)
    if "scanner" in requested_domains:
        jobs["scanner"] = lambda: historical_scanner_snapshot(as_of, lookback_minutes=15)
    if "sec" in requested_domains:
        jobs["sec"] = lambda: _query_sec(cutoff)

    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {name: executor.submit(job) for name, job in jobs.items()}
        for name, future in futures.items():
            try:
                results[name] = future.result()
            except Exception as exc:  # A failed context source must not blank unrelated containers.
                errors[name] = str(exc)
    try:
        _attach_sec_tickers(results.get("sec", []))
    except Exception as exc:
        errors["sec_identity"] = str(exc)

    scanner_result = results.get("scanner")
    scanner = scanner_result[0] if isinstance(scanner_result, tuple) else []
    scanner_meta = scanner_result[1] if isinstance(scanner_result, tuple) else {
        "complete_universe": False,
        "row_count": 0,
        "status": "not_requested" if "scanner" not in requested_domains else "unavailable",
    }
    _enrich_scanner_intelligence(scanner, results.get("news", []), results.get("sec", []), as_of)
    scanner.sort(key=lambda row: (-abs(float(row.get("change_5m_pct") or 0)), str(row.get("symbol") or "")))
    for rank, row in enumerate(scanner, start=1):
        row["rank"] = rank
    reference_price = float(scanner[0].get("last", 100.0)) if scanner else 100.0

    portfolio_fixture = _portfolio_fixture(reference_price)
    order_fixture = _order_fixture(reference_price)
    fill_fixture: list[dict[str, Any]] = []
    strategy = strategy_canvas_payload(as_of=as_of, ticker=symbol)
    return {
        "as_of": as_of.isoformat(),
        "coverage": results.get("coverage", {}),
        "chart": {
            "bars": [],
            "indicators": [],
            "symbol": symbol,
            "timeframe": chart_timeframe,
        },
        "errors": errors,
        "fills": fill_fixture,
        "journal": [
            {
                "time": row.get("effective_at"),
                "category": "strategy",
                "event": row.get("action"),
                "detail": row.get("reason"),
            }
            for row in strategy.get("signals", [])
        ],
        "news": results.get("news", []),
        "orders": order_fixture,
        "portfolio": portfolio_fixture,
        "preview_kind": "point_in_time_configuration",
        "scanner": scanner,
        "scanner_meta": scanner_meta,
        "sec": results.get("sec", []),
        "strategy": strategy,
        "trading": _canonical_trading_fixture(as_of, portfolio_fixture, order_fixture, fill_fixture),
        "xbrl": [],
    }


def scanner_snapshot_payload(
    *,
    as_of: datetime,
    enrichment_scope: str = "full",
    materialize_discovery: bool = True,
    lookback_minutes: int = 15,
    row_limit: int = 500,
    row_offset: int = 0,
    technical_windows: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    normalized_windows = tuple(sorted({str(value).strip() for value in technical_windows if str(value).strip()}))
    key = (
        as_of.isoformat(),
        enrichment_scope,
        bool(materialize_discovery),
        int(lookback_minutes),
        int(row_limit),
        int(row_offset),
        normalized_windows,
        id(historical_scanner_snapshot),
    )
    now = monotonic()
    with _SCANNER_RESPONSE_CACHE_LOCK:
        cached = _SCANNER_RESPONSE_CACHE.get(key)
        if cached and cached[0] > now:
            return deepcopy(cached[1])
        key_lock = _SCANNER_RESPONSE_KEY_LOCKS.setdefault(key, Lock())
    with key_lock:
        now = monotonic()
        with _SCANNER_RESPONSE_CACHE_LOCK:
            cached = _SCANNER_RESPONSE_CACHE.get(key)
            if cached and cached[0] > now:
                return deepcopy(cached[1])
        payload = _build_scanner_snapshot_payload(
            as_of=as_of,
            enrichment_scope=enrichment_scope,
            materialize_discovery=materialize_discovery,
            lookback_minutes=lookback_minutes,
            row_limit=row_limit,
            row_offset=row_offset,
            technical_windows=normalized_windows,
        )
        with _SCANNER_RESPONSE_CACHE_LOCK:
            _SCANNER_RESPONSE_CACHE[key] = (monotonic() + _SCANNER_RESPONSE_CACHE_TTL_SECONDS, payload)
            expired = [cache_key for cache_key, (expires_at, _) in _SCANNER_RESPONSE_CACHE.items() if expires_at <= now]
            for cache_key in expired:
                _SCANNER_RESPONSE_CACHE.pop(cache_key, None)
                _SCANNER_RESPONSE_KEY_LOCKS.pop(cache_key, None)
        return deepcopy(payload)


def _build_scanner_snapshot_payload(
    *,
    as_of: datetime,
    enrichment_scope: str = "full",
    materialize_discovery: bool = True,
    lookback_minutes: int = 15,
    row_limit: int = 500,
    row_offset: int = 0,
    technical_windows: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return the causal cross-sectional scanner independently of other Canvas sources."""
    if enrichment_scope not in {"core", "full"}:
        raise ValueError("enrichment_scope must be core or full")
    row_limit = max(1, min(int(row_limit), 2_000))
    row_offset = max(0, int(row_offset))
    from src.backend.discovery_projection import (
        configured_discovery_technical_windows,
        project_discovery_columns,
    )
    from src.backend.trading_configuration_service import market_discovery_runtime_configuration
    from src.backend.data_field_contracts import (
        compile_data_field_plan,
        project_composition_data_field_columns,
        project_data_field_outputs,
    )

    configuration = market_discovery_runtime_configuration()
    technical_windows = tuple(sorted({
        *technical_windows,
        *configured_discovery_technical_windows(configuration),
    }))
    rows, meta = historical_scanner_snapshot(as_of, lookback_minutes=lookback_minutes)
    source_total_rows = len(rows)
    effective_as_of = datetime.fromisoformat(
        str(meta.get("snapshot_at_utc") or as_of.isoformat()).replace("Z", "+00:00")
    )
    if effective_as_of.tzinfo is None:
        effective_as_of = effective_as_of.replace(tzinfo=UTC)
    effective_as_of = effective_as_of.astimezone(UTC)
    if rows:
        local_clock = effective_as_of.astimezone(NEW_YORK)
        local_minutes = local_clock.hour * 60 + local_clock.minute
        session_phase = (
            "premarket" if 4 * 60 <= local_minutes < 9 * 60 + 30
            else "regular" if 9 * 60 + 30 <= local_minutes < 16 * 60
            else "aftermarket" if 16 * 60 <= local_minutes < 20 * 60
            else "maintenance"
        )
        # A non-empty causal event cross-section proves that this historical
        # date/session was active; no holiday status is inferred for empty days.
        for row in rows:
            if row.get("session_phase") is None:
                row["session_phase"] = session_phase
            if row.get("market_status") is None:
                row["market_status"] = "active"
            if row.get("market_is_open") is None:
                row["market_is_open"] = session_phase != "maintenance"
            if row.get("is_trading_day") is None:
                row["is_trading_day"] = True
    page_limited = enrichment_scope == "full" and not materialize_discovery
    if page_limited:
        rows.sort(key=lambda row: (-abs(float(row.get("change_5m_pct") or 0)), str(row.get("symbol") or row.get("ticker") or "")))
        rows = rows[row_offset:row_offset + row_limit]
    cutoff = effective_as_of
    errors: dict[str, str] = {}
    news: list[dict[str, Any]] = []
    sec: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    prices_by_ticker = {
        str(row.get("symbol") or row.get("ticker") or "").strip().upper(): float(row.get("last") or 0)
        for row in rows
        if row.get("symbol") or row.get("ticker")
    }
    page_tickers = tuple(prices_by_ticker) if page_limited else ()
    if rows and technical_windows and enrichment_scope == "full":
        technical_projection, technical_meta = historical_scanner_technical_projection_or_schedule(
            effective_as_of,
            calculation_windows=technical_windows,
        )
        for row in rows:
            row.update(technical_projection.get(str(row.get("symbol") or "").upper(), {}))
        meta = {**meta, **technical_meta}
        if technical_meta.get("technical_status") in {"building", "capacity_limited", "error"}:
            meta["refresh_status"] = technical_meta["technical_status"]
    enrichment_names: list[str] = []
    if rows and enrichment_scope == "full":
        enrichment_names = ["fundamentals", "news", "qmd", "reference", "sec"]
    with ThreadPoolExecutor(max_workers=max(1, len(enrichment_names))) as executor:
        futures = {}
        if "fundamentals" in enrichment_names:
            futures["fundamentals"] = executor.submit(
                historical_scanner_fundamental_projection,
                effective_as_of,
                prices_by_ticker=prices_by_ticker,
                tickers=page_tickers,
            )
        if "news" in enrichment_names:
            futures["news"] = executor.submit(_query_scanner_news_intelligence, cutoff)
        if "qmd" in enrichment_names:
            futures["qmd"] = executor.submit(
                historical_scanner_qmd_projection_or_schedule,
                effective_as_of,
                source_revision=str(meta.get("source_revision") or ""),
                schedule_missing=False,
            )
        if "reference" in enrichment_names:
            futures["reference"] = executor.submit(
                historical_scanner_reference_projection,
                effective_as_of,
                tickers=page_tickers,
            )
        if "sec" in enrichment_names:
            futures["sec"] = executor.submit(_query_scanner_sec_intelligence, cutoff)
        for name, future in futures.items():
            try:
                if name == "fundamentals":
                    projection = future.result()
                    for row in rows:
                        row.update(projection.get(str(row.get("symbol") or "").upper(), {}))
                elif name == "news":
                    news = future.result()
                elif name == "qmd":
                    projection, signal_rows, qmd_meta = future.result()
                    for row in rows:
                        row.update(projection.get(str(row.get("symbol") or "").upper(), {}))
                    meta = {**meta, **qmd_meta}
                elif name == "reference":
                    projection = future.result()
                    for row in rows:
                        row.update(projection.get(str(row.get("symbol") or "").upper(), {}))
                else:
                    sec = future.result()
            except Exception as exc:
                errors[name] = str(exc)
    if enrichment_scope == "full":
        _merge_scanner_intelligence(rows, news, sec, effective_as_of)
    from src.backend.watchlist_runtime_service import normalize_watchlist_candidate

    normalized_rows = [normalize_watchlist_candidate(row) for row in rows]
    if not page_limited:
        source_total_rows = len(normalized_rows)
    if enrichment_scope == "core":
        normalized_rows.sort(key=lambda row: (-abs(float(row.get("change_5m_pct") or 0)), str(row.get("symbol") or "")))
        for rank, row in enumerate(normalized_rows, start=1):
            row["rank"] = rank
        normalized_rows = normalized_rows[row_offset:row_offset + row_limit]
    rows = project_discovery_columns(normalized_rows)
    discovery = dict(configuration.get("market_discovery") or {})
    core_scan = dict(discovery.get("core_scan") or {})
    core_scan_id = str(core_scan.get("scan_id") or "")
    data_field_plan = compile_data_field_plan(
        discovery,
        composition_ids=[core_scan_id] if (enrichment_scope == "core" or not materialize_discovery) and core_scan_id else (),
    )
    active_field_refs = data_field_plan.get("field_refs") or []
    rows = project_data_field_outputs(
        rows,
        list(discovery.get("data_fields") or []),
        field_refs=list(active_field_refs),
        field_instances=list(data_field_plan.get("field_instances") or []),
    )
    rows = project_composition_data_field_columns(
        rows,
        core_scan,
        discovery.get("column_catalog") or [],
    )
    if enrichment_scope == "full":
        rows.sort(key=lambda row: (-abs(float(row.get("change_5m_pct") or 0)), str(row.get("symbol") or "")))
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
    projected_fields = (
        "company_name", "exchange", "country", "sector", "industry", "market_cap",
        "market_cap_category", "shares_outstanding", "float_shares", "float_category",
        "float_source", "float_quality", "short_pressure", "short_interest",
        "short_crowding_pct", "short_interest_pct", "days_to_cover", "short_volume",
        "short_volume_pct", "fails_to_deliver", "ftd_value", "reg_sho_threshold",
        "borrow_status", "borrow_shares", "borrow_fee", "previous_close",
        "market_event_at", "market_event_age_ms", "market_quality_state",
        "market_quality_flags", "market_degradation_reason", "spread_bps",
        "trade_rate_10s", "trade_rate_60s", "liquidity_rank", "liquidity_score",
        *SCANNER_FUNDAMENTAL_FIELDS,
        "indicator_type", "indicator_producer", "indicator_timeframe",
        "flow_structure_composite_score", "flow_structure_composite_confidence",
        "flow_structure_composite_bias", "flow_structure_composite_reason",
        "microstructure_unified_signal", "microstructure_unified_confidence",
        "microstructure_signed_volume_imbalance", "microstructure_level1_ofi",
        "microstructure_queue_imbalance", "qmd_structure_score", "qmd_structure_confidence",
        "signal_domain", "signal_producer", "signal_type", "direction", "signal_score",
        "signal_rank_score", "signal_confidence", "active_signal_count", "working_timeframe",
        "input_basis", "update_trigger", "evidence",
        *sorted(
            {
                field
                for row in rows
                for field in row
                if field.startswith("technical__")
            }
        ),
    )
    coverage_rows = rows
    total = max(1, len(coverage_rows))
    meta = {
        **meta,
        "enrichment_scope": enrichment_scope,
        "enrichment_status": "ready" if enrichment_scope == "full" else "partial",
        "included_enrichments": enrichment_names,
        "field_coverage": {
            field: round(sum(row.get(field) not in (None, "") for row in coverage_rows) / total * 100, 1)
            for field in projected_fields
        },
    }
    if enrichment_scope == "core" or not materialize_discovery:
        watchlist_runtime = {
            "as_of": effective_as_of.isoformat(),
            "status": "not_requested",
            "watchlists": [],
        }
    else:
        try:
            from src.backend.watchlist_runtime_service import (
                project_configured_rule_set_columns,
                project_watchlists_from_candidates,
            )

            rows = project_configured_rule_set_columns(configuration, rows)
            watchlist_runtime = project_watchlists_from_candidates(
                configuration,
                rows,
                as_of=effective_as_of,
                available_fields=set().union(*(set(row) for row in rows)) if rows else set(),
                candidates_projected=True,
                source_complete=bool(meta.get("complete_universe")),
                source_status=str(meta.get("status") or "partial"),
            )
        except Exception as exc:
            errors["watchlists"] = str(exc)
            watchlist_runtime = {
                "as_of": effective_as_of.isoformat(),
                "error": str(exc),
                "status": "error",
                "watchlists": [],
            }
    if enrichment_scope == "core" or not materialize_discovery:
        signal_stream_runtime = {
            "as_of": effective_as_of.isoformat(),
            "status": "not_requested",
            "signal_streams": [],
        }
        signal_rows = []
    else:
        try:
            from src.backend.signal_stream_runtime_service import SIGNAL_STREAM_RUNTIME
            from src.backend.trading_runtime_service import trading_journal

            signal_stream_runtime = SIGNAL_STREAM_RUNTIME.snapshot(
                trading_journal(),
                as_of=effective_as_of,
                limit=10_000,
                configuration=configuration,
            )
            signal_rows = []
            signal_stream_runtime = {
                key: value
                for key, value in signal_stream_runtime.items()
                if key != "occurrences"
            }
        except Exception as exc:
            errors["signal_stream"] = str(exc)
            signal_stream_runtime = {
                "as_of": effective_as_of.isoformat(),
                "error": str(exc),
                "status": "error",
                "occurrences": [],
            }
            signal_rows = []
    total_rows = source_total_rows
    response_rows = rows if enrichment_scope == "core" or page_limited else rows[row_offset:row_offset + row_limit]
    meta = {
        **meta,
        "page_row_count": len(response_rows),
        "row_limit": row_limit,
        "row_offset": row_offset,
        "total_row_count": total_rows,
    }
    return {
        "as_of": effective_as_of.isoformat(),
        "errors": errors,
        "feature_projection": compact_feature_projection(
            response_rows,
            as_of=effective_as_of,
            source_revision=str(meta.get("source_revision") or ""),
            source_schema_version=str(meta.get("schema_version") or "1"),
        ),
        "meta": meta,
        "rows": response_rows,
        "signal_rows": signal_rows,
        "signal_stream_runtime": signal_stream_runtime,
        "watchlist_runtime": watchlist_runtime,
    }


def _canonical_trading_fixture(
    as_of: datetime,
    portfolio: dict[str, Any],
    orders: list[dict[str, Any]],
    executions: list[dict[str, Any]],
) -> dict[str, Any]:
    account_id = str(portfolio["account"]["acctId"])
    projector = TradingStateProjector(TradingMode.REPLAY, BrokerProvider.SIMULATED)
    projector.set_accounts(
        [
            BrokerAccount(
                provider=BrokerProvider.SIMULATED,
                account_id=account_id,
                base_currency="USD",
                account_type="DEMO",
                alias="Replay preview",
                title="Deterministic broker preview",
                can_view=True,
                can_trade=True,
                valid_at=as_of.astimezone(UTC),
            )
        ]
    )
    summary = {
        "netliquidation": {"amount": portfolio["summary"]["netLiquidation"], "currency": "USD", "timestamp": int(as_of.timestamp() * 1000)},
        "availablefunds": {"amount": portfolio["summary"]["availableFunds"], "currency": "USD", "timestamp": int(as_of.timestamp() * 1000)},
        "excessliquidity": {"amount": portfolio["summary"]["availableFunds"], "currency": "USD", "timestamp": int(as_of.timestamp() * 1000)},
        "buyingpower": {"amount": portfolio["summary"]["availableFunds"] * 2, "currency": "USD", "timestamp": int(as_of.timestamp() * 1000)},
        "totalcashvalue": {"amount": 76_120.10, "currency": "USD", "timestamp": int(as_of.timestamp() * 1000)},
        "grosspositionvalue": {"amount": 26_318.32, "currency": "USD", "timestamp": int(as_of.timestamp() * 1000)},
    }
    projector.set_account_values(normalize_account_values(summary, account_id))
    projector.merge_ledger(normalize_ledger({"BASE": {"cashbalance": 76_120.10, "settledcash": 76_120.10, "stockmarketvalue": 26_318.32, "netliquidationvalue": portfolio["summary"]["netLiquidation"], "currency": "USD", "timestamp": int(as_of.timestamp() * 1000)}}, account_id))
    manifest, position_rows = normalize_position_snapshot(portfolio["positions"], account_id)
    projector.apply_position_snapshot(account_id, manifest.snapshot_id, True, position_rows)
    projector.set_orders([normalize_order(row, account_id) for row in orders])
    execution_rows = [normalize_execution(row, account_id) for row in executions]
    projector.set_executions(execution_rows)
    for row in projector.orders.values():
        projector.record_activity(BrokerEventEnvelope.create(event_type=BrokerEventType.ORDER_STATUS_CHANGED, provider=BrokerProvider.SIMULATED, mode=TradingMode.REPLAY, account_id=account_id, payload=row.raw, source_event_time=row.source_event_time, broker_order_id=row.broker_order_id, client_order_id=row.client_order_id))
    for row in execution_rows:
        projector.record_activity(BrokerEventEnvelope.create(event_type=BrokerEventType.EXECUTION_REPORTED, provider=BrokerProvider.SIMULATED, mode=TradingMode.REPLAY, account_id=account_id, payload=row.raw, source_event_time=row.source_event_time, broker_order_id=row.broker_order_id, execution_id=row.execution_id))
    projector.closed_trades = {row.trade_id: row for row in derive_round_trip_trades(execution_rows)}
    projector.complete = True
    projector.stale = False
    projector.stale_reason = ""
    payload = trading_state_payload(projector.snapshot())
    # The preview is a point-in-time product. Projector construction happens at
    # request time, but its presentation clock must remain the requested market
    # instant rather than leaking wall-clock time into Replay/Backtest views.
    payload["as_of"] = as_of.astimezone(UTC).isoformat()
    policy = default_policy_for_account("simulated")
    metrics = payload["portfolio"]["metrics"]
    exposure = payload["portfolio"]["exposure"]
    net_liquidation = float(metrics.get("net_liquidation") or 0)
    gross = float(exposure.get("gross_value") or 0)
    payload["portfolio"]["management"] = {
        "schema_version": 1,
        "as_of": payload["as_of"],
        "complete": True,
        "stale": False,
        "stale_reason": "",
        "accounts": [
            {
                "account_key": "replay-preview",
                "account_id": account_id,
                "account_class": "simulated",
                "mode": "replay",
                "session_key": "simulated-replay",
                "base_currency": "USD",
                "enabled": True,
                "sync_state": "synchronized",
                "control_mode": "enabled",
                "observed_at": payload["as_of"],
                "stale_reason": "",
                "policy": {**asdict(policy), "identity": policy.identity},
                "available_policies": [{**asdict(policy), "identity": policy.identity}],
                "strategy_allocations": {},
                "disabled_strategy_allocations": [],
                "metrics": {
                    **metrics,
                    **exposure,
                    "eligible_equity": net_liquidation * policy.eligible_equity_fraction,
                    "reserved_notional": 0,
                    "reserved_planned_risk": 0,
                    "gross_headroom": max(0.0, policy.maximum_gross_exposure - gross),
                    "net_long_headroom": max(0.0, policy.maximum_net_long_exposure - max(0.0, float(exposure.get("net_value") or 0))),
                    "net_short_headroom": policy.maximum_net_short_exposure,
                    "planned_risk_headroom": net_liquidation * policy.maximum_open_risk_fraction,
                },
                "position_count": payload["portfolio"]["position_count"],
                "working_order_count": payload["portfolio"]["working_order_count"],
                "reservations": [],
                "allocations": [],
                "reconciliation": [],
                "continuous_risk": {},
                "managed_order_groups": [],
                "pending_operational_commands": [],
            }
        ],
        "groups": [],
        "recent_decisions": [],
    }
    return payload


def _as_of(session_date: date, preview_time: str) -> datetime:
    match = re.fullmatch(r"(\d{2}):(\d{2})", preview_time.strip())
    if not match:
        raise ValueError("preview_time must use HH:MM")
    hour, minute = (int(value) for value in match.groups())
    if hour > 23 or minute > 59:
        raise ValueError("preview_time must use a valid 24-hour time")
    return datetime(session_date.year, session_date.month, session_date.day, hour, minute, tzinfo=NEW_YORK)


def _clickhouse_rows(query: str) -> list[dict[str, Any]]:
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    normalized = query.strip().rstrip(";")
    if not re.search(r"\bFORMAT\s+JSONEachRow\s*$", normalized, re.IGNORECASE):
        normalized += "\nFORMAT JSONEachRow"
    payload = client.execute(normalized)
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


def _query_news(cutoff: datetime) -> list[dict[str, Any]]:
    return _clickhouse_rows(
        canvas_context_v1.company_news(
            cutoff, engine_version=ENGINE_VERSION, synthesis_table=SYNTHESIS_TABLE
        )
    )


def _query_sec(cutoff: datetime) -> list[dict[str, Any]]:
    return _clickhouse_rows(canvas_context_v1.sec_filings(cutoff))


def _query_scanner_news_intelligence(cutoff: datetime) -> list[dict[str, Any]]:
    """Return one causal synthesized-news summary per ticker for scanner enrichment."""
    source_rows = _clickhouse_rows(
        canvas_context_v1.scanner_company_news(
            cutoff, engine_version=ENGINE_VERSION, synthesis_table=SYNTHESIS_TABLE
        )
    )
    latest_by_ticker: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    today_counts: dict[str, int] = {}
    labels: dict[str, set[str]] = {}
    market_date = cutoff.astimezone(ZoneInfo("America/New_York")).date()
    for row in source_rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        counts[ticker] = counts.get(ticker, 0) + 1
        try:
            published_at = datetime.fromisoformat(str(row.get("published_at_utc") or "").replace("Z", "+00:00"))
            if published_at.astimezone(ZoneInfo("America/New_York")).date() == market_date:
                today_counts[ticker] = today_counts.get(ticker, 0) + 1
        except ValueError:
            pass
        labels.setdefault(ticker, set()).update(_string_values(row.get("news_labels")))
        latest_by_ticker.setdefault(ticker, dict(row))
    ai_by_news = load_news_ai_state(
        [str(row.get("canonical_news_id") or "") for row in latest_by_ticker.values()],
        query_rows=_clickhouse_rows,
    )
    result: list[dict[str, Any]] = []
    for ticker, row in latest_by_ticker.items():
        row["live_news_count"] = counts[ticker]
        row["today_news_count"] = today_counts.get(ticker, 0)
        row["latest_news_at"] = row.get("published_at_utc")
        row["news_labels"] = sorted(labels[ticker])
        row["ai_state"] = ai_by_news.get(str(row.get("canonical_news_id") or ""))
        result.append(row)
    return result


def scanner_news_intelligence_projection(cutoff: datetime) -> list[dict[str, Any]]:
    """Return a bounded shared projection for live scanner/watchlist refreshes."""
    global _NEWS_INTELLIGENCE_CACHE
    now = monotonic()
    with _NEWS_INTELLIGENCE_CACHE_LOCK:
        if _NEWS_INTELLIGENCE_CACHE and _NEWS_INTELLIGENCE_CACHE[0] > now:
            return deepcopy(_NEWS_INTELLIGENCE_CACHE[1])
        rows = _query_scanner_news_intelligence(cutoff)
        _NEWS_INTELLIGENCE_CACHE = (
            monotonic() + _NEWS_INTELLIGENCE_CACHE_TTL_SECONDS,
            rows,
        )
        return deepcopy(rows)


def _query_scanner_sec_intelligence(cutoff: datetime) -> list[dict[str, Any]]:
    """Return one point-in-time filing summary per ticker using the SEC identity bridge."""
    return _clickhouse_rows(canvas_context_v1.scanner_sec_filings(cutoff))


def _attach_sec_tickers(rows: Any) -> None:
    if not isinstance(rows, list):
        return
    ciks = sorted({str(row.get("cik") or "").strip() for row in rows if isinstance(row, dict) and row.get("cik")})
    if not ciks:
        return
    identities = _clickhouse_rows(canvas_context_v1.sec_ticker_identities(ciks))
    ticker_by_cik = {str(row.get("cik") or ""): str(row.get("mapped_ticker") or "").upper() for row in identities}
    for row in rows:
        if isinstance(row, dict):
            row["ticker"] = ticker_by_cik.get(str(row.get("cik") or ""), "")


def _enrich_scanner_intelligence(scanner: list[dict[str, Any]], news: Any, sec: Any, as_of: datetime) -> None:
    news_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for item in news if isinstance(news, list) else []:
        if not isinstance(item, dict):
            continue
        tickers = item.get("tickers")
        if isinstance(tickers, str):
            tickers = [value.strip() for value in tickers.strip("[]").replace("'", "").split(",") if value.strip()]
        for ticker in tickers if isinstance(tickers, list) else []:
            news_by_ticker.setdefault(str(ticker).upper(), []).append(item)
    sec_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for item in sec if isinstance(sec, list) else []:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").strip().upper()
        if ticker:
            sec_by_ticker.setdefault(ticker, []).append(item)
    for row in scanner:
        ticker = str(row.get("symbol") or "").upper()
        ticker_news = [item for item in news_by_ticker.get(ticker, []) if _is_truthy(item.get("is_company_news"))]
        ticker_sec = sec_by_ticker.get(ticker, [])
        row["live_news_count"] = len(ticker_news)
        row["live_news_recency"] = _latest_recency(ticker_news, "published_at_utc", as_of)
        row["news_labels"] = ", ".join(sorted({label for item in ticker_news for label in _string_values(item.get("news_topics"))}))
        row["sec_count"] = len(ticker_sec)
        row["sec_recency"] = _latest_recency(ticker_sec, "accepted_at_utc", as_of)
        row["sec_labels"] = ", ".join(sorted({str(item.get("form_type") or "") for item in ticker_sec if item.get("form_type")}))


def _merge_scanner_intelligence(scanner: list[dict[str, Any]], news: Any, sec: Any, as_of: datetime) -> None:
    """Merge already-aggregated intelligence without making scanner cost scale with documents."""
    enrich_scanner_news_intelligence(scanner, news, as_of)
    sec_by_ticker = {
        str(item.get("ticker") or "").strip().upper(): item
        for item in sec if isinstance(sec, list) and isinstance(item, dict) and item.get("ticker")
    }
    for row in scanner:
        ticker = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        sec_item = sec_by_ticker.get(ticker, {})
        row["sec_count"] = int(sec_item.get("sec_count") or 0)
        row["sec_recency"] = _latest_recency(
            [{"accepted_at_utc": sec_item.get("latest_sec_at")}], "accepted_at_utc", as_of
        )
        row["sec_labels"] = ", ".join(sorted(set(_string_values(sec_item.get("sec_labels")))))


def enrich_scanner_news_intelligence(scanner: list[dict[str, Any]], news: Any, as_of: datetime) -> None:
    """Attach current news presentation fields without changing scanner eligibility."""
    news_by_ticker = {
        str(item.get("ticker") or "").strip().upper(): item
        for item in news if isinstance(news, list) and isinstance(item, dict) and item.get("ticker")
    }
    for row in scanner:
        ticker = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        news_item = news_by_ticker.get(ticker, {})
        row["live_news_count"] = int(news_item.get("live_news_count") or 0)
        row["today_news_count"] = int(news_item.get("today_news_count") or 0)
        row["live_news_recency"] = _latest_recency(
            [{"published_at_utc": news_item.get("latest_news_at")}], "published_at_utc", as_of
        )
        row["news_labels"] = ", ".join(sorted(set(_string_values(news_item.get("news_labels")))))
        _attach_compact_news_intelligence(row, news_item)


def _attach_compact_news_intelligence(row: dict[str, Any], news_item: dict[str, Any]) -> None:
    """Project the latest story into sortable, presentation-safe table fields."""
    if not news_item.get("canonical_news_id"):
        return
    purpose = str(news_item.get("communication_purpose") or "unknown")
    origin = str(news_item.get("information_origin") or "unknown")
    structure = str(news_item.get("document_structure") or "unknown")
    article_class = (
        "Why moving" if purpose == "explain_move" else
        "Analyst" if origin == "analyst" else
        "Regulatory" if origin == "regulator" else
        "Market event" if structure in {"market_overview", "reference_list"} else
        "Multi-company" if structure == "multi_subject_digest" else
        "Company" if origin == "issuer" else "Editorial"
    )
    concepts = _string_values(news_item.get("news_labels"))
    direction = str(news_item.get("synthesis_direction") or "unknown")
    direction_score = {"positive": 1.0, "mixed": 0.5, "neutral": 0.0, "negative": -1.0}.get(direction, 0.0)
    row.update({
        "latest_news_id": str(news_item.get("canonical_news_id") or ""),
        "latest_news_published_at": str(news_item.get("published_at_utc") or news_item.get("latest_news_at") or ""),
        "latest_news_title": str(news_item.get("title") or ""),
        "news_synthesis": direction_score,
        "news_synthesis_class": article_class,
        "news_synthesis_purpose": purpose,
        "news_synthesis_origin": origin,
        "news_synthesis_direction": direction,
        "news_synthesis_event": concepts[0] if concepts else "",
        "news_synthesis_text": str(news_item.get("text_availability") or "unknown"),
    })
    ai_state = news_item.get("ai_state") if isinstance(news_item.get("ai_state"), dict) else {}
    funnel = ai_state.get("funnel") if isinstance(ai_state.get("funnel"), dict) else {}
    review = ai_state.get("review") if isinstance(ai_state.get("review"), dict) else {}
    labels_payload = review.get("labels") if isinstance(review.get("labels"), dict) else {}
    issuer_labels = [value for value in labels_payload.get("issuers") or [] if isinstance(value, dict)]
    issuer_labels.sort(key=lambda value: float(value.get("forecast_relevance_probability") or 0), reverse=True)
    primary = issuer_labels[0] if issuer_labels else {}
    positive = float(primary.get("positive_implication_probability") or 0)
    negative = float(primary.get("negative_implication_probability") or 0)
    sentiment = "mixed" if positive >= 0.5 and negative >= 0.5 else "positive" if positive >= 0.5 else "negative" if negative >= 0.5 else "neutral"
    relevance = float(primary.get("forecast_relevance_probability") or 0)
    row.update({
        "news_ai_review": relevance * 100 if primary else None,
        "news_ai_review_state": str(review.get("status") or "not_reviewed"),
        "news_ai_eligibility": "eligible" if relevance >= 0.5 else "not_eligible" if primary else "pending",
        "news_ai_sentiment": sentiment if primary else "pending",
        "news_ai_positive_probability": positive * 100 if primary else None,
        "news_ai_negative_probability": negative * 100 if primary else None,
        "news_deepfm_probability": float(funnel.get("eligible_probability") or 0) * 100 if funnel else None,
        "news_deepfm_eligibility": str(funnel.get("forecast_eligibility") or "pending"),
    })
    hypotheses = [value for value in ai_state.get("hypotheses") or [] if isinstance(value, dict)]
    hypothesis = next((value for value in hypotheses if str(value.get("ticker") or "").upper() == str(row.get("symbol") or row.get("ticker") or "").upper()), hypotheses[0] if hypotheses else {})
    prediction_payload = hypothesis.get("prediction") if isinstance(hypothesis.get("prediction"), dict) else {}
    predictions = prediction_payload.get("predictions") if isinstance(prediction_payload.get("predictions"), dict) else {}
    prediction = predictions.get("5m") if isinstance(predictions.get("5m"), dict) else {}
    row.update({
        "news_ai_reaction": float(prediction.get("expected_return_pct") or 0) if prediction else None,
        "news_ai_reaction_state": "complete" if prediction else "available" if relevance >= 0.5 else "review_first",
        "news_ai_reaction_confidence": float(prediction.get("confidence") or 0) * 100 if prediction else None,
        "news_ai_reaction_up_probability": float(prediction.get("upside_probability") or 0) * 100 if prediction else None,
        "news_ai_reaction_down_probability": float(prediction.get("downside_probability") or 0) * 100 if prediction else None,
        "news_ai_reaction_regime": str(prediction_payload.get("regime_compatibility") or "unknown"),
    })


def _latest_recency(items: list[dict[str, Any]], key: str, as_of: datetime) -> str:
    ages: list[float] = []
    for item in items:
        try:
            value = datetime.fromisoformat(str(item.get(key) or "").replace("Z", "+00:00"))
            ages.append(max(0.0, (as_of.astimezone(UTC) - value.astimezone(UTC)).total_seconds()))
        except (TypeError, ValueError):
            continue
    if not ages:
        return "none"
    age = min(ages)
    return "hot" if age <= 4 * 3600 else "cold" if age <= 24 * 3600 else "old"


def _string_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.strip("[]").replace("'", "").split(",") if item.strip()]
    return []


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _portfolio_fixture(price: float) -> dict[str, Any]:
    return {
        "fixture": True,
        "account": {"acctId": "DU0000000", "accountTitle": "Canvas preview", "type": "DEMO"},
        "summary": {"netLiquidation": 102_438.42, "availableFunds": 76_120.10, "unrealizedPnl": 842.12, "realizedPnl": 196.30},
        "positions": [
            {"acctId": "DU0000000", "conid": 265598, "ticker": "AAPL", "position": 120, "mktPrice": price, "avgCost": price - 1.42, "unrealizedPnl": 170.40},
            {"acctId": "DU0000000", "conid": 4815747, "ticker": "MSFT", "position": 35, "mktPrice": 497.18, "avgCost": 493.02, "unrealizedPnl": 145.60},
        ],
    }


def _order_fixture(price: float) -> list[dict[str, Any]]:
    return [
        {"acctId": "DU0000000", "orderId": 73101, "cOID": "preview-entry-01", "conid": 265598, "ticker": "AAPL", "side": "BUY", "orderType": "LMT", "price": round(price - 0.08, 2), "auxPrice": None, "quantity": 120, "filledQuantity": 0, "status": "Submitted", "tif": "DAY", "outsideRTH": False},
        {"acctId": "DU0000000", "orderId": 73102, "cOID": "preview-protection-01", "conid": 265598, "ticker": "AAPL", "side": "SELL", "orderType": "STP", "price": None, "auxPrice": round(price - 1.25, 2), "quantity": 120, "filledQuantity": 0, "status": "PreSubmitted", "tif": "DAY", "outsideRTH": False},
        {"acctId": "DU0000000", "orderId": 73096, "cOID": "msft-entry-02", "conid": 4815747, "ticker": "MSFT", "side": "BUY", "orderType": "MKT", "price": None, "auxPrice": None, "quantity": 35, "filledQuantity": 35, "status": "Filled", "tif": "DAY", "outsideRTH": False},
    ]
