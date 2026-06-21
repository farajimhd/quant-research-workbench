from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
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


DEFAULT_TARGET_DATABASE = "q_live"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/q_live_migration/step_06_bridge_features")


@dataclass(frozen=True, slots=True)
class StepPaths:
    run_root: Path
    manifest_json: Path
    execution_jsonl: Path
    validation_jsonl: Path
    rendered_sql: Path
    summary_md: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "StepPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            manifest_json=run_root / "step_06_manifest.json",
            execution_jsonl=run_root / "step_06_execution.jsonl",
            validation_jsonl=run_root / "step_06_validation.jsonl",
            rendered_sql=run_root / "step_06_insert_select.sql",
            summary_md=run_root / "step_06_summary.md",
        )


@dataclass(frozen=True, slots=True)
class BuildSpec:
    name: str
    target_table: str
    insert_sql: str
    expected_sql: str
    target_count_sql: str
    critical_columns: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Step 6 of q_live migration: build q_live SEC market bridge, SEC document metadata, "
            "tradable universe, and scanner static feature publications from migrated q_live tables."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_migration_clickhouse_url())
    parser.add_argument("--user", default=default_migration_clickhouse_user())
    parser.add_argument("--password", default=default_migration_clickhouse_password())
    parser.add_argument("--target-database", default=os.environ.get("QLIVE_MIGRATION_TARGET_DATABASE", DEFAULT_TARGET_DATABASE))
    parser.add_argument("--output-root-win", default=os.environ.get("QLIVE_MIGRATION_STEP_06_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--feature-date", default=os.environ.get("QLIVE_MIGRATION_FEATURE_DATE", date.today().isoformat()))
    parser.add_argument("--execute", action="store_true", help="Execute inserts. Without this flag, the script is dry-run only.")
    parser.add_argument("--validate-only", action="store_true", help="Validate current target rows without inserting.")
    parser.add_argument("--allow-non-empty-targets", action="store_true", help="Permit appending/upserting into non-empty target tables.")
    parser.add_argument("--skip-non-empty-targets", action="store_true", help="Resume mode: execute only specs whose target table is empty.")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_database_name(args.target_database, "--target-database")
    feature_date = parse_iso_date(args.feature_date)

    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    build_run_id = f"step_06_bridge_features_{run_id}"
    inserted_at = clickhouse_now64()
    paths = StepPaths.create(Path(args.output_root_win), run_id)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    specs = build_specs(args.target_database, build_run_id, inserted_at, feature_date)

    print_header(args, paths, loaded_env, build_run_id, specs, feature_date)
    preflight = run_preflight(client, args, specs)
    write_rendered_sql(paths.rendered_sql, specs)
    write_manifest(paths.manifest_json, args, paths, loaded_env, build_run_id, specs, preflight, feature_date)

    if args.validate_only:
        validations = validate_specs(client, args, specs)
        write_jsonl(paths.validation_jsonl, validations)
        write_summary(paths.summary_md, build_run_id, preflight, validations, execute=False)
        insert_run_row(client, args.target_database, build_run_id, "validation_only", inserted_at, rows_read=sum(row["expected_rows"] for row in validations), rows_written=0, rows_failed=sum(1 for row in validations if row["status"] == "fail"))
        insert_json_each_row(client, args.target_database, "sync_validation_v1", sync_validation_rows(build_run_id, validations, inserted_at))
        print("validation_only=true; no build rows inserted", flush=True)
        print(f"summary_md={paths.summary_md}", flush=True)
        return

    if not args.execute:
        write_jsonl(paths.execution_jsonl, [{"status": "dry_run", "build_run_id": build_run_id, "preflight": preflight}])
        validations = validate_specs(client, args, specs)
        write_jsonl(paths.validation_jsonl, validations)
        write_summary(paths.summary_md, build_run_id, preflight, validations, execute=False)
        print("dry_run=true; no rows inserted", flush=True)
        print(f"rendered_sql={paths.rendered_sql}", flush=True)
        print(f"summary_md={paths.summary_md}", flush=True)
        return

    ensure_empty_or_allowed(preflight, args)
    specs_to_execute = filter_specs_for_resume(specs, preflight, args.skip_non_empty_targets)
    insert_run_row(client, args.target_database, build_run_id, "running", inserted_at, rows_read=0, rows_written=0, rows_failed=0)
    execution_rows = execute_specs(client, args, specs_to_execute, paths.execution_jsonl)
    validations = validate_specs(client, args, specs)
    write_jsonl(paths.validation_jsonl, validations)
    write_summary(paths.summary_md, build_run_id, preflight, validations, execute=True)
    sync_rows = sync_validation_rows(build_run_id, validations, inserted_at)
    insert_json_each_row(client, args.target_database, "sync_validation_v1", sync_rows)
    insert_run_row(
        client,
        args.target_database,
        build_run_id,
        "completed",
        inserted_at,
        rows_read=sum(row["expected_rows"] for row in validations),
        rows_written=sum(row["inserted_delta"] for row in execution_rows),
        rows_failed=sum(1 for row in validations if row["status"] == "fail"),
    )
    print("summary=" + json.dumps({"build_run_id": build_run_id, "validations": validations}, sort_keys=True), flush=True)
    print(f"summary_md={paths.summary_md}", flush=True)


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


def build_specs(target_db: str, run_id: str, inserted_at: str, feature_date: date) -> list[BuildSpec]:
    db = quote_ident(target_db)
    literal_run_id = sql_string(run_id)
    literal_inserted_at = sql_string(inserted_at)
    literal_feature_date = sql_string(feature_date.isoformat())
    inserted_expr = f"toDateTime64({literal_inserted_at}, 3, 'UTC')"
    return [
        BuildSpec(
            name="sec_market_bridge",
            target_table="id_sec_market_bridge_v1",
            critical_columns=("bridge_id", "cik", "issuer_id", "mapping_method", "mapping_status"),
            expected_sql=f"""
SELECT uniqExact(tuple(iii.identifier_value_normalized, sec.security_id, l.listing_id, sym.symbol_id))
FROM {db}.id_issuer_identifier_v1 AS iii
INNER JOIN {db}.id_security_v1 AS sec ON sec.issuer_id = iii.issuer_id
INNER JOIN {db}.id_listing_v1 AS l ON l.security_id = sec.security_id
LEFT JOIN {db}.id_symbol_v1 AS sym ON sym.listing_id = l.listing_id AND sym.primary_symbol_flag = 1
WHERE iii.identifier_kind = 'cik'
  AND sec.status = 'active'
  AND l.listing_status = 'active'
""",
            target_count_sql=f"SELECT count() FROM {db}.id_sec_market_bridge_v1 FINAL",
            insert_sql=f"""
INSERT INTO {db}.id_sec_market_bridge_v1
(bridge_id, cik, issuer_id, security_id, listing_id, symbol_id, ticker, accession_number, valid_from_date, valid_to_date_exclusive, mapping_method, mapping_status, confidence_score, ambiguity_status, evidence_json, first_seen_at_utc, last_seen_at_utc, source_run_id, source_content_sha256, inserted_at)
WITH bridge_source AS
(
    SELECT
        iii.identifier_value_normalized AS cik,
        iii.issuer_id AS issuer_id,
        sec.security_id AS security_id,
        l.listing_id AS listing_id,
        sym.symbol_id AS symbol_id,
        sym.ticker AS ticker,
        iii.valid_from_date AS valid_from_date,
        iii.valid_to_date_exclusive AS valid_to_date_exclusive,
        iii.confidence_score AS identifier_confidence_score,
        count() OVER (PARTITION BY iii.identifier_value_normalized) AS mappings_per_cik,
        iii.evidence_json AS identifier_evidence_json,
        iii.source_content_sha256 AS source_content_sha256
    FROM {db}.id_issuer_identifier_v1 AS iii
    INNER JOIN {db}.id_security_v1 AS sec ON sec.issuer_id = iii.issuer_id
    INNER JOIN {db}.id_listing_v1 AS l ON l.security_id = sec.security_id
    LEFT JOIN {db}.id_symbol_v1 AS sym ON sym.listing_id = l.listing_id AND sym.primary_symbol_flag = 1
    WHERE iii.identifier_kind = 'cik'
      AND sec.status = 'active'
      AND l.listing_status = 'active'
)
SELECT
    concat('sec-market-bridge:', lower(hex(MD5(concat(cik, ':', issuer_id, ':', ifNull(security_id, ''), ':', ifNull(listing_id, ''), ':', ifNull(symbol_id, '')))))) AS bridge_id,
    cik,
    issuer_id,
    security_id,
    listing_id,
    symbol_id,
    ticker,
    CAST(NULL, 'Nullable(String)') AS accession_number,
    valid_from_date,
    valid_to_date_exclusive,
    'issuer_cik_to_active_market_listing' AS mapping_method,
    'active' AS mapping_status,
    least(0.95, greatest(0.50, identifier_confidence_score)) AS confidence_score,
    if(mappings_per_cik = 1, 'unique', 'ambiguous_multi_listing') AS ambiguity_status,
    toJSONString(map('source', 'id_issuer_identifier_v1', 'identifier_evidence_json', identifier_evidence_json, 'mappings_per_cik', toString(mappings_per_cik))) AS evidence_json,
    {inserted_expr} AS first_seen_at_utc,
    {inserted_expr} AS last_seen_at_utc,
    {literal_run_id} AS source_run_id,
    lower(hex(MD5(concat(cik, ':', issuer_id, ':', ifNull(security_id, ''), ':', ifNull(listing_id, ''), ':', ifNull(symbol_id, ''), ':', source_content_sha256)))) AS source_content_sha256,
    {inserted_expr} AS inserted_at
FROM bridge_source
""",
        ),
        BuildSpec(
            name="tradable_universe",
            target_table="feature_tradable_universe_v1",
            critical_columns=("universe_date", "symbol_id", "listing_id", "security_id", "issuer_id", "ticker"),
            expected_sql=f"""
SELECT count()
FROM {db}.id_symbol_v1 AS sym
INNER JOIN {db}.id_listing_v1 AS l ON l.listing_id = sym.listing_id
INNER JOIN {db}.id_security_v1 AS sec ON sec.security_id = l.security_id
WHERE sym.primary_symbol_flag = 1
""",
            target_count_sql=f"SELECT count() FROM {db}.feature_tradable_universe_v1 FINAL WHERE universe_date = toDate({literal_feature_date})",
            insert_sql=f"""
INSERT INTO {db}.feature_tradable_universe_v1
(universe_date, symbol_id, listing_id, security_id, issuer_id, ticker, exchange_code, currency_code, ibkr_conid, massive_ticker, product_type, asset_class, listing_status, symbol_status, is_tradable, exclusion_reason, source_run_id, inserted_at)
SELECT
    toDate({literal_feature_date}) AS universe_date,
    sym.symbol_id,
    l.listing_id,
    sec.security_id,
    sec.issuer_id,
    sym.ticker,
    l.exchange_code,
    l.currency_code,
    l.ibkr_conid,
    if(sym.source_system = 'market_reference', sym.ticker, CAST(NULL, 'Nullable(String)')) AS massive_ticker,
    sec.product_type,
    sec.asset_class,
    l.listing_status,
    sym.status AS symbol_status,
    if(l.listing_status = 'active' AND sym.status = 'active' AND ifNull(l.ibkr_conid, '') != '' AND l.currency_code = 'USD' AND sec.product_type IN ('STK', 'ETF'), 1, 0) AS is_tradable,
    multiIf(
        l.listing_status != 'active', 'inactive_listing',
        sym.status != 'active', 'inactive_symbol',
        ifNull(l.ibkr_conid, '') = '', 'missing_ibkr_conid',
        l.currency_code != 'USD', 'non_usd_currency',
        sec.product_type NOT IN ('STK', 'ETF'), 'unsupported_product_type',
        CAST(NULL, 'Nullable(String)')
    ) AS exclusion_reason,
    {literal_run_id} AS source_run_id,
    {inserted_expr} AS inserted_at
FROM {db}.id_symbol_v1 AS sym
INNER JOIN {db}.id_listing_v1 AS l ON l.listing_id = sym.listing_id
INNER JOIN {db}.id_security_v1 AS sec ON sec.security_id = l.security_id
WHERE sym.primary_symbol_flag = 1
""",
        ),
        BuildSpec(
            name="scanner_static",
            target_table="feature_scanner_static_v1",
            critical_columns=("feature_date", "symbol_id", "listing_id", "security_id", "issuer_id", "ticker", "float_bucket", "short_pressure_label"),
            expected_sql=f"SELECT count() FROM {db}.feature_tradable_universe_v1 FINAL WHERE universe_date = toDate({literal_feature_date}) AND is_tradable = 1",
            target_count_sql=f"SELECT count() FROM {db}.feature_scanner_static_v1 FINAL WHERE feature_date = toDate({literal_feature_date})",
            insert_sql=f"""
INSERT INTO {db}.feature_scanner_static_v1
(feature_date, symbol_id, listing_id, security_id, issuer_id, ticker, free_float, float_bucket, short_interest, days_to_cover, short_volume_ratio, short_pressure_label, market_cap, sector, industry, logo_asset_id, source_run_id, inserted_at)
WITH latest_float AS
(
    SELECT
        symbol_id,
        argMax(free_float, effective_date) AS free_float
    FROM {db}.market_security_float_v1
    GROUP BY symbol_id
),
latest_short_interest AS
(
    SELECT
        symbol_id,
        argMax(short_interest, settlement_date) AS short_interest,
        argMax(days_to_cover, settlement_date) AS days_to_cover
    FROM {db}.market_short_interest_v1
    GROUP BY symbol_id
),
latest_short_volume AS
(
    SELECT
        symbol_id,
        argMax(short_volume_ratio, trade_date) AS short_volume_ratio
    FROM {db}.market_short_volume_v1
    GROUP BY symbol_id
),
latest_snapshot AS
(
    SELECT
        symbol_id,
        argMax(market_cap, observed_at_utc) AS market_cap
    FROM {db}.market_security_market_snapshot_v1
    GROUP BY symbol_id
)
SELECT
    toDate({literal_feature_date}) AS feature_date,
    u.symbol_id,
    u.listing_id,
    u.security_id,
    u.issuer_id,
    u.ticker,
    lf.free_float,
    multiIf(
        lf.free_float IS NULL, 'unknown',
        lf.free_float < 10000000, 'micro_float',
        lf.free_float < 50000000, 'small_float',
        lf.free_float < 200000000, 'mid_float',
        'large_float'
    ) AS float_bucket,
    lsi.short_interest,
    lsi.days_to_cover,
    lsv.short_volume_ratio,
    multiIf(
        lsi.short_interest IS NULL AND lsi.days_to_cover IS NULL AND lsv.short_volume_ratio IS NULL, 'no_short_data',
        ifNull(lsi.days_to_cover, 0) >= 5 OR ifNull(lsv.short_volume_ratio, 0) >= 0.50, 'crowded_short',
        ifNull(lsi.days_to_cover, 0) >= 3 OR ifNull(lsv.short_volume_ratio, 0) >= 0.35, 'elevated_short',
        'normal'
    ) AS short_pressure_label,
    snap.market_cap,
    issuer.sector,
    issuer.industry,
    issuer.logo_asset_id,
    {literal_run_id} AS source_run_id,
    {inserted_expr} AS inserted_at
FROM
(
    SELECT *
    FROM {db}.feature_tradable_universe_v1 FINAL
) AS u
LEFT JOIN latest_float AS lf ON lf.symbol_id = u.symbol_id
LEFT JOIN latest_short_interest AS lsi ON lsi.symbol_id = u.symbol_id
LEFT JOIN latest_short_volume AS lsv ON lsv.symbol_id = u.symbol_id
LEFT JOIN latest_snapshot AS snap ON snap.symbol_id = u.symbol_id
LEFT JOIN
(
    SELECT *
    FROM {db}.id_issuer_v1 FINAL
) AS issuer ON issuer.issuer_id = u.issuer_id
WHERE u.universe_date = toDate({literal_feature_date})
  AND u.is_tradable = 1
""",
        ),
    ]


def run_preflight(client: ClickHouseHttpClient, args: argparse.Namespace, specs: list[BuildSpec]) -> list[dict[str, Any]]:
    required_tables = sorted({spec.target_table for spec in specs} | {"source_run_v1", "sync_validation_v1", "id_issuer_identifier_v1", "id_security_v1", "id_listing_v1", "id_symbol_v1", "sec_filing_v2"})
    missing = missing_tables(client, args.target_database, required_tables)
    if missing:
        raise SystemExit("Missing required target tables: " + json.dumps(missing, indent=2))
    rows = []
    for spec in specs:
        expected_rows = scalar_int(client, spec.expected_sql)
        target_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)}")
        target_logical_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)} FINAL")
        rows.append({"name": spec.name, "target_table": spec.target_table, "expected_rows": expected_rows, "target_rows_before": target_rows, "target_logical_rows_before": target_logical_rows})
        print(f"preflight {spec.name}: expected_rows={expected_rows:,} target_rows_before={target_rows:,} target_logical_rows_before={target_logical_rows:,}", flush=True)
    return rows


