from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from research.mlops.clickhouse_ingest_sip_flatfiles import ClickHouseHttpClient, quote_ident, sql_string


DEFAULT_NEWS_TABLE = "benzinga_news_normalized_v1"
DEFAULT_MANIFEST_TABLE = "benzinga_news_ingest_manifest_v1"


@dataclass(frozen=True, slots=True)
class NewsIngestManifestRow:
    run_id: str
    bucket_id: str
    bucket_start_utc: str
    bucket_end_utc: str
    status: str
    downloaded_rows: int = 0
    normalized_rows: int = 0
    inserted_rows: int = 0
    page_count: int = 0
    saturated: int = 0
    wall_seconds: float = 0.0
    exception: str = ""


def create_news_database_and_tables(
    client: ClickHouseHttpClient,
    *,
    database: str,
    news_table: str = DEFAULT_NEWS_TABLE,
    manifest_table: str = DEFAULT_MANIFEST_TABLE,
    storage_policy: str = "",
) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(database)}")
    client.execute(news_table_sql(database, news_table, storage_policy))
    client.execute(manifest_table_sql(database, manifest_table, storage_policy))


def news_table_sql(database: str, table: str, storage_policy: str = "") -> str:
    settings = merge_tree_settings(storage_policy)
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    provider LowCardinality(String),
    provider_article_id String,
    canonical_news_id String,
    published_date Date,
    published_at_utc DateTime64(9, 'UTC'),
    published_raw String,
    last_updated_at_utc Nullable(DateTime64(9, 'UTC')),
    last_updated_raw String,
    downloaded_at_utc DateTime64(9, 'UTC'),
    provider_delay_ns Nullable(Int64),
    title String,
    normalized_title String,
    teaser String,
    body_text String,
    external_text String,
    pdf_text String,
    normalized_full_text String,
    text_hash String,
    article_url String,
    url_domain LowCardinality(String),
    author String,
    tickers Array(String),
    channels Array(String),
    provider_tags Array(String),
    image_urls Array(String),
    links Array(String),
    has_body UInt8,
    is_title_only UInt8,
    has_external_text UInt8,
    has_pdf UInt8,
    pdf_urls Array(String),
    pdf_artifact_paths Array(String),
    content_quality_flags Array(LowCardinality(String)),
    external_fetch_status LowCardinality(String),
    external_fetch_error String,
    pdf_extract_status LowCardinality(String),
    pdf_extract_error String,
    raw_artifact_path String,
    raw_payload_hash String,
    normalizer_version LowCardinality(String),
    updated_at_utc DateTime64(9, 'UTC') DEFAULT now64(9)
)
ENGINE = ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (published_date, provider_article_id)
SETTINGS {settings}
"""


def manifest_table_sql(database: str, table: str, storage_policy: str = "") -> str:
    settings = merge_tree_settings(storage_policy)
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    run_id String,
    bucket_id String,
    bucket_start_utc DateTime64(9, 'UTC'),
    bucket_end_utc DateTime64(9, 'UTC'),
    status LowCardinality(String),
    downloaded_rows UInt64,
    normalized_rows UInt64,
    inserted_rows UInt64,
    page_count UInt32,
    saturated UInt8,
    wall_seconds Float64,
    exception String,
    updated_at_utc DateTime64(9, 'UTC') DEFAULT now64(9)
)
ENGINE = MergeTree
ORDER BY (bucket_start_utc, bucket_end_utc, bucket_id, updated_at_utc)
SETTINGS {settings}
"""


def insert_news_rows(client: ClickHouseHttpClient, *, database: str, table: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    body = "\n".join(json.dumps(row, ensure_ascii=False, default=str) for row in rows)
    client.execute(f"INSERT INTO {quote_ident(database)}.{quote_ident(table)} FORMAT JSONEachRow\n{body}")
    return len(rows)


def insert_manifest_rows(
    client: ClickHouseHttpClient,
    *,
    database: str,
    table: str,
    rows: list[NewsIngestManifestRow],
) -> int:
    if not rows:
        return 0
    payload = "\n".join(json.dumps(asdict(row), ensure_ascii=False, default=str) for row in rows)
    client.execute(f"INSERT INTO {quote_ident(database)}.{quote_ident(table)} FORMAT JSONEachRow\n{payload}")
    return len(rows)


def latest_manifest_statuses(
    client: ClickHouseHttpClient,
    *,
    database: str,
    table: str,
) -> dict[str, str]:
    sql = f"""
SELECT
    bucket_id,
    argMax(status, updated_at_utc) AS status
FROM {quote_ident(database)}.{quote_ident(table)}
GROUP BY bucket_id
FORMAT JSONEachRow
"""
    try:
        text = client.execute(sql)
    except Exception:  # noqa: BLE001
        return {}
    statuses: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        statuses[str(value.get("bucket_id", ""))] = str(value.get("status", ""))
    return statuses


def merge_tree_settings(storage_policy: str) -> str:
    settings = ["index_granularity = 8192"]
    if storage_policy.strip():
        settings.append(f"storage_policy = {sql_string(storage_policy.strip())}")
    return ", ".join(settings)
