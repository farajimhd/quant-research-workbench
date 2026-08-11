from __future__ import annotations

import json
import re
import urllib.error
from typing import Any, Iterable

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from src.backend.query_plans.market_ticker_presentation_v1 import (
    ticker_presentation as ticker_presentation_sql,
)
from src.backend.real_live_market_data.startup import logo_asset_url


MAX_PRESENTATION_TICKERS = 200
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")


def ticker_presentation_payload(tickers: Iterable[str], *, database: str = "q_live") -> dict[str, Any]:
    normalized = normalize_tickers(tickers)
    if not normalized:
        return {"presentations": {}, "source": f"{database}.market_presentation_asset_v1", "status": "ready"}
    try:
        rows = _clickhouse_rows(ticker_presentation_sql(normalized, database=database))
    except (RuntimeError, TimeoutError, urllib.error.URLError):
        # Branding is optional presentation data. Database pressure must not make
        # the ticker identity or its containing Canvas surface unavailable.
        return {"presentations": {}, "source": f"{database}.market_presentation_asset_v1", "status": "unavailable"}
    presentations: dict[str, dict[str, str]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker not in normalized:
            continue
        relative_path = str(row.get("logo_relative_path") or "").strip()
        presentations[ticker] = {
            "issuer_name": str(row.get("issuer_name") or "").strip(),
            "logo_url": logo_asset_url(relative_path),
            "ticker": ticker,
        }
    return {"presentations": presentations, "source": f"{database}.market_presentation_asset_v1", "status": "ready"}


def normalize_tickers(tickers: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for value in tickers:
        ticker = str(value or "").strip().upper()
        if not TICKER_PATTERN.fullmatch(ticker) or ticker in normalized:
            continue
        normalized.append(ticker)
        if len(normalized) >= MAX_PRESENTATION_TICKERS:
            break
    return normalized


def _clickhouse_rows(query: str) -> list[dict[str, Any]]:
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    payload = client.execute(query)
    return [json.loads(line) for line in payload.splitlines() if line.strip()]
