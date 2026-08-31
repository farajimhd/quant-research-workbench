from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pipelines.news.benzinga.core.clickhouse_writer import NewsBatchWriteSummary, insert_json_each_row, table_exists
from pipelines.news.benzinga.core.clickhouse_writer_v2 import json_each_row_batches, v2_batch_query_id
from pipelines.news.benzinga.core.contracts import NewsPipelineResult
from pipelines.news.benzinga.news_benzinga_body_v3 import BODY_RENDERER_VERSION
from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string


DEFAULT_DATABASE = "q_live"
DEFAULT_EVENT_TABLE = "benzinga_news_event_v3"
DEFAULT_SOURCE_TABLE = "benzinga_news_source_v3"
DEFAULT_BLOCK_TABLE = "benzinga_news_block_v3"
DEFAULT_RENDERED_TABLE = "benzinga_news_rendered_v3"
DEFAULT_TICKER_TABLE = "benzinga_news_ticker_v3"
DEFAULT_LINEAGE_TABLE = "benzinga_news_body_lineage_v1"
DEFAULT_AUTHORITY_TABLE = "benzinga_news_body_authority_v1"

EVENT_COLUMNS = [
    "provider", "provider_article_id", "canonical_news_id", "published_date", "published_at_utc",
    "published_raw", "last_updated_at_utc", "last_updated_raw", "downloaded_at_utc", "provider_delay_ns",
    "title", "normalized_title", "teaser", "article_url", "url_domain", "author", "tickers", "channels",
    "provider_tags", "image_urls", "links", "raw_artifact_path", "raw_payload_hash", "source_revision_key",
    "source_selection_version", "cleaner_version", "renderer_version", "content_quality_flags", "updated_at_utc",
]
SOURCE_COLUMNS = [
    "canonical_news_id", "published_date", "source_kind", "source_ordinal", "source_role", "disposition",
    "disposition_reason", "identity_score", "source_url", "artifact_path", "content_format", "original_hash",
    "original_chars", "cleaned_text", "cleaned_hash", "cleaned_chars", "block_count", "included_block_count",
    "quality_flags", "source_selection_version", "cleaner_version", "renderer_version", "source_revision_key",
    "updated_at_utc",
]
BLOCK_COLUMNS = [
    "canonical_news_id", "published_date", "source_kind", "source_ordinal", "block_ordinal", "block_kind",
    "block_role", "disposition", "disposition_reason", "original_text", "cleaned_text", "original_hash",
    "cleaned_hash", "table_ordinal", "table_row_ordinal", "cleaner_version", "source_revision_key", "updated_at_utc",
]
RENDERED_COLUMNS = [
    "canonical_news_id", "provider_article_id", "published_date", "published_at_utc", "title",
    "canonical_body_text", "display_text", "body_hash", "body_status", "primary_source_kind",
    "primary_source_ordinal", "source_revision_key", "source_count", "included_source_count",
    "supporting_source_count", "excluded_source_count", "included_block_count", "excluded_block_count",
    "source_selection_version", "cleaner_version", "renderer_version", "text_contract", "quality_flags", "updated_at_utc",
]
TICKER_COLUMNS = [
    "canonical_news_id", "provider_article_id", "published_date", "published_at_utc", "ticker", "ticker_index",
    "ticker_count", "body_hash", "source_revision_key", "renderer_version", "updated_at_utc",
]
LINEAGE_COLUMNS = [
    "canonical_news_id", "provider_article_id", "published_date", "previous_rendered_text_hash",
    "previous_renderer_version", "body_hash", "body_renderer_version", "source_revision_key",
    "label_mutation_status", "updated_at_utc",
]


@dataclass(frozen=True, slots=True)
class NewsBodyV3TargetConfig:
    database: str = DEFAULT_DATABASE
    event_table: str = DEFAULT_EVENT_TABLE
    source_table: str = DEFAULT_SOURCE_TABLE
    block_table: str = DEFAULT_BLOCK_TABLE
    rendered_table: str = DEFAULT_RENDERED_TABLE
    ticker_table: str = DEFAULT_TICKER_TABLE
    lineage_table: str = DEFAULT_LINEAGE_TABLE
    authority_table: str = DEFAULT_AUTHORITY_TABLE
    execute: bool = False
    require_certified: bool = False
    skip_table_validation: bool = False


