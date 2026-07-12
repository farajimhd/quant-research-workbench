# q_live Migration MLOps

This folder contains read-only audit and migration planning utilities for moving trusted runtime/publication data from `trading_dashboard_dev` into `q_live`.

## Phase 1: Source Schema Audit

Run the audit before designing the corrected `q_live` schema:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\audit_trading_dashboard_dev.py --profile-mode metadata --output-root-win D:/market-data/prepared/q_live_migration/schema_audit
```

Workstation runtime command after sync:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\audit_trading_dashboard_dev.py --profile-mode metadata --output-root-win D:/market-data/prepared/q_live_migration/schema_audit
```

Local command:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\audit_trading_dashboard_dev.py --profile-mode metadata --output-root-win D:/market-data/prepared/q_live_migration/schema_audit
```

Use `--profile-mode metadata` for the default audit. It reads ClickHouse system metadata only, so it avoids expensive scans of large tables.

Use `--profile-mode light` only when you want small row samples and key-column profiles. It scans likely key columns and can be slower on large tables:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\audit_trading_dashboard_dev.py --profile-mode light --sample-rows 3 --output-root-win D:/market-data/prepared/q_live_migration/schema_audit
```

## Outputs

Each run writes a timestamped folder containing:

- `tables.jsonl`: table engines, keys, row counts, bytes, storage policy, and source create SQL fields.
- `columns.jsonl`: full column inventory with type, key membership, codec, defaults, and role hints.
- `parts.jsonl`: active part storage by table and disk from `system.parts`.
- `table_profiles.jsonl`: per-table summary; in `light` mode includes samples and key-column profiles.
- `inferred_relations.jsonl`: schema-name relation candidates to validate before migration.
- `create_statements.sql`: source DDL snapshot.
- `audit_manifest.json`: reproducibility metadata and secret presence only.
- `schema_audit_summary.md`: human-readable summary for phase 2 schema design.

## Environment

Connection resolution order:

- `QLIVE_MIGRATION_CLICKHOUSE_URL`, then `REAL_LIVE_CLICKHOUSE_READ_URL`, then `SEC_CLICKHOUSE_URL`, then `QMD_CLICKHOUSE_URL`.
- `QLIVE_MIGRATION_CLICKHOUSE_USER`, then matching read/source user variables.
- `QLIVE_MIGRATION_CLICKHOUSE_PASSWORD`, then matching read/source password variables.

Other useful variables:

- `QLIVE_MIGRATION_SOURCE_DATABASE`, default `trading_dashboard_dev`.
- `QLIVE_MIGRATION_TARGET_DATABASE`, default `q_live`.
- `QLIVE_MIGRATION_OUTPUT_ROOT_WIN`, default `D:/market-data/prepared/q_live_migration/schema_audit`.
- `QLIVE_MIGRATION_PROFILE_MODE`, default `metadata`.
- `QLIVE_MIGRATION_SAMPLE_ROWS`, default `3`.

The audit does not write to ClickHouse.

## Phase 2: Target Schema Design

The first q_live target design is documented in:

- `q_live_schema_design.md`
- `q_live_target_schema.sql`

These are review artifacts. They do not execute migration. The SQL file is a draft that must be run through a schema creation script after replacing the storage-policy placeholder with `CLICKHOUSE_LIVE_STORAGE_POLICY`.

## Step 1: Create q_live Schema

Render the schema without touching ClickHouse:

Local:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_01_create_q_live_schema.py --output-root-win D:/market-data/prepared/q_live_migration/schema_create
```

Workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\step_01_create_q_live_schema.py --output-root-win D:/market-data/prepared/q_live_migration/schema_create
```

Execute the schema after reviewing the rendered SQL:

Local:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_01_create_q_live_schema.py --execute --output-root-win D:/market-data/prepared/q_live_migration/schema_create
```

Workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\step_01_create_q_live_schema.py --execute --output-root-win D:/market-data/prepared/q_live_migration/schema_create
```

Important behavior:

- Default mode is dry-run.
- `--execute` is required before any DDL is sent to ClickHouse.
- The script requires `CLICKHOUSE_LIVE_STORAGE_POLICY` unless `--allow-empty-storage-policy` is passed.
- Every run writes `rendered_q_live_schema.sql`, `schema_create_manifest.json`, and `schema_create_execution.jsonl`.

## Step 2: Migrate Reference And Identity Tables

Dry-run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_02_migrate_reference_identity.py --output-root-win D:/market-data/prepared/q_live_migration/step_02_reference_identity
```

Dry-run on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\step_02_migrate_reference_identity.py --output-root-win D:/market-data/prepared/q_live_migration/step_02_reference_identity
```

Execute locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_02_migrate_reference_identity.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_02_reference_identity
```

Execute on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\step_02_migrate_reference_identity.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_02_reference_identity
```