def execute_specs(client: ClickHouseHttpClient, args: argparse.Namespace, specs: list[BuildSpec], log_path: Path) -> list[dict[str, Any]]:
    rows = []
    for index, spec in enumerate(specs, start=1):
        started = time.perf_counter()
        before_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)}")
        row: dict[str, Any] = {"index": index, "name": spec.name, "target_table": spec.target_table, "target_rows_before": before_rows, "started_at_utc": datetime.now(UTC).isoformat()}
        try:
            client.execute(spec.insert_sql)
            after_rows = scalar_int(client, f"SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(spec.target_table)}")
            row.update({"status": "ok", "target_rows_after": after_rows, "inserted_delta": max(0, after_rows - before_rows), "wall_seconds": round(time.perf_counter() - started, 3), "finished_at_utc": datetime.now(UTC).isoformat()})
        except Exception as exc:
            row.update({"status": "failed", "target_rows_after": before_rows, "inserted_delta": 0, "error_type": type(exc).__name__, "error": str(exc), "wall_seconds": round(time.perf_counter() - started, 3), "finished_at_utc": datetime.now(UTC).isoformat()})
            write_jsonl_append(log_path, row)
            raise
        rows.append(row)
        write_jsonl_append(log_path, row)
        print(f"executed {index}/{len(specs)} {spec.name}: inserted_delta={row['inserted_delta']:,} seconds={row['wall_seconds']}", flush=True)
    return rows


