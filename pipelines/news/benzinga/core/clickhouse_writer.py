from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pipelines.news.benzinga.core.contracts import NewsPipelineResult
from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string


DEFAULT_DATABASE = "q_live"
DEFAULT_NORMALIZED_TABLE = "benzinga_news_normalized_v1"
DEFAULT_TICKER_TABLE = "benzinga_news_ticker_v1"
DEFAULT_COVERAGE_TABLE = "benzinga_news_coverage_manifest_v1"

NORMALIZED_COLUMNS = [
    "provider",
    "provider_article_id",
    "canonical_news_id",
    "published_date",
    "published_at_utc",
    "published_raw",
    "last_updated_at_utc",
    "last_updated_raw",
    "downloaded_at_utc",
    "provider_delay_ns",
    "title",
    "normalized_title",
    "teaser",
    "body_text",
    "external_text",
    "pdf_text",
    "normalized_full_text",
    "text_hash",
    "article_url",
    "url_domain",
    "author",
    "tickers",
    "channels",
    "provider_tags",
    "image_urls",
    "links",
    "has_body",
    "is_title_only",
    "has_external_text",
    "has_pdf",
    "pdf_urls",
    "pdf_artifact_paths",
    "pdf_metadata_json",
    "content_quality_flags",
    "external_fetch_status",
    "external_fetch_error",
    "pdf_extract_status",
    "pdf_extract_error",
    "raw_artifact_path",
    "raw_payload_hash",
    "normalizer_version",
    "updated_at_utc",
]

TICKER_LINK_COLUMNS = [
    "canonical_news_id",
    "provider",
    "provider_article_id",
    "published_date",
    "published_at_utc",
    "ticker",
    "ticker_index",
    "ticker_count",
    "text_hash",
    "content_quality_flags",
    "normalizer_version",
    "updated_at_utc",
]


@dataclass(frozen=True, slots=True)
class NewsWriteConfig:
    database: str = DEFAULT_DATABASE
    normalized_table: str = DEFAULT_NORMALIZED_TABLE
    ticker_table: str = DEFAULT_TICKER_TABLE
    execute: bool = False
    allow_ticker_change: bool = False
    skip_table_validation: bool = False


@dataclass(frozen=True, slots=True)
class NewsWriteSummary:
    status: str
    execute: bool
    canonical_news_id: str
    provider_article_id: str
    normalized_rows_inserted: int
    ticker_rows_inserted: int
    existing_normalized_rows: int
    existing_tickers: list[str] = field(default_factory=list)
    new_tickers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NewsBatchWriteConfig:
    database: str = DEFAULT_DATABASE
    normalized_table: str = DEFAULT_NORMALIZED_TABLE
    ticker_table: str = DEFAULT_TICKER_TABLE
    execute: bool = False
    skip_existing: bool = True
    skip_table_validation: bool = False


@dataclass(frozen=True, slots=True)
class NewsBatchWriteSummary:
    status: str
    execute: bool
    input_results: int
    normalized_rows_inserted: int
    ticker_rows_inserted: int
    skipped_existing: int
    skipped_existing_ids: list[str] = field(default_factory=list)
    input_duplicate_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def write_news_pipeline_result(
    client: ClickHouseHttpClient,
    result: NewsPipelineResult,
    *,
    config: NewsWriteConfig | None = None,
) -> NewsWriteSummary:
    cfg = config or NewsWriteConfig()
    if not cfg.skip_table_validation:
        validate_target_tables(client, cfg)

    row = normalized_row_for_insert(result.normalized_row)
    ticker_rows = ticker_rows_for_insert(result.ticker_links)
    sanity_warnings = sanity_check_normalized_row(row)
    existing_count = count_existing_news(client, cfg, str(row["canonical_news_id"]))
    existing_tickers = load_existing_tickers(client, cfg, str(row["canonical_news_id"])) if existing_count else []
    new_tickers = sorted({str(item["ticker"]) for item in ticker_rows})
    if existing_tickers and sorted(existing_tickers) != new_tickers and not cfg.allow_ticker_change:
        raise RuntimeError(
            "ticker set changed for existing news row; refusing to write stale ticker links. "
            f"canonical_news_id={row['canonical_news_id']} existing={existing_tickers} new={new_tickers}. "
            "Run a controlled ticker-link replacement before enabling this update."
        )

    if cfg.execute:
        insert_json_each_row(client, cfg.database, cfg.normalized_table, NORMALIZED_COLUMNS, [row])
        if ticker_rows:
            insert_json_each_row(client, cfg.database, cfg.ticker_table, TICKER_LINK_COLUMNS, ticker_rows)

    return NewsWriteSummary(
        status="written" if cfg.execute else "dry_run",
        execute=cfg.execute,
        canonical_news_id=str(row["canonical_news_id"]),
        provider_article_id=str(row["provider_article_id"]),
        normalized_rows_inserted=1 if cfg.execute else 0,
        ticker_rows_inserted=len(ticker_rows) if cfg.execute else 0,
        existing_normalized_rows=existing_count,
        existing_tickers=existing_tickers,
        new_tickers=new_tickers,
        warnings=sanity_warnings,
    )


