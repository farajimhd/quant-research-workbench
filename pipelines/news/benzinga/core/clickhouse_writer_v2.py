from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from pipelines.news.benzinga.core.clickhouse_writer import (
    NewsBatchWriteSummary,
    NewsWriteSummary,
    insert_json_each_row,
    table_exists,
)
from pipelines.news.benzinga.core.contracts import NewsPipelineResult
from pipelines.news.benzinga.news_benzinga_render_v2 import NEWS_RENDERER_VERSION
from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string


DEFAULT_DATABASE = "q_live"
DEFAULT_EVENT_TABLE = "benzinga_news_event_v2"
DEFAULT_SOURCE_TABLE = "benzinga_news_source_v2"
DEFAULT_BLOCK_TABLE = "benzinga_news_block_v2"
DEFAULT_RENDERED_TABLE = "benzinga_news_rendered_v2"
DEFAULT_TICKER_TABLE = "benzinga_news_ticker_v2"
DEFAULT_AUTHORITY_TABLE = "benzinga_news_render_authority_v2"
DEFAULT_INSERT_MAX_ROWS = 500
DEFAULT_INSERT_TARGET_BYTES = 4 * 1024 * 1024
DEFAULT_INSERT_MAX_ROW_BYTES = 8 * 1024 * 1024

EVENT_COLUMNS = [
    "provider", "provider_article_id", "canonical_news_id", "published_date",
    "published_at_utc", "published_raw", "last_updated_at_utc", "last_updated_raw",
    "downloaded_at_utc", "provider_delay_ns", "title", "normalized_title", "teaser",
    "article_url", "url_domain", "author", "tickers", "channels", "provider_tags",
    "image_urls", "links", "raw_artifact_path", "raw_payload_hash",
    "source_revision_key", "renderer_version", "content_quality_flags", "updated_at_utc",
]
SOURCE_COLUMNS = [
    "canonical_news_id", "published_date", "source_kind", "source_ordinal",
    "source_url", "artifact_path", "content_format", "source_hash", "source_chars",
    "rendered_text", "rendered_hash", "block_count", "table_block_count",
    "quality_flags", "renderer_version", "source_revision_key", "updated_at_utc",
]
BLOCK_COLUMNS = [
    "canonical_news_id", "published_date", "source_kind", "source_ordinal",
    "block_ordinal", "block_kind", "block_text", "block_hash", "table_ordinal",
    "table_row_ordinal", "renderer_version", "source_revision_key", "updated_at_utc",
]
RENDERED_COLUMNS = [
    "canonical_news_id", "provider_article_id", "published_date", "published_at_utc",
    "title", "rendered_text", "rendered_text_hash", "source_revision_key",
    "source_count", "block_count", "renderer_version", "text_contract",
    "quality_flags", "updated_at_utc",
]
TICKER_COLUMNS = [
    "canonical_news_id", "provider_article_id", "published_date", "published_at_utc",
    "ticker", "ticker_index", "ticker_count", "rendered_text_hash",
    "source_revision_key", "renderer_version", "updated_at_utc",
]


@dataclass(frozen=True, slots=True)
class JsonEachRowBatch:
    rows: list[dict[str, Any]]
    body_bytes: int
    max_row_bytes: int


class OversizedNewsRowError(RuntimeError):
    """A serialized v2 product row exceeds the safe ClickHouse contract."""


@dataclass(frozen=True, slots=True)
class NewsV2TargetConfig:
    database: str = DEFAULT_DATABASE
    event_table: str = DEFAULT_EVENT_TABLE
    source_table: str = DEFAULT_SOURCE_TABLE
    block_table: str = DEFAULT_BLOCK_TABLE
    rendered_table: str = DEFAULT_RENDERED_TABLE
    ticker_table: str = DEFAULT_TICKER_TABLE
    authority_table: str = DEFAULT_AUTHORITY_TABLE
    execute: bool = False
    require_ready: bool = True
    skip_table_validation: bool = False