def create_body_v3_tables(client: ClickHouseHttpClient, config: NewsBodyV3TargetConfig) -> None:
    db = quote_ident(config.database)
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(config.event_table)}
(
 provider LowCardinality(String), provider_article_id String, canonical_news_id String, published_date Date,
 published_at_utc DateTime64(9, 'UTC'), published_raw String, last_updated_at_utc Nullable(DateTime64(9, 'UTC')),
 last_updated_raw String, downloaded_at_utc DateTime64(9, 'UTC'), provider_delay_ns Nullable(Int64), title String,
 normalized_title String, teaser String, article_url String, url_domain LowCardinality(String), author String,
 tickers Array(String), channels Array(String), provider_tags Array(String), image_urls Array(String), links Array(String),
 raw_artifact_path String, raw_payload_hash String, source_revision_key FixedString(64),
 source_selection_version LowCardinality(String), cleaner_version LowCardinality(String),
 renderer_version LowCardinality(String), content_quality_flags Array(LowCardinality(String)),
 updated_at_utc DateTime64(6, 'UTC')
) ENGINE=ReplacingMergeTree(updated_at_utc) PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (published_date, provider_article_id)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(config.source_table)}
(
 canonical_news_id String, published_date Date, source_kind LowCardinality(String), source_ordinal UInt16,
 source_role LowCardinality(String), disposition LowCardinality(String), disposition_reason LowCardinality(String),
 identity_score Float32, source_url String, artifact_path String, content_format LowCardinality(String),
 original_hash FixedString(64), original_chars UInt64, cleaned_text String, cleaned_hash FixedString(64),
 cleaned_chars UInt64, block_count UInt32, included_block_count UInt32,
 quality_flags Array(LowCardinality(String)), source_selection_version LowCardinality(String),
 cleaner_version LowCardinality(String), renderer_version LowCardinality(String), source_revision_key FixedString(64),
 updated_at_utc DateTime64(6, 'UTC')
) ENGINE=ReplacingMergeTree(updated_at_utc) PARTITION BY toYYYYMM(published_date)
ORDER BY (published_date, canonical_news_id, source_revision_key, source_kind, source_ordinal)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(config.block_table)}
(
 canonical_news_id String, published_date Date, source_kind LowCardinality(String), source_ordinal UInt16,
 block_ordinal UInt32, block_kind LowCardinality(String), block_role LowCardinality(String),
 disposition LowCardinality(String), disposition_reason LowCardinality(String), original_text String, cleaned_text String,
 original_hash FixedString(64), cleaned_hash FixedString(64), table_ordinal UInt16, table_row_ordinal UInt32,
 cleaner_version LowCardinality(String), source_revision_key FixedString(64), updated_at_utc DateTime64(6, 'UTC')
) ENGINE=ReplacingMergeTree(updated_at_utc) PARTITION BY toYYYYMM(published_date)
ORDER BY (published_date, canonical_news_id, source_revision_key, source_kind, source_ordinal, block_ordinal)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(config.rendered_table)}
(
 canonical_news_id String, provider_article_id String, published_date Date, published_at_utc DateTime64(9, 'UTC'),
 title String, canonical_body_text String, display_text String, body_hash FixedString(64),
 body_status LowCardinality(String), primary_source_kind LowCardinality(String), primary_source_ordinal UInt16,
 source_revision_key FixedString(64), source_count UInt16, included_source_count UInt16,
 supporting_source_count UInt16, excluded_source_count UInt16, included_block_count UInt32, excluded_block_count UInt32,
 source_selection_version LowCardinality(String), cleaner_version LowCardinality(String),
 renderer_version LowCardinality(String), text_contract LowCardinality(String),
 quality_flags Array(LowCardinality(String)), updated_at_utc DateTime64(6, 'UTC')
) ENGINE=ReplacingMergeTree(updated_at_utc) PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (published_date, provider_article_id)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(config.ticker_table)}
(
 canonical_news_id String, provider_article_id String, published_date Date, published_at_utc DateTime64(9, 'UTC'),
 ticker LowCardinality(String), ticker_index UInt16, ticker_count UInt16, body_hash FixedString(64),
 source_revision_key FixedString(64), renderer_version LowCardinality(String), updated_at_utc DateTime64(6, 'UTC')
) ENGINE=ReplacingMergeTree(updated_at_utc) PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (published_date, ticker, published_at_utc, canonical_news_id, source_revision_key)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(config.lineage_table)}
(
 canonical_news_id String, provider_article_id String, published_date Date,
 previous_rendered_text_hash FixedString(64), previous_renderer_version LowCardinality(String), body_hash FixedString(64),
 body_renderer_version LowCardinality(String), source_revision_key FixedString(64),
 label_mutation_status LowCardinality(String), updated_at_utc DateTime64(6, 'UTC')
) ENGINE=ReplacingMergeTree(updated_at_utc) PARTITION BY toYYYYMM(published_date)
ORDER BY (published_date, canonical_news_id, source_revision_key)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(config.authority_table)}
(
 renderer_version LowCardinality(String), run_id String, status LowCardinality(String), is_active UInt8,
 source_table String, rendered_table String, source_rows UInt64, rendered_rows UInt64, missing_body_rows UInt64,
 partial_body_rows UInt64, purity_error_rows UInt64, relational_error_rows UInt64, audit_report_path String,
 previous_active_renderer_version String, started_at_utc DateTime64(6, 'UTC'), updated_at_utc DateTime64(6, 'UTC')
) ENGINE=ReplacingMergeTree(updated_at_utc) ORDER BY renderer_version
""")


def validate_body_v3_tables(client: ClickHouseHttpClient, config: NewsBodyV3TargetConfig) -> None:
    required = [config.event_table, config.source_table, config.block_table, config.rendered_table,
                config.ticker_table, config.lineage_table, config.authority_table]
    missing = [name for name in required if not table_exists(client, config.database, name)]
    if missing:
        raise RuntimeError(f"missing Benzinga body-v3 tables in {config.database}: {missing}")
    if config.require_certified:
        table = f"{quote_ident(config.database)}.{quote_ident(config.authority_table)}"
        sql = (
            f"SELECT status FROM {table} FINAL WHERE renderer_version={sql_string(BODY_RENDERER_VERSION)} "
            "ORDER BY updated_at_utc DESC LIMIT 1 FORMAT TSV"
        )
        if client.execute(sql).strip() not in {"certified", "promoted"}:
            raise RuntimeError("Benzinga body-v3 authority is not certified")


def write_many_news_pipeline_results_body_v3(
    client: ClickHouseHttpClient,
    results: list[NewsPipelineResult],
    *,
    config: NewsBodyV3TargetConfig,
) -> NewsBatchWriteSummary:
    if not config.skip_table_validation:
        validate_body_v3_tables(client, config)
    events: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    rendered: list[dict[str, Any]] = []
    tickers: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    duplicates: list[str] = []
    for result in results:
        if not result.body_v3_event_row or not result.body_v3_rendered_row:
            raise RuntimeError(f"pipeline result lacks body-v3 rows: {result.canonical_news_id}")
        key = (str(result.body_v3_event_row["published_date"]), str(result.provider_article_id))
        if key in seen:
            duplicates.append(result.canonical_news_id)
            continue
        seen.add(key)
        events.append(result.body_v3_event_row)
        sources.extend(result.body_v3_source_rows)
        blocks.extend(result.body_v3_block_rows)
        rendered.append(result.body_v3_rendered_row)
        tickers.extend(result.body_v3_ticker_links)
        lineage.append(result.body_v3_lineage_row)
    if config.execute and events:
        products = [
            (config.event_table, EVENT_COLUMNS, events), (config.source_table, SOURCE_COLUMNS, sources),
            (config.block_table, BLOCK_COLUMNS, blocks), (config.rendered_table, RENDERED_COLUMNS, rendered),
            (config.ticker_table, TICKER_COLUMNS, tickers), (config.lineage_table, LINEAGE_COLUMNS, lineage),
        ]
        for table, columns, rows in products:
            for batch_index, batch in enumerate(json_each_row_batches(
                rows, table=table, max_rows=500, target_bytes=4 * 1024 * 1024, max_row_bytes=8 * 1024 * 1024
            ), start=1):
                insert_json_each_row(
                    client, config.database, table, columns, batch.rows,
                    query_id=v2_batch_query_id(table, batch_index, batch.rows),
                )
    return NewsBatchWriteSummary(
        status="written" if config.execute else "dry_run", execute=config.execute, input_results=len(results),
        normalized_rows_inserted=len(events) if config.execute else 0,
        ticker_rows_inserted=len(tickers) if config.execute else 0, skipped_existing=0, skipped_existing_ids=[],
        input_duplicate_ids=sorted(set(duplicates)), input_duplicate_provider_keys=[], stale_ticker_rows_deleted=0,
        warnings=[],
    )


def authority_row_from_json(line: str) -> dict[str, Any]:
    return json.loads(line) if line.strip() else {}