def write_many_news_pipeline_results(
    client: ClickHouseHttpClient,
    results: list[NewsPipelineResult],
    *,
    config: NewsBatchWriteConfig | None = None,
) -> NewsBatchWriteSummary:
    cfg = config or NewsBatchWriteConfig()
    if not results:
        return NewsBatchWriteSummary(
            status="empty",
            execute=cfg.execute,
            input_results=0,
            normalized_rows_inserted=0,
            ticker_rows_inserted=0,
            skipped_existing=0,
            skipped_existing_ids=[],
            input_duplicate_ids=[],
        )
    if not cfg.skip_table_validation:
        validate_target_tables(
            client,
            NewsWriteConfig(
                database=cfg.database,
                normalized_table=cfg.normalized_table,
                ticker_table=cfg.ticker_table,
                skip_table_validation=True,
            ),
        )

    normalized_rows: list[dict[str, Any]] = []
    ticker_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for result in results:
        row = normalized_row_for_insert(result.normalized_row)
        warnings.extend(sanity_check_normalized_row(row))
        normalized_rows.append(row)
        ticker_rows.extend(ticker_rows_for_insert(result.ticker_links))

    skipped_existing = 0
    skipped_existing_ids: list[str] = []
    input_duplicate_ids = duplicate_ids([str(row["canonical_news_id"]) for row in normalized_rows])
    if cfg.skip_existing:
        existing = load_existing_news_ids(client, cfg, [str(row["canonical_news_id"]) for row in normalized_rows])
        if existing:
            skipped_existing = len(existing)
            skipped_existing_ids = sorted(existing)
            normalized_rows = [row for row in normalized_rows if str(row["canonical_news_id"]) not in existing]
            ticker_rows = [row for row in ticker_rows if str(row["canonical_news_id"]) not in existing]

    if cfg.execute and normalized_rows:
        insert_json_each_row(client, cfg.database, cfg.normalized_table, NORMALIZED_COLUMNS, normalized_rows)
        if ticker_rows:
            insert_json_each_row(client, cfg.database, cfg.ticker_table, TICKER_LINK_COLUMNS, ticker_rows)

    return NewsBatchWriteSummary(
        status="written" if cfg.execute else "dry_run",
        execute=cfg.execute,
        input_results=len(results),
        normalized_rows_inserted=len(normalized_rows) if cfg.execute else 0,
        ticker_rows_inserted=len(ticker_rows) if cfg.execute else 0,
        skipped_existing=skipped_existing,
        skipped_existing_ids=skipped_existing_ids,
        input_duplicate_ids=input_duplicate_ids,
        warnings=sorted(set(warnings)),
    )


def validate_target_tables(client: ClickHouseHttpClient, config: NewsWriteConfig) -> None:
    missing_tables = [
        table
        for table in [config.normalized_table, config.ticker_table]
        if not table_exists(client, config.database, table)
    ]
    if missing_tables:
        raise RuntimeError(f"missing required ClickHouse tables in {config.database}: {missing_tables}")
    assert_columns(client, config.database, config.normalized_table, NORMALIZED_COLUMNS)
    assert_columns(client, config.database, config.ticker_table, TICKER_LINK_COLUMNS)


def normalized_row_for_insert(row: dict[str, Any]) -> dict[str, Any]:
    output = {column: row.get(column) for column in NORMALIZED_COLUMNS}
    missing = [column for column in NORMALIZED_COLUMNS if column not in row]
    if missing:
        raise RuntimeError(f"normalized row missing required columns: {missing}")
    return output


