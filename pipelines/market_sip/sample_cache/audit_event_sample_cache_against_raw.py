from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse_events import (  # noqa: E402
    DEFAULT_CONTEXT_EVENTS,
    EVENT_ROW_DTYPE,
    ClickHouseEventsDataConfig,
    PersistentClickHouseBytesClient,
    encode_unified_event_window,
    normalized_config,
    query_settings,
)
from research.mlops.clickhouse import (  # noqa: E402
    CLICKHOUSE_ENDPOINT_ENV,
    CLICKHOUSE_URL_ENV,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402
from research.mlops.event_sample_cache import (  # noqa: E402
    SAMPLE_BYTES,
    encode_label_records,
    encode_sample_records,
    resolve_event_sample_cache_root,
)


@dataclass(slots=True)
class AuditResult:
    split: str
    shard_index: int
    sample_index_in_shard: int
    ticker: str
    origin_ordinal: int
    origin_timestamp_ns: int
    status: str
    events_match_sample: bool
    raw_contains_events: bool
    raw_matches_events: bool
    raw_matches_sample: bool
    event_rows: int
    raw_rows: int
    raw_match_start: int
    elapsed_seconds: float
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit sample-cache bytes against both market_sip_compact.events and "
            "the underlying compact quotes/trades tables."
        )
    )
    parser.add_argument("--cache-root", required=True)
    parser.add_argument("--splits", default="train,validation")
    parser.add_argument("--checks", type=int, default=25)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--events-table", default="events")
    parser.add_argument("--quote-table", default="quotes")
    parser.add_argument("--trade-table", default="trades")
    parser.add_argument("--clean-mode", choices=("strict", "issue_flags_zero"), default="issue_flags_zero")
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="80G")
    parser.add_argument("--output", default="")
    parser.add_argument("--write-decoded-jsonl", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_files(discover_env_files(REPO_ROOT))
    root = resolve_event_sample_cache_root(Path(args.cache_root))
    audit_rows = load_audit_rows(root, args.splits)
    if not audit_rows:
        raise RuntimeError(
            f"No audit samples found under {root}. Existing shards can only be traced to raw tables "
            "for samples listed in *_audit_samples.jsonl."
        )
    rng = random.Random(args.seed)
    rng.shuffle(audit_rows)
    checks = audit_rows[: max(0, int(args.checks))]
    output_path = Path(args.output) if args.output else root / "raw_source_audit_report.jsonl"
    decoded_path = output_path.with_suffix(".decoded.jsonl")
    config = normalized_config(
        ClickHouseEventsDataConfig(
            clickhouse_url=args.clickhouse_url or sample_cache_default_clickhouse_url(),
            user=args.user or default_clickhouse_user(),
            password=args.password or default_clickhouse_password(),
            database=args.database,
            events_table=args.events_table,
            max_threads=args.max_threads,
            max_memory_usage=args.max_memory_usage,
        )
    )
    client = PersistentClickHouseBytesClient(config.clickhouse_url, config.user, config.password)
    print("=" * 100, flush=True)
    print("Audit event sample cache against raw compact quotes/trades", flush=True)
    print(f"cache_root={root}", flush=True)
    print(f"database={args.database} events={args.events_table} quotes={args.quote_table} trades={args.trade_table}", flush=True)
    print(f"clean_mode={args.clean_mode}", flush=True)
    print(f"available_audit_samples={len(audit_rows):,} checks={len(checks):,}", flush=True)
    print(f"output={output_path}", flush=True)
    print("=" * 100, flush=True)
    errors = 0
    started = time.perf_counter()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open("w", encoding="utf-8") as out_handle:
            decoded_handle = decoded_path.open("w", encoding="utf-8") if args.write_decoded_jsonl else None
            try:
                for index, row in enumerate(checks, start=1):
                    result, decoded = audit_one(client, config, args, root, row)
                    if result.status != "ok":
                        errors += 1
                    out_handle.write(json.dumps(asdict(result), separators=(",", ":")) + "\n")
                    out_handle.flush()
                    if decoded_handle is not None and decoded is not None:
                        decoded_handle.write(json.dumps(decoded, separators=(",", ":"), default=str) + "\n")
                    print(
                        f"AUDIT [{index}/{len(checks)}] {result.status} "
                        f"{result.ticker}:{result.origin_ordinal} shard={result.shard_index:06d} "
                        f"event_rows={result.event_rows} raw_rows={result.raw_rows} errors={errors} "
                        f"elapsed={result.elapsed_seconds:.2f}s",
                        flush=True,
                    )
            finally:
                if decoded_handle is not None:
                    decoded_handle.close()
    finally:
        client.close()
    elapsed = time.perf_counter() - started
    print(f"DONE checks={len(checks):,} errors={errors:,} elapsed_seconds={elapsed:.1f}", flush=True)
    if errors:
        raise SystemExit(2)


def load_audit_rows(root: Path, splits: str) -> list[dict[str, Any]]:
    allowed = {item.strip() for item in splits.split(",") if item.strip()}
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*_audit_samples.jsonl")):
        split = path.name.removesuffix("_audit_samples.jsonl")
        if allowed and split not in allowed:
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
    return rows


def audit_one(
    client: PersistentClickHouseBytesClient,
    config: ClickHouseEventsDataConfig,
    args: argparse.Namespace,
    root: Path,
    audit_row: dict[str, Any],
) -> tuple[AuditResult, dict[str, Any] | None]:
    started = time.perf_counter()
    split = str(audit_row["split"])
    shard_index = int(audit_row["shard_index"])
    sample_index = int(audit_row["sample_index_in_shard"])
    ticker = str(audit_row["ticker"])
    origin_ordinal = int(audit_row["origin_ordinal"])
    origin_timestamp_ns = int(audit_row.get("origin_timestamp_ns", 0))
    try:
        v2_meta_path = root / split / f"shard_{shard_index:06d}.json"
        is_labeled = v2_meta_path.exists()
        label_chunks = 0
        if is_labeled:
            meta = json.loads(v2_meta_path.read_text(encoding="utf-8"))
            label_chunks = int(meta.get("label_chunks", audit_row.get("label_chunks", 8)))
            expected_record = read_one_record(root / str(meta["x_path"]), sample_index)
            expected_label_record = read_one_record(root / str(meta["y_path"]), sample_index, sample_bytes=label_chunks * SAMPLE_BYTES)
        else:
            shard_path = root / split / f"shard_{shard_index:06d}.samples.bin"
            expected_record = read_one_record(shard_path, sample_index)
            expected_label_record = None
        low_ordinal = max(0, origin_ordinal - DEFAULT_CONTEXT_EVENTS + 1)
        high_ordinal = origin_ordinal + label_chunks * DEFAULT_CONTEXT_EVENTS
        event_rows_plus = fetch_events_window(client, config, ticker, low_ordinal, high_ordinal)
        expected_event_rows = DEFAULT_CONTEXT_EVENTS + label_chunks * DEFAULT_CONTEXT_EVENTS + (1 if low_ordinal > 0 else 0)
        if event_rows_plus.shape[0] != expected_event_rows:
            return failure_result(
                split,
                shard_index,
                sample_index,
                ticker,
                origin_ordinal,
                origin_timestamp_ns,
                started,
                f"events row count mismatch expected={expected_event_rows} actual={event_rows_plus.shape[0]}",
                event_rows=int(event_rows_plus.shape[0]),
            )
        event_encoded = encode_x_y_from_rows(event_rows_plus, low_ordinal=low_ordinal, origin_ordinal=origin_ordinal, label_chunks=label_chunks)
        if isinstance(event_encoded, str):
            return failure_result(
                split,
                shard_index,
                sample_index,
                ticker,
                origin_ordinal,
                origin_timestamp_ns,
                started,
                f"events re-encode failed: {event_encoded}",
                event_rows=int(event_rows_plus.shape[0]),
            )
        event_record, event_label_record, event_header, event_events = event_encoded
        events_match_sample = bool(np.array_equal(expected_record, event_record)) and (
            expected_label_record is None or bool(np.array_equal(expected_label_record, event_label_record))
        )

        start_us = int(event_rows_plus["sip_timestamp_us"][0])
        end_us = int(event_rows_plus["sip_timestamp_us"][-1])
        raw_rows = fetch_raw_unified_rows(client, config, args, ticker, start_us, end_us)
        match_start = find_event_subsequence(raw_rows, event_rows_plus)
        raw_contains_events = match_start >= 0
        raw_matches_events = raw_contains_events
        raw_matches_sample = False
        decoded: dict[str, Any] | None = None
        if raw_contains_events:
            raw_subset_plus = raw_rows[match_start : match_start + event_rows_plus.shape[0]]
            raw_encoded = encode_x_y_from_rows(raw_subset_plus, low_ordinal=low_ordinal, origin_ordinal=origin_ordinal, label_chunks=label_chunks)
            if isinstance(raw_encoded, str):
                error = f"raw re-encode failed: {raw_encoded}"
            else:
                raw_record, raw_label_record, raw_header, raw_events = raw_encoded
                raw_matches_sample = bool(np.array_equal(expected_record, raw_record)) and (
                    expected_label_record is None or bool(np.array_equal(expected_label_record, raw_label_record))
                )
                error = "" if events_match_sample and raw_matches_sample else "byte mismatch"
                decoded = decoded_sample_summary(ticker, origin_ordinal, raw_header, raw_events)
        else:
            error = "raw compact rows do not contain event window as a contiguous subsequence"
        status = "ok" if events_match_sample and raw_contains_events and raw_matches_events and raw_matches_sample else "failed"
        return (
            AuditResult(
                split=split,
                shard_index=shard_index,
                sample_index_in_shard=sample_index,
                ticker=ticker,
                origin_ordinal=origin_ordinal,
                origin_timestamp_ns=origin_timestamp_ns,
                status=status,
                events_match_sample=events_match_sample,
                raw_contains_events=raw_contains_events,
                raw_matches_events=raw_matches_events,
                raw_matches_sample=raw_matches_sample,
                event_rows=int(event_rows_plus.shape[0]),
                raw_rows=int(raw_rows.shape[0]),
                raw_match_start=int(match_start),
                elapsed_seconds=time.perf_counter() - started,
                error=error,
            ),
            decoded,
        )
    except Exception as exc:  # noqa: BLE001
        return (
            failure_result(
                split,
                shard_index,
                sample_index,
                ticker,
                origin_ordinal,
                origin_timestamp_ns,
                started,
                repr(exc),
            ),
            None,
        )


def failure_result(
    split: str,
    shard_index: int,
    sample_index: int,
    ticker: str,
    origin_ordinal: int,
    origin_timestamp_ns: int,
    started: float,
    error: str,
    *,
    event_rows: int = 0,
    raw_rows: int = 0,
) -> AuditResult:
    return AuditResult(
        split=split,
        shard_index=shard_index,
        sample_index_in_shard=sample_index,
        ticker=ticker,
        origin_ordinal=origin_ordinal,
        origin_timestamp_ns=origin_timestamp_ns,
        status="failed",
        events_match_sample=False,
        raw_contains_events=False,
        raw_matches_events=False,
        raw_matches_sample=False,
        event_rows=event_rows,
        raw_rows=raw_rows,
        raw_match_start=-1,
        elapsed_seconds=time.perf_counter() - started,
        error=error,
    )


def fetch_events_window(
    client: PersistentClickHouseBytesClient,
    config: ClickHouseEventsDataConfig,
    ticker: str,
    low_ordinal: int,
    high_ordinal: int,
) -> np.ndarray:
    start_ordinal = max(0, int(low_ordinal) - 1)
    table = f"{quote_ident(config.database)}.{quote_ident(config.events_table)}"
    sql = f"""
SELECT
    toUInt32(0) AS span_id,
    ordinal,
    event_type,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    event_flags,
    conditions_packed
FROM {table}
PREWHERE ticker = {sql_string(ticker)}
  AND ordinal >= {start_ordinal}
  AND ordinal <= {int(high_ordinal)}
ORDER BY ordinal
{query_settings(config)}
FORMAT RowBinary
"""
    return decode_event_rows(client.execute_bytes(sql))


def encode_x_y_from_rows(
    rows_plus: np.ndarray,
    *,
    low_ordinal: int,
    origin_ordinal: int,
    label_chunks: int,
) -> tuple[np.ndarray, np.ndarray | None, np.ndarray, np.ndarray] | str:
    offset = 1 if low_ordinal > 0 else 0
    x_previous_sip_us = int(rows_plus["sip_timestamp_us"][0]) if offset else None
    x_window = rows_plus[offset : offset + DEFAULT_CONTEXT_EVENTS]
    x_encoded = encode_unified_event_window(x_window, previous_sip_us=x_previous_sip_us)
    if isinstance(x_encoded, str):
        return "x_" + x_encoded
    x_header, x_events = x_encoded
    x_record = encode_sample_records(x_header.reshape(1, -1), x_events.reshape(1, DEFAULT_CONTEXT_EVENTS, -1))[0]
    if label_chunks <= 0:
        return x_record, None, x_header, x_events
    label_headers = np.zeros((1, label_chunks, x_header.shape[0]), dtype=np.uint8)
    label_events = np.zeros((1, label_chunks, DEFAULT_CONTEXT_EVENTS, x_events.shape[1]), dtype=np.uint8)
    for label_index in range(label_chunks):
        label_start = offset + DEFAULT_CONTEXT_EVENTS + label_index * DEFAULT_CONTEXT_EVENTS
        label_end = label_start + DEFAULT_CONTEXT_EVENTS
        previous_sip_us = int(rows_plus["sip_timestamp_us"][label_start - 1])
        label_encoded = encode_unified_event_window(rows_plus[label_start:label_end], previous_sip_us=previous_sip_us)
        if isinstance(label_encoded, str):
            return f"label_{label_index}_{label_encoded}"
        label_header, label_event_bytes = label_encoded
        label_headers[0, label_index] = label_header
        label_events[0, label_index] = label_event_bytes
    y_record = encode_label_records(label_headers, label_events)[0]
    return x_record, y_record, x_header, x_events


def fetch_raw_unified_rows(
    client: PersistentClickHouseBytesClient,
    config: ClickHouseEventsDataConfig,
    args: argparse.Namespace,
    ticker: str,
    start_us: int,
    end_us: int,
) -> np.ndarray:
    sql = raw_unified_query(config, args, ticker, start_us, end_us)
    return decode_event_rows(client.execute_bytes(sql))


def condition_code_expr(slot: int) -> str:
    return f"toInt16OrZero(arrayElement(splitByChar(',', conditions), {slot}))"


def quote_condition_pack_expr() -> str:
    return """
bitOr(
    bitOr(toUInt32(coalesce(qc1.dense_id, 0)), bitShiftLeft(toUInt32(coalesce(qc2.dense_id, 0)), 8)),
    bitOr(bitShiftLeft(toUInt32(coalesce(qc3.dense_id, 0)), 16), bitShiftLeft(toUInt32(coalesce(qc4.dense_id, 0)), 24))
)
""".strip()


def trade_condition_pack_expr() -> str:
    return """
bitOr(
    bitOr(toUInt32(coalesce(tc1.dense_id, 0)), bitShiftLeft(toUInt32(coalesce(tc2.dense_id, 0)), 6)),
    bitOr(
        bitOr(bitShiftLeft(toUInt32(coalesce(tc3.dense_id, 0)), 12), bitShiftLeft(toUInt32(coalesce(tc4.dense_id, 0)), 18)),
        bitShiftLeft(toUInt32(coalesce(tc5.dense_id, 0)), 24)
    )
)
""".strip()


def condition_reference_subquery(args: argparse.Namespace, table: str) -> str:
    return (
        f"(SELECT modifier_int, min(dense_id) AS dense_id "
        f"FROM {quote_ident(args.database)}.{quote_ident(table)} "
        "GROUP BY modifier_int)"
    )


def quote_clean_predicate(args: argparse.Namespace) -> str:
    base = """
q.ticker != ''
AND q.sip_timestamp_us > 0
AND q.sequence_number > 0
AND q.bid_price_int > 0
AND q.ask_price_int > 0
AND q.bid_size > 0
AND q.ask_size > 0
AND if(bitAnd(q.quote_flags, 1) = 1, q.bid_price_int / 10000.0, q.bid_price_int / 100.0)
    <= if(bitAnd(bitShiftRight(q.quote_flags, 1), 1) = 1, q.ask_price_int / 10000.0, q.ask_price_int / 100.0)
""".strip()
    if args.clean_mode == "issue_flags_zero":
        base += "\nAND q.issue_flags = 0"
    return base


def trade_clean_predicate(args: argparse.Namespace) -> str:
    base = """
t.ticker != ''
AND t.sip_timestamp_us > 0
AND t.sequence_number > 0
AND t.price_int > 0
AND t.size > 0
""".strip()
    if args.clean_mode == "issue_flags_zero":
        base += "\nAND t.issue_flags = 0"
    return base


def raw_unified_query(config: ClickHouseEventsDataConfig, args: argparse.Namespace, ticker: str, start_us: int, end_us: int) -> str:
    db = quote_ident(config.database)
    quote_table = quote_ident(args.quote_table)
    trade_table = quote_ident(args.trade_table)
    ref_args = argparse.Namespace(database=config.database)
    return f"""
SELECT
    toUInt32(0) AS span_id,
    toUInt64(0) AS ordinal,
    event_type,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    event_flags,
    conditions_packed
FROM
(
    SELECT
        toUInt8(0) AS event_type,
        q.sip_timestamp_us AS sip_timestamp_us,
        q.sequence_number AS sequence_number,
        q.ask_price_int AS price_primary_int,
        q.bid_price_int AS price_secondary_int,
        toFloat32(q.ask_size) AS size_primary,
        toFloat32(q.bid_size) AS size_secondary,
        q.ask_exchange AS exchange_primary,
        q.bid_exchange AS exchange_secondary,
        toUInt8(
            bitOr(
                bitOr(bitAnd(bitShiftRight(q.quote_flags, 1), 1), bitShiftLeft(bitAnd(q.quote_flags, 1), 1)),
                bitShiftLeft(bitAnd(bitShiftRight(q.quote_flags, 2), 7), 2)
            )
        ) AS event_flags,
        {quote_condition_pack_expr()} AS conditions_packed
    FROM
    (
        SELECT
            *,
            {condition_code_expr(1)} AS condition_code_1,
            {condition_code_expr(2)} AS condition_code_2,
            {condition_code_expr(3)} AS condition_code_3,
            {condition_code_expr(4)} AS condition_code_4
        FROM {db}.{quote_table}
        PREWHERE ticker = {sql_string(ticker)}
          AND sip_timestamp_us >= {int(start_us)}
          AND sip_timestamp_us <= {int(end_us)}
    ) AS q
    LEFT JOIN {condition_reference_subquery(ref_args, "ref_quote_conditions")} AS qc1 ON qc1.modifier_int = q.condition_code_1
    LEFT JOIN {condition_reference_subquery(ref_args, "ref_quote_conditions")} AS qc2 ON qc2.modifier_int = q.condition_code_2
    LEFT JOIN {condition_reference_subquery(ref_args, "ref_quote_conditions")} AS qc3 ON qc3.modifier_int = q.condition_code_3
    LEFT JOIN {condition_reference_subquery(ref_args, "ref_quote_conditions")} AS qc4 ON qc4.modifier_int = q.condition_code_4
    WHERE {quote_clean_predicate(args)}

    UNION ALL

    SELECT
        toUInt8(1) AS event_type,
        t.sip_timestamp_us AS sip_timestamp_us,
        t.sequence_number AS sequence_number,
        t.price_int AS price_primary_int,
        toUInt32(0) AS price_secondary_int,
        t.size AS size_primary,
        toFloat32(0) AS size_secondary,
        t.exchange AS exchange_primary,
        toUInt8(0) AS exchange_secondary,
        toUInt8(
            bitOr(
                bitAnd(t.trade_flags, 1),
                bitShiftLeft(bitAnd(bitShiftRight(t.trade_flags, 1), 7), 2)
            )
        ) AS event_flags,
        {trade_condition_pack_expr()} AS conditions_packed
    FROM
    (
        SELECT
            *,
            {condition_code_expr(1)} AS condition_code_1,
            {condition_code_expr(2)} AS condition_code_2,
            {condition_code_expr(3)} AS condition_code_3,
            {condition_code_expr(4)} AS condition_code_4,
            {condition_code_expr(5)} AS condition_code_5
        FROM {db}.{trade_table}
        PREWHERE ticker = {sql_string(ticker)}
          AND sip_timestamp_us >= {int(start_us)}
          AND sip_timestamp_us <= {int(end_us)}
    ) AS t
    LEFT JOIN {condition_reference_subquery(ref_args, "ref_trade_conditions")} AS tc1 ON tc1.modifier_int = t.condition_code_1
    LEFT JOIN {condition_reference_subquery(ref_args, "ref_trade_conditions")} AS tc2 ON tc2.modifier_int = t.condition_code_2
    LEFT JOIN {condition_reference_subquery(ref_args, "ref_trade_conditions")} AS tc3 ON tc3.modifier_int = t.condition_code_3
    LEFT JOIN {condition_reference_subquery(ref_args, "ref_trade_conditions")} AS tc4 ON tc4.modifier_int = t.condition_code_4
    LEFT JOIN {condition_reference_subquery(ref_args, "ref_trade_conditions")} AS tc5 ON tc5.modifier_int = t.condition_code_5
    WHERE {trade_clean_predicate(args)}
)
ORDER BY sip_timestamp_us, sequence_number, event_type
{query_settings(config)}
FORMAT RowBinary
"""


def decode_event_rows(payload: bytes) -> np.ndarray:
    if len(payload) % EVENT_ROW_DTYPE.itemsize != 0:
        raise RuntimeError(f"RowBinary payload size {len(payload):,} is not divisible by {EVENT_ROW_DTYPE.itemsize}")
    return np.frombuffer(payload, dtype=EVENT_ROW_DTYPE).copy()


COMPARE_FIELDS = (
    "event_type",
    "sip_timestamp_us",
    "price_primary_int",
    "price_secondary_int",
    "size_primary",
    "size_secondary",
    "exchange_primary",
    "exchange_secondary",
    "event_flags",
    "conditions_packed",
)


def find_event_subsequence(raw_rows: np.ndarray, event_rows: np.ndarray) -> int:
    if raw_rows.shape[0] < event_rows.shape[0]:
        return -1
    first_type = event_rows["event_type"][0]
    first_ts = event_rows["sip_timestamp_us"][0]
    candidates = np.flatnonzero((raw_rows["event_type"] == first_type) & (raw_rows["sip_timestamp_us"] == first_ts))
    for start in candidates:
        end = int(start) + event_rows.shape[0]
        if end > raw_rows.shape[0]:
            continue
        window = raw_rows[int(start) : end]
        if rows_equal_on_fields(window, event_rows):
            return int(start)
    return -1


def rows_equal_on_fields(left: np.ndarray, right: np.ndarray) -> bool:
    if left.shape[0] != right.shape[0]:
        return False
    for field in COMPARE_FIELDS:
        if not np.array_equal(left[field], right[field]):
            return False
    return True


def decoded_sample_summary(ticker: str, origin_ordinal: int, header: np.ndarray, events: np.ndarray) -> dict[str, Any]:
    decoded_header, decoded_events = decode_sample_for_humans(header, events)
    return {"ticker": ticker, "origin_ordinal": origin_ordinal, "header": decoded_header, "events": decoded_events}


def read_one_record(path: Path, sample_index: int, *, sample_bytes: int = SAMPLE_BYTES) -> np.ndarray:
    with path.open("rb") as handle:
        handle.seek(sample_index * sample_bytes)
        payload = handle.read(sample_bytes)
    if len(payload) != sample_bytes:
        raise RuntimeError(f"Could not read full sample from {path}:{sample_index}")
    return np.frombuffer(payload, dtype=np.uint8).copy()


def decode_sample_for_humans(header: np.ndarray, events: np.ndarray) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ask_anchor_ticks = int.from_bytes(bytes([int(header[0]), int(header[1]), int(header[2] & 0x0F)]), byteorder="little")
    spread_anchor_ticks = int.from_bytes(bytes([int(header[3]), int(header[4])]), byteorder="little")
    tick_size = 0.01 if (int(header[13]) & 0x04) else 0.0001
    decoded_header = {
        "ask_anchor_ticks": ask_anchor_ticks,
        "spread_anchor_ticks": spread_anchor_ticks,
        "tick_size": tick_size,
        "quote_count": int(header[11]),
        "trade_count": int(header[12]),
        "flags": int(header[13]),
    }
    rows: list[dict[str, Any]] = []
    for idx, event in enumerate(events):
        event_type = int(event[0] & 0x01)
        present = bool(event[0] & 0x02)
        delta_us_bucket = int.from_bytes(event[1:3].tobytes(), byteorder="little")
        price_delta_1 = int.from_bytes(event[3:5].tobytes(), byteorder="little", signed=True)
        price_delta_2 = int.from_bytes(event[5:7].tobytes(), byteorder="little", signed=True)
        row: dict[str, Any] = {
            "event_idx": idx,
            "present": present,
            "event_type": "trade" if event_type else "quote",
            "delta_us_bucket": delta_us_bucket,
            "size_primary_bucket": int(event[7]),
            "size_secondary_bucket": int(event[8]),
            "exchange_primary": int(event[10]),
            "exchange_secondary": int(event[11]),
            "conditions_packed_bytes": [int(value) for value in event[12:16]],
        }
        if event_type == 0:
            ask_ticks = ask_anchor_ticks + price_delta_1
            spread_ticks = spread_anchor_ticks + price_delta_2
            row["ask"] = ask_ticks * tick_size
            row["bid"] = (ask_ticks - spread_ticks) * tick_size
            row["spread"] = spread_ticks * tick_size
        else:
            row["trade_price"] = (ask_anchor_ticks + price_delta_1) * tick_size
        rows.append(row)
    return decoded_header, rows


def sample_cache_default_clickhouse_url() -> str:
    return (
        os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL")
        or os.environ.get(CLICKHOUSE_URL_ENV)
        or os.environ.get(CLICKHOUSE_ENDPOINT_ENV)
        or os.environ.get("REAL_LIVE_CLICKHOUSE_READ_URL")
        or default_clickhouse_url()
        or "http://localhost:18123"
    )


if __name__ == "__main__":
    main()
