from __future__ import annotations

import array
import base64
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import tiktoken

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    insert_json_each_row,
)
from research.news_reaction_model.openai_embeddings_v1.config import PipelineConfig
from research.news_reaction_model.openai_embeddings_v1.openai_api import (
    OpenAIAPIError,
    OpenAIHTTPClient,
    batch_error_summary,
    is_quota_message,
)
from research.news_reaction_model.v5.text_features import article_model_text


ACTIVE_BATCH_STATUSES = {"prepared", "uploaded", "validating", "in_progress", "finalizing", "submitted"}
REMOTE_ACTIVE_STATUSES = {"validating", "in_progress", "finalizing", "cancelling"}
REMOTE_TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}
RETRYABLE_ITEM_CODES = {
    "rate_limit_exceeded",
    "server_error",
    "internal_error",
    "batch_expired",
    "missing_output",
}
MAX_ITEM_ATTEMPTS = 3


def q(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def qi(value: str) -> str:
    return "`" + str(value).replace("`", "``") + "`"


@dataclass(slots=True)
class PreparedText:
    text: str
    text_sha256: str
    input_tokens: int
    truncated: int


@dataclass(slots=True)
class BatchInput:
    canonical_news_id: str
    ticker: str
    published_at_utc: str
    text_sha256: str
    input_tokens: int
    truncated: int
    attempts: int
    text: str


@dataclass(slots=True)
class BatchFiles:
    batch_key: str
    input_path: Path
    manifest_path: Path
    item_count: int
    request_count: int
    estimated_tokens: int


@dataclass(slots=True)
class UsageSummary:
    source_rows: int
    item_rows: int
    embedded_rows: int
    planned_rows: int
    active_rows: int
    failed_rows: int
    actual_tokens: int
    reserved_tokens: int
    actual_cost_usd: Decimal
    reserved_cost_usd: Decimal

    @property
    def protected_total_usd(self) -> Decimal:
        return self.actual_cost_usd + self.reserved_cost_usd


def iso_utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def clickhouse_utc_now() -> str:
    """Return the unambiguous native DateTime64 text accepted by JSONEachRow."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def append_status(config: PipelineConfig, event: str, **fields: Any) -> None:
    config.runtime_root.mkdir(parents=True, exist_ok=True)
    payload = {"at_utc": iso_utc_now(), "event": event, **fields}
    with config.status_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def money_for_tokens(tokens: int, price_per_million: Decimal) -> Decimal:
    return (Decimal(max(0, int(tokens))) * price_per_million / Decimal(1_000_000)).quantize(Decimal("0.000001"))


def month_ranges(start: str, end_exclusive: str) -> list[tuple[dt.date, dt.date]]:
    cursor = dt.date.fromisoformat(start).replace(day=1)
    end = dt.date.fromisoformat(end_exclusive)
    ranges: list[tuple[dt.date, dt.date]] = []
    while cursor < end:
        next_month = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        ranges.append((cursor, min(next_month, end)))
        cursor = next_month
    return ranges


def create_schema(client: ClickHouseHttpClient, config: PipelineConfig) -> None:
    database = qi(config.database)
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {database}.{qi(config.embedding_table)}
(
 embedding_version LowCardinality(String),
 model LowCardinality(String),
 dimensions UInt16,
 text_contract LowCardinality(String),
 canonical_news_id String,
 ticker LowCardinality(String),
 published_at_utc DateTime64(9, 'UTC'),
 text_sha256 FixedString(64),
 input_tokens UInt32,
 truncated UInt8,
 embedding Array(Float32) CODEC(ZSTD(3)),
 openai_request_id String,
 batch_id String,
 embedded_at_utc DateTime64(3, 'UTC'),
 built_at_utc DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(built_at_utc)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (embedding_version, published_at_utc, ticker, canonical_news_id)
SETTINGS index_granularity = 8192
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {database}.{qi(config.item_table)}
(
 embedding_version LowCardinality(String),
 canonical_news_id String,
 ticker LowCardinality(String),
 published_at_utc DateTime64(9, 'UTC'),
 text_sha256 FixedString(64),
 input_tokens UInt32,
 truncated UInt8,
 status LowCardinality(String),
 batch_key String,
 request_custom_id String,
 request_index UInt16,
 attempts UInt8,
 error_code LowCardinality(String),
 error_message String,
 updated_at_utc DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (embedding_version, published_at_utc, ticker, canonical_news_id)
SETTINGS index_granularity = 8192
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {database}.{qi(config.batch_table)}
(
 embedding_version LowCardinality(String),
 batch_key String,
 batch_id String,
 input_file_id String,
 output_file_id String,
 error_file_id String,
 status LowCardinality(String),
 item_count UInt32,
 request_count UInt32,
 estimated_tokens UInt64,
 actual_tokens UInt64,
 expected_cost_usd Decimal64(6),
 reservation_cost_usd Decimal64(6),
 actual_cost_usd Decimal64(6),
 completed_items UInt32,
 failed_items UInt32,
 local_input_path String,
 local_manifest_path String,
 last_error String,
 created_at_utc DateTime64(3, 'UTC'),
 updated_at_utc DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
ORDER BY (embedding_version, batch_key)
SETTINGS index_granularity = 8192
""")


