from __future__ import annotations

import argparse
import concurrent.futures
import http.client as http_client
import json
import random
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import parse

import numpy as np
import polars as pl
import torch


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.compact_events import (  # noqa: E402
    EVENT_BYTES,
    HEADER_BYTES,
    QUOTE_EVENT_TYPE,
    TRADE_EVENT_TYPE,
    ReferenceMaps,
    encode_events_chunk_from_frame,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402
from research.mlops.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback  # noqa: E402
from research.mlops.clickhouse_ingest_sip_compact_codec import DEFAULT_DATABASE, env_status_keys  # noqa: E402
from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT_WIN,
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    parse_size_bytes,
    quote_ident,
    sql_string,
)


DEFAULT_INDEX_TABLE = "train_2019_to_2025"
DEFAULT_EVENTS_PER_CHUNK = 128
DEFAULT_FETCH_PER_KIND = 256
DEFAULT_BATCH_SIZE = 256
DEFAULT_BENCHMARK_BATCHES = 10
DEFAULT_QUERY_BUNDLE_SIZE = 32
DEFAULT_REFERENCE_DIR = REPO_ROOT / "research" / "market_references" / "massive"
_THREAD_STATE = threading.local()


@dataclass(frozen=True, slots=True)
class IndexRow:
    ticker: str
    event_count: int
    first_sip_timestamp_us: int
    last_sip_timestamp_us: int
    max_valid_ordinal: int


@dataclass(frozen=True, slots=True)
class OriginSample:
    sample_id: int
    ticker: str
    origin_timestamp_us: int


@dataclass(frozen=True, slots=True)
class SampleResult:
    sample_id: int
    ticker: str
    accepted: bool
    origin_timestamp_ns: int
    header: bytes
    events: bytes
    quote_rows: int
    trade_rows: int
    merged_rows: int
    query_seconds: float
    encode_seconds: float
    reject_reason: str


class PersistentClickHouseHttpClient:
    """Small per-thread ClickHouse HTTP client.

    The benchmark may issue thousands of small queries. Reusing one TCP connection
    per worker avoids Windows ephemeral-port exhaustion from repeated urlopen
    connect/close cycles.
    """

    def __init__(self, base_url: str, user: str, password: str) -> None:
        parsed = parse.urlsplit(base_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"Unsupported ClickHouse URL scheme: {parsed.scheme!r}")
        if not parsed.hostname:
            raise ValueError(f"Invalid ClickHouse URL: {base_url!r}")
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.path_prefix = parsed.path.rstrip("/")
        self.user = user
        self.password = password
        self._conn: http_client.HTTPConnection | http_client.HTTPSConnection | None = None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _connection(self) -> http_client.HTTPConnection | http_client.HTTPSConnection:
        if self._conn is None:
            cls = http_client.HTTPSConnection if self.scheme == "https" else http_client.HTTPConnection
            self._conn = cls(self.host, self.port, timeout=600)
        return self._conn

    def execute(self, sql: str, *, query_id: str | None = None) -> str:
        path = (self.path_prefix or "") + "/"
        if query_id:
            path += "?" + parse.urlencode({"query_id": query_id})
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        if self.user:
            headers["X-ClickHouse-User"] = self.user
        if self.password:
            headers["X-ClickHouse-Key"] = self.password
        body = sql.encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                conn = self._connection()
                conn.request("POST", path, body=body, headers=headers)
                response = conn.getresponse()
                payload = response.read().decode("utf-8", errors="replace")
                if response.status >= 400:
                    raise RuntimeError(f"ClickHouse HTTP {response.status} {response.reason}: {payload}")
                return payload
            except (OSError, http_client.HTTPException) as exc:
                last_error = exc
                self.close()
                if attempt == 1:
                    raise
        raise RuntimeError(f"ClickHouse request failed: {last_error!r}")

    def query_tsv(self, sql: str) -> str:
        return self.execute(sql.rstrip(";") + " FORMAT TSV")


