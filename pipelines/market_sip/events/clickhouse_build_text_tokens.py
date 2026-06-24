from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback  # noqa: E402
from research.mlops.clickhouse import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT_WIN,
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_user,
    default_storage_policy,
    discover_clickhouse_env_files,
    mergetree_settings_sql,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402


DEFAULT_SOURCE_DATABASE = "q_live"
DEFAULT_CONTEXT_DATABASE = "market_sip_compact"
DEFAULT_TARGET_DATABASE = "market_sip_compact"
DEFAULT_NEWS_TOKEN_TABLE = "news_text_tokens"
DEFAULT_SEC_TOKEN_TABLE = "sec_filing_text_tokens"
DEFAULT_SEC_TEXT_CONTEXT_TABLE = "sec_filing_text_context"
DEFAULT_OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT_WIN / "text_tokens"
DEFAULT_TOKENIZER_MODEL = "Qwen/Qwen3-0.6B"


@dataclass(frozen=True, slots=True)
class SourceBatch:
    source: str
    rows: list[dict[str, Any]]
    seconds: float


class TextTokenizer:
    def __init__(self, *, model: str, max_tokens: int, local_files_only: bool, strict: bool) -> None:
        self.model = str(model)
        self.max_tokens = max(1, int(max_tokens))
        self.tokenizer: Any | None = None
        try:
            from transformers import AutoTokenizer  # type: ignore

            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model,
                trust_remote_code=True,
                local_files_only=bool(local_files_only),
            )
        except Exception as exc:  # noqa: BLE001
            if strict:
                raise RuntimeError(f"Could not load tokenizer {self.model!r}") from exc
            self.tokenizer = None
            print(f"WARN tokenizer unavailable; using deterministic fallback: {exc!r}", flush=True)

    def encode(self, texts: list[str]) -> dict[str, list[list[int]]]:
        if not texts:
            return {"input_ids": [], "attention_mask": []}
        if self.tokenizer is not None:
            encoded = self.tokenizer(
                texts,
                max_length=self.max_tokens,
                truncation=True,
                padding="max_length",
                return_attention_mask=True,
                return_tensors=None,
            )
            return {
                "input_ids": [[int(value) for value in row] for row in encoded["input_ids"]],
                "attention_mask": [[int(value) for value in row] for row in encoded["attention_mask"]],
            }
        return fallback_tokenize(texts, max_tokens=self.max_tokens)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Tokenize market text context into ClickHouse training tables. News and SEC filing text are "
            "stored separately with source metadata plus fixed-length tokenizer outputs."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--source-database", default=DEFAULT_SOURCE_DATABASE, help="q_live database containing Benzinga news.")
    parser.add_argument("--context-database", default=DEFAULT_CONTEXT_DATABASE, help="Database containing sec_filing_text_context.")
    parser.add_argument("--target-database", default=DEFAULT_TARGET_DATABASE)
    parser.add_argument("--news-token-table", default=DEFAULT_NEWS_TOKEN_TABLE)
    parser.add_argument("--sec-token-table", default=DEFAULT_SEC_TOKEN_TABLE)
    parser.add_argument("--sec-text-context-table", default=DEFAULT_SEC_TEXT_CONTEXT_TABLE)
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--sources", default="news,sec", help="Comma-separated subset of news,sec.")
    parser.add_argument("--tokenizer-model", default=DEFAULT_TOKENIZER_MODEL)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--local-files-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--strict-tokenizer", action="store_true", help="Deprecated alias; strict mode is now the default.")
    parser.add_argument("--allow-fallback-tokenizer", action="store_true", help="Allow deterministic fallback tokens when the real tokenizer is unavailable. Use only for smoke tests.")
    parser.add_argument("--chunk-days", type=int, default=1)
    parser.add_argument("--insert-batch-size", type=int, default=2048)
    parser.add_argument("--news-text-prefix-chars", type=int, default=12000)
    parser.add_argument("--sec-text-prefix-chars", type=int, default=16000)
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument("--max-threads", type=int, default=16)
    parser.add_argument("--max-memory-usage", default="120G")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--replace-range", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--wait-mutations", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--mutation-timeout-seconds", type=int, default=7200)
    parser.add_argument("--drop-target-tables", action="store_true")
    parser.add_argument("--limit-rows-per-chunk", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    started = time.perf_counter()
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    sources = parse_sources(args.sources)
    start_date = parse_date(args.start_date)
    end_date_exclusive = parse_date(args.end_date) + timedelta(days=1)
    report_path = Path(args.output_root_win) / f"text_token_build_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 100, flush=True)
    print("Market SIP text token table builder", flush=True)
    print(f"sources={sources} source_database={args.source_database} context_database={args.context_database}", flush=True)
    print(f"target_database={args.target_database} tables={args.news_token_table},{args.sec_token_table}", flush=True)
    print(f"date_range=[{start_date.isoformat()}, {end_date_exclusive.isoformat()}) chunk_days={args.chunk_days}", flush=True)
    print(f"tokenizer={args.tokenizer_model} max_tokens={args.max_tokens} local_files_only={args.local_files_only}", flush=True)
    print(f"insert_batch_size={args.insert_batch_size} storage_policy={args.storage_policy or '<default>'}", flush=True)
    print(
        f"replace_range={args.replace_range} wait_mutations={args.wait_mutations} "
        f"drop_target_tables={args.drop_target_tables} dry_run={args.dry_run}",
        flush=True,
    )
    print(f"report={report_path}", flush=True)
    print(
        "secret_status="
        f"{secret_status(['CLICKHOUSE_URL', 'REAL_LIVE_CLICKHOUSE_WRITE_URL', 'CLICKHOUSE_WORKSTATION_USER', 'CLICKHOUSE_WORKSTATION_PASSWORD', 'CLICKHOUSE_USER', 'CLICKHOUSE_PASSWORD', 'CLICKHOUSE_HISTORICAL_STORAGE_POLICY'])}",
        flush=True,
    )
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 100, flush=True)

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    tokenizer = TextTokenizer(
        model=str(args.tokenizer_model),
        max_tokens=int(args.max_tokens),
        local_files_only=bool(args.local_files_only),
        strict=(not bool(args.allow_fallback_tokenizer)) or bool(args.strict_tokenizer),
    )
    try:
        build_tokens(
            client,
            tokenizer,
            args,
            sources=sources,
            start_date=start_date,
            end_date_exclusive=end_date_exclusive,
            report_path=report_path,
        )
    except KeyboardInterrupt:
        append_jsonl(report_path, {"operation": "build_tokens", "status": "interrupted", "elapsed_seconds": round(time.perf_counter() - started, 3)})
        print("=" * 100, flush=True)
        print(f"INTERRUPTED elapsed_minutes={(time.perf_counter() - started) / 60.0:.1f} report={report_path}", flush=True)
        print("=" * 100, flush=True)
        return 130

    print("=" * 100, flush=True)
    print(f"DONE elapsed_minutes={(time.perf_counter() - started) / 60.0:.1f} report={report_path}", flush=True)
    print("=" * 100, flush=True)
    return 0


