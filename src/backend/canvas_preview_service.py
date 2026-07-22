from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    sql_string,
)
from src.backend.trading_runtime_service import (
    SUPPORTED_HISTORICAL_TIMEFRAMES,
    historical_bar_chunk,
    historical_day_coverage,
)
from src.backend.canonical_trading_service import trading_state_payload
from src.trading_runtime.domain import BrokerAccount, BrokerEventEnvelope, BrokerEventType, BrokerProvider, TradingMode
from src.trading_runtime.ibkr_normalizer import normalize_account_values, normalize_execution, normalize_ledger, normalize_order, normalize_position_snapshot
from src.trading_runtime.projector import TradingStateProjector
from src.trading_runtime.round_trips import derive_round_trip_trades


NEW_YORK = ZoneInfo("America/New_York")
SCANNER_SYMBOLS = ("AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META")


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

    offset_at_clock = max(0, int((as_of - as_of.replace(hour=4, minute=0)).total_seconds() // 60))
    scanner_start = max(0, offset_at_clock - 15)
    cutoff = as_of.astimezone(UTC)

    jobs: dict[str, Callable[[], Any]] = {
        "coverage": lambda: historical_day_coverage(session_date),
        "news": lambda: _query_news(cutoff),
        "sec": lambda: _query_sec(cutoff),
        "xbrl": lambda: _query_xbrl(cutoff, symbol),
    }
    for scanner_symbol in SCANNER_SYMBOLS:
        jobs[f"scanner:{scanner_symbol}"] = lambda ticker=scanner_symbol: historical_bar_chunk(
            anchor_date=session_date,
            ticker=ticker,
            timeframe="1m",
            offset_minutes=scanner_start,
            window_minutes=min(15, max(1, offset_at_clock - scanner_start)),
        )

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

    scanner = [
        row
        for scanner_symbol in SCANNER_SYMBOLS
        if (row := _scanner_row(scanner_symbol, results.get(f"scanner:{scanner_symbol}"))) is not None
    ]
    _enrich_scanner_intelligence(scanner, results.get("news", []), results.get("sec", []), as_of)
    scanner.sort(key=lambda row: (-abs(float(row.get("change_5m_pct") or 0)), str(row.get("symbol") or "")))
    for rank, row in enumerate(scanner, start=1):
        row["rank"] = rank
    reference_price = float(scanner[0].get("last", 100.0)) if scanner else 100.0

    portfolio_fixture = _portfolio_fixture(reference_price)
    order_fixture = _order_fixture(reference_price)
    fill_fixture = _fill_fixture(as_of, reference_price)
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
        "journal": _journal_fixture(as_of),
        "news": results.get("news", []),
        "orders": order_fixture,
        "portfolio": portfolio_fixture,
        "preview_kind": "point_in_time_configuration",
        "scanner": scanner,
        "sec": results.get("sec", []),
        "strategy": _strategy_fixture(as_of, symbol, reference_price),
        "trading": _canonical_trading_fixture(as_of, portfolio_fixture, order_fixture, fill_fixture),
        "xbrl": results.get("xbrl", []),
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


def _utc_sql(value: datetime) -> str:
    return sql_string(value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])


def _query_news(cutoff: datetime) -> list[dict[str, Any]]:
    start = cutoff - timedelta(days=3)
    return _clickhouse_rows(
        f"""
        SELECT
            canonical_news_id,
            formatDateTime(published_at_raw, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS published_at_utc,
            title, teaser, tickers, channels
        FROM
        (
            SELECT canonical_news_id, published_at_utc AS published_at_raw, title, teaser, tickers, channels
            FROM q_live.benzinga_news_normalized_v1 FINAL
            WHERE published_at_utc BETWEEN toDateTime64({_utc_sql(start)}, 3, 'UTC')
                AND toDateTime64({_utc_sql(cutoff)}, 3, 'UTC')
            ORDER BY published_at_utc DESC
            LIMIT 30
        )
        ORDER BY published_at_raw DESC
        LIMIT 30
        """
    )


def _query_sec(cutoff: datetime) -> list[dict[str, Any]]:
    start = cutoff - timedelta(days=45)
    return _clickhouse_rows(
        f"""
        SELECT cik, accession_number, company_name, form_type,
            formatDateTime(accepted_at_raw, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS accepted_at_utc
        FROM
        (
            SELECT cik, accession_number, company_name, form_type, accepted_at_utc AS accepted_at_raw
            FROM q_live.sec_filing_v3 FINAL
            WHERE accepted_at_utc BETWEEN toDateTime64({_utc_sql(start)}, 3, 'UTC')
                AND toDateTime64({_utc_sql(cutoff)}, 3, 'UTC')
            ORDER BY accepted_at_utc DESC
            LIMIT 30
        )
        ORDER BY accepted_at_raw DESC
        LIMIT 30
        """
    )


def _attach_sec_tickers(rows: Any) -> None:
    if not isinstance(rows, list):
        return
    ciks = sorted({str(row.get("cik") or "").strip() for row in rows if isinstance(row, dict) and row.get("cik")})
    if not ciks:
        return
    values = ", ".join(sql_string(cik) for cik in ciks)
    identities = _clickhouse_rows(
        f"""
        SELECT toString(cik) AS cik, argMax(upper(ticker), confidence_score) AS mapped_ticker
        FROM q_live.id_sec_market_bridge_v3 FINAL
        WHERE toString(cik) IN ({values}) AND notEmpty(ticker)
        GROUP BY cik
        """
    )
    ticker_by_cik = {str(row.get("cik") or ""): str(row.get("mapped_ticker") or "").upper() for row in identities}
    for row in rows:
        if isinstance(row, dict):
            row["ticker"] = ticker_by_cik.get(str(row.get("cik") or ""), "")


def _query_xbrl(cutoff: datetime, symbol: str) -> list[dict[str, Any]]:
    # A symbol's latest periodic filing can be several months old; keep one annual
    # cycle while still bounding the point-in-time preview query.
    start = cutoff - timedelta(days=400)
    ticker = sql_string(symbol)
    return _clickhouse_rows(
        f"""
        SELECT cik, taxonomy, tag, unit_code, fiscal_year, fiscal_period, value, form_type, accession_number,
            formatDateTime(filed_at_raw, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS filed_at_utc
        FROM
        (
            SELECT cik, taxonomy, tag, unit_code, fiscal_year, fiscal_period, value, form_type, accession_number,
                filed_at_utc AS filed_at_raw
            FROM q_live.sec_xbrl_company_fact_v3 FINAL
            WHERE filed_at_utc BETWEEN toDateTime64({_utc_sql(start)}, 3, 'UTC')
                AND toDateTime64({_utc_sql(cutoff)}, 3, 'UTC')
                AND cik IN
                (
                    SELECT DISTINCT cik
                    FROM q_live.id_sec_market_bridge_v3 FINAL
                    WHERE upper(ticker) = {ticker}
                      AND (valid_from_date IS NULL OR valid_from_date <= toDate({_utc_sql(cutoff)}))
                      AND (valid_to_date_exclusive IS NULL OR toDate({_utc_sql(cutoff)}) < valid_to_date_exclusive)
                )
            ORDER BY filed_at_utc DESC
            LIMIT 30
        )
        ORDER BY filed_at_raw DESC
        LIMIT 30
        """
    )


def _scanner_row(symbol: str, payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not payload.get("bars"):
        return None
    bars = payload["bars"]
    first, last = bars[0], bars[-1]
    five_minute_start = bars[max(0, len(bars) - 5)]
    first_open = float(first.get("open") or 0)
    last_close = float(last.get("close") or 0)
    return {
        "symbol": symbol,
        "last": last_close,
        "change_pct": ((last_close / first_open) - 1) * 100 if first_open else 0,
        "change_5m_pct": ((last_close / float(five_minute_start.get("open") or 0)) - 1) * 100 if float(five_minute_start.get("open") or 0) else 0,
        "volume": sum(int(bar.get("volume") or 0) for bar in bars),
        "trade_count": sum(int(bar.get("trade_count") or 0) for bar in bars),
        "quote_count": sum(int(bar.get("quote_count") or 0) for bar in bars),
    }


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
        ticker_news = news_by_ticker.get(ticker, [])
        ticker_sec = sec_by_ticker.get(ticker, [])
        row["live_news_count"] = len(ticker_news)
        row["live_news_recency"] = _latest_recency(ticker_news, "published_at_utc", as_of)
        row["news_labels"] = ", ".join(sorted({label for item in ticker_news for label in _string_values(item.get("channels"))}))
        row["sec_count"] = len(ticker_sec)
        row["sec_recency"] = _latest_recency(ticker_sec, "accepted_at_utc", as_of)
        row["sec_labels"] = ", ".join(sorted({str(item.get("form_type") or "") for item in ticker_sec if item.get("form_type")}))


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
        {"acctId": "DU0000000", "orderId": 73101, "cOID": "orb-entry-01", "conid": 265598, "ticker": "AAPL", "side": "BUY", "orderType": "LMT", "price": round(price - 0.08, 2), "auxPrice": None, "quantity": 120, "filledQuantity": 0, "status": "Submitted", "tif": "DAY", "outsideRTH": False},
        {"acctId": "DU0000000", "orderId": 73102, "cOID": "orb-stop-01", "conid": 265598, "ticker": "AAPL", "side": "SELL", "orderType": "STP", "price": None, "auxPrice": round(price - 1.25, 2), "quantity": 120, "filledQuantity": 0, "status": "PreSubmitted", "tif": "DAY", "outsideRTH": False},
        {"acctId": "DU0000000", "orderId": 73096, "cOID": "msft-entry-02", "conid": 4815747, "ticker": "MSFT", "side": "BUY", "orderType": "MKT", "price": None, "auxPrice": None, "quantity": 35, "filledQuantity": 35, "status": "Filled", "tif": "DAY", "outsideRTH": False},
    ]


def _fill_fixture(as_of: datetime, price: float) -> list[dict[str, Any]]:
    return [
        {"acctId": "DU0000000", "executionId": "0000.0001.01", "orderId": 73081, "conid": 76792991, "ticker": "NVDA", "side": "BOT", "shares": 80, "price": 172.10, "commission": 0.40, "time": (as_of - timedelta(minutes=42)).isoformat(), "strategy_id": "opening-range-breakout", "strategy_revision": 4, "run_id": "preview-run", "setup": "Opening drive", "planned_risk": 72.0, "signal_price": 172.04, "arrival_midpoint": 172.08},
        {"acctId": "DU0000000", "executionId": "0000.0002.01", "orderId": 73082, "conid": 76792991, "ticker": "NVDA", "side": "SLD", "shares": 80, "price": 173.22, "commission": 0.40, "time": (as_of - timedelta(minutes=35)).isoformat(), "exit_reason": "target"},
        {"acctId": "DU0000000", "executionId": "0000.0003.01", "orderId": 73083, "conid": 265598, "ticker": "AAPL", "side": "BOT", "shares": 120, "price": round(price - 0.82, 2), "commission": 0.60, "time": (as_of - timedelta(minutes=31)).isoformat(), "strategy_id": "opening-range-breakout", "strategy_revision": 4, "run_id": "preview-run", "setup": "Range break", "planned_risk": 96.0, "signal_price": round(price - 0.85, 2), "arrival_midpoint": round(price - 0.83, 2)},
        {"acctId": "DU0000000", "executionId": "0000.0004.01", "orderId": 73084, "conid": 265598, "ticker": "AAPL", "side": "SLD", "shares": 120, "price": round(price - 1.31, 2), "commission": 0.60, "time": (as_of - timedelta(minutes=25)).isoformat(), "exit_reason": "stop"},
        {"acctId": "DU0000000", "executionId": "0000.0005.01", "orderId": 73085, "conid": 272093, "ticker": "TSLA", "side": "SLD", "shares": 50, "price": 321.84, "commission": 0.30, "time": (as_of - timedelta(minutes=20)).isoformat(), "strategy_id": "liquidity-reversal", "strategy_revision": 2, "run_id": "preview-run", "setup": "Failed breakout", "planned_risk": 62.5, "signal_price": 321.88, "arrival_midpoint": 321.86},
        {"acctId": "DU0000000", "executionId": "0000.0006.01", "orderId": 73086, "conid": 272093, "ticker": "TSLA", "side": "BOT", "shares": 50, "price": 320.72, "commission": 0.30, "time": (as_of - timedelta(minutes=13)).isoformat(), "exit_reason": "liquidity_target"},
        {"acctId": "DU0000000", "executionId": "0000.0007.01", "orderId": 73096, "conid": 4815747, "ticker": "MSFT", "side": "BOT", "shares": 35, "price": 497.18, "commission": 0.35, "time": (as_of - timedelta(minutes=9)).isoformat(), "strategy_id": "opening-range-breakout", "strategy_revision": 4, "run_id": "preview-run", "setup": "Continuation"},
    ]


def _strategy_fixture(as_of: datetime, symbol: str, price: float) -> dict[str, Any]:
    return {
        "fixture": True,
        "strategy_id": "opening-range-breakout",
        "revision": 4,
        "automatic": True,
        "state": "monitoring",
        "signals": [
            {"time": (as_of - timedelta(minutes=7)).isoformat(), "symbol": symbol, "signal": "range_set", "value": round(price - 0.52, 2)},
            {"time": (as_of - timedelta(minutes=2)).isoformat(), "symbol": symbol, "signal": "breakout_watch", "value": round(price, 2)},
        ],
    }


def _journal_fixture(as_of: datetime) -> list[dict[str, Any]]:
    return [
        {"time": (as_of - timedelta(minutes=15)).isoformat(), "category": "runtime", "event": "SESSION_STARTED", "detail": "Historical stream positioned at 09:30 ET"},
        {"time": (as_of - timedelta(minutes=7)).isoformat(), "category": "strategy", "event": "SIGNAL_EMITTED", "detail": "Opening range established"},
        {"time": (as_of - timedelta(minutes=4)).isoformat(), "category": "broker", "event": "EXECUTION", "detail": "Partial fill applied to account and portfolio"},
        {"time": as_of.isoformat(), "category": "checkpoint", "event": "STATE_SNAPSHOT", "detail": "Point-in-time state persisted"},
    ]
