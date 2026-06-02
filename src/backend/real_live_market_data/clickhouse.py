from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

import polars as pl

from src.backend.real_live_market_data.config import ClickHouseConfig


@dataclass
class ClickHouseHttpClient:
    config: ClickHouseConfig

    def query_json(self, sql: str) -> list[dict[str, Any]]:
        text = self.query_text(sql.rstrip().removesuffix(";") + " FORMAT JSONEachRow")
        rows: list[dict[str, Any]] = []
        for line in text.splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def query_frame(self, sql: str) -> pl.DataFrame:
        rows = self.query_json(sql)
        return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()

    def execute(self, sql: str, *, use_database: bool = True) -> None:
        self.query_text(sql, use_database=use_database)

    def insert_json_each_row(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        body = "\n".join(json.dumps(row, separators=(",", ":"), default=str) for row in rows)
        self.query_text(f"INSERT INTO {table} FORMAT JSONEachRow", body=body.encode("utf-8"))

    def query_text(self, sql: str, body: bytes | None = None, *, use_database: bool = True) -> str:
        params = urllib.parse.urlencode({"database": self.config.database}) if use_database and self.config.database else ""
        url = f"{self.config.endpoint_url}/?{params}" if params else f"{self.config.endpoint_url}/"
        request_body = sql.encode("utf-8") if body is None else sql.encode("utf-8") + b"\n" + body
        request = urllib.request.Request(url, data=request_body, method="POST")
        request.add_header("Content-Type", "text/plain; charset=utf-8")
        request.add_header("X-ClickHouse-User", self.config.user)
        if self.config.password:
            request.add_header("X-ClickHouse-Key", self.config.password)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ClickHouse HTTP {exc.code}: {text[:1000]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"ClickHouse request failed: {exc.reason}") from exc


def ensure_replay_tables(client: ClickHouseHttpClient) -> None:
    if not client.config.database.strip():
        raise RuntimeError("ClickHouse write database is required for replay persistence.")
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_identifier(client.config.database)}", use_database=False)
    client.execute(
        """
        CREATE TABLE IF NOT EXISTS live_massive_trades
        (
            session_date Date,
            ts DateTime64(3, 'UTC'),
            participant_ts DateTime64(3, 'UTC'),
            trf_ts DateTime64(3, 'UTC'),
            ingest_ts DateTime64(3, 'UTC'),
            sym LowCardinality(String),
            trade_id String,
            seq UInt64,
            exchange UInt16,
            tape UInt8,
            price Float64,
            size Float64,
            conditions Array(UInt16),
            trf_id UInt16,
            raw String
        )
        ENGINE = MergeTree
        PARTITION BY session_date
        ORDER BY (session_date, sym, ts, seq)
        """
    )
    client.execute(
        """
        CREATE TABLE IF NOT EXISTS live_massive_quotes
        (
            session_date Date,
            ts DateTime64(3, 'UTC'),
            ingest_ts DateTime64(3, 'UTC'),
            sym LowCardinality(String),
            seq UInt64,
            bid_exchange UInt16,
            ask_exchange UInt16,
            bid_price Float64,
            ask_price Float64,
            bid_size UInt32,
            ask_size UInt32,
            conditions Array(UInt16),
            indicators Array(UInt16),
            tape UInt8,
            raw String
        )
        ENGINE = MergeTree
        PARTITION BY session_date
        ORDER BY (session_date, sym, ts, seq)
        """
    )
    client.execute(
        """
        CREATE TABLE IF NOT EXISTS live_market_bars
        (
            session_date Date,
            timeframe LowCardinality(String),
            bar_start DateTime64(3, 'UTC'),
            bar_end DateTime64(3, 'UTC'),
            sym LowCardinality(String),
            open Float64,
            high Float64,
            low Float64,
            close Float64,
            volume Float64,
            dollar_volume Float64,
            trade_count UInt32,
            vwap Float64,
            source LowCardinality(String)
        )
        ENGINE = ReplacingMergeTree
        PARTITION BY session_date
        ORDER BY (session_date, timeframe, sym, bar_start)
        """
    )


def quote_identifier(value: str) -> str:
    return f"`{value.replace('`', '``')}`"
