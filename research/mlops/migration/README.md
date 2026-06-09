# q_live Migration MLOps

This folder contains read-only audit and migration planning utilities for moving trusted runtime/publication data from `trading_dashboard_dev` into `q_live`.

## Phase 1: Source Schema Audit

Run the audit before designing the corrected `q_live` schema:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\audit_trading_dashboard_dev.py --profile-mode metadata --output-root-win D:/market-data/prepared/q_live_migration/schema_audit
```

Workstation runtime command after sync:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\migration\audit_trading_dashboard_dev.py --profile-mode metadata --output-root-win D:/market-data/prepared/q_live_migration/schema_audit
```

Local command:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\audit_trading_dashboard_dev.py --profile-mode metadata --output-root-win D:/market-data/prepared/q_live_migration/schema_audit
```

Use `--profile-mode metadata` for the default audit. It reads ClickHouse system metadata only, so it avoids expensive scans of large tables.

Use `--profile-mode light` only when you want small row samples and key-column profiles. It scans likely key columns and can be slower on large tables:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\migration\audit_trading_dashboard_dev.py --profile-mode light --sample-rows 3 --output-root-win D:/market-data/prepared/q_live_migration/schema_audit
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
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_01_create_q_live_schema.py --output-root-win D:/market-data/prepared/q_live_migration/schema_create
```

Workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\migration\step_01_create_q_live_schema.py --output-root-win D:/market-data/prepared/q_live_migration/schema_create
```

Execute the schema after reviewing the rendered SQL:

Local:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_01_create_q_live_schema.py --execute --output-root-win D:/market-data/prepared/q_live_migration/schema_create
```

Workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\migration\step_01_create_q_live_schema.py --execute --output-root-win D:/market-data/prepared/q_live_migration/schema_create
```

Important behavior:

- Default mode is dry-run.
- `--execute` is required before any DDL is sent to ClickHouse.
- The script requires `CLICKHOUSE_LIVE_STORAGE_POLICY` unless `--allow-empty-storage-policy` is passed.
- Every run writes `rendered_q_live_schema.sql`, `schema_create_manifest.json`, and `schema_create_execution.jsonl`.

## Step 2: Migrate Reference And Identity Tables

Dry-run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_02_migrate_reference_identity.py --output-root-win D:/market-data/prepared/q_live_migration/step_02_reference_identity
```

Dry-run on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\migration\step_02_migrate_reference_identity.py --output-root-win D:/market-data/prepared/q_live_migration/step_02_reference_identity
```

Execute locally:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_02_migrate_reference_identity.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_02_reference_identity
```

Execute on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\migration\step_02_migrate_reference_identity.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_02_reference_identity
```

Record validation only after a completed migration, without inserting migrated rows:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_02_migrate_reference_identity.py --validate-only --output-root-win D:/market-data/prepared/q_live_migration/step_02_reference_identity
```

Step 2 migrates:

- Reference tables: country, asset class, exchange, exchange currency, ticker type.
- Identity tables: issuer, issuer identifiers, security, security identifiers, listing, symbol, source mappings, mapping issues.

Default mode is dry-run. The script refuses to append into non-empty target tables unless `--allow-non-empty-targets` is passed. Validation compares target logical `FINAL` row counts to source distinct-key counts, because the source tables are `ReplacingMergeTree` and can contain duplicate physical rows.

## Step 3: Migrate Market Publication Tables

Dry-run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_03_migrate_market_publications.py --output-root-win D:/market-data/prepared/q_live_migration/step_03_market_publications
```

Dry-run on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\migration\step_03_migrate_market_publications.py --output-root-win D:/market-data/prepared/q_live_migration/step_03_market_publications
```

Execute locally:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_03_migrate_market_publications.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_03_market_publications
```

Resume after a partial run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_03_migrate_market_publications.py --execute --skip-non-empty-targets --output-root-win D:/market-data/prepared/q_live_migration/step_03_market_publications
```

Execute on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\migration\step_03_migrate_market_publications.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_03_market_publications
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
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_04_migrate_sec_publications.py --output-root-win D:/market-data/prepared/q_live_migration/step_04_sec_publications
```

Dry-run on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\migration\step_04_migrate_sec_publications.py --output-root-win D:/market-data/prepared/q_live_migration/step_04_sec_publications
```

Execute locally:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_04_migrate_sec_publications.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_04_sec_publications
```

Resume after a partial run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_04_migrate_sec_publications.py --execute --skip-non-empty-targets --output-root-win D:/market-data/prepared/q_live_migration/step_04_sec_publications
```

Execute on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\migration\step_04_migrate_sec_publications.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_04_sec_publications
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
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_05_validate_q_live_migration.py --output-root-win D:/market-data/prepared/q_live_migration/step_05_validation
```

Dry-run on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\migration\step_05_validate_q_live_migration.py --output-root-win D:/market-data/prepared/q_live_migration/step_05_validation
```

Execute locally:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_05_validate_q_live_migration.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_05_validation
```

Execute on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\migration\step_05_validate_q_live_migration.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_05_validation
```

Step 5 validates:

- source-to-target logical row reconciliation for Steps 2-4
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
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_06_build_q_live_bridge_features.py --output-root-win D:/market-data/prepared/q_live_migration/step_06_bridge_features --feature-date 2026-06-09
```

Execute locally:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_06_build_q_live_bridge_features.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_06_bridge_features --feature-date 2026-06-09
```

Resume after a partial run locally:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\migration\step_06_build_q_live_bridge_features.py --execute --skip-non-empty-targets --output-root-win D:/market-data/prepared/q_live_migration/step_06_bridge_features --feature-date 2026-06-09
```

Execute on workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\migration\step_06_build_q_live_bridge_features.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_06_bridge_features --feature-date 2026-06-09
```

Step 6 builds:

- `id_sec_market_bridge_v1` from issuer CIK identifiers to active security/listing/symbol rows
- `sec_filing_document_v1` metadata rows from `sec_filing_v2.primary_document`
- `feature_tradable_universe_v1` for the requested feature date
- `feature_scanner_static_v1` for the requested feature date

This step intentionally does not invent SEC accepted timestamps or filing text. Those require SEC submissions/daily-feed artifacts and must be produced by the SEC bulk/feed parser before `feature_sec_event_market_bridge_v1` can be populated.