def source_rows_sql(config: PipelineConfig, start: dt.date, end: dt.date) -> str:
    return f"""
SELECT
 p.canonical_news_id, p.ticker, p.published_at_utc,
 n.provider, n.title, n.teaser,
 substring(n.body_text, 1, {config.max_text_chars}) AS body_text,
 substring(n.external_text, 1, {config.max_text_chars}) AS external_text,
 substring(n.pdf_text, 1, {config.max_text_chars}) AS pdf_text,
 arrayStringConcat(n.channels, ',') AS channels,
 arrayStringConcat(n.provider_tags, ',') AS provider_tags
FROM {qi(config.database)}.{qi(config.source_table)} AS p FINAL
ANY INNER JOIN {qi(config.news_database)}.{qi(config.news_table)} AS n FINAL
 ON n.canonical_news_id = p.canonical_news_id
WHERE p.dataset_version = {q(config.source_version)}
 AND p.published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND p.published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
ORDER BY p.published_at_utc, p.ticker, p.canonical_news_id
SETTINGS max_threads={config.clickhouse_threads}, max_memory_usage={q(config.clickhouse_memory)}
FORMAT JSONEachRow
"""


def source_count(client: ClickHouseHttpClient, config: PipelineConfig) -> int:
    return int(client.execute(f"""
SELECT count()
FROM {qi(config.database)}.{qi(config.source_table)} FINAL
WHERE dataset_version = {q(config.source_version)}
 AND published_at_utc >= toDateTime64({q(config.start_date)}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(config.end_date_exclusive)}, 9, 'UTC')
""").strip() or "0")


def prepare_text(row: dict[str, Any], config: PipelineConfig, encoding: Any) -> PreparedText:
    source_text = article_model_text(row, max_chars=config.max_text_chars)
    # News is untrusted source text. Tokenizer control-token spellings are
    # literal article content here; they must not be interpreted or rejected.
    token_ids = encoding.encode(source_text, disallowed_special=())
    truncated = int(len(token_ids) > config.max_input_tokens)
    if truncated:
        source_text = encoding.decode(token_ids[: config.max_input_tokens])
        token_ids = encoding.encode(source_text, disallowed_special=())
        if len(token_ids) > config.max_input_tokens:
            raise RuntimeError("Token-safe truncation did not satisfy the configured input ceiling")
    digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    return PreparedText(source_text, digest, len(token_ids), truncated)


def existing_month_items(
    client: ClickHouseHttpClient,
    config: PipelineConfig,
    start: dt.date,
    end: dt.date,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    text = client.execute(f"""
SELECT canonical_news_id, ticker, toString(published_at_utc) AS published_at_utc,
 text_sha256, input_tokens, truncated, status, batch_key, request_custom_id,
 request_index, attempts, error_code, error_message
FROM
(
 SELECT canonical_news_id, ticker, published_at_utc, text_sha256, input_tokens,
  truncated, status, batch_key, request_custom_id, request_index, attempts,
  error_code, error_message
 FROM {qi(config.database)}.{qi(config.item_table)} FINAL
 WHERE embedding_version = {q(config.embedding_version)}
  AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
  AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
)
FORMAT JSONEachRow
""")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return {
        (str(row["canonical_news_id"]), str(row["ticker"]), str(row["published_at_utc"])): row
        for row in rows
    }


def plan_month(config: PipelineConfig, start: dt.date, end: dt.date, *, execute: bool) -> dict[str, Any]:
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    encoding = tiktoken.encoding_for_model(config.model)
    source = [json.loads(line) for line in client.execute(source_rows_sql(config, start, end)).splitlines() if line.strip()]
    existing = existing_month_items(client, config, start, end) if execute else {}
    inserts: list[dict[str, Any]] = []
    total_tokens = 0
    truncated = 0
    for row in source:
        prepared = prepare_text(row, config, encoding)
        total_tokens += prepared.input_tokens
        truncated += prepared.truncated
        key = (str(row["canonical_news_id"]), str(row["ticker"]), str(row["published_at_utc"]))
        prior = existing.get(key)
        if prior:
            if str(prior["text_sha256"]) != prepared.text_sha256 or int(prior["input_tokens"]) != prepared.input_tokens:
                raise RuntimeError(
                    f"Text-contract drift for {key}: persisted {prior['text_sha256']} / {prior['input_tokens']} "
                    f"but current is {prepared.text_sha256} / {prepared.input_tokens}"
                )
            continue
        inserts.append({
            "embedding_version": config.embedding_version,
            "canonical_news_id": key[0],
            "ticker": key[1],
            "published_at_utc": key[2],
            "text_sha256": prepared.text_sha256,
            "input_tokens": prepared.input_tokens,
            "truncated": prepared.truncated,
            "status": "planned",
            "batch_key": "",
            "request_custom_id": "",
            "request_index": 0,
            "attempts": 0,
            "error_code": "",
            "error_message": "",
            "updated_at_utc": clickhouse_utc_now(),
        })
    if execute:
        columns = list(inserts[0].keys()) if inserts else []
        for offset in range(0, len(inserts), 2_000):
            insert_json_each_row(
                client,
                config.database,
                config.item_table,
                columns,
                inserts[offset:offset + 2_000],
            )
    return {
        "month": start.strftime("%Y-%m"),
        "rows": len(source),
        "tokens": total_tokens,
        "truncated": truncated,
        "inserted": len(inserts),
    }


def plan_corpus(config: PipelineConfig, *, execute: bool) -> dict[str, int]:
    ranges = month_ranges(config.start_date, config.end_date_exclusive)
    started = time.monotonic()
    totals = {"rows": 0, "tokens": 0, "truncated": 0, "inserted": 0}
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.planner_workers) as pool:
        futures = {pool.submit(plan_month, config, start, end, execute=execute): start for start, end in ranges}
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            completed += 1
            for key in totals:
                totals[key] += int(result[key])
            elapsed = time.monotonic() - started
            rate = completed / elapsed if elapsed else 0.0
            eta = (len(ranges) - completed) / rate if rate else 0.0
            print(
                f"PLAN {completed:02d}/{len(ranges)} {result['month']} rows={result['rows']:,} "
                f"tokens={result['tokens']:,} new={result['inserted']:,} "
                f"truncated={result['truncated']:,} eta={eta / 60:.1f}m",
                flush=True,
            )
    append_status(config, "plan_complete", execute=execute, **totals)
    return totals