def validate_specs(client: ClickHouseHttpClient, args: argparse.Namespace, specs: list[BuildSpec]) -> list[dict[str, Any]]:
    rows = []
    for spec in specs:
        expected_rows = scalar_int(client, spec.expected_sql)
        target_rows = scalar_int(client, spec.target_count_sql)
        critical_empty = critical_empty_count(client, args.target_database, spec.target_table, spec.critical_columns) if target_rows else 0
        mismatch = abs(target_rows - expected_rows)
        status = "pass" if mismatch == 0 and critical_empty == 0 else "fail"
        row = {
            "name": spec.name,
            "target_table": spec.target_table,
            "status": status,
            "expected_rows": expected_rows,
            "target_rows": target_rows,
            "mismatch_count": mismatch,
            "critical_empty": critical_empty,
            "critical_columns": spec.critical_columns,
        }
        rows.append(row)
        print(f"validate {spec.name}: status={status} expected={expected_rows:,} target={target_rows:,} mismatch={mismatch:,} critical_empty={critical_empty:,}", flush=True)
    return rows


def ensure_empty_or_allowed(preflight: list[dict[str, Any]], args: argparse.Namespace) -> None:
    non_empty = [row for row in preflight if row["target_rows_before"] > 0]
    if non_empty and not args.allow_non_empty_targets and not args.skip_non_empty_targets:
        raise SystemExit("Target tables are not empty. Pass --allow-non-empty-targets to append/upsert or --skip-non-empty-targets to resume: " + json.dumps(non_empty, indent=2))