def build_tokens(
    client: ClickHouseHttpClient,
    tokenizer: TextTokenizer,
    args: argparse.Namespace,
    *,
    sources: tuple[str, ...],
    start_date: date,
    end_date_exclusive: date,
    report_path: Path,
) -> None:
    schema_sql = [f"CREATE DATABASE IF NOT EXISTS {quote_ident(args.target_database)}"]
    if "news" in sources:
        schema_sql.append(create_news_token_table_sql(args.target_database, args.news_token_table, args.storage_policy))
    if "sec" in sources:
        schema_sql.append(create_sec_token_table_sql(args.target_database, args.sec_token_table, args.storage_policy))
    if args.drop_target_tables:
        drops = []
        if "news" in sources:
            drops.append(f"DROP TABLE IF EXISTS {quote_ident(args.target_database)}.{quote_ident(args.news_token_table)}")
        if "sec" in sources:
            drops.append(f"DROP TABLE IF EXISTS {quote_ident(args.target_database)}.{quote_ident(args.sec_token_table)}")
        schema_sql = [*drops, *schema_sql]
    for index, sql in enumerate(schema_sql, 1):
        run_sql(client, f"schema_{index}", sql, report_path, dry_run=bool(args.dry_run))

    if args.replace_range:
        if "news" in sources:
            run_sql(
                client,
                "delete_news_tokens",
                delete_range_sql(args.target_database, args.news_token_table, timestamp_column="published_at_utc", start_date=start_date, end_date_exclusive=end_date_exclusive),
                report_path,
                dry_run=bool(args.dry_run),
            )
            if args.wait_mutations and not args.dry_run:
                wait_for_mutations(
                    client,
                    database=args.target_database,
                    table=args.news_token_table,
                    timeout_seconds=int(args.mutation_timeout_seconds),
                    report_path=report_path,
                )
        if "sec" in sources:
            run_sql(
                client,
                "delete_sec_tokens",
                delete_range_sql(args.target_database, args.sec_token_table, timestamp_column="accepted_at_utc", start_date=start_date, end_date_exclusive=end_date_exclusive),
                report_path,
                dry_run=bool(args.dry_run),
            )
            if args.wait_mutations and not args.dry_run:
                wait_for_mutations(
                    client,
                    database=args.target_database,
                    table=args.sec_token_table,
                    timeout_seconds=int(args.mutation_timeout_seconds),
                    report_path=report_path,
                )

    total_rows = {"news": 0, "sec": 0}
    total_inserted = {"news": 0, "sec": 0}
    for chunk_start, chunk_end in iter_date_chunks(start_date, end_date_exclusive, days=max(1, int(args.chunk_days))):
        print("=" * 100, flush=True)
        print(f"CHUNK [{chunk_start.isoformat()}, {chunk_end.isoformat()})", flush=True)
        for source in sources:
            source_batch = fetch_source_batch(client, args, source=source, chunk_start=chunk_start, chunk_end=chunk_end)
            total_rows[source] += len(source_batch.rows)
            print(f"FETCH {source} rows={len(source_batch.rows):,} seconds={source_batch.seconds:.2f}", flush=True)
            inserted = tokenize_and_insert_source_batch(client, tokenizer, args, source_batch, report_path=report_path)
            total_inserted[source] += inserted
            append_jsonl(
                report_path,
                {
                    "operation": "chunk",
                    "source": source,
                    "chunk_start": chunk_start.isoformat(),
                    "chunk_end": chunk_end.isoformat(),
                    "source_rows": len(source_batch.rows),
                    "inserted_rows": inserted,
                    "fetch_seconds": round(source_batch.seconds, 3),
                },
            )
            print(f"INSERTED {source} rows={inserted:,} total_inserted={total_inserted[source]:,}", flush=True)

    if not args.dry_run:
        for source in sources:
            table = args.news_token_table if source == "news" else args.sec_token_table
            summarize_table(client, args.target_database, table, source=source, start_date=start_date, end_date_exclusive=end_date_exclusive, report_path=report_path)
    append_jsonl(report_path, {"operation": "complete", "source_rows": total_rows, "inserted_rows": total_inserted})