def thread_client(args: argparse.Namespace) -> PersistentClickHouseHttpClient:
    key = (args.clickhouse_url, args.user, args.password)
    current_key = getattr(_THREAD_STATE, "clickhouse_key", None)
    client = getattr(_THREAD_STATE, "clickhouse_client", None)
    if client is None or current_key != key:
        if client is not None:
            client.close()
        client = PersistentClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
        _THREAD_STATE.clickhouse_client = client
        _THREAD_STATE.clickhouse_key = key
    return client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark v4 batch construction from existing compact ClickHouse quotes/trades tables."
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--quote-table", default="quotes")
    parser.add_argument("--trade-table", default="trades")
    parser.add_argument("--index-table", default=DEFAULT_INDEX_TABLE)
    parser.add_argument("--reference-dir", default=str(DEFAULT_REFERENCE_DIR))
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--benchmark-batches", type=int, default=DEFAULT_BENCHMARK_BATCHES)
    parser.add_argument("--events-per-chunk", type=int, default=DEFAULT_EVENTS_PER_CHUNK)
    parser.add_argument("--fetch-per-kind", type=int, default=DEFAULT_FETCH_PER_KIND)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--query-mode", choices=["per-sample", "union-all"], default="per-sample")
    parser.add_argument("--query-bundle-size", type=int, default=DEFAULT_QUERY_BUNDLE_SIZE)
    parser.add_argument("--max-sample-attempt-multiplier", type=int, default=5)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--strict-lossless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-memory-usage", default="20G")
    parser.add_argument("--max-threads-per-query", type=int, default=1)
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "compact_batch_provider_benchmark"))
    parser.add_argument("--limit-index-tickers", type=int, default=0)
    return parser.parse_args()