Record validation only after a completed migration, without inserting migrated rows:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_02_migrate_reference_identity.py --validate-only --output-root-win D:/market-data/prepared/q_live_migration/step_02_reference_identity
```

Step 2 migrates:

- Reference tables: country, asset class, exchange, exchange currency, ticker type.
- Identity tables: issuer, issuer identifiers, security, security identifiers, listing, symbol, source mappings, mapping issues.

Legacy source mapping issues are migrated only when their `source_entity_key`
can be linked to the migrated issuer/security/listing/symbol graph or an active
symbol ticker. Historical unresolved source-only issues are not copied into
`q_live.id_mapping_issue_v1`; they are migration artifacts, not current canonical
graph blockers.

Default mode is dry-run. The script refuses to append into non-empty target tables unless `--allow-non-empty-targets` is passed. Validation compares target logical `FINAL` row counts to source distinct-key counts, because the source tables are `ReplacingMergeTree` and can contain duplicate physical rows.

## Step 2b: Repair Reference Identity

Run this after Step 2 and before Step 3, Step 5, or Step 6. It handles the
identity problems that should be resolved during migration rather than by the
reference gateway:

- duplicate durable issuer identifiers: pick a deterministic canonical issuer,
  mark duplicate aliases as merged, remove non-canonical CIK/LEI/EIN rows, and
  remap securities from duplicate issuer aliases to the canonical issuer
- weak issuer identity: create explicit `weak_issuer_identity` mapping issues
  for active US stock candidates that still lack CIK/LEI/EIN
- stale open mapping issues: delete migrated legacy open issues whose
  `source_entity_key` does not link to the current canonical graph

Dry-run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_02b_repair_reference_identity.py --target-database q_live --output-root-win D:/market-data/prepared/q_live_migration/step_02b_reference_identity_repair
```

Dry-run on workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\step_02b_repair_reference_identity.py --target-database q_live --output-root-win D:/market-data/prepared/q_live_migration/step_02b_reference_identity_repair
```

Execute locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_02b_repair_reference_identity.py --target-database q_live --execute --output-root-win D:/market-data/prepared/q_live_migration/step_02b_reference_identity_repair
```

Execute on workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\step_02b_repair_reference_identity.py --target-database q_live --execute --output-root-win D:/market-data/prepared/q_live_migration/step_02b_reference_identity_repair
```

The step writes a manifest, rendered SQL, execution JSONL, and Markdown summary.
It uses ClickHouse mutations for cleanup because several current migration
tables have mutable fields in their sorting keys, so an inserted replacement row
alone cannot remove old non-canonical identity rows or old open issue rows.

## Step 3: Migrate Market Publication Tables

Dry-run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_03_migrate_market_publications.py --output-root-win D:/market-data/prepared/q_live_migration/step_03_market_publications
```

Dry-run on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\step_03_migrate_market_publications.py --output-root-win D:/market-data/prepared/q_live_migration/step_03_market_publications
```

Execute locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_03_migrate_market_publications.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_03_market_publications
```

Resume after a partial run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_03_migrate_market_publications.py --execute --skip-non-empty-targets --output-root-win D:/market-data/prepared/q_live_migration/step_03_market_publications
```

Execute on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\step_03_migrate_market_publications.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_03_market_publications
```

Step 3 migrates:

- security classification
- market/security snapshots
- float
- short interest
- short volume
- stock splits
- cash dividends
- IPOs
- presentation assets
- Massive flatfile source inventory

Financial statement snapshots are intentionally deferred to the fundamentals/feature migration because they need stronger alignment with SEC/XBRL feature design.

Large date-partitioned publication tables are inserted in calendar-year batches to avoid ClickHouse's `max_partitions_per_insert_block` guard. Validation compares target `FINAL` counts against the same logical keys used by each target `ReplacingMergeTree` sorting key, not always a single provider event id.

## Step 4: Migrate SEC Filing and XBRL Publication Tables

Dry-run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_04_migrate_sec_publications.py --output-root-win D:/market-data/prepared/q_live_migration/step_04_sec_publications
```

Dry-run on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\step_04_migrate_sec_publications.py --output-root-win D:/market-data/prepared/q_live_migration/step_04_sec_publications
```

Execute locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_04_migrate_sec_publications.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_04_sec_publications
```

Resume after a partial run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_04_migrate_sec_publications.py --execute --skip-non-empty-targets --output-root-win D:/market-data/prepared/q_live_migration/step_04_sec_publications
```

Execute on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\step_04_migrate_sec_publications.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_04_sec_publications
```

Step 4 migrates:

- SEC filing metadata into `sec_filing_v2`
- SEC XBRL concept metadata
- SEC XBRL company facts
- SEC XBRL frame metadata
- SEC XBRL frame observations

The source filing table does not contain exact SEC acceptance timestamps, so `accepted_at_utc` is intentionally null and `accepted_at_source` is `missing_in_source`. That field must be backfilled by the SEC downloader/parser that reads submissions bulk data or filing headers.

SEC date-batched inserts use `toYear(batch_column) = year` instead of an exclusive next-year date bound. ClickHouse `Date` values can reach the upper supported boundary, such as `2149-06-06`, and an upper bound like `2150-01-01` can overflow.

## Step 5: Validate q_live Migration

Dry-run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_05_validate_q_live_migration.py --output-root-win D:/market-data/prepared/q_live_migration/step_05_validation
```

