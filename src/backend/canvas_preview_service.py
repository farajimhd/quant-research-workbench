from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import UTC, date, datetime
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
from src.trading_runtime.watchlist_resolver import classify_watchlist_row


NEW_YORK = ZoneInfo("America/New_York")
def canvas_preview_payload(
    *,
    session_date: date,
    preview_time: str = "09:45",
    chart_symbol: str = "AAPL",
    chart_timeframe: str = "1m",
) -> dict[str, Any]:
    as_of = _as_of(session_date, preview_time)
    symbol = chart_symbol.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
        raise ValueError("chart_symbol must be a valid ticker")
    if chart_timeframe not in SUPPORTED_HISTORICAL_TIMEFRAMES:
        raise ValueError(f"chart_timeframe must be one of {', '.join(sorted(SUPPORTED_HISTORICAL_TIMEFRAMES))}")

    cutoff = as_of.astimezone(UTC)

    jobs: dict[str, Callable[[], Any]] = {
        "coverage": lambda: historical_day_coverage(session_date),
        "news": lambda: _query_news(cutoff),
        "scanner": lambda: historical_scanner_snapshot(as_of, lookback_minutes=15),
        "sec": lambda: _query_sec(cutoff),
    }

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
        "status": "unavailable",
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
    lookback_minutes: int = 15,
    technical_windows: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return the causal cross-sectional scanner independently of other Canvas sources."""
    if enrichment_scope not in {"core", "full"}:
        raise ValueError("enrichment_scope must be core or full")
    from src.backend.discovery_projection import (
        configured_discovery_technical_windows,
        project_discovery_columns,
    )
    from src.backend.trading_configuration_service import configuration_base
    from src.backend.data_field_contracts import (
        project_composition_data_field_columns,
        project_data_field_outputs,
    )

    configuration = configuration_base()
    technical_windows = tuple(sorted({
        *technical_windows,
        *configured_discovery_technical_windows(configuration),
    }))
    rows, meta = historical_scanner_snapshot(as_of, lookback_minutes=lookback_minutes)
    effective_as_of = datetime.fromisoformat(
        str(meta.get("snapshot_at_utc") or as_of.isoformat()).replace("Z", "+00:00")
    )
    if effective_as_of.tzinfo is None:
        effective_as_of = effective_as_of.replace(tzinfo=UTC)
    effective_as_of = effective_as_of.astimezone(UTC)
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
    if technical_windows:
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
    if rows:
        enrichment_names = ["reference"] if enrichment_scope == "core" else ["fundamentals", "news", "qmd", "reference", "sec"]
    with ThreadPoolExecutor(max_workers=max(1, len(enrichment_names))) as executor:
        futures = {}
        if "fundamentals" in enrichment_names:
            futures["fundamentals"] = executor.submit(
                historical_scanner_fundamental_projection,
                effective_as_of,
                prices_by_ticker=prices_by_ticker,
            )
        if "news" in enrichment_names:
            futures["news"] = executor.submit(_query_scanner_news_intelligence, cutoff)
        if "qmd" in enrichment_names:
            futures["qmd"] = executor.submit(
                historical_scanner_qmd_projection_or_schedule,
                effective_as_of,
                source_revision=str(meta.get("source_revision") or ""),
            )
        if "reference" in enrichment_names:
            futures["reference"] = executor.submit(historical_scanner_reference_projection, effective_as_of)
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
    rows = project_discovery_columns(
        (classify_watchlist_row(row) for row in rows),
    )
    rows = project_data_field_outputs(
        rows,
        list(dict(configuration.get("market_discovery") or {}).get("data_fields") or []),
    )
    discovery = dict(configuration.get("market_discovery") or {})
    rows = project_composition_data_field_columns(
        rows,
        dict(discovery.get("core_scan") or {}),
        discovery.get("column_catalog") or [],
    )
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
    total = max(1, len(rows))
    meta = {
        **meta,
        "enrichment_scope": enrichment_scope,
        "enrichment_status": "ready" if enrichment_scope == "full" else "partial",
        "included_enrichments": enrichment_names,
        "field_coverage": {
            field: round(sum(row.get(field) not in (None, "") for row in rows) / total * 100, 1)
            for field in projected_fields
        },
    }
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
    try:
        from src.backend.signal_stream_runtime_service import SIGNAL_STREAM_RUNTIME
        from src.backend.trading_runtime_service import trading_journal

        signal_stream_runtime = SIGNAL_STREAM_RUNTIME.snapshot(
            trading_journal(),
            as_of=effective_as_of,
            limit=10_000,
        )
        signal_rows = signal_stream_runtime.get("occurrences") or []
    except Exception as exc:
        errors["signal_stream"] = str(exc)
        signal_stream_runtime = {
            "as_of": effective_as_of.isoformat(),
            "error": str(exc),
            "status": "error",
            "occurrences": [],
        }
        signal_rows = []
    return {
        "as_of": effective_as_of.isoformat(),
        "errors": errors,
        "feature_projection": compact_feature_projection(
            rows,
            as_of=effective_as_of,
            source_revision=str(meta.get("source_revision") or ""),
            source_schema_version=str(meta.get("schema_version") or "1"),
        ),
        "meta": meta,
        "rows": rows,
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
    payload = client.execute(query.strip().rstrip(";") + "\nFORMAT JSONEachRow")
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
    """Return one causal company-news summary per ticker for scanner enrichment."""
    return _clickhouse_rows(
        canvas_context_v1.scanner_company_news(
            cutoff, engine_version=ENGINE_VERSION, synthesis_table=SYNTHESIS_TABLE
        )
    )


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
    news_by_ticker = {
        str(item.get("ticker") or "").strip().upper(): item
        for item in news if isinstance(news, list) and isinstance(item, dict) and item.get("ticker")
    }
    sec_by_ticker = {
        str(item.get("ticker") or "").strip().upper(): item
        for item in sec if isinstance(sec, list) and isinstance(item, dict) and item.get("ticker")
    }
    for row in scanner:
        ticker = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        news_item = news_by_ticker.get(ticker, {})
        sec_item = sec_by_ticker.get(ticker, {})
        row["live_news_count"] = int(news_item.get("live_news_count") or 0)
        row["live_news_recency"] = _latest_recency(
            [{"published_at_utc": news_item.get("latest_news_at")}], "published_at_utc", as_of
        )
        row["news_labels"] = ", ".join(sorted(set(_string_values(news_item.get("news_labels")))))
        row["sec_count"] = int(sec_item.get("sec_count") or 0)
        row["sec_recency"] = _latest_recency(
            [{"accepted_at_utc": sec_item.get("latest_sec_at")}], "accepted_at_utc", as_of
        )
        row["sec_labels"] = ", ".join(sorted(set(_string_values(sec_item.get("sec_labels")))))


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