def json_rows(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def latest_batches(client: ClickHouseHttpClient, config: PipelineConfig) -> list[dict[str, Any]]:
    return json_rows(client, f"""
SELECT * EXCEPT(created_at_utc, updated_at_utc), toString(created_at_utc) AS created_at_utc,
 toString(updated_at_utc) AS updated_at_utc
FROM {qi(config.database)}.{qi(config.batch_table)} FINAL
WHERE embedding_version = {q(config.embedding_version)}
ORDER BY created_at_utc
FORMAT JSONEachRow
""")


def batch_row(config: PipelineConfig, files: BatchFiles, **overrides: Any) -> dict[str, Any]:
    now = clickhouse_utc_now()
    base = {
        "embedding_version": config.embedding_version,
        "batch_key": files.batch_key,
        "batch_id": "",
        "input_file_id": "",
        "output_file_id": "",
        "error_file_id": "",
        "status": "prepared",
        "item_count": files.item_count,
        "request_count": files.request_count,
        "estimated_tokens": files.estimated_tokens,
        "actual_tokens": 0,
        "expected_cost_usd": float(money_for_tokens(files.estimated_tokens, config.batch_price_usd_per_million)),
        "reservation_cost_usd": float(money_for_tokens(files.estimated_tokens, config.reservation_price_usd_per_million)),
        "actual_cost_usd": 0.0,
        "completed_items": 0,
        "failed_items": 0,
        "local_input_path": str(files.input_path),
        "local_manifest_path": str(files.manifest_path),
        "last_error": "",
        "created_at_utc": now,
        "updated_at_utc": now,
    }
    base.update(overrides)
    base["updated_at_utc"] = now
    return base


def write_batch_record(client: ClickHouseHttpClient, config: PipelineConfig, row: dict[str, Any]) -> None:
    insert_json_each_row(client, config.database, config.batch_table, list(row.keys()), [row])


def write_item_states(client: ClickHouseHttpClient, config: PipelineConfig, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = [
        "embedding_version", "canonical_news_id", "ticker", "published_at_utc", "text_sha256",
        "input_tokens", "truncated", "status", "batch_key", "request_custom_id", "request_index",
        "attempts", "error_code", "error_message", "updated_at_utc",
    ]
    for offset in range(0, len(rows), 2_000):
        insert_json_each_row(client, config.database, config.item_table, columns, rows[offset:offset + 2_000])


def planned_source_rows(client: ClickHouseHttpClient, config: PipelineConfig, limit: int = 12_000) -> list[dict[str, Any]]:
    return json_rows(client, f"""
SELECT
 i.canonical_news_id AS canonical_news_id,
 i.ticker AS ticker,
 toString(i.published_at_utc) AS published_at_utc,
 i.text_sha256 AS text_sha256,
 i.input_tokens AS input_tokens,
 i.truncated AS truncated,
 i.attempts AS attempts,
 n.provider AS provider,
 n.title AS title,
 n.teaser AS teaser,
 substring(n.body_text, 1, {config.max_text_chars}) AS body_text,
 substring(n.external_text, 1, {config.max_text_chars}) AS external_text,
 substring(n.pdf_text, 1, {config.max_text_chars}) AS pdf_text,
 arrayStringConcat(n.channels, ',') AS channels,
 arrayStringConcat(n.provider_tags, ',') AS provider_tags
FROM
(
 SELECT canonical_news_id, ticker, published_at_utc, text_sha256, input_tokens, truncated, attempts
 FROM {qi(config.database)}.{qi(config.item_table)} FINAL
 WHERE embedding_version = {q(config.embedding_version)} AND status = 'planned'
 ORDER BY published_at_utc, ticker, canonical_news_id
 LIMIT {int(limit)}
) AS i
INNER JOIN {qi(config.database)}.{qi(config.source_table)} AS p FINAL
 ON p.dataset_version = {q(config.source_version)}
 AND p.canonical_news_id = i.canonical_news_id AND p.ticker = i.ticker
 AND p.published_at_utc = i.published_at_utc
ANY INNER JOIN {qi(config.news_database)}.{qi(config.news_table)} AS n FINAL
 ON n.canonical_news_id = i.canonical_news_id
ORDER BY i.published_at_utc, i.ticker, i.canonical_news_id
SETTINGS max_threads={config.clickhouse_threads}, max_memory_usage={q(config.clickhouse_memory)}
FORMAT JSONEachRow
""")


def atomic_write_lines(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            handle.write(line)
            handle.write("\n")
    temporary.replace(path)


def build_batch_files(client: ClickHouseHttpClient, config: PipelineConfig) -> BatchFiles | None:
    raw_rows = planned_source_rows(client, config)
    if not raw_rows:
        return None
    encoding = tiktoken.encoding_for_model(config.model)
    selected: list[BatchInput] = []
    selected_tokens = 0
    for row in raw_rows:
        prepared = prepare_text(row, config, encoding)
        if prepared.text_sha256 != str(row["text_sha256"]) or prepared.input_tokens != int(row["input_tokens"]):
            raise RuntimeError(f"Text drift while batching {row['canonical_news_id']} / {row['ticker']}")
        if selected and (
            selected_tokens + prepared.input_tokens > config.max_batch_tokens
            or len(selected) >= config.max_batch_inputs
        ):
            break
        selected.append(BatchInput(
            canonical_news_id=str(row["canonical_news_id"]),
            ticker=str(row["ticker"]),
            published_at_utc=str(row["published_at_utc"]),
            text_sha256=prepared.text_sha256,
            input_tokens=prepared.input_tokens,
            truncated=prepared.truncated,
            attempts=int(row.get("attempts") or 0),
            text=prepared.text,
        ))
        selected_tokens += prepared.input_tokens
    if not selected:
        return None
    digest = hashlib.sha256()
    digest.update(config.embedding_version.encode())
    for item in selected:
        digest.update(f"{item.canonical_news_id}\0{item.ticker}\0{item.published_at_utc}\0{item.text_sha256}\n".encode())
    batch_key = digest.hexdigest()
    input_path = config.input_root / f"{batch_key}.jsonl"
    manifest_path = config.input_root / f"{batch_key}.manifest.jsonl"
    request_lines: list[str] = []
    manifest_lines: list[str] = []
    item_states: list[dict[str, Any]] = []
    request_index = 0
    cursor = 0
    while cursor < len(selected):
        request_items: list[BatchInput] = []
        request_tokens = 0
        while cursor < len(selected):
            candidate = selected[cursor]
            if request_items and (
                request_tokens + candidate.input_tokens > config.max_request_tokens
                or len(request_items) >= config.max_request_inputs
            ):
                break
            request_items.append(candidate)
            request_tokens += candidate.input_tokens
            cursor += 1
        custom_id = f"{batch_key[:12]}-{request_index:05d}"
        request_lines.append(json.dumps({
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/embeddings",
            "body": {
                "model": config.model,
                "dimensions": config.dimensions,
                "encoding_format": "base64",
                "input": [item.text for item in request_items],
            },
        }, ensure_ascii=False, separators=(",", ":")))
        for index, item in enumerate(request_items):
            manifest = {
                "batch_key": batch_key,
                "custom_id": custom_id,
                "index": index,
                "canonical_news_id": item.canonical_news_id,
                "ticker": item.ticker,
                "published_at_utc": item.published_at_utc,
                "text_sha256": item.text_sha256,
                "input_tokens": item.input_tokens,
                "truncated": item.truncated,
                "attempts": item.attempts,
            }
            manifest_lines.append(json.dumps(manifest, separators=(",", ":")))
            item_states.append({
                "embedding_version": config.embedding_version,
                "canonical_news_id": item.canonical_news_id,
                "ticker": item.ticker,
                "published_at_utc": item.published_at_utc,
                "text_sha256": item.text_sha256,
                "input_tokens": item.input_tokens,
                "truncated": item.truncated,
                "status": "prepared",
                "batch_key": batch_key,
                "request_custom_id": custom_id,
                "request_index": index,
                "attempts": item.attempts + 1,
                "error_code": "",
                "error_message": "",
                "updated_at_utc": clickhouse_utc_now(),
            })
        request_index += 1
    atomic_write_lines(input_path, request_lines)
    atomic_write_lines(manifest_path, manifest_lines)
    files = BatchFiles(batch_key, input_path, manifest_path, len(selected), len(request_lines), selected_tokens)
    write_batch_record(client, config, batch_row(config, files))
    write_item_states(client, config, item_states)
    append_status(config, "batch_prepared", **asdict(files))
    return files


def files_from_batch(row: dict[str, Any]) -> BatchFiles:
    return BatchFiles(
        batch_key=str(row["batch_key"]),
        input_path=Path(str(row["local_input_path"])),
        manifest_path=Path(str(row["local_manifest_path"])),
        item_count=int(row["item_count"]),
        request_count=int(row["request_count"]),
        estimated_tokens=int(row["estimated_tokens"]),
    )


def update_batch(client: ClickHouseHttpClient, config: PipelineConfig, prior: dict[str, Any], **changes: Any) -> dict[str, Any]:
    row = dict(prior)
    row.update(changes)
    row["updated_at_utc"] = clickhouse_utc_now()
    write_batch_record(client, config, row)
    return row


def latest_batch(client: ClickHouseHttpClient, config: PipelineConfig, batch_key: str) -> dict[str, Any]:
    matches = [row for row in latest_batches(client, config) if str(row["batch_key"]) == batch_key]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one durable state for batch {batch_key}, found {len(matches)}")
    return matches[0]


def mark_batch_items(client: ClickHouseHttpClient, config: PipelineConfig, batch_key: str, status: str) -> None:
    rows = json_rows(client, f"""
SELECT embedding_version, canonical_news_id, ticker, toString(published_at_utc) AS published_at_utc,
 text_sha256, input_tokens, truncated, batch_key, request_custom_id, request_index,
 attempts, error_code, error_message
FROM {qi(config.database)}.{qi(config.item_table)} FINAL
WHERE embedding_version = {q(config.embedding_version)} AND batch_key = {q(batch_key)}
FORMAT JSONEachRow
""")
    now = clickhouse_utc_now()
    for row in rows:
        row["status"] = status
        row["updated_at_utc"] = now
    write_item_states(client, config, rows)


def block_batch_for_budget(
    client: ClickHouseHttpClient,
    config: PipelineConfig,
    batch: dict[str, Any],
    message: str,
) -> None:
    rows = json_rows(client, f"""
SELECT embedding_version, canonical_news_id, ticker, toString(published_at_utc) AS published_at_utc,
 text_sha256, input_tokens, truncated, batch_key, request_custom_id, request_index,
 attempts, error_code, error_message
FROM {qi(config.database)}.{qi(config.item_table)} FINAL
WHERE embedding_version = {q(config.embedding_version)} AND batch_key = {q(str(batch['batch_key']))}
FORMAT JSONEachRow
""")
    now = clickhouse_utc_now()
    for row in rows:
        row.update({
            "status": "failed",
            "error_code": "budget_limit",
            "error_message": message,
            "updated_at_utc": now,
        })
    write_item_states(client, config, rows)
    update_batch(
        client,
        config,
        batch,
        status="budget_blocked",
        reservation_cost_usd=0.0,
        failed_items=len(rows),
        last_error=message,
    )


def submit_batch(
    client: ClickHouseHttpClient,
    api: OpenAIHTTPClient,
    config: PipelineConfig,
    row: dict[str, Any],
    remote_by_key: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    files = files_from_batch(row)
    if not files.input_path.exists() or not files.manifest_path.exists():
        raise RuntimeError(f"Prepared batch files are missing for {files.batch_key}; refusing to reconstruct silently")
    if not str(row.get("input_file_id") or ""):
        uploaded = api.upload_batch_file(files.input_path)
        row = update_batch(client, config, row, status="uploaded", input_file_id=str(uploaded["id"]))
        append_status(config, "batch_uploaded", batch_key=files.batch_key, input_file_id=str(uploaded["id"]))
    remote = remote_by_key.get(files.batch_key)
    if remote is None:
        remote = api.create_embedding_batch(
            str(row["input_file_id"]),
            {"pipeline": "news-openai-emb-v1", "embedding_version": config.embedding_version, "batch_key": files.batch_key},
        )
    remote_status = str(remote.get("status") or "submitted")
    row = update_batch(
        client,
        config,
        row,
        status=remote_status,
        batch_id=str(remote["id"]),
        output_file_id=str(remote.get("output_file_id") or ""),
        error_file_id=str(remote.get("error_file_id") or ""),
    )
    mark_batch_items(client, config, files.batch_key, "submitted")
    append_status(config, "batch_submitted", batch_key=files.batch_key, batch_id=str(remote["id"]), status=remote_status)
    return row


def load_manifest(path: Path) -> dict[str, list[dict[str, Any]]]:
    mapping: dict[str, list[dict[str, Any]]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            mapping.setdefault(str(row["custom_id"]), []).append(row)
    for rows in mapping.values():
        rows.sort(key=lambda item: int(item["index"]))
    return mapping


def decode_embedding(value: str, dimensions: int) -> list[float]:
    raw = base64.b64decode(value, validate=True)
    if len(raw) != dimensions * 4:
        raise ValueError(f"Embedding payload has {len(raw)} bytes; expected {dimensions * 4}")
    values = array.array("f")
    values.frombytes(raw)
    if sys.byteorder != "little":
        values.byteswap()
    if len(values) != dimensions:
        raise ValueError(f"Decoded {len(values)} dimensions; expected {dimensions}")
    return values.tolist()


def request_error(row: dict[str, Any]) -> tuple[str, str]:
    error = row.get("error") or {}
    if not isinstance(error, dict):
        error = {}
    response = row.get("response") or {}
    body = response.get("body") if isinstance(response, dict) else {}
    body_error = body.get("error") if isinstance(body, dict) else {}
    if not isinstance(body_error, dict):
        body_error = {}
    code = str(error.get("code") or body_error.get("code") or "request_failed")
    message = str(error.get("message") or body_error.get("message") or "Batch request did not return an embedding")
    return code, message[:2_000]


def process_output_file(
    client: ClickHouseHttpClient,
    config: PipelineConfig,
    batch: dict[str, Any],
    output_path: Path,
) -> tuple[int, int, int, bool]:
    manifest = load_manifest(Path(str(batch["local_manifest_path"])))
    completed_states: list[dict[str, Any]] = []
    failed_states: list[dict[str, Any]] = []
    embedding_rows: list[dict[str, Any]] = []
    actual_tokens = 0
    quota_error = False
    seen_custom_ids: set[str] = set()
    with output_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            result = json.loads(line)
            custom_id = str(result.get("custom_id") or "")
            items = manifest.get(custom_id)
            if not items:
                raise RuntimeError(f"Output contains unknown custom_id {custom_id!r}")
            seen_custom_ids.add(custom_id)
            response = result.get("response") or {}
            status_code = int(response.get("status_code") or 0) if isinstance(response, dict) else 0
            body = response.get("body") if isinstance(response, dict) else None
            if status_code != 200 or not isinstance(body, dict):
                code, message = request_error(result)
                quota_error = quota_error or is_quota_message(f"{code} {message}")
                for item in items:
                    failed_states.append(item_state_from_manifest(config, item, code, message))
                continue
            data = body.get("data") or []
            if len(data) != len(items):
                raise RuntimeError(f"{custom_id} returned {len(data)} embeddings for {len(items)} inputs")
            usage = body.get("usage") or {}
            actual_tokens += int(usage.get("total_tokens") or usage.get("prompt_tokens") or 0)
            request_id = str(response.get("request_id") or body.get("id") or "")
            for item, embedding_data in zip(items, sorted(data, key=lambda value: int(value.get("index") or 0))):
                embedding = decode_embedding(str(embedding_data["embedding"]), config.dimensions)
                embedding_rows.append({
                    "embedding_version": config.embedding_version,
                    "model": str(body.get("model") or config.model),
                    "dimensions": config.dimensions,
                    "text_contract": config.text_contract,
                    "canonical_news_id": item["canonical_news_id"],
                    "ticker": item["ticker"],
                    "published_at_utc": item["published_at_utc"],
                    "text_sha256": item["text_sha256"],
                    "input_tokens": item["input_tokens"],
                    "truncated": item["truncated"],
                    "embedding": embedding,
                    "openai_request_id": request_id,
                    "batch_id": batch["batch_id"],
                    "embedded_at_utc": clickhouse_utc_now(),
                })
                completed_states.append(item_state_from_manifest(config, item, "", "", status="completed"))
            if len(embedding_rows) >= config.insert_rows:
                insert_embedding_rows(client, config, embedding_rows)
                embedding_rows.clear()
    if embedding_rows:
        insert_embedding_rows(client, config, embedding_rows)
    missing = set(manifest) - seen_custom_ids
    for custom_id in sorted(missing):
        for item in manifest[custom_id]:
            failed_states.append(item_state_from_manifest(config, item, "missing_output", "No output row was returned"))
    write_item_states(client, config, completed_states + failed_states)
    return len(completed_states), len(failed_states), actual_tokens, quota_error


def item_state_from_manifest(
    config: PipelineConfig,
    item: dict[str, Any],
    error_code: str,
    error_message: str,
    *,
    status: str = "failed",
) -> dict[str, Any]:
    attempts = int(item.get("attempts") or 0) + 1
    if status == "failed" and error_code in RETRYABLE_ITEM_CODES and attempts < MAX_ITEM_ATTEMPTS:
        status = "planned"
    return {
        "embedding_version": config.embedding_version,
        "canonical_news_id": item["canonical_news_id"],
        "ticker": item["ticker"],
        "published_at_utc": item["published_at_utc"],
        "text_sha256": item["text_sha256"],
        "input_tokens": item["input_tokens"],
        "truncated": item["truncated"],
        "status": status,
        "batch_key": "" if status == "planned" else str(item.get("batch_key") or ""),
        "request_custom_id": "" if status == "planned" else str(item.get("custom_id") or ""),
        "request_index": 0 if status == "planned" else int(item.get("index") or 0),
        "attempts": attempts,
        "error_code": error_code,
        "error_message": error_message[:2_000],
        "updated_at_utc": clickhouse_utc_now(),
    }


def insert_embedding_rows(client: ClickHouseHttpClient, config: PipelineConfig, rows: list[dict[str, Any]]) -> None:
    columns = [
        "embedding_version", "model", "dimensions", "text_contract", "canonical_news_id", "ticker",
        "published_at_utc", "text_sha256", "input_tokens", "truncated", "embedding",
        "openai_request_id", "batch_id", "embedded_at_utc",
    ]
    insert_json_each_row(client, config.database, config.embedding_table, columns, rows)


def cleanup_batch_files(api: OpenAIHTTPClient, batch: dict[str, Any], output_path: Path) -> None:
    for file_id in (str(batch.get("input_file_id") or ""), str(batch.get("output_file_id") or ""), str(batch.get("error_file_id") or "")):
        if file_id:
            try:
                api.delete_file(file_id)
            except Exception:
                pass
    for path in (Path(str(batch["local_input_path"])), Path(str(batch["local_manifest_path"])), output_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def reconcile_remote_batch(
    client: ClickHouseHttpClient,
    api: OpenAIHTTPClient,
    config: PipelineConfig,
    batch: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    remote = api.retrieve_batch(str(batch["batch_id"]))
    status = str(remote.get("status") or batch["status"])
    error_summary = batch_error_summary(remote)
    batch = update_batch(
        client,
        config,
        batch,
        status=status,
        output_file_id=str(remote.get("output_file_id") or batch.get("output_file_id") or ""),
        error_file_id=str(remote.get("error_file_id") or batch.get("error_file_id") or ""),
        last_error=error_summary,
    )
    if status not in REMOTE_TERMINAL_STATUSES:
        return batch, False
    quota_error = is_quota_message(error_summary)
    completed = failed = actual_tokens = 0
    output_file_id = str(batch.get("output_file_id") or "")
    output_path = config.output_root / f"{batch['batch_key']}.output.jsonl"
    if output_file_id:
        if not output_path.exists():
            api.download_file(output_file_id, output_path)
        completed, failed, actual_tokens, output_quota = process_output_file(client, config, batch, output_path)
        quota_error = quota_error or output_quota
    else:
        manifest = load_manifest(Path(str(batch["local_manifest_path"])))
        states: list[dict[str, Any]] = []
        error_code = "batch_expired" if status == "expired" else f"batch_{status}"
        for rows in manifest.values():
            for item in rows:
                states.append(item_state_from_manifest(config, item, error_code, error_summary or f"Batch ended as {status}"))
        failed = len(states)
        write_item_states(client, config, states)
    actual_cost = money_for_tokens(actual_tokens, config.batch_price_usd_per_million)
    batch = update_batch(
        client,
        config,
        batch,
        status="quota_blocked" if quota_error else ("completed" if failed == 0 else "completed_with_errors"),
        actual_tokens=actual_tokens,
        actual_cost_usd=float(actual_cost),
        reservation_cost_usd=0.0,
        completed_items=completed,
        failed_items=failed,
        last_error=error_summary,
    )
    append_status(
        config,
        "batch_reconciled",
        batch_key=batch["batch_key"],
        batch_id=batch["batch_id"],
        status=batch["status"],
        completed=completed,
        failed=failed,
        actual_tokens=actual_tokens,
        actual_cost_usd=str(actual_cost),
    )
    cleanup_batch_files(api, batch, output_path)
    return batch, quota_error


def usage_summary(client: ClickHouseHttpClient, config: PipelineConfig) -> UsageSummary:
    source_rows = source_count(client, config)
    item_rows = json_rows(client, f"""
SELECT
 count() AS item_rows,
 countIf(status = 'planned') AS planned_rows,
 countIf(status IN ({','.join(q(value) for value in sorted(ACTIVE_BATCH_STATUSES))})) AS active_rows,
 countIf(status = 'failed') AS failed_rows
FROM {qi(config.database)}.{qi(config.item_table)} FINAL
WHERE embedding_version = {q(config.embedding_version)}
FORMAT JSONEachRow
""")
    item = item_rows[0] if item_rows else {}
    embedded_rows = int(client.execute(f"""
SELECT count()
FROM {qi(config.database)}.{qi(config.embedding_table)} FINAL
WHERE embedding_version = {q(config.embedding_version)}
""").strip() or "0")
    batches = latest_batches(client, config)
    actual_tokens = sum(int(row.get("actual_tokens") or 0) for row in batches)
    reserved_tokens = sum(int(row.get("estimated_tokens") or 0) for row in batches if str(row.get("status")) in ACTIVE_BATCH_STATUSES)
    actual_cost = sum((Decimal(str(row.get("actual_cost_usd") or 0)) for row in batches), Decimal("0"))
    reserved_cost = sum((Decimal(str(row.get("reservation_cost_usd") or 0)) for row in batches if str(row.get("status")) in ACTIVE_BATCH_STATUSES), Decimal("0"))
    return UsageSummary(
        source_rows=source_rows,
        item_rows=int(item.get("item_rows") or 0),
        embedded_rows=embedded_rows,
        planned_rows=int(item.get("planned_rows") or 0),
        active_rows=int(item.get("active_rows") or 0),
        failed_rows=int(item.get("failed_rows") or 0),
        actual_tokens=actual_tokens,
        reserved_tokens=reserved_tokens,
        actual_cost_usd=actual_cost,
        reserved_cost_usd=reserved_cost,
    )


def print_status(summary: UsageSummary, config: PipelineConfig, *, focus: str) -> None:
    remaining = max(0, summary.source_rows - summary.embedded_rows)
    print(
        f"OPENAI NEWS EMBEDDINGS | {focus}\n"
        f"durable {summary.embedded_rows:,}/{summary.source_rows:,} | remaining {remaining:,} | "
        f"planned {summary.planned_rows:,} | active {summary.active_rows:,} | failed {summary.failed_rows:,}\n"
        f"usage actual {summary.actual_tokens:,} tokens / ${summary.actual_cost_usd:.4f} | "
        f"reserved {summary.reserved_tokens:,} / ${summary.reserved_cost_usd:.4f} | "
        f"protected total ${summary.protected_total_usd:.4f}/${config.hard_max_cost_usd:.2f}",
        flush=True,
    )


def discover_remote_batches(api: OpenAIHTTPClient, config: PipelineConfig) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in api.list_batches(limit=100):
        metadata = row.get("metadata") or {}
        if isinstance(metadata, dict) and metadata.get("embedding_version") == config.embedding_version:
            key = str(metadata.get("batch_key") or "")
            if key:
                result[key] = row
    return result


def retry_failed_items(client: ClickHouseHttpClient, config: PipelineConfig) -> int:
    rows = json_rows(client, f"""
SELECT embedding_version, canonical_news_id, ticker, toString(published_at_utc) AS published_at_utc,
 text_sha256, input_tokens, truncated, attempts, error_code, error_message
FROM {qi(config.database)}.{qi(config.item_table)} FINAL
WHERE embedding_version = {q(config.embedding_version)} AND status = 'failed' AND attempts < {MAX_ITEM_ATTEMPTS}
FORMAT JSONEachRow
""")
    for row in rows:
        row.update({
            "status": "planned",
            "batch_key": "",
            "request_custom_id": "",
            "request_index": 0,
            "error_code": "",
            "error_message": "",
            "updated_at_utc": clickhouse_utc_now(),
        })
    write_item_states(client, config, rows)
    return len(rows)


def run_pipeline(config: PipelineConfig, *, execute: bool, retry_failed: bool = False, no_wait: bool = False) -> int:
    if config.hard_max_cost_usd > Decimal("50.00"):
        raise ValueError("The pipeline hard maximum cannot exceed $50.00")
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    if not execute:
        rows = source_count(client, config)
        estimated_tokens = int(round(rows * 376.13295225562365))
        expected = money_for_tokens(estimated_tokens, config.batch_price_usd_per_million)
        protected = money_for_tokens(estimated_tokens, config.reservation_price_usd_per_million)
        print(
            f"PLAN ONLY | source={rows:,} model={config.model} dimensions={config.dimensions}\n"
            f"estimated_tokens={estimated_tokens:,} expected_batch=${expected:.2f} "
            f"protected_reservation=${protected:.2f} hard_max=${config.hard_max_cost_usd:.2f}\n"
            "No database or OpenAI writes were made. Run with --execute to pre-tokenize exactly and submit.",
            flush=True,
        )
        return 0
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing after .env discovery; no database or API write was made")
    api = OpenAIHTTPClient(api_key, project_id=os.environ.get("OPENAI_PROJECT_ID", ""))
    # Authentication is free to verify and must fail before the expensive
    # corpus scan or any durable database mutation.
    api.verify_auth()
    create_schema(client, config)
    plan_totals = plan_corpus(config, execute=True)
    source_rows = source_count(client, config)
    if plan_totals["rows"] != source_rows:
        raise RuntimeError(
            f"Corpus join coverage is incomplete: source has {source_rows:,} rows but only "
            f"{plan_totals['rows']:,} rows resolved to normalized news; no API call was made"
        )
    planned_summary = usage_summary(client, config)
    if planned_summary.item_rows != source_rows:
        raise RuntimeError(
            f"Durable item plan is incomplete: expected {source_rows:,} rows but found "
            f"{planned_summary.item_rows:,}; no API call was made"
        )
    exact_expected = money_for_tokens(plan_totals["tokens"], config.batch_price_usd_per_million)
    exact_protected = money_for_tokens(plan_totals["tokens"], config.reservation_price_usd_per_million)
    if exact_protected > config.hard_max_cost_usd:
        raise RuntimeError(
            f"Exact protected corpus cost ${exact_protected} exceeds hard maximum ${config.hard_max_cost_usd}; no API call was made"
        )
    if retry_failed:
        retried = retry_failed_items(client, config)
        print(f"Retry reset: {retried:,} bounded failed items", flush=True)
    print(
        f"AUTHORIZED | exact_tokens={plan_totals['tokens']:,} expected_batch=${exact_expected:.4f} "
        f"protected=${exact_protected:.4f}",
        flush=True,
    )
    quota_blocked = False
    while True:
        opening_summary = usage_summary(client, config)
        if opening_summary.protected_total_usd > config.hard_max_cost_usd:
            raise RuntimeError(
                f"Durable actual plus reserved spend ${opening_summary.protected_total_usd:.6f} exceeds "
                f"the ${config.hard_max_cost_usd:.2f} hard maximum; no new API call was made"
            )
        remote_by_key = discover_remote_batches(api, config)
        batches = latest_batches(client, config)
        for batch in batches:
            status = str(batch["status"])
            if status in {"prepared", "uploaded"}:
                try:
                    submit_batch(client, api, config, batch, remote_by_key)
                except OpenAIAPIError as exc:
                    durable_batch = latest_batch(client, config, str(batch["batch_key"]))
                    update_batch(client, config, durable_batch, last_error=str(exc))
                    if exc.is_quota_error:
                        quota_blocked = True
                        append_status(config, "quota_blocked", error=str(exc))
                        break
                    raise
            elif status in ACTIVE_BATCH_STATUSES and batch.get("batch_id"):
                updated, hit_quota = reconcile_remote_batch(client, api, config, batch)
                quota_blocked = quota_blocked or hit_quota
        summary = usage_summary(client, config)
        print_status(summary, config, focus="quota blocked" if quota_blocked else "reconciled")
        if quota_blocked:
            print("STOPPED | OpenAI reported insufficient quota or a billing limit. Durable progress is retained.", flush=True)
            return 3
        if summary.embedded_rows == summary.source_rows and summary.failed_rows == 0 and summary.active_rows == 0:
            append_status(config, "complete", **asdict(summary))
            print("COMPLETED | every source article has one durable embedding and no unresolved failure", flush=True)
            return 0
        if summary.failed_rows and summary.planned_rows == 0 and summary.active_rows == 0:
            append_status(config, "completed_with_failures", **asdict(summary))
            print("STOPPED | bounded failures remain; inspect status and use --retry-failed after correction", flush=True)
            return 4
        active_batches = [row for row in latest_batches(client, config) if str(row["status"]) in ACTIVE_BATCH_STATUSES]
        if len(active_batches) < config.max_inflight_batches and summary.planned_rows > 0:
            files = build_batch_files(client, config)
            if files:
                projected = summary.protected_total_usd + money_for_tokens(files.estimated_tokens, config.reservation_price_usd_per_million)
                if projected > config.hard_max_cost_usd:
                    row = latest_batch(client, config, files.batch_key)
                    message = (
                        f"Protected spend ${projected:.6f} would exceed the "
                        f"${config.hard_max_cost_usd:.2f} hard maximum"
                    )
                    block_batch_for_budget(client, config, row, message)
                    append_status(config, "budget_blocked", projected_usd=str(projected), hard_max_usd=str(config.hard_max_cost_usd))
                    print(f"STOPPED | protected spend ${projected:.4f} would exceed ${config.hard_max_cost_usd:.2f}", flush=True)
                    return 2
                row = latest_batch(client, config, files.batch_key)
                try:
                    submit_batch(client, api, config, row, remote_by_key)
                except OpenAIAPIError as exc:
                    if exc.is_quota_error:
                        append_status(config, "quota_blocked", error=str(exc))
                        print("STOPPED | OpenAI reported insufficient quota. Prepared work is retained.", flush=True)
                        return 3
                    raise
        if no_wait:
            print("SUBMITTED | --no-wait requested; rerun the same command to reconcile and continue", flush=True)
            return 0
        time.sleep(config.poll_seconds)


def audit_pipeline(config: PipelineConfig) -> dict[str, Any]:
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    create_schema(client, config)
    summary = usage_summary(client, config)
    duplicates = int(client.execute(f"""
SELECT count() FROM
(
 SELECT canonical_news_id, ticker, published_at_utc, count() AS rows
 FROM {qi(config.database)}.{qi(config.embedding_table)} FINAL
 WHERE embedding_version = {q(config.embedding_version)}
 GROUP BY canonical_news_id, ticker, published_at_utc HAVING rows > 1
)
""").strip() or "0")
    invalid_dimensions = int(client.execute(f"""
SELECT count()
FROM {qi(config.database)}.{qi(config.embedding_table)} FINAL
WHERE embedding_version = {q(config.embedding_version)} AND length(embedding) != {config.dimensions}
""").strip() or "0")
    result = {**asdict(summary), "duplicates": duplicates, "invalid_dimensions": invalid_dimensions}
    print(json.dumps(result, indent=2, default=str), flush=True)
    return result
