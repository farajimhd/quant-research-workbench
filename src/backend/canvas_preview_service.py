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
from src.backend.trading_runtime_service import historical_bar_chunk, historical_day_coverage


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
    if chart_timeframe not in {"1s", "10s", "30s", "1m", "5m", "1h"}:
        raise ValueError("chart_timeframe must be 1s, 10s, 30s, 1m, 5m, or 1h")

    offset_at_clock = max(0, int((as_of - as_of.replace(hour=4, minute=0)).total_seconds() // 60))
    chart_start = max(0, offset_at_clock - 30)
    scanner_start = max(0, offset_at_clock - 15)
    cutoff = as_of.astimezone(UTC)

    jobs: dict[str, Callable[[], Any]] = {
        "coverage": lambda: historical_day_coverage(session_date),
        "chart": lambda: historical_bar_chunk(
            anchor_date=session_date,
            ticker=symbol,
            timeframe=chart_timeframe,
            offset_minutes=chart_start,
            window_minutes=min(30, max(1, offset_at_clock - chart_start)),
        ),
        "news": lambda: _query_news(cutoff),
        "sec": lambda: _query_sec(cutoff),
        "xbrl": lambda: _query_xbrl(cutoff),
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

    chart_payload = results.get("chart", {})
    chart_bars = chart_payload.get("bars", []) if isinstance(chart_payload, dict) else []
    scanner = [
        row
        for scanner_symbol in SCANNER_SYMBOLS
        if (row := _scanner_row(scanner_symbol, results.get(f"scanner:{scanner_symbol}"))) is not None
    ]
    reference_price = float(chart_bars[-1].get("close", 100.0)) if chart_bars else 100.0

    return {
        "as_of": as_of.isoformat(),
        "coverage": results.get("coverage", {}),
        "chart": {
            "bars": chart_bars,
            "indicators": chart_payload.get("indicators", []) if isinstance(chart_payload, dict) else [],
            "symbol": symbol,
            "timeframe": chart_timeframe,
        },
        "errors": errors,
        "fills": _fill_fixture(as_of, reference_price),
        "journal": _journal_fixture(as_of),
        "news": results.get("news", []),
        "orders": _order_fixture(reference_price),
        "portfolio": _portfolio_fixture(reference_price),
        "preview_kind": "point_in_time_configuration",
        "scanner": scanner,
        "sec": results.get("sec", []),
        "strategy": _strategy_fixture(as_of, symbol, reference_price),
        "xbrl": results.get("xbrl", []),
    }


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


def _query_xbrl(cutoff: datetime) -> list[dict[str, Any]]:
    start = cutoff - timedelta(days=45)
    return _clickhouse_rows(
        f"""
        SELECT cik, taxonomy, tag, unit_code, fiscal_year, fiscal_period, value, form_type, accession_number,
            formatDateTime(filed_at_raw, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS filed_at_utc
        FROM
        (
            SELECT cik, taxonomy, tag, unit_code, fiscal_year, fiscal_period, value, form_type, accession_number,
                filed_at_utc AS filed_at_raw
            FROM q_live.sec_xbrl_company_fact_v3
            WHERE filed_at_utc BETWEEN toDateTime64({_utc_sql(start)}, 3, 'UTC')
                AND toDateTime64({_utc_sql(cutoff)}, 3, 'UTC')
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
    first_open = float(first.get("open") or 0)
    last_close = float(last.get("close") or 0)
    return {
        "symbol": symbol,
        "last": last_close,
        "change_pct": ((last_close / first_open) - 1) * 100 if first_open else 0,
        "volume": sum(int(bar.get("volume") or 0) for bar in bars),
        "trade_count": sum(int(bar.get("trade_count") or 0) for bar in bars),
        "quote_count": sum(int(bar.get("quote_count") or 0) for bar in bars),
    }


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
        {"acctId": "DU0000000", "executionId": "0000.0001.01", "orderId": 73096, "conid": 4815747, "ticker": "MSFT", "side": "BOT", "shares": 35, "price": 497.18, "commission": 0.35, "time": (as_of - timedelta(minutes=9)).isoformat()},
        {"acctId": "DU0000000", "executionId": "0000.0002.01", "orderId": 73091, "conid": 265598, "ticker": "AAPL", "side": "SLD", "shares": 40, "price": round(price - 0.34, 2), "commission": 0.40, "time": (as_of - timedelta(minutes=4)).isoformat()},
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
