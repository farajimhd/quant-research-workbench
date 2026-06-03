from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import polars as pl

from src.backend.real_live_market_data.config import MarketGatewayConfig, env_first, load_real_live_market_env


DEFAULT_MASSIVE_REST_URL = "https://api.massive.com"


def fetch_massive_stock_snapshot_frame(config: MarketGatewayConfig, *, timeout: int = 30) -> pl.DataFrame:
    payload = massive_get_json(config, "/v2/snapshot/locale/us/markets/stocks/tickers", {}, timeout=timeout)
    tickers = payload.get("tickers") or payload.get("results") or []
    rows = [normalize_massive_snapshot_row(item) for item in tickers if isinstance(item, dict)]
    rows = [row for row in rows if row.get("snapshot_ticker")]
    return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()


def fetch_massive_scanner_enrichment_frame(
    config: MarketGatewayConfig,
    tickers: list[str],
    *,
    timeout: int = 45,
) -> pl.DataFrame:
    ticker_set = {ticker.upper() for ticker in tickers if ticker}
    if not ticker_set:
        return pl.DataFrame()
    with ThreadPoolExecutor(max_workers=3) as executor:
        float_future = executor.submit(
            fetch_massive_paginated_results,
            config,
            env_first("REAL_LIVE_MASSIVE_FLOAT_PATH", default="/stocks/vX/float"),
            {"limit": "5000", "sort": "ticker.asc"},
            timeout=timeout,
            max_pages=10,
        )
        short_interest_future = executor.submit(
            fetch_massive_paginated_results,
            config,
            "/stocks/v1/short-interest",
            {"limit": "50000", "sort": "settlement_date.desc"},
            timeout=timeout,
            max_pages=5,
        )
        short_volume_future = executor.submit(
            fetch_massive_paginated_results,
            config,
            "/stocks/v1/short-volume",
            {"limit": "50000", "sort": "date.desc"},
            timeout=timeout,
            max_pages=5,
        )
        float_rows = latest_by_ticker(float_future.result(), ticker_set, date_key="effective_date")
        short_interest_rows = latest_by_ticker(short_interest_future.result(), ticker_set, date_key="settlement_date")
        short_volume_rows = latest_by_ticker(short_volume_future.result(), ticker_set, date_key="date")
    rows = []
    for ticker in sorted(ticker_set):
        float_row = float_rows.get(ticker, {})
        short_interest_row = short_interest_rows.get(ticker, {})
        short_volume_row = short_volume_rows.get(ticker, {})
        rows.append(
            {
                "candidate_massive_ticker": ticker,
                "massive_float": optional_float(float_row.get("free_float")),
                "massive_float_percent": optional_float(float_row.get("free_float_percent")),
                "massive_float_date": float_row.get("effective_date") or "",
                "massive_short_interest": optional_float(short_interest_row.get("short_interest")),
                "massive_short_interest_date": short_interest_row.get("settlement_date") or "",
                "massive_days_to_cover": optional_float(short_interest_row.get("days_to_cover")),
                "massive_short_interest_avg_daily_volume": optional_float(short_interest_row.get("avg_daily_volume")),
                "massive_short_volume": optional_float(short_volume_row.get("short_volume")),
                "massive_short_volume_date": short_volume_row.get("date") or "",
                "massive_short_volume_ratio": optional_float(short_volume_row.get("short_volume_ratio")),
                "massive_short_volume_total_volume": optional_float(short_volume_row.get("total_volume")),
                "massive_float_raw": json.dumps(float_row, separators=(",", ":"), default=str) if float_row else "",
                "massive_short_interest_raw": json.dumps(short_interest_row, separators=(",", ":"), default=str) if short_interest_row else "",
                "massive_short_volume_raw": json.dumps(short_volume_row, separators=(",", ":"), default=str) if short_volume_row else "",
            }
        )
    return pl.DataFrame(rows, infer_schema_length=None)


