from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from research.mlops.paths import machine_name  # noqa: E402


DEFAULT_SOURCE_DATABASE = "trading_dashboard_dev"
DEFAULT_TARGET_DATABASE = "q_live"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/q_live_migration/step_02_reference_identity")


@dataclass(frozen=True, slots=True)
class StepPaths:
    run_root: Path
    manifest_json: Path
    execution_jsonl: Path
    rendered_sql: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "StepPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            manifest_json=run_root / "step_02_manifest.json",
            execution_jsonl=run_root / "step_02_execution.jsonl",
            rendered_sql=run_root / "step_02_insert_select.sql",
        )


@dataclass(frozen=True, slots=True)
class MigrationSpec:
    name: str
    target_table: str
    source_tables: tuple[str, ...]
    columns: tuple[str, ...]
    select_sql: str
    source_count_sql: str
    expected_count_sql: str
    critical_columns: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Step 2 of q_live migration: migrate reference dimensions and identity graph tables. "
            "Default mode is dry-run and writes counts/rendered SQL without inserting rows."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_migration_clickhouse_url())
    parser.add_argument("--user", default=default_migration_clickhouse_user())
    parser.add_argument("--password", default=default_migration_clickhouse_password())
    parser.add_argument("--source-database", default=os.environ.get("QLIVE_MIGRATION_SOURCE_DATABASE", DEFAULT_SOURCE_DATABASE))
    parser.add_argument("--target-database", default=os.environ.get("QLIVE_MIGRATION_TARGET_DATABASE", DEFAULT_TARGET_DATABASE))
    parser.add_argument("--output-root-win", default=os.environ.get("QLIVE_MIGRATION_STEP_02_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--execute", action="store_true", help="Execute inserts. Without this flag, the script is dry-run only.")
    parser.add_argument("--validate-only", action="store_true", help="Record validation rows for already migrated targets without inserting migration rows.")
    parser.add_argument("--allow-non-empty-targets", action="store_true", help="Permit appending/upserting into target tables that already contain rows.")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_database_name(args.source_database, "--source-database")
    validate_database_name(args.target_database, "--target-database")

    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    migration_run_id = f"step_02_reference_identity_{run_id}"
    inserted_at = clickhouse_now64()
    paths = StepPaths.create(Path(args.output_root_win), run_id)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    specs = build_specs(args.source_database, args.target_database, migration_run_id, inserted_at)

    print_header(args, paths, loaded_env, migration_run_id, specs)
    preflight = run_preflight(client, args, specs)
    write_rendered_sql(paths.rendered_sql, args.target_database, specs)
    write_manifest(paths.manifest_json, args, paths, loaded_env, migration_run_id, specs, preflight)

    if args.validate_only:
        totals = totals_from_preflight(preflight)
        insert_run_row(client, args.source_database, args.target_database, migration_run_id, "validation_only", inserted_at, rows_read=sum(item["source_rows"] for item in totals), rows_written=0, rows_failed=0)
        write_validations(client, args.target_database, migration_run_id, specs, totals, inserted_at)
        write_execution_row(paths.execution_jsonl, {"status": "validation_only", "migration_run_id": migration_run_id, "preflight": preflight})
        print("validation_only=true; no migration rows inserted", flush=True)
        return

    if not args.execute:
        write_execution_row(paths.execution_jsonl, {"status": "dry_run", "migration_run_id": migration_run_id, "preflight": preflight})
        print("dry_run=true; no rows inserted", flush=True)
        print(f"rendered_sql={paths.rendered_sql}", flush=True)
        return

    ensure_empty_or_allowed(preflight, args)
    insert_run_row(client, args.source_database, args.target_database, migration_run_id, "running", inserted_at, rows_read=0, rows_written=0, rows_failed=0)
    totals = execute_specs(client, args, specs, paths.execution_jsonl)
    write_validations(client, args.target_database, migration_run_id, specs, totals, inserted_at)
    insert_run_row(
        client,
        args.source_database,
        args.target_database,
        migration_run_id,
        "completed",
        inserted_at,
        rows_read=sum(item["source_rows"] for item in totals),
        rows_written=sum(item["inserted_delta"] for item in totals),
        rows_failed=sum(item["failed_rows"] for item in totals),
    )
    print("summary=" + json.dumps({"migration_run_id": migration_run_id, "totals": totals}, sort_keys=True), flush=True)


def default_migration_clickhouse_url() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_URL") or os.environ.get("QMD_CLICKHOUSE_URL") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL") or default_clickhouse_url()