def filter_specs_for_resume(specs: list[BuildSpec], preflight: list[dict[str, Any]], skip_non_empty: bool) -> list[BuildSpec]:
    if not skip_non_empty:
        return specs
    non_empty_by_name = {row["name"]: row["target_rows_before"] > 0 for row in preflight}
    skipped = [spec.name for spec in specs if non_empty_by_name.get(spec.name, False)]
    print("resume_skip_non_empty=" + json.dumps(skipped), flush=True)
    return [spec for spec in specs if not non_empty_by_name.get(spec.name, False)]


def sync_validation_rows(run_id: str, validations: list[dict[str, Any]], checked_at: str) -> list[dict[str, Any]]:
    rows = []
    for row in validations:
        rows.append(
            {
                "validation_id": f"{run_id}:{row['name']}:row_count",
                "run_id": run_id,
                "check_name": "row_count_after_step_06",
                "target_table": row["target_table"],
                "check_status": "pass" if row["mismatch_count"] == 0 else "fail",
                "severity": "info" if row["mismatch_count"] == 0 else "error",
                "expected_value": str(row["expected_rows"]),
                "observed_value": str(row["target_rows"]),
                "mismatch_count": row["mismatch_count"],
                "details_json": json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str),
                "checked_at_utc": checked_at,
            }
        )
        rows.append(
            {
                "validation_id": f"{run_id}:{row['name']}:critical_empty",
                "run_id": run_id,
                "check_name": "critical_columns_not_empty_after_step_06",
                "target_table": row["target_table"],
                "check_status": "pass" if row["critical_empty"] == 0 else "fail",
                "severity": "info" if row["critical_empty"] == 0 else "error",
                "expected_value": "0",
                "observed_value": str(row["critical_empty"]),
                "mismatch_count": row["critical_empty"],
                "details_json": json.dumps({"critical_columns": row["critical_columns"]}, ensure_ascii=False, separators=(",", ":"), default=str),
                "checked_at_utc": checked_at,
            }
        )
    return rows


