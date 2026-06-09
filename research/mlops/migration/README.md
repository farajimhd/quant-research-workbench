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