def create_news_token_table_sql(database: str, table: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    ticker LowCardinality(String),
    timestamp_us UInt64 CODEC(T64, ZSTD(1)),
    published_at_utc DateTime64(9, 'UTC') CODEC(Delta, ZSTD(1)),
    source_id String,
    provider LowCardinality(String),
    provider_article_id String,
    title String CODEC(ZSTD(3)),
    article_url String CODEC(ZSTD(3)),
    url_domain LowCardinality(String),
    channels String CODEC(ZSTD(3)),
    provider_tags String CODEC(ZSTD(3)),
    quality_flags String CODEC(ZSTD(3)),
    tokenizer_model LowCardinality(String),
    max_tokens UInt16,
    token_count UInt16,
    input_ids Array(UInt32) CODEC(ZSTD(3)),
    attention_mask Array(UInt8) CODEC(ZSTD(3)),
    text_hash UInt64,
    text_char_count UInt32,
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (ticker, timestamp_us, source_id)
{mergetree_settings_sql(storage_policy)}
"""


def create_sec_token_table_sql(database: str, table: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    ticker LowCardinality(String),
    timestamp_us UInt64 CODEC(T64, ZSTD(1)),
    accepted_at_utc DateTime64(9, 'UTC') CODEC(Delta, ZSTD(1)),
    source_id String,
    cik String,
    accession_number String,
    form_type LowCardinality(String),
    text_rank UInt8,
    document_id String,
    text_kind LowCardinality(String),
    quality_flags String CODEC(ZSTD(3)),
    tokenizer_model LowCardinality(String),
    max_tokens UInt16,
    token_count UInt16,
    input_ids Array(UInt32) CODEC(ZSTD(3)),
    attention_mask Array(UInt8) CODEC(ZSTD(3)),
    text_hash UInt64,
    text_char_count UInt32,
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(accepted_at_utc)
ORDER BY (ticker, timestamp_us, accession_number, text_rank, document_id, source_id)
{mergetree_settings_sql(storage_policy)}
"""


def fetch_source_batch(client: ClickHouseHttpClient, args: argparse.Namespace, *, source: str, chunk_start: date, chunk_end: date) -> SourceBatch:
    started = time.perf_counter()
    sql = news_source_sql(args, chunk_start=chunk_start, chunk_end=chunk_end) if source == "news" else sec_source_sql(args, chunk_start=chunk_start, chunk_end=chunk_end)
    rows = [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
    return SourceBatch(source=source, rows=rows, seconds=time.perf_counter() - started)


def news_source_sql(args: argparse.Namespace, *, chunk_start: date, chunk_end: date) -> str:
    db = quote_ident(args.source_database)
    limit_sql = f"\nLIMIT {int(args.limit_rows_per_chunk)}" if int(args.limit_rows_per_chunk) > 0 else ""
    text_chars = max(1, int(args.news_text_prefix_chars))
    return f"""
SELECT
    nt.ticker AS ticker,
    toUInt64(toUnixTimestamp64Micro(nt.published_at_utc)) AS timestamp_us,
    nt.published_at_utc AS published_at_utc,
    nt.canonical_news_id AS source_id,
    nt.provider AS provider,
    nt.provider_article_id AS provider_article_id,
    n.title AS title,
    n.teaser AS teaser,
    substring(n.normalized_full_text, 1, {text_chars}) AS text,
    n.article_url AS article_url,
    n.url_domain AS url_domain,
    arrayStringConcat(n.channels, ',') AS channels,
    arrayStringConcat(n.provider_tags, ',') AS provider_tags,
    arrayStringConcat(n.content_quality_flags, ',') AS quality_flags
FROM {db}.benzinga_news_ticker_v1 AS nt
ANY INNER JOIN {db}.benzinga_news_normalized_v1 AS n
    ON nt.canonical_news_id = n.canonical_news_id
WHERE nt.published_at_utc >= {date_time64_sql(chunk_start)}
  AND nt.published_at_utc < {date_time64_sql(chunk_end)}
ORDER BY nt.ticker, nt.published_at_utc, nt.canonical_news_id
{limit_sql}
{query_settings(args)}
FORMAT JSONEachRow
"""


def sec_source_sql(args: argparse.Namespace, *, chunk_start: date, chunk_end: date) -> str:
    table = f"{quote_ident(args.context_database)}.{quote_ident(args.sec_text_context_table)}"
    limit_sql = f"\nLIMIT {int(args.limit_rows_per_chunk)}" if int(args.limit_rows_per_chunk) > 0 else ""
    text_chars = max(1, int(args.sec_text_prefix_chars))
    return f"""
SELECT
    ticker,
    timestamp_us,
    accepted_at_utc,
    concat(accession_number, ':', toString(text_rank), ':', document_id) AS source_id,
    cik,
    accession_number,
    form_type,
    text_rank,
    document_id,
    text_kind,
    substring(text, 1, {text_chars}) AS text,
    text_char_count,
    quality_flags
FROM {table}
WHERE accepted_at_utc >= {date_time64_sql(chunk_start)}
  AND accepted_at_utc < {date_time64_sql(chunk_end)}
ORDER BY ticker, accepted_at_utc, accession_number, text_rank, document_id
{limit_sql}
{query_settings(args)}
FORMAT JSONEachRow
"""


def tokenize_and_insert_source_batch(
    client: ClickHouseHttpClient,
    tokenizer: TextTokenizer,
    args: argparse.Namespace,
    source_batch: SourceBatch,
    *,
    report_path: Path,
) -> int:
    if not source_batch.rows:
        return 0
    table = args.news_token_table if source_batch.source == "news" else args.sec_token_table
    target = f"{quote_ident(args.target_database)}.{quote_ident(table)}"
    inserted = 0
    batch_size = max(1, int(args.insert_batch_size))
    for start in range(0, len(source_batch.rows), batch_size):
        source_rows = source_batch.rows[start : start + batch_size]
        texts = [news_model_text(row) if source_batch.source == "news" else sec_model_text(row) for row in source_rows]
        encoded = tokenizer.encode(texts)
        insert_rows = []
        for index, row in enumerate(source_rows):
            text = texts[index]
            input_ids = encoded["input_ids"][index]
            attention_mask = encoded["attention_mask"][index]
            token_count = int(sum(1 for value in attention_mask if int(value) != 0))
            if source_batch.source == "news":
                insert_rows.append(news_token_row(args, row, text, input_ids, attention_mask, token_count))
            else:
                insert_rows.append(sec_token_row(args, row, text, input_ids, attention_mask, token_count))
        if not args.dry_run:
            started = time.perf_counter()
            insert_json_each_row(client, target, insert_rows)
            seconds = time.perf_counter() - started
        else:
            seconds = 0.0
        inserted += len(insert_rows)
        append_jsonl(
            report_path,
            {
                "operation": "insert_batch",
                "source": source_batch.source,
                "rows": len(insert_rows),
                "inserted_total": inserted,
                "seconds": round(seconds, 3),
            },
        )
    return inserted


def news_model_text(row: dict[str, Any]) -> str:
    parts = [
        "NEWS",
        f"provider: {row.get('provider', '') or ''}",
        f"ticker: {row.get('ticker', '') or ''}",
        f"published_at_utc: {row.get('published_at_utc', '') or ''}",
        f"title: {row.get('title', '') or ''}",
        f"teaser: {row.get('teaser', '') or ''}",
        f"channels: {row.get('channels', '') or ''}",
        f"tags: {row.get('provider_tags', '') or ''}",
        str(row.get("text", "") or ""),
    ]
    return "\n".join(part for part in parts if str(part).strip())


def sec_model_text(row: dict[str, Any]) -> str:
    parts = [
        "SEC FILING",
        f"form: {row.get('form_type', '') or ''}",
        f"ticker: {row.get('ticker', '') or ''}",
        f"cik: {row.get('cik', '') or ''}",
        f"accession: {row.get('accession_number', '') or ''}",
        f"accepted_at_utc: {row.get('accepted_at_utc', '') or ''}",
        f"text_kind: {row.get('text_kind', '') or ''}",
        str(row.get("text", "") or ""),
    ]
    return "\n".join(part for part in parts if str(part).strip())


def news_token_row(args: argparse.Namespace, row: dict[str, Any], text: str, input_ids: list[int], attention_mask: list[int], token_count: int) -> dict[str, Any]:
    return {
        "ticker": str(row.get("ticker", "") or "").upper(),
        "timestamp_us": int(row.get("timestamp_us", 0) or 0),
        "published_at_utc": str(row.get("published_at_utc", "") or ""),
        "source_id": str(row.get("source_id", "") or ""),
        "provider": str(row.get("provider", "") or ""),
        "provider_article_id": str(row.get("provider_article_id", "") or ""),
        "title": str(row.get("title", "") or ""),
        "article_url": str(row.get("article_url", "") or ""),
        "url_domain": str(row.get("url_domain", "") or ""),
        "channels": str(row.get("channels", "") or ""),
        "provider_tags": str(row.get("provider_tags", "") or ""),
        "quality_flags": str(row.get("quality_flags", "") or ""),
        "tokenizer_model": str(args.tokenizer_model),
        "max_tokens": int(args.max_tokens),
        "token_count": int(token_count),
        "input_ids": [int(value) for value in input_ids],
        "attention_mask": [int(value) for value in attention_mask],
        "text_hash": stable_uint64(text),
        "text_char_count": len(text),
    }


def sec_token_row(args: argparse.Namespace, row: dict[str, Any], text: str, input_ids: list[int], attention_mask: list[int], token_count: int) -> dict[str, Any]:
    return {
        "ticker": str(row.get("ticker", "") or "").upper(),
        "timestamp_us": int(row.get("timestamp_us", 0) or 0),
        "accepted_at_utc": str(row.get("accepted_at_utc", "") or ""),
        "source_id": str(row.get("source_id", "") or ""),
        "cik": str(row.get("cik", "") or ""),
        "accession_number": str(row.get("accession_number", "") or ""),
        "form_type": str(row.get("form_type", "") or ""),
        "text_rank": int(row.get("text_rank", 0) or 0),
        "document_id": str(row.get("document_id", "") or ""),
        "text_kind": str(row.get("text_kind", "") or ""),
        "quality_flags": str(row.get("quality_flags", "") or ""),
        "tokenizer_model": str(args.tokenizer_model),
        "max_tokens": int(args.max_tokens),
        "token_count": int(token_count),
        "input_ids": [int(value) for value in input_ids],
        "attention_mask": [int(value) for value in attention_mask],
        "text_hash": stable_uint64(text),
        "text_char_count": int(row.get("text_char_count", 0) or len(text)),
    }


def insert_json_each_row(client: ClickHouseHttpClient, table: str, rows: list[dict[str, Any]]) -> None:
    payload = "\n".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False) for row in rows)
    if payload:
        client.execute(f"INSERT INTO {table} FORMAT JSONEachRow\n{payload}")