def missing_tables(client: ClickHouseHttpClient, database: str, tables: list[str]) -> list[str]:
    values = ", ".join(sql_string(table) for table in tables)
    found = query_json_each_row(client, f"SELECT name FROM system.tables WHERE database = {sql_string(database)} AND name IN ({values})")
    found_names = {row["name"] for row in found}
    return [table for table in tables if table not in found_names]


def critical_empty_count(client: ClickHouseHttpClient, database: str, table: str, columns: tuple[str, ...]) -> int:
    checks = []
    for column in columns:
        quoted = quote_ident(column)
        checks.append(f"isNull({quoted})")
        checks.append(f"toString({quoted}) = ''")
    return scalar_int(client, f"SELECT countIf({' OR '.join(checks)}) FROM {quote_ident(database)}.{quote_ident(table)}")


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    text = execute_readonly_with_retries(client, sql.strip().rstrip(";") + "\nFORMAT TSV").strip()
    return int(text.splitlines()[0].split("\t")[0]) if text else 0


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    normalized = re.sub(r"\s+FORMAT\s+JSONEachRow\s*;?\s*$", "", sql, flags=re.IGNORECASE).strip().rstrip(";")
    text = execute_readonly_with_retries(client, normalized + "\nFORMAT JSONEachRow")
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