def ticker_rows_for_insert(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        missing = [column for column in TICKER_LINK_COLUMNS if column not in row]
        if missing:
            raise RuntimeError(f"ticker row missing required columns: {missing}")
        output.append({column: row.get(column) for column in TICKER_LINK_COLUMNS})
    return output


def sanity_check_normalized_row(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    warnings: list[str] = []
    for column in ["provider", "provider_article_id", "canonical_news_id", "published_at_utc", "title", "normalized_full_text", "text_hash", "updated_at_utc"]:
        if not row.get(column):
            errors.append(f"missing_{column}")
    if row.get("provider") != "benzinga":
        errors.append("provider_not_benzinga")
    if not isinstance(row.get("tickers"), list):
        errors.append("tickers_not_array")
    if not isinstance(row.get("content_quality_flags"), list):
        errors.append("content_quality_flags_not_array")
    if row.get("is_title_only"):
        warnings.append("title_only")
    if errors:
        raise RuntimeError(f"normalized row failed sanity checks: {errors}")
    return warnings


def count_existing_news(client: ClickHouseHttpClient, config: NewsWriteConfig, canonical_news_id: str) -> int:
    sql = (
        f"SELECT count() FROM {table_name(config.database, config.normalized_table)} FINAL "
        f"WHERE canonical_news_id = {sql_string(canonical_news_id)}"
    )
    return int((client.execute(sql).strip() or "0").splitlines()[0])


def load_existing_tickers(client: ClickHouseHttpClient, config: NewsWriteConfig, canonical_news_id: str) -> list[str]:
    sql = (
        f"SELECT groupArray(ticker) FROM {table_name(config.database, config.ticker_table)} FINAL "
        f"WHERE canonical_news_id = {sql_string(canonical_news_id)} FORMAT JSONEachRow"
    )
    text = client.execute(sql)
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        values = row.get("groupArray(ticker)") or row.get("groupArray(ticker)") or []
        return sorted({str(item) for item in values if str(item)})
    return []


def load_existing_news_ids(client: ClickHouseHttpClient, config: NewsBatchWriteConfig, canonical_news_ids: list[str]) -> set[str]:
    output: set[str] = set()
    ids = sorted({item for item in canonical_news_ids if item})
    for index in range(0, len(ids), 1_000):
        chunk = ids[index : index + 1_000]
        if not chunk:
            continue
        in_list = ", ".join(sql_string(item) for item in chunk)
        sql = (
            f"SELECT canonical_news_id FROM {table_name(config.database, config.normalized_table)} FINAL "
            f"WHERE canonical_news_id IN ({in_list}) FORMAT JSONEachRow"
        )
        text = client.execute(sql)
        for line in text.splitlines():
            if line.strip():
                output.add(str(json.loads(line).get("canonical_news_id") or ""))
    return output


def duplicate_ids(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if not value:
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def table_exists(client: ClickHouseHttpClient, database: str, table: str) -> bool:
    sql = (
        "SELECT count() FROM system.tables "
        f"WHERE database = {sql_string(database)} AND name = {sql_string(table)}"
    )
    return int((client.execute(sql).strip() or "0").splitlines()[0]) > 0


def assert_columns(client: ClickHouseHttpClient, database: str, table: str, required_columns: list[str]) -> None:
    sql = (
        "SELECT name FROM system.columns "
        f"WHERE database = {sql_string(database)} AND table = {sql_string(table)} FORMAT JSONEachRow"
    )
    actual: set[str] = set()
    for line in client.execute(sql).splitlines():
        if line.strip():
            actual.add(str(json.loads(line).get("name") or ""))
    missing = [column for column in required_columns if column not in actual]
    if missing:
        raise RuntimeError(f"{database}.{table} missing required columns: {missing}")


def insert_json_each_row(client: ClickHouseHttpClient, database: str, table: str, columns: list[str], rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows)
    column_sql = ", ".join(quote_ident(column) for column in columns)
    client.execute(f"INSERT INTO {table_name(database, table)} ({column_sql}) FORMAT JSONEachRow\n{body}")


def table_name(database: str, table: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(table)}"