def delete_range_sql(database: str, table: str, *, timestamp_column: str, start_date: date, end_date_exclusive: date) -> str:
    return f"""
ALTER TABLE {quote_ident(database)}.{quote_ident(table)}
DELETE WHERE {quote_ident(timestamp_column)} >= {date_time64_sql(start_date)}
  AND {quote_ident(timestamp_column)} < {date_time64_sql(end_date_exclusive)}
"""


def summarize_table(client: ClickHouseHttpClient, database: str, table: str, *, source: str, start_date: date, end_date_exclusive: date, report_path: Path) -> None:
    timestamp_column = "published_at_utc" if source == "news" else "accepted_at_utc"
    sql = f"""
SELECT
    count() AS rows,
    uniqExact(ticker) AS tickers,
    min({quote_ident(timestamp_column)}) AS min_timestamp,
    max({quote_ident(timestamp_column)}) AS max_timestamp,
    avg(token_count) AS avg_token_count,
    max(token_count) AS max_token_count
FROM {quote_ident(database)}.{quote_ident(table)}
WHERE {quote_ident(timestamp_column)} >= {date_time64_sql(start_date)}
  AND {quote_ident(timestamp_column)} < {date_time64_sql(end_date_exclusive)}
FORMAT JSONEachRow
"""
    started = time.perf_counter()
    raw = client.execute(sql).strip()
    seconds = time.perf_counter() - started
    summary = json.loads(raw) if raw else {}
    append_jsonl(report_path, {"operation": "summary", "source": source, "table": table, "seconds": round(seconds, 3), **summary})
    print(
        f"SUMMARY {source} table={table} rows={int(summary.get('rows', 0)):,} tickers={int(summary.get('tickers', 0)):,} "
        f"avg_tokens={float(summary.get('avg_token_count', 0) or 0):.1f} seconds={seconds:.1f}",
        flush=True,
    )


