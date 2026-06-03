from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
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


def massive_get_json(config: MarketGatewayConfig, path: str, params: dict[str, str], *, timeout: int) -> dict[str, Any]:
    if not config.massive.api_key:
        raise RuntimeError("MASSIVE_API_KEY is required for Massive REST snapshots.")
    query = {**params, "apiKey": config.massive.api_key}
    url = f"{massive_rest_base_url()}{path}?{urllib.parse.urlencode(query)}"
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