def default_migration_clickhouse_user() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_USER") or os.environ.get("QMD_CLICKHOUSE_USER") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER") or default_clickhouse_user()


def default_migration_clickhouse_password() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or default_clickhouse_password()
    )


def build_specs(source_db: str, target_db: str, run_id: str, inserted_at: str) -> list[MigrationSpec]:
    s = quote_ident(source_db)
    literal_run_id = sql_string(run_id)
    literal_inserted_at = sql_string(inserted_at)
    source_suffix = ", ".join([literal_run_id, "source_content_sha256", f"toDateTime64({literal_inserted_at}, 3, 'UTC')"])
    source_suffix_no_hash = ", ".join([literal_run_id, "''", f"toDateTime64({literal_inserted_at}, 3, 'UTC')"])
    return [
        MigrationSpec(
            name="ref_country",
            target_table="ref_country_v1",
            source_tables=("market_country_v1",),
            columns=("country_id", "country_code", "name", "region_code", "status", "first_seen_at_utc", "last_seen_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT country_id, country_code, name, region_code, status, first_seen_at_utc, last_seen_at_utc, {source_suffix}
FROM {s}.market_country_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.market_country_v1",
            expected_count_sql=f"SELECT uniqExact(country_id) FROM {s}.market_country_v1",
            critical_columns=("country_id", "country_code"),
        ),
        MigrationSpec(
            name="ref_asset_class",
            target_table="ref_asset_class_v1",
            source_tables=("market_asset_class_v1",),
            columns=("asset_class_id", "asset_class", "display_name", "status", "source_system", "first_seen_at_utc", "last_seen_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT asset_class_id, asset_class, display_name, status, source_system, first_seen_at_utc, last_seen_at_utc, {source_suffix}
FROM {s}.market_asset_class_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.market_asset_class_v1",
            expected_count_sql=f"SELECT uniqExact(asset_class_id) FROM {s}.market_asset_class_v1",
            critical_columns=("asset_class_id", "asset_class"),
        ),
        MigrationSpec(
            name="ref_exchange",
            target_table="ref_exchange_v1",
            source_tables=("market_exchange_v1",),
            columns=("exchange_id", "exchange_code", "name", "acronym", "mic", "operating_mic", "iso_country_code", "exchange_type", "status", "supported_asset_classes", "first_seen_at_utc", "last_seen_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT exchange_id, exchange_code, name, acronym, mic, operating_mic, iso_country_code, exchange_type, status, supported_asset_classes, first_seen_at_utc, last_seen_at_utc, {source_suffix}
FROM {s}.market_exchange_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.market_exchange_v1",
            expected_count_sql=f"SELECT uniqExact(exchange_id) FROM {s}.market_exchange_v1",
            critical_columns=("exchange_id", "exchange_code"),
        ),
        MigrationSpec(
            name="ref_exchange_currency",
            target_table="ref_exchange_currency_v1",
            source_tables=("market_exchange_currency_v1",),
            columns=("exchange_currency_id", "exchange_code", "currency_code", "relation_status", "is_default", "source_system", "source_product_count", "first_seen_at_utc", "last_seen_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT exchange_currency_id, exchange_code, currency_code, relation_status, is_default, source_system, source_product_count, first_seen_at_utc, last_seen_at_utc, {source_suffix}
FROM {s}.market_exchange_currency_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.market_exchange_currency_v1",
            expected_count_sql=f"SELECT uniqExact(exchange_currency_id) FROM {s}.market_exchange_currency_v1",
            critical_columns=("exchange_currency_id", "exchange_code", "currency_code"),
        ),
        MigrationSpec(
            name="ref_ticker_type",
            target_table="ref_ticker_type_v1",
            source_tables=("market_ticker_type_v1",),
            columns=("ticker_type_id", "asset_class", "provider_code", "name", "description", "locale", "status", "first_seen_at_utc", "last_seen_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT ticker_type_id, asset_class, provider_code, name, description, locale, status, first_seen_at_utc, last_seen_at_utc, {source_suffix}
FROM {s}.market_ticker_type_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.market_ticker_type_v1",
            expected_count_sql=f"SELECT uniqExact(ticker_type_id) FROM {s}.market_ticker_type_v1",
            critical_columns=("ticker_type_id", "provider_code"),
        ),
        MigrationSpec(
            name="id_issuer",
            target_table="id_issuer_v1",
            source_tables=("market_issuer_v1",),
            columns=("issuer_id", "issuer_name", "issuer_name_normalized", "legal_name", "branding_name", "entity_type", "domicile_country_code", "state_of_incorporation", "sic_code", "sic_description", "sector", "industry", "industry_group", "website_url", "investor_website_url", "logo_asset_id", "status", "first_seen_at_utc", "last_seen_at_utc", "last_verified_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT issuer_id, issuer_name, issuer_name_normalized, legal_name, branding_name, entity_type, country_code, state_of_incorporation, sic_code, sic_description, sector, industry, industry_group, website_url, investor_website_url, logo_asset_id, status, first_seen_at_utc, last_seen_at_utc, last_verified_at_utc, {source_suffix}
FROM {s}.market_issuer_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.market_issuer_v1",
            expected_count_sql=f"SELECT uniqExact(issuer_id) FROM {s}.market_issuer_v1",
            critical_columns=("issuer_id", "issuer_name"),
        ),
        MigrationSpec(
            name="id_issuer_identifier",
            target_table="id_issuer_identifier_v1",
            source_tables=("market_issuer_v1",),
            columns=("issuer_identifier_id", "issuer_id", "identifier_kind", "identifier_value", "identifier_value_normalized", "source_system", "confidence_score", "is_primary", "valid_from_date", "valid_to_date_exclusive", "first_seen_at_utc", "last_seen_at_utc", "evidence_json", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
WITH raw AS
(
    SELECT issuer_id, 'source_issuer_id' AS identifier_kind, issuer_id AS identifier_value, issuer_id AS identifier_value_normalized,
           multiIf(startsWith(issuer_id, 'issuer:cik:'), 'sec', startsWith(issuer_id, 'issuer:ibkr_public:'), 'ibkr_public', 'trading_dashboard_dev') AS source_system,
           1.0 AS confidence_score, 1 AS is_primary, first_seen_at_utc, last_seen_at_utc, source_content_sha256,
           concat('{{"source_table":"market_issuer_v1","source_column":"issuer_id"}}') AS evidence_json
    FROM {s}.market_issuer_v1
    WHERE issuer_id != ''
    UNION ALL
    SELECT issuer_id, 'cik', cik, cik, 'sec', 1.0, 1, first_seen_at_utc, last_seen_at_utc, source_content_sha256,
           concat('{{"source_table":"market_issuer_v1","source_column":"cik"}}')
    FROM {s}.market_issuer_v1
    WHERE ifNull(cik, '') != ''
    UNION ALL
    SELECT issuer_id, 'ein', ein, replaceRegexpAll(ein, '[^0-9]', ''), 'sec', 0.9, 0, first_seen_at_utc, last_seen_at_utc, source_content_sha256,
           concat('{{"source_table":"market_issuer_v1","source_column":"ein"}}')
    FROM {s}.market_issuer_v1
    WHERE ifNull(ein, '') != ''
    UNION ALL
    SELECT issuer_id, 'lei', lei, upper(lei), 'lei', 0.9, 0, first_seen_at_utc, last_seen_at_utc, source_content_sha256,
           concat('{{"source_table":"market_issuer_v1","source_column":"lei"}}')
    FROM {s}.market_issuer_v1
    WHERE ifNull(lei, '') != ''
)
SELECT
    concat('issuer_identifier:', lower(hex(SHA256(concat(issuer_id, '|', identifier_kind, '|', identifier_value_normalized))))) AS issuer_identifier_id,
    issuer_id,
    identifier_kind,
    identifier_value,
    identifier_value_normalized,
    source_system,
    confidence_score,
    toUInt8(is_primary),
    CAST(NULL, 'Nullable(Date)') AS valid_from_date,
    CAST(NULL, 'Nullable(Date)') AS valid_to_date_exclusive,
    first_seen_at_utc,
    last_seen_at_utc,
    evidence_json,
    {literal_run_id},
    source_content_sha256,
    toDateTime64({literal_inserted_at}, 3, 'UTC')
FROM raw
""",
            source_count_sql=f"""
SELECT
    countIf(issuer_id != '')
    + countIf(ifNull(cik, '') != '')
    + countIf(ifNull(ein, '') != '')
    + countIf(ifNull(lei, '') != '')
FROM {s}.market_issuer_v1
""",
            expected_count_sql=f"""
WITH raw AS
(
    SELECT issuer_id, 'source_issuer_id' AS identifier_kind, issuer_id AS identifier_value_normalized
    FROM {s}.market_issuer_v1
    WHERE issuer_id != ''
    UNION ALL
    SELECT issuer_id, 'cik', cik
    FROM {s}.market_issuer_v1
    WHERE ifNull(cik, '') != ''
    UNION ALL
    SELECT issuer_id, 'ein', replaceRegexpAll(ein, '[^0-9]', '')
    FROM {s}.market_issuer_v1
    WHERE ifNull(ein, '') != ''
    UNION ALL
    SELECT issuer_id, 'lei', upper(lei)
    FROM {s}.market_issuer_v1
    WHERE ifNull(lei, '') != ''
)
SELECT uniqExact(concat('issuer_identifier:', lower(hex(SHA256(concat(issuer_id, '|', identifier_kind, '|', identifier_value_normalized))))))
FROM raw
""",
            critical_columns=("issuer_identifier_id", "issuer_id", "identifier_kind", "identifier_value"),
        ),
        MigrationSpec(
            name="id_security",
            target_table="id_security_v1",
            source_tables=("market_security_v1",),
            columns=("security_id", "issuer_id", "product_type", "asset_class", "instrument_type", "security_type", "security_name", "has_options", "status", "first_seen_at_utc", "last_seen_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT security_id, issuer_id, product_type, asset_class, instrument_type, security_type, security_name, has_options, 'active' AS status, first_seen_at_utc, last_seen_at_utc, {source_suffix}
FROM {s}.market_security_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.market_security_v1",
            expected_count_sql=f"SELECT uniqExact(security_id) FROM {s}.market_security_v1",
            critical_columns=("security_id", "issuer_id"),
        ),
        MigrationSpec(
            name="id_security_identifier",
            target_table="id_security_identifier_v1",
            source_tables=("market_security_identifier_v1",),
            columns=("security_identifier_id", "security_id", "identifier_kind", "identifier_value", "identifier_value_normalized", "source_system", "is_primary", "valid_from_date", "valid_to_date_exclusive", "first_seen_at_utc", "last_seen_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT security_identifier_id, security_id, identifier_kind, identifier_value, identifier_value_normalized, source_system, is_primary, valid_from_date, valid_to_date_exclusive, first_seen_at_utc, last_seen_at_utc, {source_suffix}
FROM {s}.market_security_identifier_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.market_security_identifier_v1",
            expected_count_sql=f"SELECT uniqExact(security_identifier_id) FROM {s}.market_security_identifier_v1",
            critical_columns=("security_identifier_id", "security_id", "identifier_kind", "identifier_value"),
        ),
        MigrationSpec(
            name="id_listing",
            target_table="id_listing_v1",
            source_tables=("market_listing_v1",),
            columns=("listing_id", "security_id", "exchange_code", "currency_code", "ibkr_conid", "board_code", "segment_name", "listing_status", "is_primary_listing", "list_date", "delisted_date", "first_seen_at_utc", "last_seen_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT listing_id, security_id, exchange_code, currency_code, ibkr_conid, board_code, segment_name, listing_status, is_primary_listing, list_date, delisted_date, first_seen_at_utc, last_seen_at_utc, {source_suffix}
FROM {s}.market_listing_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.market_listing_v1",
            expected_count_sql=f"SELECT uniqExact(listing_id) FROM {s}.market_listing_v1",
            critical_columns=("listing_id", "security_id", "exchange_code", "currency_code"),
        ),
        MigrationSpec(
            name="id_symbol",
            target_table="id_symbol_v1",
            source_tables=("market_symbol_v1",),
            columns=("symbol_id", "listing_id", "source_system", "ticker", "ticker_normalized", "display_name", "ticker_root", "ticker_suffix", "ticker_type_id", "asset_type", "instrument_type", "security_type", "status", "primary_symbol_flag", "first_seen_at_utc", "last_seen_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT symbol_id, listing_id, 'market_reference' AS source_system, ticker, ticker_normalized, display_name, ticker_root, ticker_suffix, ticker_type_id, asset_type, instrument_type, security_type, status, primary_symbol_flag, first_seen_at_utc, last_seen_at_utc, {source_suffix}
FROM {s}.market_symbol_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.market_symbol_v1",
            expected_count_sql=f"SELECT uniqExact(symbol_id) FROM {s}.market_symbol_v1",
            critical_columns=("symbol_id", "listing_id", "ticker"),
        ),
        MigrationSpec(
            name="id_source_mapping",
            target_table="id_source_mapping_v1",
            source_tables=("market_source_identity_mapping_v1",),
            columns=("source_mapping_id", "source_system", "source_entity_kind", "source_entity_key", "source_identifier", "mapped_entity_kind", "mapped_entity_id", "mapping_status", "confidence_score", "evidence_json", "resolved_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT source_mapping_id, source_system, source_entity_kind, source_entity_key, source_identifier, mapped_entity_kind, mapped_entity_id, mapping_status, confidence_score, evidence_json, resolved_at_utc, {source_suffix}
FROM {s}.market_source_identity_mapping_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.market_source_identity_mapping_v1",
            expected_count_sql=f"SELECT uniqExact(source_mapping_id) FROM {s}.market_source_identity_mapping_v1",
            critical_columns=("source_mapping_id", "source_system", "source_entity_kind", "source_entity_key"),
        ),
        MigrationSpec(
            name="id_mapping_issue",
            target_table="id_mapping_issue_v1",
            source_tables=("market_canonical_reference_issue_v1",),
            columns=("mapping_issue_id", "source_mapping_id", "source_system", "source_entity_kind", "source_entity_key", "mapped_entity_kind", "issue_type", "issue_status", "issue_message", "evidence_json", "opened_at_utc", "resolved_at_utc", "source_run_id", "source_content_sha256", "inserted_at"),
            select_sql=f"""
SELECT reference_issue_id AS mapping_issue_id, source_mapping_id, source_system, source_entity_kind, source_entity_key, mapped_entity_kind, issue_type, issue_status, issue_message, evidence_json, opened_at_utc, resolved_at_utc, {source_suffix}
FROM {s}.market_canonical_reference_issue_v1
""",
            source_count_sql=f"SELECT count() FROM {s}.market_canonical_reference_issue_v1",
            expected_count_sql=f"SELECT uniqExact(reference_issue_id) FROM {s}.market_canonical_reference_issue_v1",
            critical_columns=("mapping_issue_id", "source_mapping_id", "source_system", "source_entity_key"),
        ),
    ]


def run_preflight(client: ClickHouseHttpClient, args: argparse.Namespace, specs: list[MigrationSpec]) -> list[dict[str, Any]]:
    required_source_tables = sorted({table for spec in specs for table in spec.source_tables})
    required_target_tables = sorted({spec.target_table for spec in specs} | {"source_run_v1", "sync_validation_v1"})
    missing_source = missing_tables(client, args.source_database, required_source_tables)
    missing_target = missing_tables(client, args.target_database, required_target_tables)
    if missing_source or missing_target:
        raise SystemExit("Missing required tables: " + json.dumps({"source": missing_source, "target": missing_target}, indent=2))

    rows = []
    for spec in specs:
        source_rows = scalar_int(client, spec.source_count_sql)
        expected_rows = scalar_int(client, spec.expected_count_sql)
        target_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)}")
        target_logical_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)} FINAL")
        critical_empty = critical_empty_count(client, args.target_database, spec.target_table, spec.critical_columns) if target_rows else 0
        rows.append(
            {
                "name": spec.name,
                "target_table": spec.target_table,
                "source_rows": source_rows,
                "expected_logical_rows": expected_rows,
                "target_rows_before": target_rows,
                "target_logical_rows_before": target_logical_rows,
                "target_critical_empty_before": critical_empty,
            }
        )
        print(
            f"preflight {spec.name}: source_rows={source_rows:,} expected_logical_rows={expected_rows:,} "
            f"target_rows_before={target_rows:,} target_logical_rows_before={target_logical_rows:,}",
            flush=True,
        )
    return rows


def execute_specs(client: ClickHouseHttpClient, args: argparse.Namespace, specs: list[MigrationSpec], log_path: Path) -> list[dict[str, Any]]:
    totals = []
    for index, spec in enumerate(specs, start=1):
        started = time.perf_counter()
        source_rows = scalar_int(client, spec.source_count_sql)
        expected_rows = scalar_int(client, spec.expected_count_sql)
        before_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)}")
        statement = insert_statement(args.target_database, spec)
        row: dict[str, Any] = {
            "index": index,
            "name": spec.name,
            "target_table": spec.target_table,
            "source_rows": source_rows,
            "expected_logical_rows": expected_rows,
            "target_rows_before": before_rows,
            "started_at_utc": datetime.now(UTC).isoformat(),
        }
        try:
            client.execute(statement)
            after_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)}")
            after_logical_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)} FINAL")
            critical_empty = critical_empty_count(client, args.target_database, spec.target_table, spec.critical_columns)
            duplicate_keys = duplicate_key_count(client, args.target_database, spec.target_table, spec.critical_columns[:1])
            row.update(
                {
                    "status": "ok",
                    "target_rows_after": after_rows,
                    "target_logical_rows_after": after_logical_rows,
                    "inserted_delta": max(0, after_rows - before_rows),
                    "critical_empty_after": critical_empty,
                    "duplicate_primary_key_estimate": duplicate_keys,
                    "failed_rows": 0,
                    "wall_seconds": round(time.perf_counter() - started, 3),
                    "finished_at_utc": datetime.now(UTC).isoformat(),
                }
            )
        except Exception as exc:
            row.update(
                {
                    "status": "failed",
                    "target_rows_after": before_rows,
                    "target_logical_rows_after": before_rows,
                    "failed_rows": source_rows,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "wall_seconds": round(time.perf_counter() - started, 3),
                    "finished_at_utc": datetime.now(UTC).isoformat(),
                }
            )
            write_execution_row(log_path, row)
            raise
        write_execution_row(log_path, row)
        totals.append(row)
        print(f"executed {index}/{len(specs)} {spec.name}: target_rows_after={row['target_rows_after']:,} target_logical_rows_after={row['target_logical_rows_after']:,} seconds={row['wall_seconds']}", flush=True)
    return totals


def totals_from_preflight(preflight: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals = []
    for row in preflight:
        totals.append(
            {
                "name": row["name"],
                "target_table": row["target_table"],
                "source_rows": row["source_rows"],
                "expected_logical_rows": row["expected_logical_rows"],
                "target_rows_before": row["target_rows_before"],
                "target_rows_after": row["target_rows_before"],
                "target_logical_rows_after": row["target_logical_rows_before"],
                "inserted_delta": 0,
                "failed_rows": 0,
                "critical_empty_after": row["target_critical_empty_before"],
            }
        )
    return totals


def insert_statement(target_db: str, spec: MigrationSpec) -> str:
    columns = ", ".join(quote_ident(column) for column in spec.columns)
    return f"INSERT INTO {quote_ident(target_db)}.{quote_ident(spec.target_table)} ({columns})\n{spec.select_sql.strip()}"


def write_rendered_sql(path: Path, target_db: str, specs: list[MigrationSpec]) -> None:
    statements = []
    for spec in specs:
        statements.append(f"-- {spec.name}\n-- target: {spec.target_table}\n{insert_statement(target_db, spec)};")
    path.write_text("\n\n".join(statements) + "\n", encoding="utf-8")


def ensure_empty_or_allowed(preflight: list[dict[str, Any]], args: argparse.Namespace) -> None:
    non_empty = [row for row in preflight if row["target_rows_before"] > 0]
    if non_empty and not args.allow_non_empty_targets:
        raise SystemExit("Target tables are not empty. Pass --allow-non-empty-targets to append/upsert: " + json.dumps(non_empty, indent=2))


def insert_run_row(
    client: ClickHouseHttpClient,
    source_db: str,
    target_db: str,
    run_id: str,
    status: str,
    inserted_at: str,
    *,
    rows_read: int,
    rows_written: int,
    rows_failed: int,
) -> None:
    now = clickhouse_now64()
    row = {
        "run_id": run_id,
        "job_name": "step_02_migrate_reference_identity",
        "job_type": "migration",
        "source_system": "trading_dashboard_dev",
        "source_database": source_db,
        "target_database": target_db,
        "status": status,
        "started_at_utc": inserted_at,
        "finished_at_utc": now if status != "running" else None,
        "source_watermark_before": None,
        "source_watermark_after": None,
        "rows_read": rows_read,
        "rows_written": rows_written,
        "rows_failed": rows_failed,
        "config_json": "{}",
        "error_json": "{}",
        "code_version": quiet_git_commit(REPO_ROOT),
        "inserted_at": now,
    }
    insert_json_each_row(client, target_db, "source_run_v1", [row])


def write_validations(client: ClickHouseHttpClient, target_db: str, run_id: str, specs: list[MigrationSpec], totals: list[dict[str, Any]], inserted_at: str) -> None:
    rows = []
    by_name = {row["name"]: row for row in totals}
    for spec in specs:
        result = by_name[spec.name]
        mismatch = abs(int(result["target_logical_rows_after"]) - int(result["expected_logical_rows"]))
        rows.append(
            {
                "validation_id": f"{run_id}:{spec.name}:row_count",
                "run_id": run_id,
                "check_name": "row_count_after_step_02",
                "target_table": spec.target_table,
                "check_status": "pass" if mismatch == 0 else "warn",
                "severity": "info" if mismatch == 0 else "warning",
                "expected_value": str(result["expected_logical_rows"]),
                "observed_value": str(result["target_logical_rows_after"]),
                "mismatch_count": mismatch,
                "details_json": json.dumps(
                    {
                        "source_rows": result["source_rows"],
                        "expected_logical_rows": result["expected_logical_rows"],
                        "target_rows_after": result["target_rows_after"],
                        "target_logical_rows_after": result["target_logical_rows_after"],
                    },
                    separators=(",", ":"),
                ),
                "checked_at_utc": inserted_at,
            }
        )
        rows.append(
            {
                "validation_id": f"{run_id}:{spec.name}:critical_empty",
                "run_id": run_id,
                "check_name": "critical_columns_not_empty",
                "target_table": spec.target_table,
                "check_status": "pass" if int(result.get("critical_empty_after", 0)) == 0 else "fail",
                "severity": "info" if int(result.get("critical_empty_after", 0)) == 0 else "error",
                "expected_value": "0",
                "observed_value": str(result.get("critical_empty_after", 0)),
                "mismatch_count": int(result.get("critical_empty_after", 0)),
                "details_json": json.dumps({"critical_columns": spec.critical_columns}, separators=(",", ":")),
                "checked_at_utc": inserted_at,
            }
        )
    insert_json_each_row(client, target_db, "sync_validation_v1", rows)


def insert_json_each_row(client: ClickHouseHttpClient, database: str, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows)
    client.execute(f"INSERT INTO {quote_ident(database)}.{quote_ident(table)} FORMAT JSONEachRow\n{body}")


def missing_tables(client: ClickHouseHttpClient, database: str, tables: list[str]) -> list[str]:
    if not tables:
        return []
    values = ", ".join(sql_string(table) for table in tables)
    found = query_json_each_row(
        client,
        f"""
        SELECT name
        FROM system.tables
        WHERE database = {sql_string(database)}
          AND name IN ({values})
        """,
    )
    found_names = {row["name"] for row in found}
    return [table for table in tables if table not in found_names]


def critical_empty_count(client: ClickHouseHttpClient, database: str, table: str, columns: tuple[str, ...]) -> int:
    if not columns:
        return 0
    checks = []
    for column in columns:
        quoted = quote_ident(column)
        checks.append(f"isNull({quoted})")
        checks.append(f"toString({quoted}) = ''")
    return scalar_int(client, f"SELECT countIf({' OR '.join(checks)}) FROM {quote_ident(database)}.{quote_ident(table)}")


def duplicate_key_count(client: ClickHouseHttpClient, database: str, table: str, columns: tuple[str, ...]) -> int:
    if not columns:
        return 0
    grouped = ", ".join(quote_ident(column) for column in columns)
    return scalar_int(
        client,
        f"""
        SELECT count()
        FROM
        (
            SELECT {grouped}, count() AS rows
            FROM {quote_ident(database)}.{quote_ident(table)}
            GROUP BY {grouped}
            HAVING rows > 1
        )
        """,
    )


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    text = execute_readonly_with_retries(client, sql.strip() + "\nFORMAT TSV").strip()
    if not text:
        return 0
    return int(text.splitlines()[0].split("\t")[0])


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = execute_readonly_with_retries(client, sql.rstrip(";") + "\nFORMAT JSONEachRow")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def execute_readonly_with_retries(client: ClickHouseHttpClient, sql: str, *, attempts: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return client.execute(sql)
        except (ConnectionResetError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(0.5 * attempt)
    assert last_error is not None
    raise last_error


def write_execution_row(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def write_manifest(path: Path, args: argparse.Namespace, paths: StepPaths, loaded_env: list[Path], run_id: str, specs: list[MigrationSpec], preflight: list[dict[str, Any]]) -> None:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "machine": machine_name(),
        "repo_root": str(REPO_ROOT),
        "git_commit": quiet_git_commit(REPO_ROOT),
        "job_type": "step_02_migrate_reference_identity",
        "migration_run_id": run_id,
        "dry_run": not args.execute,
        "source_database": args.source_database,
        "target_database": args.target_database,
        "run_root": str(paths.run_root),
        "rendered_sql": str(paths.rendered_sql),
        "execution_jsonl": str(paths.execution_jsonl),
        "loaded_env_files": [str(path) for path in loaded_env],
        "tables": [{"name": spec.name, "target_table": spec.target_table, "source_tables": spec.source_tables} for spec in specs],
        "preflight": preflight,
        "secret_status": secret_status(
            [
                "QLIVE_MIGRATION_CLICKHOUSE_URL",
                "QLIVE_MIGRATION_CLICKHOUSE_USER",
                "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
                "QMD_CLICKHOUSE_URL",
                "QMD_CLICKHOUSE_USER",
                "QMD_CLICKHOUSE_PASSWORD",
                "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
            ]
        ),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def clickhouse_now64() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def validate_database_name(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def print_header(args: argparse.Namespace, paths: StepPaths, loaded_env: list[Path], run_id: str, specs: list[MigrationSpec]) -> None:
    print("=" * 96, flush=True)
    print("q_live migration step 2: reference and identity migration", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"source_database={args.source_database}", flush=True)
    print(f"target_database={args.target_database}", flush=True)
    print(f"migration_run_id={run_id}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print(f"specs={len(specs)}", flush=True)
    print("loaded_env_files=" + json.dumps([str(path) for path in loaded_env]), flush=True)
    print(
        "secret_status="
        + json.dumps(
            secret_status(
                [
                    "QLIVE_MIGRATION_CLICKHOUSE_URL",
                    "QLIVE_MIGRATION_CLICKHOUSE_USER",
                    "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
                    "QMD_CLICKHOUSE_URL",
                    "QMD_CLICKHOUSE_USER",
                    "QMD_CLICKHOUSE_PASSWORD",
                    "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                    "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                    "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                ]
            ),
            sort_keys=True,
        ),
        flush=True,
    )
    print("=" * 96, flush=True)


def quiet_git_commit(cwd: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()
