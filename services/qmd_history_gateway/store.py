from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from src.market_engine.events import MarketEvent, QuoteEvent, TradeEvent


@dataclass(frozen=True, slots=True)
class HistoricalStoreConfig:
    endpoint_url: str
    database: str = "market_sip_compact"
    table_prefix: str = "events_"
    user: str = "default"
    password: str = ""
    timeout: float = 30.0


@dataclass(frozen=True, slots=True)
class HistoricalCursor:
    sip_timestamp_us: int = 0
    ticker: str = ""
    ordinal: int = 0

    def token(self) -> str:
        return f"{self.sip_timestamp_us}:{self.ticker}:{self.ordinal}"


class HistoricalEventStore:
    def __init__(self, config: HistoricalStoreConfig) -> None:
        self.config = config
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", config.database):
            raise ValueError("Invalid ClickHouse database")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", config.table_prefix):
            raise ValueError("Invalid ClickHouse table prefix")

    def health(self) -> dict[str, Any]:
        value = self._query("SELECT 1 AS ok FORMAT JSONEachRow")
        return {"ready": bool(value and value[0].get("ok") == 1), "database": self.config.database, "source": "historical_compact_events"}

    def fetch_batch(
        self,
        *,
        start: datetime,
        end: datetime,
        tickers: list[str] | None = None,
        cursor: HistoricalCursor | None = None,
        limit: int = 10_000,
    ) -> tuple[list[dict[str, Any]], HistoricalCursor | None]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Historical event boundaries must be timezone-aware")
        if end <= start:
            raise ValueError("end must be later than start")
        if not 1 <= limit <= 100_000:
            raise ValueError("limit must be between 1 and 100000")
        tables = self._tables(start.date(), (end.date()))
        ticker_filter = ""
        if tickers:
            normalized = sorted({_ticker(ticker) for ticker in tickers})
            ticker_filter = " AND ticker IN (" + ",".join(_literal(ticker) for ticker in normalized) + ")"
        cursor_filter = ""
        if cursor and cursor.sip_timestamp_us:
            cursor_filter = (
                " AND tuple(sip_timestamp_us, ticker, ordinal) > tuple("
                f"{cursor.sip_timestamp_us}, {_literal(cursor.ticker)}, {cursor.ordinal})"
            )
        start_us = int(start.astimezone(timezone.utc).timestamp() * 1_000_000)
        end_us = int(end.astimezone(timezone.utc).timestamp() * 1_000_000)
        selects = [
            f"""SELECT ticker, ordinal, event_meta, sip_timestamp_us, price_primary_int,
                       price_secondary_int, size_primary, size_secondary, exchange_primary,
                       exchange_secondary, condition_token_1, condition_token_2,
                       condition_token_3, condition_token_4, condition_token_5, event_date
                FROM {table}
                PREWHERE sip_timestamp_us >= {start_us} AND sip_timestamp_us < {end_us}
                WHERE 1{ticker_filter}{cursor_filter}"""
            for table in tables
        ]
        query = (
            "SELECT * FROM (" + " UNION ALL ".join(selects) + ") "
            "ORDER BY sip_timestamp_us, ticker, ordinal LIMIT " + str(limit) + " FORMAT JSONEachRow"
        )
        rows = self._query(query)
        next_cursor = None
        if rows:
            last = rows[-1]
            next_cursor = HistoricalCursor(int(last["sip_timestamp_us"]), str(last["ticker"]), int(last["ordinal"]))
        return rows, next_cursor

    def compact_events(
        self,
        *,
        ticker: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows, _ = self.fetch_batch(start=start, end=end, tickers=[ticker], limit=limit)
        return [historical_to_live_compact(row) for row in rows]

    def _tables(self, start: date, end: date) -> list[str]:
        return [f"{self.config.database}.{self.config.table_prefix}{year}" for year in range(start.year, end.year + 1)]

    def _query(self, query: str) -> list[dict[str, Any]]:
        url = f"{self.config.endpoint_url.rstrip('/')}?{urllib.parse.urlencode({'query': query})}"
        request = urllib.request.Request(url, method="POST")
        request.add_header("X-ClickHouse-User", self.config.user)
        request.add_header("X-ClickHouse-Key", self.config.password)
        with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
            return [json.loads(line) for raw in response for line in [raw.decode("utf-8").strip()] if line]


def historical_to_live_compact(row: dict[str, Any]) -> dict[str, Any]:
    sip_us = int(row["sip_timestamp_us"])
    return {
        "arrival_sequence": int(row["ordinal"]),
        "condition_token_1": int(row.get("condition_token_1", 0)),
        "condition_token_2": int(row.get("condition_token_2", 0)),
        "condition_token_3": int(row.get("condition_token_3", 0)),
        "condition_token_4": int(row.get("condition_token_4", 0)),
        "condition_token_5": int(row.get("condition_token_5", 0)),
        "event_date": str(row["event_date"]),
        "event_meta": int(row["event_meta"]),
        "exchange_primary": int(row["exchange_primary"]),
        "exchange_secondary": int(row["exchange_secondary"]),
        "ingest_ts": datetime.fromtimestamp(sip_us / 1_000_000, tz=timezone.utc).isoformat(),
        "issue_flags": 0,
        "price_primary_int": int(row["price_primary_int"]),
        "price_secondary_int": int(row["price_secondary_int"]),
        "schema_version": 4,
        "sip_timestamp_us": sip_us,
        "size_primary": float(row["size_primary"]),
        "size_secondary": float(row["size_secondary"]),
        "source_sequence": int(row["ordinal"]),
        "ticker": str(row["ticker"]),
    }


def row_to_market_event(row: dict[str, Any]) -> MarketEvent:
    meta = int(row["event_meta"])
    ts = datetime.fromtimestamp(int(row["sip_timestamp_us"]) / 1_000_000, tz=timezone.utc)
    primary_scale = 10_000 if meta & 0x02 else 100
    secondary_scale = 10_000 if meta & 0x04 else 100
    conditions = tuple(int(row.get(f"condition_token_{index}", 0)) for index in range(1, 6) if int(row.get(f"condition_token_{index}", 0)))
    common = {
        "conditions": conditions,
        "ingest_ts": ts,
        "raw": {"conid": int(row.get("conid", 0)), "ordinal": int(row["ordinal"]), "event_meta": meta},
        "sequence": int(row["ordinal"]),
        "source": "qmd_history_gateway",
        "tape": (meta >> 3) & 0x07,
        "ticker": str(row["ticker"]),
        "ts": ts,
    }
    if meta & 1:
        return TradeEvent(
            event_id=f"H-{row['event_date']}-{row['ticker']}-{row['ordinal']}",
            exchange=int(row["exchange_primary"]),
            participant_ts=None,
            price=int(row["price_primary_int"]) / primary_scale,
            size=float(row["size_primary"]),
            trf_id=0,
            trf_ts=None,
            **common,
        )
    return QuoteEvent(
        ask_exchange=int(row["exchange_primary"]),
        ask_price=int(row["price_primary_int"]) / primary_scale,
        ask_size=float(row["size_primary"]),
        bid_exchange=int(row["exchange_secondary"]),
        bid_price=int(row["price_secondary_int"]) / secondary_scale,
        bid_size=float(row["size_secondary"]),
        indicators=(),
        **common,
    )


def _ticker(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized or not re.fullmatch(r"[A-Z0-9.\-]+", normalized):
        raise ValueError(f"Invalid ticker: {value}")
    return normalized


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