def create_v2_tables(client: ClickHouseHttpClient, config: NewsV2TargetConfig) -> None:
    db = quote_ident(config.database)
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(config.event_table)}
(
 provider LowCardinality(String), provider_article_id String, canonical_news_id String,
 published_date Date, published_at_utc DateTime64(9, 'UTC'), published_raw String,
 last_updated_at_utc Nullable(DateTime64(9, 'UTC')), last_updated_raw String,
 downloaded_at_utc DateTime64(9, 'UTC'), provider_delay_ns Nullable(Int64),
 title String, normalized_title String, teaser String, article_url String,
 url_domain LowCardinality(String), author String, tickers Array(String),
 channels Array(String), provider_tags Array(String), image_urls Array(String),
 links Array(String), raw_artifact_path String, raw_payload_hash String,
 source_revision_key FixedString(64), renderer_version LowCardinality(String),
 content_quality_flags Array(LowCardinality(String)), updated_at_utc DateTime64(6, 'UTC')
)
ENGINE=ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (published_date, provider_article_id)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(config.source_table)}
(
 canonical_news_id String, published_date Date, source_kind LowCardinality(String),
 source_ordinal UInt16, source_url String, artifact_path String,
 content_format LowCardinality(String), source_hash FixedString(64), source_chars UInt64,
 rendered_text String, rendered_hash FixedString(64), block_count UInt32,
 table_block_count UInt32, quality_flags Array(LowCardinality(String)),
 renderer_version LowCardinality(String), source_revision_key FixedString(64),
 updated_at_utc DateTime64(6, 'UTC')
)
ENGINE=ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(published_date)
ORDER BY (published_date, canonical_news_id, source_revision_key, source_kind, source_ordinal)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(config.block_table)}
(
 canonical_news_id String, published_date Date, source_kind LowCardinality(String),
 source_ordinal UInt16, block_ordinal UInt32, block_kind LowCardinality(String),
 block_text String, block_hash FixedString(64), table_ordinal UInt16,
 table_row_ordinal UInt32, renderer_version LowCardinality(String),
 source_revision_key FixedString(64), updated_at_utc DateTime64(6, 'UTC')
)
ENGINE=ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(published_date)
ORDER BY (published_date, canonical_news_id, source_revision_key, source_kind, source_ordinal, block_ordinal)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(config.rendered_table)}
(
 canonical_news_id String, provider_article_id String, published_date Date,
 published_at_utc DateTime64(9, 'UTC'), title String, rendered_text String,
 rendered_text_hash FixedString(64), source_revision_key FixedString(64),
 source_count UInt16, block_count UInt32, renderer_version LowCardinality(String),
 text_contract LowCardinality(String), quality_flags Array(LowCardinality(String)),
 updated_at_utc DateTime64(6, 'UTC')
)
ENGINE=ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (published_date, provider_article_id)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(config.ticker_table)}
(
 canonical_news_id String, provider_article_id String, published_date Date,
 published_at_utc DateTime64(9, 'UTC'), ticker LowCardinality(String),
 ticker_index UInt16, ticker_count UInt16, rendered_text_hash FixedString(64),
 source_revision_key FixedString(64), renderer_version LowCardinality(String),
 updated_at_utc DateTime64(6, 'UTC')
)
ENGINE=ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (published_date, ticker, published_at_utc, canonical_news_id, source_revision_key)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(config.authority_table)}
(
 authority_version LowCardinality(String), run_id String, status LowCardinality(String),
 source_rows UInt64, event_rows UInt64, rendered_rows UInt64, source_parts UInt64,
 block_rows UInt64, ticker_rows UInt64, audit_errors UInt64, audit_report_path String,
 started_at_utc DateTime64(6, 'UTC'), updated_at_utc DateTime64(6, 'UTC')
)
ENGINE=ReplacingMergeTree(updated_at_utc)
ORDER BY authority_version
""")


def assert_v2_ready(client: ClickHouseHttpClient, config: NewsV2TargetConfig) -> None:
    validate_v2_tables(client, config)
    if not config.require_ready:
        return
    table = f"{quote_ident(config.database)}.{quote_ident(config.authority_table)}"
    sql = (
        f"SELECT status, audit_errors FROM {table} FINAL "
        f"WHERE authority_version={sql_string(NEWS_RENDERER_VERSION)} "
        "ORDER BY updated_at_utc DESC LIMIT 1 FORMAT JSONEachRow"
    )
    rows = [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
    if not rows or rows[0].get("status") != "ready" or int(rows[0].get("audit_errors") or 0):
        raise RuntimeError(
            "Benzinga v2 rendering authority is not ready. Stop the gateway, run "
            "`python -m pipelines.news.benzinga.run_news_rendered_v2_rebuild --execute`, "
            "and restart only after the audit reports status=ready."
        )


def validate_v2_tables(client: ClickHouseHttpClient, config: NewsV2TargetConfig) -> None:
    required = [
        config.event_table, config.source_table, config.block_table,
        config.rendered_table, config.ticker_table, config.authority_table,
    ]
    missing = [name for name in required if not table_exists(client, config.database, name)]
    if missing:
        raise RuntimeError(f"missing Benzinga v2 ClickHouse tables in {config.database}: {missing}")


def write_news_pipeline_result_v2(
    client: ClickHouseHttpClient,
    result: NewsPipelineResult,
    *,
    config: NewsV2TargetConfig,
) -> NewsWriteSummary:
    summary = write_many_news_pipeline_results_v2(client, [result], config=config)
    return NewsWriteSummary(
        status=summary.status,
        execute=summary.execute,
        canonical_news_id=result.canonical_news_id,
        provider_article_id=result.provider_article_id,
        normalized_rows_inserted=summary.normalized_rows_inserted,
        ticker_rows_inserted=summary.ticker_rows_inserted,
        existing_normalized_rows=0,
        existing_tickers=[],
        new_tickers=sorted({str(row.get("ticker") or "") for row in result.v2_ticker_links}),
        warnings=summary.warnings,
    )


def write_many_news_pipeline_results_v2(
    client: ClickHouseHttpClient,
    results: list[NewsPipelineResult],
    *,
    config: NewsV2TargetConfig,
) -> NewsBatchWriteSummary:
    if not config.skip_table_validation:
        assert_v2_ready(client, config)
    events, sources, blocks, rendered, tickers = [], [], [], [], []
    seen_provider: set[tuple[str, str]] = set()
    duplicate_ids: list[str] = []
    for result in results:
        if not result.v2_event_row or not result.v2_rendered_row:
            raise RuntimeError(f"pipeline result lacks v2 rendering rows: {result.canonical_news_id}")
        key = (str(result.v2_event_row["published_date"]), str(result.provider_article_id))
        if key in seen_provider:
            duplicate_ids.append(result.canonical_news_id)
            continue
        seen_provider.add(key)
        events.append(result.v2_event_row)
        sources.extend(result.v2_source_rows)
        blocks.extend(result.v2_block_rows)
        rendered.append(result.v2_rendered_row)
        tickers.extend(result.v2_ticker_links)
    if config.execute and events:
        products = [
            (config.event_table, EVENT_COLUMNS, events),
            (config.source_table, SOURCE_COLUMNS, sources),
            (config.block_table, BLOCK_COLUMNS, blocks),
            (config.rendered_table, RENDERED_COLUMNS, rendered),
            (config.ticker_table, TICKER_COLUMNS, tickers),
        ]
        planned = [
            (
                table,
                columns,
                list(
                    json_each_row_batches(
                        rows,
                        table=table,
                        max_rows=DEFAULT_INSERT_MAX_ROWS,
                        target_bytes=DEFAULT_INSERT_TARGET_BYTES,
                        max_row_bytes=DEFAULT_INSERT_MAX_ROW_BYTES,
                    )
                ),
            )
            for table, columns, rows in products
        ]
        for table, columns, batches in planned:
            for batch in batches:
                insert_json_each_row(
                    client,
                    config.database,
                    table,
                    columns,
                    batch.rows,
                )
    return NewsBatchWriteSummary(
        status="written" if config.execute else "dry_run",
        execute=config.execute,
        input_results=len(results),
        normalized_rows_inserted=len(events) if config.execute else 0,
        ticker_rows_inserted=len(tickers) if config.execute else 0,
        skipped_existing=0,
        skipped_existing_ids=[],
        input_duplicate_ids=sorted(set(duplicate_ids)),
        input_duplicate_provider_keys=[],
        stale_ticker_rows_deleted=0,
        warnings=[],
    )


def insert_v2_json_each_row_bounded(
    client: ClickHouseHttpClient,
    database: str,
    table: str,
    columns: list[str],
    rows: Iterable[dict[str, Any]],
    *,
    max_rows: int = DEFAULT_INSERT_MAX_ROWS,
    target_bytes: int = DEFAULT_INSERT_TARGET_BYTES,
    max_row_bytes: int = DEFAULT_INSERT_MAX_ROW_BYTES,
) -> None:
    batches = list(
        json_each_row_batches(
            rows,
            table=table,
            max_rows=max_rows,
            target_bytes=target_bytes,
            max_row_bytes=max_row_bytes,
        )
    )
    for batch in batches:
        insert_json_each_row(client, database, table, columns, batch.rows)


def json_each_row_batches(
    rows: Iterable[dict[str, Any]],
    *,
    table: str,
    max_rows: int,
    target_bytes: int,
    max_row_bytes: int,
) -> Iterable[JsonEachRowBatch]:
    """Partition v2 rows by encoded bytes and count without truncating content."""
    if min(max_rows, target_bytes, max_row_bytes) <= 0:
        raise ValueError("JSONEachRow batch limits must be greater than zero")
    current: list[dict[str, Any]] = []
    current_bytes = 0
    current_max_row_bytes = 0
    for row in rows:
        encoded_bytes = len(
            json.dumps(
                row,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
        if encoded_bytes > max_row_bytes:
            identity = safe_product_identity(row)
            raise OversizedNewsRowError(
                f"{table} row exceeds safe JSONEachRow limit: "
                f"row_bytes={encoded_bytes:,} limit={max_row_bytes:,} identity={identity}"
            )
        separator_bytes = 1 if current else 0
        if current and (
            len(current) >= max_rows
            or current_bytes + separator_bytes + encoded_bytes > target_bytes
        ):
            yield JsonEachRowBatch(
                rows=current,
                body_bytes=current_bytes,
                max_row_bytes=current_max_row_bytes,
            )
            current = []
            current_bytes = 0
            current_max_row_bytes = 0
            separator_bytes = 0
        current.append(row)
        current_bytes += separator_bytes + encoded_bytes
        current_max_row_bytes = max(current_max_row_bytes, encoded_bytes)
    if current:
        yield JsonEachRowBatch(
            rows=current,
            body_bytes=current_bytes,
            max_row_bytes=current_max_row_bytes,
        )


def safe_product_identity(row: dict[str, Any]) -> str:
    fields = (
        "published_date",
        "provider_article_id",
        "canonical_news_id",
        "source_kind",
        "source_ordinal",
        "block_ordinal",
        "ticker",
    )
    return ",".join(
        f"{field}={str(row[field])[:96]}"
        for field in fields
        if row.get(field) not in (None, "")
    ) or "unavailable"