def wait_for_mutations(client: ClickHouseHttpClient, *, database: str, table: str, timeout_seconds: int, report_path: Path) -> None:
    deadline = time.perf_counter() + float(timeout_seconds)
    while True:
        sql = f"""
SELECT count()
FROM system.mutations
WHERE database = {sql_string(database)}
  AND table = {sql_string(table)}
  AND is_done = 0
FORMAT TSV
"""
        pending = int((client.execute(sql).strip() or "0").splitlines()[0])
        if pending == 0:
            print(f"MUTATIONS DONE table={table}", flush=True)
            append_jsonl(report_path, {"operation": "wait_mutations", "table": table, "pending": pending, "status": "done"})
            return
        if time.perf_counter() >= deadline:
            raise TimeoutError(f"Timed out waiting for mutations on {database}.{table}; pending={pending}")
        print(f"MUTATIONS WAIT table={table} pending={pending}", flush=True)
        time.sleep(5.0)


def run_sql(client: ClickHouseHttpClient, label: str, sql: str, report_path: Path, *, dry_run: bool) -> None:
    print(f"SQL START {label}", flush=True)
    started = time.perf_counter()
    status = "dry_run"
    error = ""
    if not dry_run:
        try:
            client.execute(sql)
            status = "ok"
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            error = repr(exc)
    seconds = time.perf_counter() - started
    append_jsonl(report_path, {"operation": "sql", "label": label, "status": status, "seconds": round(seconds, 3), "error": error})
    if error:
        print(f"SQL FAILED {label}: {error}", flush=True)
        raise RuntimeError(f"{label} failed: {error}")
    print(f"SQL DONE {label} status={status} seconds={seconds:.1f}", flush=True)


