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


DEFAULT_TARGET_DATABASE = "q_live"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/q_live_migration/step_02b_reference_identity_repair")
DURABLE_IDENTIFIER_KINDS = ("cik", "lei", "ein")


@dataclass(frozen=True, slots=True)
class StepPaths:
    run_root: Path
    manifest_json: Path
    execution_jsonl: Path
    summary_md: Path
    rendered_sql: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "StepPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            manifest_json=run_root / "step_02b_manifest.json",
            execution_jsonl=run_root / "step_02b_execution.jsonl",
            summary_md=run_root / "step_02b_summary.md",
            rendered_sql=run_root / "step_02b_repair.sql",
        )


@dataclass(frozen=True, slots=True)
class RepairStatement:
    name: str
    target_table: str
    sql: str
    destructive: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Step 2b of q_live migration: repair deterministic reference identity issues. "
            "It canonicalizes duplicate durable issuer identifiers, remaps securities from "
            "merged issuer aliases, records weak issuer issues, and removes stale non-linkable "
            "legacy mapping issues. Default mode is dry-run."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_migration_clickhouse_url())
    parser.add_argument("--user", default=default_migration_clickhouse_user())
    parser.add_argument("--password", default=default_migration_clickhouse_password())
    parser.add_argument("--target-database", default=os.environ.get("QLIVE_MIGRATION_TARGET_DATABASE", DEFAULT_TARGET_DATABASE))
    parser.add_argument("--sec-core-database", default=os.environ.get("SEC_CORE_DATABASE", "sec_core"))
    parser.add_argument("--output-root-win", default=os.environ.get("QLIVE_MIGRATION_STEP_02B_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--execute", action="store_true", help="Execute repair statements. Without this flag, the script only writes diagnostics and SQL.")
    parser.add_argument(
        "--mutations-sync",
        type=int,
        default=1,
        choices=(0, 1, 2),
        help="ClickHouse mutations_sync value for cleanup DELETE statements. Default waits for the local mutation.",
    )
    parser.add_argument(
        "--skip-stale-issue-cleanup",
        action="store_true",
        help="Do not delete non-linkable legacy mapping issues. Useful for a diagnostic dry-run.",
    )
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_database_name(args.target_database, "--target-database")
    validate_database_name(args.sec_core_database, "--sec-core-database")

    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    migration_run_id = f"step_02b_reference_identity_repair_{run_id}"
    inserted_at = clickhouse_now64()
    paths = StepPaths.create(Path(args.output_root_win), run_id)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    print_header(args, paths, loaded_env, migration_run_id)
    diagnostics_before = collect_diagnostics(client, args.target_database, args.sec_core_database)
    write_jsonl(paths.execution_jsonl, [{"phase": "diagnostics_before", "payload": diagnostics_before}])
    print_diagnostics("before", diagnostics_before)

    statements = build_repair_statements(args.target_database, args.sec_core_database, migration_run_id, inserted_at, args.mutations_sync, not args.skip_stale_issue_cleanup)
    write_rendered_sql(paths.rendered_sql, statements)
    write_manifest(paths.manifest_json, args, paths, loaded_env, migration_run_id, diagnostics_before, statements)

    if not args.execute:
        write_summary(paths.summary_md, args, migration_run_id, diagnostics_before, None, [])
        print("dry_run=true; repair statements were not executed", flush=True)
        print(f"rendered_sql={paths.rendered_sql}", flush=True)
        print(f"summary_md={paths.summary_md}", flush=True)
        return

    insert_run_row(client, args.target_database, migration_run_id, "running", inserted_at, rows_read=sum_diagnostic_rows(diagnostics_before), rows_written=0, rows_failed=0)
    executed_rows = execute_statements(client, statements, paths.execution_jsonl)
    diagnostics_after = collect_diagnostics(client, args.target_database, args.sec_core_database)
    write_jsonl(paths.execution_jsonl, [{"phase": "diagnostics_after", "payload": diagnostics_after}])
    print_diagnostics("after", diagnostics_after)
    insert_run_row(
        client,
        args.target_database,
        migration_run_id,
        "completed",
        inserted_at,
        rows_read=sum_diagnostic_rows(diagnostics_before),
        rows_written=sum(int(row.get("delta_rows", 0)) for row in executed_rows if int(row.get("delta_rows", 0)) > 0),
        rows_failed=0,
    )
    write_summary(paths.summary_md, args, migration_run_id, diagnostics_before, diagnostics_after, executed_rows)
    print(f"summary_md={paths.summary_md}", flush=True)


def build_repair_statements(target_db: str, sec_core_db: str, run_id: str, inserted_at: str, mutations_sync: int, cleanup_stale_issues: bool) -> list[RepairStatement]:
    db = quote_ident(target_db)
    sec_db = quote_ident(sec_core_db)
    literal_run_id = sql_string(run_id)
    literal_inserted_at = sql_string(inserted_at)
    alias_ctes = canonical_alias_ctes(db)
    sec_exact_name_ctes = sec_exact_name_new_cik_ctes(db, sec_db)
    sec_existing_cik_alias_ctes = sec_exact_name_existing_cik_alias_ctes(db, sec_db)
    qlive_exact_name_alias_ctes = qlive_exact_name_alias_ctes_sql(db)
    current_ts = f"toDateTime64({literal_inserted_at}, 3, 'UTC')"

    statements = [
        RepairStatement(
            name="mark_duplicate_issuer_aliases_merged",
            target_table="id_issuer_v1",
            sql=f"""
INSERT INTO {db}.id_issuer_v1
(issuer_id, issuer_name, issuer_name_normalized, legal_name, branding_name, entity_type, domicile_country_code, state_of_incorporation, sic_code, sic_description, sector, industry, industry_group, website_url, investor_website_url, logo_asset_id, status, first_seen_at_utc, last_seen_at_utc, last_verified_at_utc, source_run_id, source_content_sha256, inserted_at)
{alias_ctes}
SELECT
    issuer.issuer_id,
    issuer.issuer_name,
    issuer.issuer_name_normalized,
    issuer.legal_name,
    issuer.branding_name,
    issuer.entity_type,
    issuer.domicile_country_code,
    issuer.state_of_incorporation,
    issuer.sic_code,
    issuer.sic_description,
    issuer.sector,
    issuer.industry,
    issuer.industry_group,
    issuer.website_url,
    issuer.investor_website_url,
    issuer.logo_asset_id,
    'merged_duplicate_identifier_alias' AS status,
    issuer.first_seen_at_utc,
    {current_ts} AS last_seen_at_utc,
    {current_ts} AS last_verified_at_utc,
    {literal_run_id} AS source_run_id,
    issuer.source_content_sha256,
    {current_ts} AS inserted_at
FROM {db}.id_issuer_v1 AS issuer FINAL
INNER JOIN safe_aliases AS alias ON alias.old_issuer_id = issuer.issuer_id
""",
        ),
        RepairStatement(
            name="insert_canonical_security_parent_rows",
            target_table="id_security_v1",
            sql=f"""
INSERT INTO {db}.id_security_v1
(security_id, issuer_id, product_type, asset_class, instrument_type, security_type, security_name, has_options, status, first_seen_at_utc, last_seen_at_utc, source_run_id, source_content_sha256, inserted_at)
{alias_ctes}
SELECT
    security.security_id,
    alias.canonical_issuer_id AS issuer_id,
    security.product_type,
    security.asset_class,
    security.instrument_type,
    security.security_type,
    security.security_name,
    security.has_options,
    security.status,
    security.first_seen_at_utc,
    {current_ts} AS last_seen_at_utc,
    {literal_run_id} AS source_run_id,
    security.source_content_sha256,
    {current_ts} AS inserted_at
FROM {db}.id_security_v1 AS security FINAL
INNER JOIN safe_aliases AS alias ON alias.old_issuer_id = security.issuer_id
""",
        ),
        RepairStatement(
            name="delete_noncanonical_security_parent_rows",
            target_table="id_security_v1",
            destructive=True,
            sql=f"""
ALTER TABLE {db}.id_security_v1
DELETE WHERE issuer_id IN
(
    {safe_alias_old_issuer_query(db)}
)
SETTINGS mutations_sync = {mutations_sync}
""",
        ),
        RepairStatement(
            name="delete_noncanonical_durable_issuer_identifiers",
            target_table="id_issuer_identifier_v1",
            destructive=True,
            sql=f"""
ALTER TABLE {db}.id_issuer_identifier_v1
DELETE WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
  AND issuer_id IN
(
    {safe_alias_old_issuer_query(db)}
)
SETTINGS mutations_sync = {mutations_sync}
""",
        ),
        RepairStatement(
            name="insert_sec_exact_name_cik_identifiers",
            target_table="id_issuer_identifier_v1",
            sql=f"""
INSERT INTO {db}.id_issuer_identifier_v1
(issuer_identifier_id, issuer_id, identifier_kind, identifier_value, identifier_value_normalized, source_system, confidence_score, is_primary, valid_from_date, valid_to_date_exclusive, first_seen_at_utc, last_seen_at_utc, evidence_json, source_run_id, source_content_sha256, inserted_at)
{sec_exact_name_ctes}
SELECT
    concat('issuer_identifier:', lower(hex(SHA256(concat(old_issuer_id, '|cik|', cik))))) AS issuer_identifier_id,
    old_issuer_id AS issuer_id,
    'cik' AS identifier_kind,
    cik AS identifier_value,
    cik AS identifier_value_normalized,
    'sec_core_exact_name' AS source_system,
    0.82 AS confidence_score,
    1 AS is_primary,
    CAST(NULL, 'Nullable(Date)') AS valid_from_date,
    CAST(NULL, 'Nullable(Date)') AS valid_to_date_exclusive,
    {current_ts} AS first_seen_at_utc,
    {current_ts} AS last_seen_at_utc,
    concat('{{"source":"sec_core.sec_bulk_mirror_company_v1","match_rule":"normalized_exact_issuer_name_unique_new_cik","issuer_name":"', replaceAll(issuer_name, '"', '\\"'), '","sec_entity_name":"', replaceAll(entity_name, '"', '\\"'), '"}}') AS evidence_json,
    {literal_run_id} AS source_run_id,
    '' AS source_content_sha256,
    {current_ts} AS inserted_at
FROM new_cik_matches
""",
        ),
        RepairStatement(
            name="insert_sec_exact_name_ein_identifiers",
            target_table="id_issuer_identifier_v1",
            sql=f"""
INSERT INTO {db}.id_issuer_identifier_v1
(issuer_identifier_id, issuer_id, identifier_kind, identifier_value, identifier_value_normalized, source_system, confidence_score, is_primary, valid_from_date, valid_to_date_exclusive, first_seen_at_utc, last_seen_at_utc, evidence_json, source_run_id, source_content_sha256, inserted_at)
{sec_exact_name_ctes}
SELECT
    concat('issuer_identifier:', lower(hex(SHA256(concat(old_issuer_id, '|ein|', replaceRegexpAll(ein, '[^0-9]', '')))))) AS issuer_identifier_id,
    old_issuer_id AS issuer_id,
    'ein' AS identifier_kind,
    ein AS identifier_value,
    replaceRegexpAll(ein, '[^0-9]', '') AS identifier_value_normalized,
    'sec_core_exact_name' AS source_system,
    0.75 AS confidence_score,
    0 AS is_primary,
    CAST(NULL, 'Nullable(Date)') AS valid_from_date,
    CAST(NULL, 'Nullable(Date)') AS valid_to_date_exclusive,
    {current_ts} AS first_seen_at_utc,
    {current_ts} AS last_seen_at_utc,
    concat('{{"source":"sec_core.sec_bulk_mirror_company_v1","match_rule":"normalized_exact_issuer_name_unique_new_cik","issuer_name":"', replaceAll(issuer_name, '"', '\\"'), '","sec_entity_name":"', replaceAll(entity_name, '"', '\\"'), '"}}') AS evidence_json,
    {literal_run_id} AS source_run_id,
    '' AS source_content_sha256,
    {current_ts} AS inserted_at
FROM new_cik_matches
WHERE ifNull(ein, '') != ''
  AND replaceRegexpAll(ein, '[^0-9]', '') NOT IN ('', '000000000')
""",
        ),
        RepairStatement(
            name="mark_sec_exact_name_alias_issuers_merged",
            target_table="id_issuer_v1",
            sql=f"""
INSERT INTO {db}.id_issuer_v1
(issuer_id, issuer_name, issuer_name_normalized, legal_name, branding_name, entity_type, domicile_country_code, state_of_incorporation, sic_code, sic_description, sector, industry, industry_group, website_url, investor_website_url, logo_asset_id, status, first_seen_at_utc, last_seen_at_utc, last_verified_at_utc, source_run_id, source_content_sha256, inserted_at)
{sec_existing_cik_alias_ctes}
SELECT
    issuer.issuer_id,
    issuer.issuer_name,
    issuer.issuer_name_normalized,
    issuer.legal_name,
    issuer.branding_name,
    issuer.entity_type,
    issuer.domicile_country_code,
    issuer.state_of_incorporation,
    issuer.sic_code,
    issuer.sic_description,
    issuer.sector,
    issuer.industry,
    issuer.industry_group,
    issuer.website_url,
    issuer.investor_website_url,
    issuer.logo_asset_id,
    'merged_sec_exact_name_alias' AS status,
    issuer.first_seen_at_utc,
    {current_ts} AS last_seen_at_utc,
    {current_ts} AS last_verified_at_utc,
    {literal_run_id} AS source_run_id,
    issuer.source_content_sha256,
    {current_ts} AS inserted_at
FROM {db}.id_issuer_v1 AS issuer FINAL
INNER JOIN existing_cik_aliases AS alias ON alias.old_issuer_id = issuer.issuer_id
""",
        ),
        RepairStatement(
            name="insert_sec_exact_name_canonical_security_parent_rows",
            target_table="id_security_v1",
            sql=f"""
INSERT INTO {db}.id_security_v1
(security_id, issuer_id, product_type, asset_class, instrument_type, security_type, security_name, has_options, status, first_seen_at_utc, last_seen_at_utc, source_run_id, source_content_sha256, inserted_at)
{sec_existing_cik_alias_ctes}
SELECT
    security.security_id,
    alias.canonical_issuer_id AS issuer_id,
    security.product_type,
    security.asset_class,
    security.instrument_type,
    security.security_type,
    security.security_name,
    security.has_options,
    security.status,
    security.first_seen_at_utc,
    {current_ts} AS last_seen_at_utc,
    {literal_run_id} AS source_run_id,
    security.source_content_sha256,
    {current_ts} AS inserted_at
FROM {db}.id_security_v1 AS security FINAL
INNER JOIN existing_cik_aliases AS alias ON alias.old_issuer_id = security.issuer_id
""",
        ),
        RepairStatement(
            name="delete_sec_exact_name_noncanonical_security_parent_rows",
            target_table="id_security_v1",
            destructive=True,
            sql=f"""
ALTER TABLE {db}.id_security_v1
DELETE WHERE issuer_id IN
(
    {sec_exact_name_existing_cik_alias_old_issuer_query(db, sec_db)}
)
SETTINGS mutations_sync = {mutations_sync}
""",
        ),
        RepairStatement(
            name="delete_sec_exact_name_resolved_weak_issuer_issues",
            target_table="id_mapping_issue_v1",
            destructive=True,
            sql=f"""
ALTER TABLE {db}.id_mapping_issue_v1
DELETE WHERE source_system = 'q_live_migration'
  AND issue_type = 'weak_issuer_identity'
  AND source_entity_key IN
  (
      {sec_exact_name_existing_cik_alias_old_issuer_query(db, sec_db)}
  )
SETTINGS mutations_sync = {mutations_sync}
""",
        ),
        RepairStatement(
            name="mark_qlive_exact_name_alias_issuers_merged",
            target_table="id_issuer_v1",
            sql=f"""
INSERT INTO {db}.id_issuer_v1
(issuer_id, issuer_name, issuer_name_normalized, legal_name, branding_name, entity_type, domicile_country_code, state_of_incorporation, sic_code, sic_description, sector, industry, industry_group, website_url, investor_website_url, logo_asset_id, status, first_seen_at_utc, last_seen_at_utc, last_verified_at_utc, source_run_id, source_content_sha256, inserted_at)
{qlive_exact_name_alias_ctes}
SELECT
    issuer.issuer_id,
    issuer.issuer_name,
    issuer.issuer_name_normalized,
    issuer.legal_name,
    issuer.branding_name,
    issuer.entity_type,
    issuer.domicile_country_code,
    issuer.state_of_incorporation,
    issuer.sic_code,
    issuer.sic_description,
    issuer.sector,
    issuer.industry,
    issuer.industry_group,
    issuer.website_url,
    issuer.investor_website_url,
    issuer.logo_asset_id,
    'merged_qlive_exact_name_alias' AS status,
    issuer.first_seen_at_utc,
    {current_ts} AS last_seen_at_utc,
    {current_ts} AS last_verified_at_utc,
    {literal_run_id} AS source_run_id,
    issuer.source_content_sha256,
    {current_ts} AS inserted_at
FROM {db}.id_issuer_v1 AS issuer FINAL
INNER JOIN qlive_exact_name_aliases AS alias ON alias.old_issuer_id = issuer.issuer_id
""",
        ),
        RepairStatement(
            name="insert_qlive_exact_name_canonical_security_parent_rows",
            target_table="id_security_v1",
            sql=f"""
INSERT INTO {db}.id_security_v1
(security_id, issuer_id, product_type, asset_class, instrument_type, security_type, security_name, has_options, status, first_seen_at_utc, last_seen_at_utc, source_run_id, source_content_sha256, inserted_at)
{qlive_exact_name_alias_ctes}
SELECT
    security.security_id,
    alias.canonical_issuer_id AS issuer_id,
    security.product_type,
    security.asset_class,
    security.instrument_type,
    security.security_type,
    security.security_name,
    security.has_options,
    security.status,
    security.first_seen_at_utc,
    {current_ts} AS last_seen_at_utc,
    {literal_run_id} AS source_run_id,
    security.source_content_sha256,
    {current_ts} AS inserted_at
FROM {db}.id_security_v1 AS security FINAL
INNER JOIN qlive_exact_name_aliases AS alias ON alias.old_issuer_id = security.issuer_id
""",
        ),
        RepairStatement(
            name="delete_qlive_exact_name_noncanonical_security_parent_rows",
            target_table="id_security_v1",
            destructive=True,
            sql=f"""
ALTER TABLE {db}.id_security_v1
DELETE WHERE issuer_id IN
(
    {qlive_exact_name_alias_old_issuer_query(db)}
)
SETTINGS mutations_sync = {mutations_sync}
""",
        ),
        RepairStatement(
            name="delete_qlive_exact_name_resolved_weak_issuer_issues",
            target_table="id_mapping_issue_v1",
            destructive=True,
            sql=f"""
ALTER TABLE {db}.id_mapping_issue_v1
DELETE WHERE source_system = 'q_live_migration'
  AND issue_type = 'weak_issuer_identity'
  AND source_entity_key IN
  (
      {qlive_exact_name_alias_old_issuer_query(db)}
  )
SETTINGS mutations_sync = {mutations_sync}
""",
        ),
        RepairStatement(
            name="delete_resolved_weak_issuer_identity_issues",
            target_table="id_mapping_issue_v1",
            destructive=True,
            sql=f"""
ALTER TABLE {db}.id_mapping_issue_v1
DELETE WHERE source_system = 'q_live_migration'
  AND issue_type = 'weak_issuer_identity'
  AND source_entity_key IN
  (
      SELECT DISTINCT issuer_id
      FROM {db}.id_issuer_identifier_v1 FINAL
      WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
        AND identifier_value_normalized != ''
  )
SETTINGS mutations_sync = {mutations_sync}
""",
        ),
        RepairStatement(
            name="insert_weak_issuer_identity_issues",
            target_table="id_mapping_issue_v1",
            sql=f"""
INSERT INTO {db}.id_mapping_issue_v1
(mapping_issue_id, source_mapping_id, source_system, source_entity_kind, source_entity_key, mapped_entity_kind, issue_type, issue_status, issue_message, evidence_json, opened_at_utc, resolved_at_utc, source_run_id, source_content_sha256, inserted_at)
WITH durable_issuers AS
(
    SELECT DISTINCT issuer_id
    FROM {db}.id_issuer_identifier_v1 FINAL
    WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
      AND identifier_value_normalized != ''
),
active_candidates AS
(
    SELECT DISTINCT sec.issuer_id AS issuer_id
    FROM {db}.id_symbol_v1 AS sym FINAL
    INNER JOIN {db}.id_listing_v1 AS listing FINAL ON listing.listing_id = sym.listing_id
    INNER JOIN {db}.id_security_v1 AS sec FINAL ON sec.security_id = listing.security_id
    LEFT JOIN {db}.ref_exchange_v1 AS ex FINAL ON ex.exchange_code = listing.exchange_code
    WHERE sym.status = 'active'
      AND sym.primary_symbol_flag = 1
      AND listing.listing_status = 'active'
      AND upper(listing.currency_code) = 'USD'
      AND upper(ifNull(ex.iso_country_code, '')) = 'US'
      AND upper(sec.product_type) IN ('STK', 'STOCK', 'STOCKS')
      AND sec.issuer_id NOT IN (SELECT issuer_id FROM durable_issuers)
)
SELECT
    concat('reference_issue:weak_issuer_identity:', lower(hex(SHA256(issuer_id)))) AS mapping_issue_id,
    '' AS source_mapping_id,
    'q_live_migration' AS source_system,
    'issuer' AS source_entity_kind,
    issuer_id AS source_entity_key,
    'issuer' AS mapped_entity_kind,
    'weak_issuer_identity' AS issue_type,
    'open' AS issue_status,
    'Active US stock candidate lacks a durable issuer identifier; keep related securities non-tradable until identity is resolved.' AS issue_message,
    concat('{{"source":"step_02b_repair_reference_identity","issuer_id":"', replaceAll(issuer_id, '"', '\\"'), '"}}') AS evidence_json,
    {current_ts} AS opened_at_utc,
    CAST(NULL, 'Nullable(DateTime64(3, \\'UTC\\'))') AS resolved_at_utc,
    {literal_run_id} AS source_run_id,
    '' AS source_content_sha256,
    {current_ts} AS inserted_at
FROM active_candidates
""",
        ),
        RepairStatement(
            name="delete_stale_weak_issuer_identity_issues",
            target_table="id_mapping_issue_v1",
            destructive=True,
            sql=f"""
ALTER TABLE {db}.id_mapping_issue_v1
DELETE WHERE source_system = 'q_live_migration'
  AND issue_type = 'weak_issuer_identity'
  AND lower(issue_status) NOT IN ('resolved', 'closed', 'ignored')
  AND source_entity_key NOT IN
  (
      {active_weak_candidate_issuer_query(db)}
  )
SETTINGS mutations_sync = {mutations_sync}
""",
        ),
    ]
    if cleanup_stale_issues:
        statements.append(
            RepairStatement(
                name="delete_nonlinkable_legacy_open_mapping_issues",
                target_table="id_mapping_issue_v1",
                destructive=True,
                sql=f"""
ALTER TABLE {db}.id_mapping_issue_v1
DELETE WHERE lower(issue_status) NOT IN ('resolved', 'closed', 'ignored')
  AND source_system != 'q_live_migration'
  AND upper(source_entity_key) NOT IN
  (
      SELECT upper(issuer_id) FROM {db}.id_issuer_v1 FINAL WHERE issuer_id != ''
      UNION DISTINCT
      SELECT upper(security_id) FROM {db}.id_security_v1 FINAL WHERE security_id != ''
      UNION DISTINCT
      SELECT upper(listing_id) FROM {db}.id_listing_v1 FINAL WHERE listing_id != ''
      UNION DISTINCT
      SELECT upper(symbol_id) FROM {db}.id_symbol_v1 FINAL WHERE symbol_id != ''
      UNION DISTINCT
      SELECT upper(ticker) FROM {db}.id_symbol_v1 FINAL WHERE ticker != ''
  )
SETTINGS mutations_sync = {mutations_sync}
""",
            )
        )
    return statements


def canonical_alias_ctes(db: str) -> str:
    return f"""
WITH duplicate_identifier_rows AS
(
    SELECT
        lower(identifier_kind) AS identifier_kind,
        identifier_value_normalized,
        issuer_id
    FROM {db}.id_issuer_identifier_v1 FINAL
    WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
      AND identifier_value_normalized != ''
      AND (identifier_kind, identifier_value_normalized) IN
      (
          SELECT lower(identifier_kind), identifier_value_normalized
          FROM {db}.id_issuer_identifier_v1 FINAL
          WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
            AND identifier_value_normalized != ''
          GROUP BY lower(identifier_kind), identifier_value_normalized
          HAVING uniqExact(issuer_id) > 1
      )
),
ranked_identifier_rows AS
(
    SELECT
        identifier_kind,
        identifier_value_normalized,
        issuer_id,
        multiIf(
            identifier_kind = 'cik' AND issuer_id = concat('issuer:cik:', identifier_value_normalized), 0,
            startsWith(issuer_id, 'issuer:cik:'), 1,
            startsWith(issuer_id, 'issuer:sec:'), 2,
            startsWith(issuer_id, 'issuer:massive:'), 3,
            startsWith(issuer_id, 'issuer:ibkr_public:'), 4,
            5
        ) AS priority
    FROM duplicate_identifier_rows
),
canonical_identifier_groups AS
(
    SELECT
        identifier_kind,
        identifier_value_normalized,
        arrayElement(arraySort(x -> (x.1, x.2), groupArray((priority, issuer_id))), 1).2 AS canonical_issuer_id
    FROM ranked_identifier_rows
    GROUP BY identifier_kind, identifier_value_normalized
),
candidate_aliases AS
(
    SELECT
        rows.issuer_id AS old_issuer_id,
        groups.canonical_issuer_id AS canonical_issuer_id
    FROM duplicate_identifier_rows AS rows
    INNER JOIN canonical_identifier_groups AS groups
        ON groups.identifier_kind = rows.identifier_kind
       AND groups.identifier_value_normalized = rows.identifier_value_normalized
    WHERE rows.issuer_id != groups.canonical_issuer_id
),
safe_alias_candidates AS
(
    SELECT old_issuer_id, groupUniqArray(canonical_issuer_id) AS canonical_issuer_ids
    FROM candidate_aliases
    GROUP BY old_issuer_id
),
safe_aliases AS
(
    SELECT old_issuer_id, arrayElement(canonical_issuer_ids, 1) AS canonical_issuer_id
    FROM safe_alias_candidates
    WHERE length(canonical_issuer_ids) = 1
)
"""


def safe_alias_old_issuer_query(db: str) -> str:
    return f"""
SELECT old_issuer_id
FROM
(
    SELECT old_issuer_id, groupUniqArray(canonical_issuer_id) AS canonical_issuer_ids
    FROM
    (
        SELECT rows.issuer_id AS old_issuer_id, groups.canonical_issuer_id AS canonical_issuer_id
        FROM
        (
            SELECT lower(identifier_kind) AS identifier_kind, identifier_value_normalized, issuer_id
            FROM {db}.id_issuer_identifier_v1 FINAL
            WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
              AND identifier_value_normalized != ''
              AND (lower(identifier_kind), identifier_value_normalized) IN
              (
                  SELECT lower(identifier_kind), identifier_value_normalized
                  FROM {db}.id_issuer_identifier_v1 FINAL
                  WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
                    AND identifier_value_normalized != ''
                  GROUP BY lower(identifier_kind), identifier_value_normalized
                  HAVING uniqExact(issuer_id) > 1
              )
        ) AS rows
        INNER JOIN
        (
            SELECT
                identifier_kind,
                identifier_value_normalized,
                arrayElement(arraySort(x -> (x.1, x.2), groupArray((priority, issuer_id))), 1).2 AS canonical_issuer_id
            FROM
            (
                SELECT
                    lower(identifier_kind) AS identifier_kind,
                    identifier_value_normalized,
                    issuer_id,
                    multiIf(
                        lower(identifier_kind) = 'cik' AND issuer_id = concat('issuer:cik:', identifier_value_normalized), 0,
                        startsWith(issuer_id, 'issuer:cik:'), 1,
                        startsWith(issuer_id, 'issuer:sec:'), 2,
                        startsWith(issuer_id, 'issuer:massive:'), 3,
                        startsWith(issuer_id, 'issuer:ibkr_public:'), 4,
                        5
                    ) AS priority
                FROM {db}.id_issuer_identifier_v1 FINAL
                WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
                  AND identifier_value_normalized != ''
                  AND (lower(identifier_kind), identifier_value_normalized) IN
                  (
                      SELECT lower(identifier_kind), identifier_value_normalized
                      FROM {db}.id_issuer_identifier_v1 FINAL
                      WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
                        AND identifier_value_normalized != ''
                      GROUP BY lower(identifier_kind), identifier_value_normalized
                      HAVING uniqExact(issuer_id) > 1
                  )
            )
            GROUP BY identifier_kind, identifier_value_normalized
        ) AS groups
            ON groups.identifier_kind = rows.identifier_kind
           AND groups.identifier_value_normalized = rows.identifier_value_normalized
        WHERE rows.issuer_id != groups.canonical_issuer_id
    )
    GROUP BY old_issuer_id
)
WHERE length(canonical_issuer_ids) = 1
"""


def active_weak_candidate_issuer_query(db: str) -> str:
    return f"""
SELECT DISTINCT issuer_id
FROM
(
    SELECT sec.issuer_id AS issuer_id
    FROM {db}.id_symbol_v1 AS sym FINAL
    INNER JOIN {db}.id_listing_v1 AS listing FINAL ON listing.listing_id = sym.listing_id
    INNER JOIN {db}.id_security_v1 AS sec FINAL ON sec.security_id = listing.security_id
    LEFT JOIN {db}.ref_exchange_v1 AS ex FINAL ON ex.exchange_code = listing.exchange_code
    WHERE sym.status = 'active'
      AND sym.primary_symbol_flag = 1
      AND listing.listing_status = 'active'
      AND upper(listing.currency_code) = 'USD'
      AND upper(ifNull(ex.iso_country_code, '')) = 'US'
      AND upper(sec.product_type) IN ('STK', 'STOCK', 'STOCKS')
      AND sec.issuer_id NOT IN
      (
          SELECT DISTINCT issuer_id
          FROM {db}.id_issuer_identifier_v1 FINAL
          WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
            AND identifier_value_normalized != ''
      )
)
"""


def sec_exact_name_new_cik_ctes(db: str, sec_db: str) -> str:
    return f"""
WITH durable_issuers AS
(
    SELECT DISTINCT issuer_id
    FROM {db}.id_issuer_identifier_v1 FINAL
    WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
      AND identifier_value_normalized != ''
),
weak_names AS
(
    SELECT sec.issuer_id AS old_issuer_id, any(issuer.issuer_name) AS issuer_name
    FROM {db}.id_symbol_v1 AS sym FINAL
    INNER JOIN {db}.id_listing_v1 AS listing FINAL ON listing.listing_id = sym.listing_id
    INNER JOIN {db}.id_security_v1 AS sec FINAL ON sec.security_id = listing.security_id
    LEFT JOIN {db}.id_issuer_v1 AS issuer FINAL ON issuer.issuer_id = sec.issuer_id
    LEFT JOIN {db}.ref_exchange_v1 AS ex FINAL ON ex.exchange_code = listing.exchange_code
    WHERE sym.status = 'active'
      AND sym.primary_symbol_flag = 1
      AND listing.listing_status = 'active'
      AND upper(listing.currency_code) = 'USD'
      AND upper(ifNull(ex.iso_country_code, '')) = 'US'
      AND upper(sec.product_type) IN ('STK', 'STOCK', 'STOCKS')
      AND sec.issuer_id NOT IN (SELECT issuer_id FROM durable_issuers)
    GROUP BY sec.issuer_id
),
weak AS
(
    SELECT old_issuer_id, issuer_name, upper(replaceRegexpAll(issuer_name, '[^A-Za-z0-9]', '')) AS normalized_name
    FROM weak_names
),
sec_names AS
(
    SELECT
        cik,
        entity_name,
        upper(replaceRegexpAll(entity_name, '[^A-Za-z0-9]', '')) AS normalized_name,
        ein,
        sic,
        sic_description,
        state_of_incorporation
    FROM {sec_db}.sec_bulk_mirror_company_v1 FINAL
    WHERE ifNull(entity_name, '') != ''
      AND ifNull(cik, '') != ''
),
raw_matches AS
(
    SELECT
        weak.old_issuer_id AS old_issuer_id,
        weak.issuer_name AS issuer_name,
        sec_names.cik AS cik,
        sec_names.entity_name AS entity_name,
        sec_names.ein AS ein,
        sec_names.sic AS sic,
        sec_names.sic_description AS sic_description,
        sec_names.state_of_incorporation AS state_of_incorporation
    FROM weak
    INNER JOIN sec_names ON sec_names.normalized_name = weak.normalized_name
),
unique_match_groups AS
(
    SELECT
        old_issuer_id,
        groupUniqArray(cik) AS ciks,
        count() AS match_rows,
        any(issuer_name) AS issuer_name,
        any(entity_name) AS entity_name,
        any(ein) AS ein,
        any(sic) AS sic,
        any(sic_description) AS sic_description,
        any(state_of_incorporation) AS state_of_incorporation
    FROM raw_matches
    GROUP BY old_issuer_id
),
unique_matches AS
(
    SELECT
        old_issuer_id,
        arrayElement(ciks, 1) AS cik,
        issuer_name,
        entity_name,
        ein,
        sic,
        sic_description,
        state_of_incorporation
    FROM unique_match_groups
    WHERE length(ciks) = 1
      AND match_rows = 1
),
existing_ciks AS
(
    SELECT DISTINCT identifier_value_normalized AS cik
    FROM {db}.id_issuer_identifier_v1 FINAL
    WHERE lower(identifier_kind) = 'cik'
      AND identifier_value_normalized != ''
),
new_cik_matches AS
(
    SELECT *
    FROM unique_matches
    WHERE cik NOT IN (SELECT cik FROM existing_ciks)
)
"""


def sec_exact_name_existing_cik_alias_ctes(db: str, sec_db: str) -> str:
    return f"""
WITH durable_issuers AS
(
    SELECT DISTINCT issuer_id
    FROM {db}.id_issuer_identifier_v1 FINAL
    WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
      AND identifier_value_normalized != ''
),
weak_names AS
(
    SELECT sec.issuer_id AS old_issuer_id, any(issuer.issuer_name) AS issuer_name
    FROM {db}.id_symbol_v1 AS sym FINAL
    INNER JOIN {db}.id_listing_v1 AS listing FINAL ON listing.listing_id = sym.listing_id
    INNER JOIN {db}.id_security_v1 AS sec FINAL ON sec.security_id = listing.security_id
    LEFT JOIN {db}.id_issuer_v1 AS issuer FINAL ON issuer.issuer_id = sec.issuer_id
    LEFT JOIN {db}.ref_exchange_v1 AS ex FINAL ON ex.exchange_code = listing.exchange_code
    WHERE sym.status = 'active'
      AND sym.primary_symbol_flag = 1
      AND listing.listing_status = 'active'
      AND upper(listing.currency_code) = 'USD'
      AND upper(ifNull(ex.iso_country_code, '')) = 'US'
      AND upper(sec.product_type) IN ('STK', 'STOCK', 'STOCKS')
      AND startsWith(sec.issuer_id, 'issuer:ibkr_public:')
      AND sec.issuer_id NOT IN (SELECT issuer_id FROM durable_issuers)
    GROUP BY sec.issuer_id
),
weak AS
(
    SELECT old_issuer_id, issuer_name, upper(replaceRegexpAll(issuer_name, '[^A-Za-z0-9]', '')) AS normalized_name
    FROM weak_names
),
sec_names AS
(
    SELECT
        cik,
        entity_name,
        upper(replaceRegexpAll(entity_name, '[^A-Za-z0-9]', '')) AS normalized_name
    FROM {sec_db}.sec_bulk_mirror_company_v1 FINAL
    WHERE ifNull(entity_name, '') != ''
      AND ifNull(cik, '') != ''
),
raw_matches AS
(
    SELECT
        weak.old_issuer_id AS old_issuer_id,
        weak.issuer_name AS issuer_name,
        sec_names.cik AS cik,
        sec_names.entity_name AS entity_name
    FROM weak
    INNER JOIN sec_names ON sec_names.normalized_name = weak.normalized_name
),
unique_match_groups AS
(
    SELECT
        old_issuer_id,
        groupUniqArray(cik) AS ciks,
        count() AS match_rows,
        any(issuer_name) AS issuer_name,
        any(entity_name) AS entity_name
    FROM raw_matches
    GROUP BY old_issuer_id
),
unique_matches AS
(
    SELECT
        old_issuer_id,
        arrayElement(ciks, 1) AS cik,
        issuer_name,
        entity_name
    FROM unique_match_groups
    WHERE length(ciks) = 1
      AND match_rows = 1
),
existing_cik_owners AS
(
    SELECT
        identifier_value_normalized AS cik,
        any(issuer_id) AS canonical_issuer_id,
        uniqExact(issuer_id) AS issuer_count
    FROM {db}.id_issuer_identifier_v1 FINAL
    WHERE lower(identifier_kind) = 'cik'
      AND identifier_value_normalized != ''
    GROUP BY identifier_value_normalized
    HAVING issuer_count = 1
),
existing_cik_aliases AS
(
    SELECT
        unique_matches.old_issuer_id AS old_issuer_id,
        existing_cik_owners.canonical_issuer_id AS canonical_issuer_id,
        unique_matches.cik AS cik,
        unique_matches.issuer_name AS issuer_name,
        unique_matches.entity_name AS entity_name
    FROM unique_matches
    INNER JOIN existing_cik_owners USING (cik)
    WHERE unique_matches.old_issuer_id != existing_cik_owners.canonical_issuer_id
      AND NOT startsWith(existing_cik_owners.canonical_issuer_id, 'issuer:ibkr_public:')
)
"""


def sec_exact_name_existing_cik_alias_old_issuer_query(db: str, sec_db: str) -> str:
    return f"""
SELECT old_issuer_id
FROM
(
    SELECT unique_matches.old_issuer_id AS old_issuer_id
    FROM
    (
        SELECT old_issuer_id, arrayElement(ciks, 1) AS cik
        FROM
        (
            SELECT old_issuer_id, groupUniqArray(cik) AS ciks, count() AS match_rows
            FROM
            (
                SELECT weak.old_issuer_id AS old_issuer_id, sec_names.cik AS cik
                FROM
                (
                    SELECT
                        weak_names.old_issuer_id AS old_issuer_id,
                        upper(replaceRegexpAll(weak_names.issuer_name, '[^A-Za-z0-9]', '')) AS normalized_name
                    FROM
                    (
                        SELECT sec.issuer_id AS old_issuer_id, any(issuer.issuer_name) AS issuer_name
                        FROM {db}.id_symbol_v1 AS sym FINAL
                        INNER JOIN {db}.id_listing_v1 AS listing FINAL ON listing.listing_id = sym.listing_id
                        INNER JOIN {db}.id_security_v1 AS sec FINAL ON sec.security_id = listing.security_id
                        LEFT JOIN {db}.id_issuer_v1 AS issuer FINAL ON issuer.issuer_id = sec.issuer_id
                        LEFT JOIN {db}.ref_exchange_v1 AS ex FINAL ON ex.exchange_code = listing.exchange_code
                        WHERE sym.status = 'active'
                          AND sym.primary_symbol_flag = 1
                          AND listing.listing_status = 'active'
                          AND upper(listing.currency_code) = 'USD'
                          AND upper(ifNull(ex.iso_country_code, '')) = 'US'
                          AND upper(sec.product_type) IN ('STK', 'STOCK', 'STOCKS')
                          AND startsWith(sec.issuer_id, 'issuer:ibkr_public:')
                          AND sec.issuer_id NOT IN
                          (
                              SELECT DISTINCT issuer_id
                              FROM {db}.id_issuer_identifier_v1 FINAL
                              WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
                                AND identifier_value_normalized != ''
                          )
                        GROUP BY sec.issuer_id
                    ) AS weak_names
                ) AS weak
                INNER JOIN
                (
                    SELECT
                        cik,
                        upper(replaceRegexpAll(entity_name, '[^A-Za-z0-9]', '')) AS normalized_name
                    FROM {sec_db}.sec_bulk_mirror_company_v1 FINAL
                    WHERE ifNull(entity_name, '') != ''
                      AND ifNull(cik, '') != ''
                ) AS sec_names
                    ON sec_names.normalized_name = weak.normalized_name
            )
            GROUP BY old_issuer_id
        )
        WHERE length(ciks) = 1
          AND match_rows = 1
    ) AS unique_matches
    INNER JOIN
    (
        SELECT
            identifier_value_normalized AS cik,
            any(issuer_id) AS canonical_issuer_id,
            uniqExact(issuer_id) AS issuer_count
        FROM {db}.id_issuer_identifier_v1 FINAL
        WHERE lower(identifier_kind) = 'cik'
          AND identifier_value_normalized != ''
        GROUP BY identifier_value_normalized
        HAVING issuer_count = 1
    ) AS existing_cik_owners USING (cik)
    WHERE unique_matches.old_issuer_id != existing_cik_owners.canonical_issuer_id
      AND NOT startsWith(existing_cik_owners.canonical_issuer_id, 'issuer:ibkr_public:')
)
"""


def qlive_exact_name_alias_ctes_sql(db: str) -> str:
    return f"""
WITH durable_issuers AS
(
    SELECT DISTINCT issuer_id
    FROM {db}.id_issuer_identifier_v1 FINAL
    WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
      AND identifier_value_normalized != ''
),
weak_names AS
(
    SELECT sec.issuer_id AS old_issuer_id, any(issuer.issuer_name) AS issuer_name
    FROM {db}.id_symbol_v1 AS sym FINAL
    INNER JOIN {db}.id_listing_v1 AS listing FINAL ON listing.listing_id = sym.listing_id
    INNER JOIN {db}.id_security_v1 AS sec FINAL ON sec.security_id = listing.security_id
    LEFT JOIN {db}.id_issuer_v1 AS issuer FINAL ON issuer.issuer_id = sec.issuer_id
    LEFT JOIN {db}.ref_exchange_v1 AS ex FINAL ON ex.exchange_code = listing.exchange_code
    WHERE sym.status = 'active'
      AND sym.primary_symbol_flag = 1
      AND listing.listing_status = 'active'
      AND upper(listing.currency_code) = 'USD'
      AND upper(ifNull(ex.iso_country_code, '')) = 'US'
      AND upper(sec.product_type) IN ('STK', 'STOCK', 'STOCKS')
      AND startsWith(sec.issuer_id, 'issuer:ibkr_public:')
      AND sec.issuer_id NOT IN (SELECT issuer_id FROM durable_issuers)
    GROUP BY sec.issuer_id
),
weak AS
(
    SELECT old_issuer_id, issuer_name, upper(replaceRegexpAll(issuer_name, '[^A-Za-z0-9]', '')) AS normalized_name
    FROM weak_names
),
canonical_names AS
(
    SELECT
        issuer.issuer_id AS canonical_issuer_id,
        issuer.issuer_name AS canonical_issuer_name,
        upper(replaceRegexpAll(issuer.issuer_name, '[^A-Za-z0-9]', '')) AS normalized_name
    FROM {db}.id_issuer_v1 AS issuer FINAL
    WHERE issuer.issuer_id IN (SELECT issuer_id FROM durable_issuers)
      AND NOT startsWith(issuer.issuer_id, 'issuer:ibkr_public:')
),
raw_matches AS
(
    SELECT
        weak.old_issuer_id AS old_issuer_id,
        weak.issuer_name AS weak_issuer_name,
        canonical_names.canonical_issuer_id AS canonical_issuer_id,
        canonical_names.canonical_issuer_name AS canonical_issuer_name
    FROM weak
    INNER JOIN canonical_names ON canonical_names.normalized_name = weak.normalized_name
    WHERE weak.old_issuer_id != canonical_names.canonical_issuer_id
),
qlive_exact_name_alias_groups AS
(
    SELECT
        old_issuer_id,
        groupUniqArray(canonical_issuer_id) AS canonical_issuer_ids,
        count() AS match_rows,
        any(weak_issuer_name) AS weak_issuer_name,
        any(canonical_issuer_name) AS canonical_issuer_name
    FROM raw_matches
    GROUP BY old_issuer_id
),
qlive_exact_name_aliases AS
(
    SELECT
        old_issuer_id,
        arrayElement(canonical_issuer_ids, 1) AS canonical_issuer_id,
        weak_issuer_name,
        canonical_issuer_name
    FROM qlive_exact_name_alias_groups
    WHERE length(canonical_issuer_ids) = 1
)
"""


def qlive_exact_name_alias_old_issuer_query(db: str) -> str:
    return f"""
SELECT old_issuer_id
FROM
(
    SELECT
        weak.old_issuer_id AS old_issuer_id
    FROM
    (
        SELECT
            weak_names.old_issuer_id AS old_issuer_id,
            upper(replaceRegexpAll(weak_names.issuer_name, '[^A-Za-z0-9]', '')) AS normalized_name
        FROM
        (
            SELECT sec.issuer_id AS old_issuer_id, any(issuer.issuer_name) AS issuer_name
            FROM {db}.id_symbol_v1 AS sym FINAL
            INNER JOIN {db}.id_listing_v1 AS listing FINAL ON listing.listing_id = sym.listing_id
            INNER JOIN {db}.id_security_v1 AS sec FINAL ON sec.security_id = listing.security_id
            LEFT JOIN {db}.id_issuer_v1 AS issuer FINAL ON issuer.issuer_id = sec.issuer_id
            LEFT JOIN {db}.ref_exchange_v1 AS ex FINAL ON ex.exchange_code = listing.exchange_code
            WHERE sym.status = 'active'
              AND sym.primary_symbol_flag = 1
              AND listing.listing_status = 'active'
              AND upper(listing.currency_code) = 'USD'
              AND upper(ifNull(ex.iso_country_code, '')) = 'US'
              AND upper(sec.product_type) IN ('STK', 'STOCK', 'STOCKS')
              AND startsWith(sec.issuer_id, 'issuer:ibkr_public:')
              AND sec.issuer_id NOT IN
              (
                  SELECT DISTINCT issuer_id
                  FROM {db}.id_issuer_identifier_v1 FINAL
                  WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
                    AND identifier_value_normalized != ''
              )
            GROUP BY sec.issuer_id
        ) AS weak_names
    ) AS weak
    INNER JOIN
    (
        SELECT issuer_id AS canonical_issuer_id, upper(replaceRegexpAll(issuer_name, '[^A-Za-z0-9]', '')) AS normalized_name
        FROM {db}.id_issuer_v1 FINAL
        WHERE issuer_id IN
        (
            SELECT DISTINCT issuer_id
            FROM {db}.id_issuer_identifier_v1 FINAL
            WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
              AND identifier_value_normalized != ''
        )
          AND NOT startsWith(issuer_id, 'issuer:ibkr_public:')
    ) AS canonical_names
        ON canonical_names.normalized_name = weak.normalized_name
    WHERE weak.old_issuer_id != canonical_names.canonical_issuer_id
    GROUP BY weak.old_issuer_id
    HAVING uniqExact(canonical_names.canonical_issuer_id) = 1
)
"""


def collect_diagnostics(client: ClickHouseHttpClient, database: str, sec_core_database: str) -> dict[str, Any]:
    db = quote_ident(database)
    sec_db = quote_ident(sec_core_database)
    diagnostics = {
        "duplicate_durable_identifier_groups": scalar_int(
            client,
            f"""
            SELECT count()
            FROM
            (
                SELECT lower(identifier_kind), identifier_value_normalized, uniqExact(issuer_id) AS issuer_count
                FROM {db}.id_issuer_identifier_v1 FINAL
                WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
                  AND identifier_value_normalized != ''
                GROUP BY lower(identifier_kind), identifier_value_normalized
                HAVING issuer_count > 1
            )
            """,
        ),
        "safe_duplicate_alias_issuers": scalar_int(client, f"SELECT count() FROM ({safe_alias_old_issuer_query(db)})"),
        "securities_on_duplicate_alias_issuers": scalar_int(
            client,
            f"""
            SELECT count()
            FROM {db}.id_security_v1 FINAL
            WHERE issuer_id IN ({safe_alias_old_issuer_query(db)})
            """,
        ),
        "active_candidates_without_durable_issuer_id": scalar_int(
            client,
            f"""
            WITH durable_issuers AS
            (
                SELECT DISTINCT issuer_id
                FROM {db}.id_issuer_identifier_v1 FINAL
                WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
                  AND identifier_value_normalized != ''
            )
            SELECT count()
            FROM
            (
                SELECT sec.issuer_id
                FROM {db}.id_symbol_v1 AS sym FINAL
                INNER JOIN {db}.id_listing_v1 AS listing FINAL ON listing.listing_id = sym.listing_id
                INNER JOIN {db}.id_security_v1 AS sec FINAL ON sec.security_id = listing.security_id
                LEFT JOIN {db}.ref_exchange_v1 AS ex FINAL ON ex.exchange_code = listing.exchange_code
                WHERE sym.status = 'active'
                  AND sym.primary_symbol_flag = 1
                  AND listing.listing_status = 'active'
                  AND upper(listing.currency_code) = 'USD'
                  AND upper(ifNull(ex.iso_country_code, '')) = 'US'
                  AND upper(sec.product_type) IN ('STK', 'STOCK', 'STOCKS')
                  AND sec.issuer_id NOT IN (SELECT issuer_id FROM durable_issuers)
            )
            """,
        ),
        "open_mapping_issues": scalar_int(
            client,
            f"""
            SELECT count()
            FROM {db}.id_mapping_issue_v1 FINAL
            WHERE lower(issue_status) NOT IN ('resolved', 'closed', 'ignored')
            """,
        ),
        "nonlinkable_legacy_open_mapping_issues": scalar_int(
            client,
            f"""
            SELECT count()
            FROM {db}.id_mapping_issue_v1 FINAL
            WHERE lower(issue_status) NOT IN ('resolved', 'closed', 'ignored')
              AND source_system != 'q_live_migration'
              AND upper(source_entity_key) NOT IN
              (
                  SELECT upper(issuer_id) FROM {db}.id_issuer_v1 FINAL WHERE issuer_id != ''
                  UNION DISTINCT
                  SELECT upper(security_id) FROM {db}.id_security_v1 FINAL WHERE security_id != ''
                  UNION DISTINCT
                  SELECT upper(listing_id) FROM {db}.id_listing_v1 FINAL WHERE listing_id != ''
                  UNION DISTINCT
                  SELECT upper(symbol_id) FROM {db}.id_symbol_v1 FINAL WHERE symbol_id != ''
                  UNION DISTINCT
                  SELECT upper(ticker) FROM {db}.id_symbol_v1 FINAL WHERE ticker != ''
              )
            """,
        ),
        "sec_exact_name_unique_matches": scalar_int(
            client,
            f"""
            {sec_exact_name_new_cik_ctes(db, sec_db)}
            SELECT count()
            FROM unique_matches
            """,
        ),
        "sec_exact_name_new_cik_matches": scalar_int(
            client,
            f"""
            {sec_exact_name_new_cik_ctes(db, sec_db)}
            SELECT count()
            FROM new_cik_matches
            """,
        ),
        "sec_exact_name_existing_cik_aliases": scalar_int(
            client,
            f"""
            {sec_exact_name_existing_cik_alias_ctes(db, sec_db)}
            SELECT count()
            FROM existing_cik_aliases
            """,
        ),
        "sec_exact_name_existing_cik_alias_security_rows": scalar_int(
            client,
            f"""
            SELECT count()
            FROM {db}.id_security_v1 FINAL
            WHERE issuer_id IN
            (
                {sec_exact_name_existing_cik_alias_old_issuer_query(db, sec_db)}
            )
            """,
        ),
        "qlive_exact_name_aliases": scalar_int(
            client,
            f"""
            {qlive_exact_name_alias_ctes_sql(db)}
            SELECT count()
            FROM qlive_exact_name_aliases
            """,
        ),
        "qlive_exact_name_alias_security_rows": scalar_int(
            client,
            f"""
            SELECT count()
            FROM {db}.id_security_v1 FINAL
            WHERE issuer_id IN
            (
                {qlive_exact_name_alias_old_issuer_query(db)}
            )
            """,
        ),
        "duplicate_identifier_sample": query_json_each_row(
            client,
            f"""
            SELECT identifier_kind, identifier_value_normalized, uniqExact(issuer_id) AS issuer_count, groupArray(issuer_id) AS issuer_ids
            FROM
            (
                SELECT lower(identifier_kind) AS identifier_kind, identifier_value_normalized, issuer_id
                FROM {db}.id_issuer_identifier_v1 FINAL
                WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
                  AND identifier_value_normalized != ''
            )
            GROUP BY identifier_kind, identifier_value_normalized
            HAVING issuer_count > 1
            ORDER BY issuer_count DESC, identifier_value_normalized
            LIMIT 20
            """,
        ),
    }
    return diagnostics


def execute_statements(client: ClickHouseHttpClient, statements: list[RepairStatement], log_path: Path) -> list[dict[str, Any]]:
    rows = []
    for index, statement in enumerate(statements, start=1):
        started = time.perf_counter()
        before = scalar_int(client, f"SELECT count() FROM {statement_target(statement)}")
        row = {
            "phase": "execute_statement",
            "index": index,
            "name": statement.name,
            "target_table": statement.target_table,
            "destructive": statement.destructive,
            "rows_before": before,
            "started_at_utc": datetime.now(UTC).isoformat(),
        }
        try:
            client.execute(statement.sql.strip())
            after = scalar_int(client, f"SELECT count() FROM {statement_target(statement)}")
            row.update(
                {
                    "status": "ok",
                    "rows_after": after,
                    "delta_rows": after - before,
                    "wall_seconds": round(time.perf_counter() - started, 3),
                    "finished_at_utc": datetime.now(UTC).isoformat(),
                }
            )
        except Exception as exc:
            row.update(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "wall_seconds": round(time.perf_counter() - started, 3),
                    "finished_at_utc": datetime.now(UTC).isoformat(),
                }
            )
            write_jsonl(log_path, [row])
            raise
        write_jsonl(log_path, [row])
        rows.append(row)
        print(
            f"executed {index}/{len(statements)} {statement.name}: status={row['status']} "
            f"rows_before={before:,} rows_after={row['rows_after']:,} seconds={row['wall_seconds']}",
            flush=True,
        )
    return rows


