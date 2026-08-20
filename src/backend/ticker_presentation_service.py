from __future__ import annotations

import json
import re
import urllib.error
from datetime import UTC, datetime
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
from src.backend.query_plans.canvas_context_v1 import (
    scanner_sec_filings,
    ticker_news_recency,
)
from src.request_context import ContextThreadPoolExecutor as ThreadPoolExecutor
from src.backend.real_live_market_data.startup import logo_asset_url


MAX_PRESENTATION_TICKERS = 200
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")


def ticker_presentation_payload(
    tickers: Iterable[str],
    *,
    database: str = "q_live",
    include_recency: bool = False,
) -> dict[str, Any]:
    normalized = normalize_tickers(tickers)
    if not normalized:
        return {"presentations": {}, "source": f"{database}.market_presentation_asset_v1", "status": "ready"}
    try:
        if include_recency:
            cutoff = datetime.now(UTC)
            with ThreadPoolExecutor(max_workers=3) as executor:
                presentation_future = executor.submit(
                    _clickhouse_rows,
                    ticker_presentation_sql(normalized, database=database),
                )
                news_future = executor.submit(
                    _clickhouse_rows,
                    ticker_news_recency(
                        cutoff,
                        tickers=normalized,
                    ),
                )
                sec_future = executor.submit(
                    _clickhouse_rows,
                    scanner_sec_filings(cutoff, tickers=normalized),
                )
                rows = presentation_future.result()
                news_rows = _optional_rows(news_future)
                sec_rows = _optional_rows(sec_future)
        else:
            cutoff = datetime.now(UTC)
            rows = _clickhouse_rows(ticker_presentation_sql(normalized, database=database))
            news_rows = []
            sec_rows = []
    except (RuntimeError, TimeoutError, urllib.error.URLError):
        # Branding is optional presentation data. Database pressure must not make
        # the ticker identity or its containing Canvas surface unavailable.
        return {"presentations": {}, "source": f"{database}.market_presentation_asset_v1", "status": "unavailable"}
    news_by_ticker = {
        str(row.get("ticker") or "").strip().upper(): row for row in news_rows
    }
    sec_by_ticker = {
        str(row.get("ticker") or "").strip().upper(): row for row in sec_rows
    }
    presentations: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker not in normalized:
            continue
        relative_path = str(row.get("logo_relative_path") or "").strip()
        presentations[ticker] = {
            "country": str(row.get("country") or "").strip().upper(),
            "issuer_name": str(row.get("issuer_name") or "").strip(),
            "logo_url": logo_asset_url(relative_path),
            "live_news_recency": _recency(
                news_by_ticker.get(ticker, {}).get("latest_news_at"), cutoff
            ),
            "sec_recency": _recency(
                sec_by_ticker.get(ticker, {}).get("latest_sec_at"), cutoff
            ),
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
    normalized = query.strip().rstrip(";")
    if "FORMAT JSONEachRow" not in normalized:
        normalized += "\nFORMAT JSONEachRow"
    payload = client.execute(normalized)
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


def _optional_rows(future: Any) -> list[dict[str, Any]]:
    """Keep optional recency unavailable from suppressing core ticker identity."""

    try:
        return future.result()
    except (json.JSONDecodeError, RuntimeError, TimeoutError, urllib.error.URLError):
        return []


def _recency(value: Any, cutoff: datetime) -> str:
    if value in (None, ""):
        return "none"
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "none"
    observed = observed.replace(tzinfo=UTC) if observed.tzinfo is None else observed.astimezone(UTC)
    age_hours = max(0.0, (cutoff - observed).total_seconds() / 3600.0)
    return "hot" if age_hours <= 4 else "cold" if age_hours <= 24 else "old"