def fallback_tokenize(texts: list[str], *, max_tokens: int) -> dict[str, list[list[int]]]:
    import re

    input_ids: list[list[int]] = []
    attention_mask: list[list[int]] = []
    for text in texts:
        ids = [0] * int(max_tokens)
        mask = [0] * int(max_tokens)
        tokens = re.findall(r"\w+|[^\w\s]", str(text).lower(), flags=re.UNICODE)[: int(max_tokens)]
        for index, token in enumerate(tokens):
            ids[index] = int(stable_uint64(token) % 151_936) + 1
            mask[index] = 1
        input_ids.append(ids)
        attention_mask.append(mask)
    return {"input_ids": input_ids, "attention_mask": attention_mask}


def iter_date_chunks(start_date: date, end_date_exclusive: date, *, days: int) -> Iterable[tuple[date, date]]:
    current = start_date
    while current < end_date_exclusive:
        next_date = min(current + timedelta(days=max(1, int(days))), end_date_exclusive)
        yield current, next_date
        current = next_date


def parse_sources(text: str) -> tuple[str, ...]:
    sources = tuple(item.strip().lower() for item in text.split(",") if item.strip())
    invalid = [item for item in sources if item not in {"news", "sec"}]
    if invalid:
        raise ValueError(f"Invalid sources {invalid}; expected subset of news,sec")
    return sources or ("news", "sec")


def parse_date(text: str) -> date:
    return date.fromisoformat(str(text)[:10])


def date_time64_sql(value: date) -> str:
    return f"toDateTime64({sql_string(value.isoformat() + ' 00:00:00')}, 9, 'UTC')"


def query_settings(args: argparse.Namespace) -> str:
    settings: list[str] = []
    if int(args.max_threads) > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings) if settings else ""


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, sort_keys=True) + "\n")


def stable_uint64(value: Any) -> int:
    data = str(value or "").encode("utf-8", errors="ignore")
    if not data:
        return 0
    digest = hashlib.blake2b(data, digest_size=8).digest()
    return int.from_bytes(digest, "little", signed=False)


if __name__ == "__main__":
    raise SystemExit(main())