def query_settings(args: argparse.Namespace) -> str:
    settings = []
    if args.max_threads_per_query > 0:
        settings.append(f"max_threads = {int(args.max_threads_per_query)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return " SETTINGS " + ", ".join(settings) if settings else ""


def load_index(client: ClickHouseHttpClient, args: argparse.Namespace) -> list[IndexRow]:
    limit = f" LIMIT {int(args.limit_index_tickers)}" if args.limit_index_tickers > 0 else ""
    query = f"""
SELECT
    ticker,
    event_count,
    first_sip_timestamp_us,
    last_sip_timestamp_us,
    max_valid_ordinal
FROM {quote_ident(args.database)}.{quote_ident(args.index_table)}
WHERE event_count > 0
ORDER BY ticker
{limit}
"""
    rows: list[IndexRow] = []
    for line in client.query_tsv(query).splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        rows.append(
            IndexRow(
                ticker=parts[0],
                event_count=int(parts[1] or 0),
                first_sip_timestamp_us=int(parts[2] or 0),
                last_sip_timestamp_us=int(parts[3] or 0),
                max_valid_ordinal=int(parts[4] or 0),
            )
        )
    if not rows:
        raise RuntimeError(f"No eligible tickers found in {args.database}.{args.index_table}")
    return rows


def sample_origins(index_rows: list[IndexRow], *, batch_size: int, rng: random.Random) -> list[OriginSample]:
    out: list[OriginSample] = []
    for sample_id in range(batch_size):
        row = rng.choice(index_rows)
        origin = rng.randint(row.first_sip_timestamp_us, row.last_sip_timestamp_us)
        out.append(OriginSample(sample_id=sample_id, ticker=row.ticker, origin_timestamp_us=origin))
    return out


def query_quote_rows(client: ClickHouseHttpClient, args: argparse.Namespace, sample: OriginSample) -> list[dict[str, Any]]:
    query = f"""
SELECT
    ticker,
    sip_timestamp_us,
    sequence_number,
    bid_price_int,
    ask_price_int,
    bid_size,
    ask_size,
    bid_exchange,
    ask_exchange,
    quote_flags,
    conditions
FROM {quote_ident(args.database)}.{quote_ident(args.quote_table)}
WHERE ticker = {sql_string(sample.ticker)}
  AND sip_timestamp_us <= {int(sample.origin_timestamp_us)}
  AND ticker != ''
  AND sip_timestamp_us > 0
  AND sequence_number > 0
ORDER BY ticker DESC, sip_timestamp_us DESC, sequence_number DESC
LIMIT {int(args.fetch_per_kind)}
{query_settings(args)}
"""
    return [parse_quote_tsv(line) for line in client.query_tsv(query).splitlines() if line]


def query_trade_rows(client: ClickHouseHttpClient, args: argparse.Namespace, sample: OriginSample) -> list[dict[str, Any]]:
    query = f"""
SELECT
    ticker,
    sip_timestamp_us,
    sequence_number,
    price_int,
    size,
    exchange,
    trade_flags,
    conditions
FROM {quote_ident(args.database)}.{quote_ident(args.trade_table)}
WHERE ticker = {sql_string(sample.ticker)}
  AND sip_timestamp_us <= {int(sample.origin_timestamp_us)}
  AND ticker != ''
  AND sip_timestamp_us > 0
  AND sequence_number > 0
ORDER BY ticker DESC, sip_timestamp_us DESC, sequence_number DESC
LIMIT {int(args.fetch_per_kind)}
{query_settings(args)}
"""
    return [parse_trade_tsv(line) for line in client.query_tsv(query).splitlines() if line]


def query_quote_rows_union(
    client: PersistentClickHouseHttpClient,
    args: argparse.Namespace,
    samples: list[OriginSample],
) -> dict[int, list[dict[str, Any]]]:
    if not samples:
        return {}
    parts = [
        f"""
SELECT
    {int(sample.sample_id)} AS sample_id,
    ticker,
    sip_timestamp_us,
    sequence_number,
    bid_price_int,
    ask_price_int,
    bid_size,
    ask_size,
    bid_exchange,
    ask_exchange,
    quote_flags,
    conditions
FROM {quote_ident(args.database)}.{quote_ident(args.quote_table)}
WHERE ticker = {sql_string(sample.ticker)}
  AND sip_timestamp_us <= {int(sample.origin_timestamp_us)}
  AND ticker != ''
  AND sip_timestamp_us > 0
  AND sequence_number > 0
ORDER BY ticker DESC, sip_timestamp_us DESC, sequence_number DESC
LIMIT {int(args.fetch_per_kind)}
""".strip()
        for sample in samples
    ]
    query = "\nUNION ALL\n".join(parts) + query_settings(args)
    grouped: dict[int, list[dict[str, Any]]] = {sample.sample_id: [] for sample in samples}
    for line in client.query_tsv(query).splitlines():
        if not line:
            continue
        sample_id_text, payload = line.split("\t", 1)
        grouped.setdefault(int(sample_id_text), []).append(parse_quote_tsv(payload))
    return grouped


def query_trade_rows_union(
    client: PersistentClickHouseHttpClient,
    args: argparse.Namespace,
    samples: list[OriginSample],
) -> dict[int, list[dict[str, Any]]]:
    if not samples:
        return {}
    parts = [
        f"""
SELECT
    {int(sample.sample_id)} AS sample_id,
    ticker,
    sip_timestamp_us,
    sequence_number,
    price_int,
    size,
    exchange,
    trade_flags,
    conditions
FROM {quote_ident(args.database)}.{quote_ident(args.trade_table)}
WHERE ticker = {sql_string(sample.ticker)}
  AND sip_timestamp_us <= {int(sample.origin_timestamp_us)}
  AND ticker != ''
  AND sip_timestamp_us > 0
  AND sequence_number > 0
ORDER BY ticker DESC, sip_timestamp_us DESC, sequence_number DESC
LIMIT {int(args.fetch_per_kind)}
""".strip()
        for sample in samples
    ]
    query = "\nUNION ALL\n".join(parts) + query_settings(args)
    grouped: dict[int, list[dict[str, Any]]] = {sample.sample_id: [] for sample in samples}
    for line in client.query_tsv(query).splitlines():
        if not line:
            continue
        sample_id_text, payload = line.split("\t", 1)
        grouped.setdefault(int(sample_id_text), []).append(parse_trade_tsv(payload))
    return grouped


def parse_quote_tsv(line: str) -> dict[str, Any]:
    parts = line.split("\t")
    flags = int(parts[9] or 0)
    bid_scale = flags & 0x01
    ask_scale = (flags >> 1) & 0x01
    tape = ((flags >> 2) & 0x07) + 1
    conditions = condition_slots(parts[10] if len(parts) > 10 else "")
    return {
        "ticker": parts[0],
        "sip_timestamp": int(parts[1]) * 1000,
        "sequence_number": int(parts[2] or 0),
        "event_type": QUOTE_EVENT_TYPE,
        "bid_price": decode_price_int(int(parts[3] or 0), bid_scale),
        "ask_price": decode_price_int(int(parts[4] or 0), ask_scale),
        "bid_size": float(parts[5] or 0),
        "ask_size": float(parts[6] or 0),
        "bid_exchange": int(parts[7] or 0),
        "ask_exchange": int(parts[8] or 0),
        "price": None,
        "size": None,
        "exchange": 0,
        "tape": tape,
        "correction": 0,
        **conditions,
    }


def parse_trade_tsv(line: str) -> dict[str, Any]:
    parts = line.split("\t")
    flags = int(parts[6] or 0)
    price_scale = flags & 0x01
    tape = ((flags >> 1) & 0x07) + 1
    correction = (flags >> 3) & 0x0F
    conditions = condition_slots(parts[7] if len(parts) > 7 else "")
    return {
        "ticker": parts[0],
        "sip_timestamp": int(parts[1]) * 1000,
        "sequence_number": int(parts[2] or 0),
        "event_type": TRADE_EVENT_TYPE,
        "bid_price": None,
        "ask_price": None,
        "bid_size": None,
        "ask_size": None,
        "bid_exchange": 0,
        "ask_exchange": 0,
        "price": decode_price_int(int(parts[3] or 0), price_scale),
        "size": float(parts[4] or 0),
        "exchange": int(parts[5] or 0),
        "tape": tape,
        "correction": correction,
        **conditions,
    }


def decode_price_int(value: int, scale_code: int) -> float:
    return float(value) / (10000.0 if int(scale_code) == 1 else 100.0)


def condition_slots(raw: str) -> dict[str, int]:
    cleaned = raw.strip()
    if not cleaned:
        values: list[int] = []
    else:
        normalized = cleaned.replace("|", ",").replace(";", ",").replace(" ", ",")
        values = []
        for item in normalized.split(","):
            if not item:
                continue
            try:
                values.append(int(item))
            except ValueError:
                continue
    return {f"condition_{index + 1}": values[index] if index < len(values) else 0 for index in range(4)}


def sample_to_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    rows = sorted(rows, key=lambda row: (int(row["sip_timestamp"]), int(row["sequence_number"]), int(row["event_type"])))
    return pl.DataFrame(rows, schema=unified_frame_schema(), orient="row")


def unified_frame_schema() -> dict[str, pl.DataType]:
    return {
        "ticker": pl.String,
        "sip_timestamp": pl.Int64,
        "sequence_number": pl.Int64,
        "event_type": pl.UInt8,
        "bid_price": pl.Float64,
        "ask_price": pl.Float64,
        "bid_size": pl.Float64,
        "ask_size": pl.Float64,
        "bid_exchange": pl.Int32,
        "ask_exchange": pl.Int32,
        "price": pl.Float64,
        "size": pl.Float64,
        "exchange": pl.Int32,
        "tape": pl.Int32,
        "correction": pl.Int32,
        "condition_1": pl.Int32,
        "condition_2": pl.Int32,
        "condition_3": pl.Int32,
        "condition_4": pl.Int32,
    }


def fetch_and_encode_sample(
    sample: OriginSample,
    *,
    args: argparse.Namespace,
    references: ReferenceMaps,
) -> SampleResult:
    client = thread_client(args)
    query_started = time.perf_counter()
    quote_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    try:
        quote_rows = query_quote_rows(client, args, sample)
        trade_rows = query_trade_rows(client, args, sample)
    except Exception as exc:  # noqa: BLE001
        query_seconds = time.perf_counter() - query_started
        return rejected_sample(sample, quote_rows, trade_rows, query_seconds, normalize_reject_reason("query_error", exc))
    query_seconds = time.perf_counter() - query_started
    return encode_sample_rows(sample, quote_rows, trade_rows, query_seconds, args=args, references=references)


def fetch_and_encode_sample_bundle(
    samples: list[OriginSample],
    *,
    args: argparse.Namespace,
    references: ReferenceMaps,
) -> list[SampleResult]:
    client = thread_client(args)
    query_started = time.perf_counter()
    try:
        quote_map = query_quote_rows_union(client, args, samples)
        trade_map = query_trade_rows_union(client, args, samples)
    except Exception as exc:  # noqa: BLE001
        query_seconds = (time.perf_counter() - query_started) / max(1, len(samples))
        return [
            rejected_sample(sample, [], [], query_seconds, normalize_reject_reason("query_error", exc))
            for sample in samples
        ]
    query_seconds = (time.perf_counter() - query_started) / max(1, len(samples))
    return [
        encode_sample_rows(
            sample,
            quote_map.get(sample.sample_id, []),
            trade_map.get(sample.sample_id, []),
            query_seconds,
            args=args,
            references=references,
        )
        for sample in samples
    ]


def encode_sample_rows(
    sample: OriginSample,
    quote_rows: list[dict[str, Any]],
    trade_rows: list[dict[str, Any]],
    query_seconds: float,
    *,
    args: argparse.Namespace,
    references: ReferenceMaps,
) -> SampleResult:
    merged_rows = quote_rows + trade_rows
    if not merged_rows:
        return rejected_sample(sample, quote_rows, trade_rows, query_seconds, "no_rows")
    frame = sample_to_frame(merged_rows)
    if frame.height > args.events_per_chunk:
        frame = frame.tail(args.events_per_chunk)
    encode_started = time.perf_counter()
    try:
        encoded = encode_events_chunk_from_frame(
            frame,
            origin_timestamp_ns=int(frame["sip_timestamp"][-1]),
            events_per_chunk=args.events_per_chunk,
            references=references,
            strict_lossless=args.strict_lossless,
        )
    except Exception as exc:  # noqa: BLE001
        encode_seconds = time.perf_counter() - encode_started
        return SampleResult(
            sample_id=sample.sample_id,
            ticker=sample.ticker,
            accepted=False,
            origin_timestamp_ns=0,
            header=b"",
            events=b"",
            quote_rows=len(quote_rows),
            trade_rows=len(trade_rows),
            merged_rows=frame.height,
            query_seconds=query_seconds,
            encode_seconds=encode_seconds,
            reject_reason=normalize_reject_reason("encode_error", exc),
        )
    encode_seconds = time.perf_counter() - encode_started
    if encoded is None:
        return rejected_sample(sample, quote_rows, trade_rows, query_seconds, "encode_rejected")
    header, events = encoded
    return SampleResult(
        sample_id=sample.sample_id,
        ticker=sample.ticker,
        accepted=True,
        origin_timestamp_ns=int(frame["sip_timestamp"][-1]),
        header=header.tobytes(),
        events=events.tobytes(),
        quote_rows=len(quote_rows),
        trade_rows=len(trade_rows),
        merged_rows=frame.height,
        query_seconds=query_seconds,
        encode_seconds=encode_seconds,
        reject_reason="",
    )


def rejected_sample(
    sample: OriginSample,
    quote_rows: list[dict[str, Any]],
    trade_rows: list[dict[str, Any]],
    query_seconds: float,
    reason: str,
) -> SampleResult:
    return SampleResult(
        sample_id=sample.sample_id,
        ticker=sample.ticker,
        accepted=False,
        origin_timestamp_ns=0,
        header=b"",
        events=b"",
        quote_rows=len(quote_rows),
        trade_rows=len(trade_rows),
        merged_rows=0,
        query_seconds=query_seconds,
        encode_seconds=0.0,
        reject_reason=reason,
    )


def normalize_reject_reason(prefix: str, exc: Exception) -> str:
    text = repr(exc)
    if "WinError 10048" in text:
        return f"{prefix}:socket_exhaustion"
    if "Connection refused" in text:
        return f"{prefix}:connection_refused"
    if "HTTP 500" in text:
        return f"{prefix}:http_500"
    if "HTTP 403" in text:
        return f"{prefix}:http_403"
    if "HTTP 404" in text:
        return f"{prefix}:http_404"
    return f"{prefix}:{type(exc).__name__}"


def chunks(items: list[OriginSample], size: int) -> list[list[OriginSample]]:
    chunk_size = max(1, int(size))
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]