def statement_target(statement: RepairStatement) -> str:
    return f"{quote_ident(_CURRENT_TARGET_DATABASE)}.{quote_ident(statement.target_table)}"


_CURRENT_TARGET_DATABASE = DEFAULT_TARGET_DATABASE


def insert_run_row(
    client: ClickHouseHttpClient,
    target_db: str,
    run_id: str,
    status: str,
    inserted_at: str,
    *,
    rows_read: int,
    rows_written: int,
    rows_failed: int,
) -> None:
    global _CURRENT_TARGET_DATABASE
    _CURRENT_TARGET_DATABASE = target_db
    now = clickhouse_now64()
    row = {
        "run_id": run_id,
        "job_name": "step_02b_repair_reference_identity",
        "job_type": "migration_repair",
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


def insert_json_each_row(client: ClickHouseHttpClient, database: str, table_name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows)
    client.execute(f"INSERT INTO {quote_ident(database)}.{quote_ident(table_name)} FORMAT JSONEachRow\n{body}")


def write_rendered_sql(path: Path, statements: list[RepairStatement]) -> None:
    path.write_text("\n\n".join(f"-- {statement.name}\n{statement.sql.strip()};" for statement in statements) + "\n", encoding="utf-8")


def write_manifest(
    path: Path,
    args: argparse.Namespace,
    paths: StepPaths,
    loaded_env: list[Path],
    run_id: str,
    diagnostics_before: dict[str, Any],
    statements: list[RepairStatement],
) -> None:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "machine": machine_name(),
        "repo_root": str(REPO_ROOT),
        "git_commit": quiet_git_commit(REPO_ROOT),
        "job_type": "step_02b_repair_reference_identity",
        "migration_run_id": run_id,
        "dry_run": not args.execute,
        "target_database": args.target_database,
        "sec_core_database": args.sec_core_database,
        "run_root": str(paths.run_root),
        "rendered_sql": str(paths.rendered_sql),
        "execution_jsonl": str(paths.execution_jsonl),
        "loaded_env_files": [str(path) for path in loaded_env],
        "diagnostics_before": diagnostics_before,
        "statements": [{"name": statement.name, "target_table": statement.target_table, "destructive": statement.destructive} for statement in statements],
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


def write_summary(
    path: Path,
    args: argparse.Namespace,
    run_id: str,
    before: dict[str, Any],
    after: dict[str, Any] | None,
    executed_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# q_live Step 02b Reference Identity Repair",
        "",
        f"- run_id: `{run_id}`",
        f"- target_database: `{args.target_database}`",
        f"- execute: `{args.execute}`",
        f"- stale_issue_cleanup: `{not args.skip_stale_issue_cleanup}`",
        "",
        "## Diagnostics Before",
        "",
    ]
    for key, value in before.items():
        if key.endswith("_sample"):
            continue
        lines.append(f"- {key}: `{value}`")
    if after is not None:
        lines.extend(["", "## Diagnostics After", ""])
        for key, value in after.items():
            if key.endswith("_sample"):
                continue
            lines.append(f"- {key}: `{value}`")
    if executed_rows:
        lines.extend(["", "## Executed Statements", ""])
        for row in executed_rows:
            lines.append(f"- {row['name']}: `{row['status']}`, delta_rows=`{row.get('delta_rows')}`, seconds=`{row.get('wall_seconds')}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_diagnostics(label: str, diagnostics: dict[str, Any]) -> None:
    print(f"diagnostics={label}", flush=True)
    for key, value in diagnostics.items():
        if key.endswith("_sample"):
            continue
        print(f"  {key}={value:,}" if isinstance(value, int) else f"  {key}={value}", flush=True)


def sum_diagnostic_rows(diagnostics: dict[str, Any]) -> int:
    return sum(value for key, value in diagnostics.items() if isinstance(value, int) and not key.endswith("_sample"))


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    text = client.execute(sql.strip().rstrip(";") + "\nFORMAT TSV").strip()
    if not text:
        return 0
    return int(text.splitlines()[0].split("\t")[0])


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = client.execute(sql.rstrip(";") + "\nFORMAT JSONEachRow")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


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


def clickhouse_now64() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def validate_database_name(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def print_header(args: argparse.Namespace, paths: StepPaths, loaded_env: list[Path], run_id: str) -> None:
    print("=" * 96, flush=True)
    print("q_live migration step 2b: reference identity repair", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"target_database={args.target_database}", flush=True)
    print(f"sec_core_database={args.sec_core_database}", flush=True)
    print(f"migration_run_id={run_id}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print(f"skip_stale_issue_cleanup={args.skip_stale_issue_cleanup}", flush=True)
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
