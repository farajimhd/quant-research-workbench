from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    append_jsonl,
    clickhouse_env_status_keys,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    default_storage_policy,
    mergetree_settings_sql,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status


DEFAULT_OUTPUT_ROOT = Path("D:/market-data/prepared/clickhouse_sip_ingest/category_references")

XBRL_FIELDS = ("taxonomy", "tag", "unit_code", "form_type", "xbrl_row_kind", "location_code")
SEC_TEXT_FIELDS = ("form_type", "text_kind", "quality_flags")
NEWS_FIELDS = ("provider", "url_domain", "channels", "provider_tags", "quality_flags")
MULTI_VALUE_FIELDS = {
    ("news", "channels"),
    ("news", "provider_tags"),
    ("news", "quality_flags"),
    ("sec_filings", "quality_flags"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build dense categorical reference ids for model training tables. "
            "Id 0 is reserved for missing/unknown; table rows start at category_id=1."
        )
    )
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--xbrl-table", default="sec_xbrl_context")
    parser.add_argument("--news-token-table", default="news_text_tokens")
    parser.add_argument("--sec-token-table", default="sec_filing_text_tokens")
    parser.add_argument("--reference-table", default="training_category_reference")
    parser.add_argument("--storage-policy", default="")
    parser.add_argument("--max-threads", type=int, default=16)
    parser.add_argument("--max-memory-usage", default="80G")
    parser.add_argument("--output-root-win", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    storage_policy = args.storage_policy.strip() or default_storage_policy()
    client = ClickHouseHttpClient(
        args.clickhouse_url or default_clickhouse_url(),
        args.user or default_clickhouse_user(),
        args.password or default_clickhouse_password(),
    )
    run_id = time.strftime("%Y%m%d_%H%M%S")
    report_path = Path(args.output_root_win) / f"training_category_reference_{run_id}.jsonl"
    settings = query_settings(args)

    print("=" * 100, flush=True)
    print("Training categorical reference builder", flush=True)
    print(
        f"database={args.database} reference_table={args.reference_table} "
        f"xbrl_table={args.xbrl_table} news_token_table={args.news_token_table} sec_token_table={args.sec_token_table}",
        flush=True,
    )
    print(f"storage_policy={storage_policy or '<default>'} settings={settings.strip()}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(clickhouse_env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 100, flush=True)

    statements = [
        (create_reference_table_sql(args.database, args.reference_table, storage_policy), False),
        (f"TRUNCATE TABLE {quote_ident(args.database)}.{quote_ident(args.reference_table)}", False),
        (insert_reference_sql(args), True),
    ]
    for statement, use_settings in statements:
        print(statement.strip() + ";", flush=True)
        if args.print_only or args.dry_run:
            continue
        started = time.perf_counter()
        client.execute(statement.rstrip(";") + (settings if use_settings else ""))
        append_jsonl(
            report_path,
            {
                "event": "statement_done",
                "elapsed_seconds": time.perf_counter() - started,
                "statement_preview": statement.strip().splitlines()[0],
            },
        )
    if args.print_only or args.dry_run:
        return 0

    summary = summarize_reference_table(client, args.database, args.reference_table)
    for row in summary:
        print(
            f"SUMMARY domain={row['domain']} field={row['field_name']} categories={int(row['categories']):,} "
            f"rows={int(row['source_rows']):,}",
            flush=True,
        )
        append_jsonl(report_path, {"event": "summary", **row})
    print(f"DONE report={report_path}", flush=True)
    return 0


def create_reference_table_sql(database: str, table: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    domain LowCardinality(String),
    source_table LowCardinality(String),
    field_name LowCardinality(String),
    category_value String,
    category_id UInt32,
    one_hot_index UInt32,
    source_rows UInt64,
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (domain, field_name, category_value)
{mergetree_settings_sql(storage_policy)}
"""


def insert_reference_sql(args: argparse.Namespace) -> str:
    candidates = "\nUNION ALL\n".join(candidate_selects(args))
    return f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(args.reference_table)}
(
    domain,
    source_table,
    field_name,
    category_value,
    category_id,
    one_hot_index,
    source_rows,
    updated_at
)
SELECT
    domain,
    source_table,
    field_name,
    category_value,
    category_id,
    category_id - 1 AS one_hot_index,
    source_rows,
    now64(3, 'UTC') AS updated_at
FROM
(
    SELECT
        domain,
        source_table,
        field_name,
        category_value,
        row_number() OVER (PARTITION BY domain, field_name ORDER BY category_value) AS category_id,
        source_rows
    FROM
    (
        {candidates}
    )
    WHERE category_value != ''
)
"""


def candidate_selects(args: argparse.Namespace) -> list[str]:
    selects: list[str] = []
    for field in XBRL_FIELDS:
        selects.append(single_value_select(args.database, args.xbrl_table, domain="xbrl", source_table=args.xbrl_table, field=field))
    for field in SEC_TEXT_FIELDS:
        if ("sec_filings", field) in MULTI_VALUE_FIELDS:
            selects.append(multi_value_select(args.database, args.sec_token_table, domain="sec_filings", source_table=args.sec_token_table, field=field))
        else:
            selects.append(single_value_select(args.database, args.sec_token_table, domain="sec_filings", source_table=args.sec_token_table, field=field))
    for field in NEWS_FIELDS:
        if ("news", field) in MULTI_VALUE_FIELDS:
            selects.append(multi_value_select(args.database, args.news_token_table, domain="news", source_table=args.news_token_table, field=field))
        else:
            selects.append(single_value_select(args.database, args.news_token_table, domain="news", source_table=args.news_token_table, field=field))
    return selects


def single_value_select(database: str, table: str, *, domain: str, source_table: str, field: str) -> str:
    field_sql = quote_ident(field)
    return f"""
SELECT
    {sql_string(domain)} AS domain,
    {sql_string(source_table)} AS source_table,
    {sql_string(field)} AS field_name,
    trim(BOTH ' ' FROM toString({field_sql})) AS category_value,
    count() AS source_rows
FROM {quote_ident(database)}.{quote_ident(table)}
GROUP BY category_value
HAVING category_value != ''
""".strip()


def multi_value_select(database: str, table: str, *, domain: str, source_table: str, field: str) -> str:
    field_sql = quote_ident(field)
    return f"""
SELECT
    {sql_string(domain)} AS domain,
    {sql_string(source_table)} AS source_table,
    {sql_string(field)} AS field_name,
    category_value,
    count() AS source_rows
FROM
(
    SELECT arrayJoin(arrayMap(x -> trim(BOTH ' ' FROM x), splitByChar(',', toString({field_sql})))) AS category_value
    FROM {quote_ident(database)}.{quote_ident(table)}
)
GROUP BY category_value
HAVING category_value != ''
""".strip()


def summarize_reference_table(client: ClickHouseHttpClient, database: str, table: str) -> list[dict[str, object]]:
    sql = f"""
SELECT
    domain,
    field_name,
    count() AS categories,
    sum(source_rows) AS source_rows
FROM {quote_ident(database)}.{quote_ident(table)}
GROUP BY domain, field_name
ORDER BY domain, field_name
FORMAT JSONEachRow
"""
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def query_settings(args: argparse.Namespace) -> str:
    settings: list[str] = []
    if int(args.max_threads) > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings) if settings else ""


if __name__ == "__main__":
    raise SystemExit(main())