def fetch_massive_paginated_results(
    config: MarketGatewayConfig,
    path: str,
    params: dict[str, str],
    *,
    timeout: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    next_url = ""
    for _ in range(max(1, max_pages)):
        payload = massive_get_json(config, path, params, timeout=timeout, absolute_url=next_url or None)
        page = payload.get("results") or payload.get("tickers") or []
        results.extend(item for item in page if isinstance(item, dict))
        next_url = str(payload.get("next_url") or "")
        if not next_url:
            break
    return results


def latest_by_ticker(rows: list[dict[str, Any]], tickers: set[str], *, date_key: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        if ticker not in tickers:
            continue
        current = latest.get(ticker)
        if current is None or str(row.get(date_key) or "") >= str(current.get(date_key) or ""):
            latest[ticker] = row
    return latest


def massive_get_json(
    config: MarketGatewayConfig,
    path: str,
    params: dict[str, str],
    *,
    timeout: int,
    absolute_url: str | None = None,
) -> dict[str, Any]:
    if not config.massive.api_key:
        raise RuntimeError("MASSIVE_API_KEY is required for Massive REST requests.")
    if absolute_url:
        separator = "&" if "?" in absolute_url else "?"
        url = absolute_url if "apiKey=" in absolute_url else f"{absolute_url}{separator}{urllib.parse.urlencode({'apiKey': config.massive.api_key})}"
    else:
        query = {**params, "apiKey": config.massive.api_key}
        url = f"{massive_rest_base_url()}{path}?{urllib.parse.urlencode(query)}"
    return massive_get_url(url, timeout=timeout)


def massive_get_url(url: str, *, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        body = response.read().decode("utf-8", errors="replace")
    payload = json.loads(body)
    return payload if isinstance(payload, dict) else {"results": payload}


def massive_rest_base_url() -> str:
    load_real_live_market_env()
    return env_first("REAL_LIVE_MASSIVE_REST_URL", "MASSIVE_BASE_URL", default=DEFAULT_MASSIVE_REST_URL).rstrip("/")


def normalize_massive_snapshot_row(item: dict[str, Any]) -> dict[str, Any]:
    day = item.get("day") or {}
    prev_day = item.get("prevDay") or item.get("prev_day") or {}
    trade = item.get("lastTrade") or item.get("last_trade") or {}
    quote = item.get("lastQuote") or item.get("last_quote") or {}
    ticker = str(item.get("ticker") or item.get("T") or "").upper()
    last_price = float_value(trade.get("p") or item.get("lastTradePrice") or day.get("c"))
    bid = optional_float(quote.get("p") or quote.get("bp"))
    ask = optional_float(quote.get("P") or quote.get("ap"))
    spread_bps = ((ask - bid) / last_price * 10_000) if ask is not None and bid is not None and ask >= bid and bid > 0 and last_price > 0 else None
    return {
        "snapshot_ticker": ticker,
        "snapshot_last_price": last_price or None,
        "snapshot_day_open": optional_float(day.get("o")),
        "snapshot_day_high": optional_float(day.get("h")),
        "snapshot_day_low": optional_float(day.get("l")),
        "snapshot_day_close": optional_float(day.get("c")),
        "snapshot_day_volume": optional_float(day.get("v")),
        "snapshot_trade_count": optional_float(day.get("n")),
        "snapshot_prev_day_close": optional_float(prev_day.get("c")),
        "snapshot_prev_day_volume": optional_float(prev_day.get("v")),
        "snapshot_bid": bid,
        "snapshot_ask": ask,
        "snapshot_spread_bps": spread_bps,
        "snapshot_todays_change": optional_float(item.get("todaysChange")),
        "snapshot_todays_change_pct": optional_float(item.get("todaysChangePerc")),
        "snapshot_updated": item.get("updated") or item.get("updated_at") or "",
        "snapshot_raw": json.dumps(item, separators=(",", ":"), default=str),
    }


def optional_float(value: Any) -> float | None:
    numeric = float_value(value)
    return numeric if numeric != 0 else None


def float_value(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if numeric == numeric else 0.0