Dry-run on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\step_05_validate_q_live_migration.py --output-root-win D:/market-data/prepared/q_live_migration/step_05_validation
```

Execute locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_05_validate_q_live_migration.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_05_validation
```

Execute on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\step_05_validate_q_live_migration.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_05_validation
```

Step 5 validates:

- source-to-target logical row reconciliation for Steps 2-4
- identity repairs from Step 2b through dedicated operational checks
- critical-key null/empty counts
- target `FINAL` counts and ReplacingMergeTree duplicate physical rows
- `source_run_id`, `source_content_sha256`, and latest insert timestamps where applicable
- `CLICKHOUSE_LIVE_STORAGE_POLICY` coverage across q_live MergeTree tables
- latest migration run status rows
- known pending work for SEC accepted timestamps, filing document/text extraction, SEC market bridge, and derived feature tables

The command writes local JSONL and Markdown reports in both dry-run and execute mode. `--execute` also records validation rows in `q_live.sync_validation_v1` and one run row in `q_live.source_run_v1`.

## Step 6: Build Bridge And Derived Feature Tables

Dry-run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_06_build_q_live_bridge_features.py --output-root-win D:/market-data/prepared/q_live_migration/step_06_bridge_features --feature-date 2026-06-09
```

Execute locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_06_build_q_live_bridge_features.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_06_bridge_features --feature-date 2026-06-09
```

Resume after a partial run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_06_build_q_live_bridge_features.py --execute --skip-non-empty-targets --output-root-win D:/market-data/prepared/q_live_migration/step_06_bridge_features --feature-date 2026-06-09
```

Execute on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\step_06_build_q_live_bridge_features.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_06_bridge_features --feature-date 2026-06-09
```

Step 6 builds:

- `id_sec_market_bridge_v1` from issuer CIK identifiers to active security/listing/symbol rows
- `feature_tradable_universe_v1` for the requested feature date
- `feature_scanner_static_v1` for the requested feature date

This step intentionally does not invent SEC accepted timestamps, SEC document
metadata, or filing text. Those require SEC submissions/daily-feed artifacts and
are handled by the SEC EDGAR pipeline, which writes `sec_filing_document_v2` and
`sec_filing_text_v2`.

## Step 7: Backfill Existing SEC Filing Accepted Timestamps

Dry-run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_07_backfill_sec_accepted_timestamps.py --output-root-win D:/market-data/prepared/q_live_migration/step_07_sec_accepted_timestamps
```

Execute locally after the accepted timestamp source table exists:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_07_backfill_sec_accepted_timestamps.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_07_sec_accepted_timestamps
```

Execute on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\migration\step_07_backfill_sec_accepted_timestamps.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_07_sec_accepted_timestamps
```

Step 7 backfills `q_live.sec_filing_v2.accepted_at_utc` from an SEC accepted timestamp source table, defaulting to `sec_core.sec_bulk_mirror_filing_acceptance_v1`.

Execution writes replacement rows by `toYYYYMM(accepted_at_utc)` so a multi-year backfill does not exceed ClickHouse's per-insert partition limit. If a run is interrupted, rerun the same command; already-filled keys are skipped because the candidate set is restricted to target rows where `accepted_at_utc IS NULL`.

Strict scope:

- It only inserts replacement versions for filing keys that already exist in `q_live.sec_filing_v2`.
- It joins on `(cik, accession_number)`.
- It never inserts a new logical filing key from the source table.
- It validates that the target logical `FINAL` row count is unchanged after execution.

Required source columns:

- `cik`
- `accession_number`
- `accepted_at_utc`
- `acceptance_datetime_raw`
- `accepted_at_source`

Optional source columns, used when present:

- `company_name`
- `primary_document`
- `primary_document_url`
- `filing_detail_url`
- `filing_size`
- `items`

Useful overrides:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\migration\step_07_backfill_sec_accepted_timestamps.py --source-database sec_core --source-table sec_bulk_mirror_filing_acceptance_v1 --output-root-win D:/market-data/prepared/q_live_migration/step_07_sec_accepted_timestamps
```

Environment defaults:

- `QLIVE_MIGRATION_SEC_ACCEPTED_SOURCE_DATABASE`, then `SEC_CLICKHOUSE_DATABASE`, then `SEC_ACCEPTED_SOURCE_DATABASE`, default `sec_core`.
- `QLIVE_MIGRATION_SEC_ACCEPTED_SOURCE_TABLE`, then `SEC_ACCEPTED_SOURCE_TABLE`, default `sec_bulk_mirror_filing_acceptance_v1`.

If the source table does not exist, dry-run records a blocked report and exits without writing to ClickHouse. Execute mode refuses to run until the source exists.

Build the focused source table first with `sec_acceptance_backfill_build.py`. It reads current missing q_live filing keys, extracts only matching rows from SEC `submissions.zip`, saves `accepted_rows.jsonl`, `not_found_keys.jsonl`, and `not_found_ciks.jsonl`, then inserts only matched accepted rows when `--execute` is used.