def build_batch(
    index_rows: list[IndexRow],
    *,
    args: argparse.Namespace,
    references: ReferenceMaps,
    rng: random.Random,
) -> dict[str, Any]:
    batch_started = time.perf_counter()
    workers = max(1, min(int(args.workers), int(args.batch_size)))
    max_attempts = max(int(args.batch_size), int(args.batch_size) * max(1, int(args.max_sample_attempt_multiplier)))
    sample_seconds = 0.0
    fetch_seconds = 0.0
    attempted = 0
    query_requests = 0
    next_sample_id = 0
    results: list[SampleResult] = []
    accepted_candidates: list[SampleResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        while len(accepted_candidates) < args.batch_size and attempted < max_attempts:
            remaining_needed = int(args.batch_size) - len(accepted_candidates)
            remaining_attempts = max_attempts - attempted
            draw_count = min(max(workers, remaining_needed), remaining_attempts)
            sample_started = time.perf_counter()
            origins = sample_origins(index_rows, batch_size=draw_count, rng=rng)
            origins = [
                OriginSample(sample_id=next_sample_id + index, ticker=sample.ticker, origin_timestamp_us=sample.origin_timestamp_us)
                for index, sample in enumerate(origins)
            ]
            next_sample_id += draw_count
            attempted += draw_count
            sample_seconds += time.perf_counter() - sample_started
            fetch_started = time.perf_counter()
            if args.query_mode == "per-sample":
                futures = [
                    executor.submit(fetch_and_encode_sample, sample, args=args, references=references)
                    for sample in origins
                ]
                query_requests += 2 * len(origins)
            else:
                bundles = chunks(origins, args.query_bundle_size)
                futures = [
                    executor.submit(fetch_and_encode_sample_bundle, bundle, args=args, references=references)
                    for bundle in bundles
                ]
                query_requests += 2 * len(bundles)
            round_results: list[SampleResult] = []
            for future in concurrent.futures.as_completed(futures):
                try:
                    value = future.result()
                    if isinstance(value, list):
                        round_results.extend(value)
                    else:
                        round_results.append(value)
                except Exception as exc:  # noqa: BLE001
                    round_results.append(
                        SampleResult(
                            sample_id=-1,
                            ticker="",
                            accepted=False,
                            origin_timestamp_ns=0,
                            header=b"",
                            events=b"",
                            quote_rows=0,
                            trade_rows=0,
                            merged_rows=0,
                            query_seconds=0.0,
                            encode_seconds=0.0,
                            reject_reason=normalize_reject_reason("worker_error", exc),
                        )
                    )
            fetch_seconds += time.perf_counter() - fetch_started
            results.extend(round_results)
            accepted_candidates.extend(item for item in round_results if item.accepted)
    accepted_candidates = sorted(accepted_candidates, key=lambda item: item.sample_id)
    accepted = accepted_candidates[: int(args.batch_size)]
    header = np.zeros((len(accepted), HEADER_BYTES), dtype=np.uint8)
    events = np.zeros((len(accepted), args.events_per_chunk, EVENT_BYTES), dtype=np.uint8)
    origin_ts = np.zeros((len(accepted),), dtype=np.int64)
    for index, item in enumerate(accepted):
        header[index] = np.frombuffer(item.header, dtype=np.uint8, count=HEADER_BYTES)
        events[index] = np.frombuffer(item.events, dtype=np.uint8, count=args.events_per_chunk * EVENT_BYTES).reshape(args.events_per_chunk, EVENT_BYTES)
        origin_ts[index] = item.origin_timestamp_ns
    reject_counts: dict[str, int] = {}
    rejected_items = [item for item in results if not item.accepted]
    for item in rejected_items:
        reject_counts[item.reject_reason] = reject_counts.get(item.reject_reason, 0) + 1
    query_seconds = sum(item.query_seconds for item in results)
    encode_seconds = sum(item.encode_seconds for item in results)
    total_quote_rows = sum(item.quote_rows for item in results)
    total_trade_rows = sum(item.trade_rows for item in results)
    profile = {
        "data/batch_build_seconds": time.perf_counter() - batch_started,
        "data/sample_select_seconds": sample_seconds,
        "data/fetch_wall_seconds": fetch_seconds,
        "data/query_sum_seconds": query_seconds,
        "data/encode_sum_seconds": encode_seconds,
        "data/requested": float(args.batch_size),
        "data/attempted": float(attempted),
        "data/accepted": float(len(accepted)),
        "data/accepted_candidates": float(len(accepted_candidates)),
        "data/unused_accepted": float(max(0, len(accepted_candidates) - len(accepted))),
        "data/rejected": float(len(rejected_items)),
        "data/query_errors": float(sum(1 for item in rejected_items if item.reject_reason.startswith("query_error"))),
        "data/encode_errors": float(sum(1 for item in rejected_items if item.reject_reason.startswith("encode_error"))),
        "data/worker_errors": float(sum(1 for item in rejected_items if item.reject_reason.startswith("worker_error"))),
        "data/query_requests": float(query_requests),
        "data/accept_pct": 100.0 * len(accepted_candidates) / max(1, len(results)),
        "data/quote_rows": float(total_quote_rows),
        "data/trade_rows": float(total_trade_rows),
        "data/workers": float(workers),
        "data/fetch_per_kind": float(args.fetch_per_kind),
        "data/query_bundle_size": float(args.query_bundle_size if args.query_mode == "union-all" else 1),
    }
    return {
        "header_uint8": torch.from_numpy(header),
        "events_uint8": torch.from_numpy(events),
        "origin_timestamp_ns": torch.from_numpy(origin_ts),
        "ticker": [item.ticker for item in accepted],
        "profile": profile,
        "reject_counts": reject_counts,
    }


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    return float(np.quantile(np.asarray(values, dtype=np.float64), q))


def summarize_profiles(profiles: list[dict[str, float]]) -> dict[str, float]:
    keys = sorted({key for profile in profiles for key in profile})
    out: dict[str, float] = {}
    for key in keys:
        values = [float(profile[key]) for profile in profiles if key in profile]
        out[f"{key}/mean"] = float(np.mean(values)) if values else 0.0
        out[f"{key}/p50"] = percentile(values, 0.50)
        out[f"{key}/p95"] = percentile(values, 0.95)
    accepted = [float(profile.get("data/accepted", 0.0)) for profile in profiles]
    seconds = [float(profile.get("data/batch_build_seconds", 0.0)) for profile in profiles]
    total_accepted = sum(accepted)
    total_seconds = sum(seconds)
    out["throughput/accepted_samples_per_second"] = total_accepted / max(total_seconds, 1e-9)
    out["throughput/requested_samples_per_second"] = (len(profiles) * float(profiles[0].get("data/requested", 0.0)) if profiles else 0.0) / max(total_seconds, 1e-9)
    return out


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    output_root = Path(args.output_root_win)
    run_id = "compact_batch_provider_benchmark_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_root / f"{run_id}.jsonl"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    rng = random.Random(args.seed)
    print("=" * 96, flush=True)
    print("Compact ClickHouse batch-provider benchmark", flush=True)
    print(f"database={args.database} quote_table={args.quote_table} trade_table={args.trade_table}", flush=True)
    print(f"index_table={args.index_table} batch_size={args.batch_size} batches={args.benchmark_batches}", flush=True)
    print(f"events_per_chunk={args.events_per_chunk} fetch_per_kind={args.fetch_per_kind} workers={args.workers}", flush=True)
    print(f"query_mode={args.query_mode} query_bundle_size={args.query_bundle_size}", flush=True)
    print(f"strict_lossless={args.strict_lossless} settings={query_settings(args).strip() or '<none>'}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    index_started = time.perf_counter()
    index_rows = load_index(client, args)
    index_seconds = time.perf_counter() - index_started
    references = ReferenceMaps.load(Path(args.reference_dir))
    print(f"Loaded index tickers={len(index_rows):,} seconds={index_seconds:.3f}", flush=True)
    output_root.mkdir(parents=True, exist_ok=True)
    append_jsonl(report_path, {"type": "run_start", "args": vars(args), "index_tickers": len(index_rows)})

    profiles: list[dict[str, float]] = []
    reject_totals: dict[str, int] = {}
    for batch_index in range(1, int(args.benchmark_batches) + 1):
        batch = build_batch(index_rows, args=args, references=references, rng=rng)
        profile = batch["profile"]
        profiles.append(profile)
        for key, value in batch["reject_counts"].items():
            reject_totals[key] = reject_totals.get(key, 0) + int(value)
        append_jsonl(report_path, {"type": "batch", "batch_index": batch_index, "profile": profile, "reject_counts": batch["reject_counts"]})
        print(
            f"BATCH [{batch_index:,}/{args.benchmark_batches:,}] "
            f"seconds={profile['data/batch_build_seconds']:.3f} "
            f"accepted={int(profile['data/accepted']):,}/{int(profile['data/requested']):,} "
            f"accept_pct={profile['data/accept_pct']:.1f} "
            f"fetch_wall={profile['data/fetch_wall_seconds']:.3f} "
            f"query_sum={profile['data/query_sum_seconds']:.3f} "
            f"encode_sum={profile['data/encode_sum_seconds']:.3f}",
            flush=True,
        )

    summary = summarize_profiles(profiles)
    append_jsonl(report_path, {"type": "summary", "summary": summary, "reject_totals": reject_totals})
    print("=" * 96, flush=True)
    print(f"SUMMARY batches={len(profiles):,} reject_totals={reject_totals}", flush=True)
    for key in sorted(summary):
        print(f"{key}={summary[key]:.6f}", flush=True)
    print(f"report={report_path}", flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()