def insert_run_row(client: ClickHouseHttpClient, target_db: str, run_id: str, status: str, inserted_at: str, *, rows_read: int, rows_written: int, rows_failed: int) -> None:
    now = clickhouse_now64()
    row = {
        "run_id": run_id,
        "job_name": "step_06_build_q_live_bridge_features",
        "job_type": "migration",
        "source_system": "q_live",
        "source_database": target_db,
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


def insert_json_each_row(client: ClickHouseHttpClient, database: str, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows)
    client.execute(f"INSERT INTO {quote_ident(database)}.{quote_ident(table)} FORMAT JSONEachRow\n{body}")


def write_rendered_sql(path: Path, specs: list[BuildSpec]) -> None:
    statements = [f"-- {spec.name}\n-- target: {spec.target_table}\n{spec.insert_sql.strip()};" for spec in specs]
    path.write_text("\n\n".join(statements) + "\n", encoding="utf-8")


def write_manifest(path: Path, args: argparse.Namespace, paths: StepPaths, loaded_env: list[Path], run_id: str, specs: list[BuildSpec], preflight: list[dict[str, Any]], feature_date: date) -> None:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "machine": machine_name(),
        "repo_root": str(REPO_ROOT),
        "git_commit": quiet_git_commit(REPO_ROOT),
        "job_type": "step_06_build_q_live_bridge_features",
        "build_run_id": run_id,
        "dry_run": not args.execute,
        "target_database": args.target_database,
        "feature_date": feature_date.isoformat(),
        "run_root": str(paths.run_root),
        "rendered_sql": str(paths.rendered_sql),
        "execution_jsonl": str(paths.execution_jsonl),
        "validation_jsonl": str(paths.validation_jsonl),
        "summary_md": str(paths.summary_md),
        "loaded_env_files": [str(path) for path in loaded_env],
        "tables": [{"name": spec.name, "target_table": spec.target_table} for spec in specs],
        "preflight": preflight,
        "secret_status": secret_status(["QLIVE_MIGRATION_CLICKHOUSE_URL", "QLIVE_MIGRATION_CLICKHOUSE_USER", "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD", "QMD_CLICKHOUSE_URL", "QMD_CLICKHOUSE_USER", "QMD_CLICKHOUSE_PASSWORD", "REAL_LIVE_CLICKHOUSE_WRITE_URL", "REAL_LIVE_CLICKHOUSE_WRITE_USER", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD"]),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_summary(path: Path, run_id: str, preflight: list[dict[str, Any]], validations: list[dict[str, Any]], *, execute: bool) -> None:
    failures = [row for row in validations if row["status"] != "pass"]
    lines = [
        "# q_live Bridge And Feature Build",
        "",
        f"- Run id: `{run_id}`",
        f"- Execute mode: `{execute}`",
        f"- Specs: `{len(validations)}`",
        f"- Failed validations: `{len(failures)}`",
        "",
        "## Validation",
        "",
        "| Name | Target | Expected | Target | Critical Empty | Status |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in validations:
        lines.append(f"| `{row['name']}` | `{row['target_table']}` | {row['expected_rows']:,} | {row['target_rows']:,} | {row['critical_empty']:,} | `{row['status']}` |")
    lines.extend(["", "## Preflight", "", "| Name | Expected | Rows Before | Logical Rows Before |", "| --- | ---: | ---: | ---: |"])
    for row in preflight:
        lines.append(f"| `{row['name']}` | {row['expected_rows']:,} | {row['target_rows_before']:,} | {row['target_logical_rows_before']:,} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def write_jsonl_append(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def clickhouse_now64() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"--feature-date must be YYYY-MM-DD: {value!r}") from exc


def validate_database_name(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def print_header(args: argparse.Namespace, paths: StepPaths, loaded_env: list[Path], run_id: str, specs: list[BuildSpec], feature_date: date) -> None:
    print("=" * 96, flush=True)
    print("q_live migration step 6: bridge and feature build", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"validate_only={args.validate_only}", flush=True)
    print(f"target_database={args.target_database}", flush=True)
    print(f"feature_date={feature_date.isoformat()}", flush=True)
    print(f"build_run_id={run_id}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print(f"specs={len(specs)}", flush=True)
    print("loaded_env_files=" + json.dumps([str(path) for path in loaded_env]), flush=True)
    print("secret_status=" + json.dumps(secret_status(["QLIVE_MIGRATION_CLICKHOUSE_URL", "QLIVE_MIGRATION_CLICKHOUSE_USER", "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD", "QMD_CLICKHOUSE_URL", "QMD_CLICKHOUSE_USER", "QMD_CLICKHOUSE_PASSWORD", "REAL_LIVE_CLICKHOUSE_WRITE_URL", "REAL_LIVE_CLICKHOUSE_WRITE_USER", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD"]), sort_keys=True), flush=True)
    print("=" * 96, flush=True)


def quiet_git_commit(cwd: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(cwd), stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()
