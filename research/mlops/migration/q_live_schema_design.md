# q_live Target Schema Design

This document is phase 2 of the migration from `trading_dashboard_dev` to `q_live`.

Source audit used for this design:

```text
D:\market-data\prepared\q_live_migration\schema_audit\20260609_132352
```

The audit found 33 source tables, 704 columns, 174.2M SEC rows, 34.0M market rows, and 7 source tables using the `hdd` storage policy. The new `q_live` schema should not copy the source database mechanically. It should preserve useful data, fix identity relationships, add missing event timestamps, and make future sync jobs reliable.

## Design Goals

1. Use `q_live` as the trusted runtime/publication database.
2. Treat `trading_dashboard_dev` as a seed/source database only.
3. Use `CLICKHOUSE_LIVE_STORAGE_POLICY` for all migrated publication tables.
4. Separate canonical entities from provider-specific identities.
5. Make SEC CIK filings joinable to tradable market listings and symbols.
6. Preserve source provenance so every migrated row can be traced and refreshed.
7. Keep large text/fact tables separate from small metadata tables.
8. Support idempotent migration, gap fill, and future live sync jobs.

## Main Problems In The Source Schema

### Provider Identity Is Mixed With Canonical Identity

The source database has SEC issuer ids such as:

```text
issuer:cik:0000320193
```

and market/security issuer ids such as:

```text
issuer:ibkr_public:conid:...
```

Those are provider identities, not durable canonical issuer identities. In `q_live`, provider identities must live in mapping tables. Canonical issuer/security/listing/symbol rows should be independent of any one provider.

### SEC Filing Time Is Not Precise Enough

`trading_dashboard_dev.sec_filing_v1` has `filing_date` but not exact SEC acceptance time. For market reaction labels we need:

```text
accepted_at_utc DateTime64(9, 'UTC')
acceptance_datetime_raw String
accepted_at_source String
```

The target SEC filing table must include these fields. Bulk submissions data should be the preferred source; SEC header parsing is fallback.

### Existing Source Sync Is Not Trusted

The old app became too complex to trust for ongoing DB sync. `q_live` must include source run, artifact, watermark, and validation tables so each future updater is small and independently auditable.

### HDD Tables Need Publication Copies

The audit found these large source tables on `hdd`:

- `market_financial_statement_snapshot_v1`
- `market_short_volume_v1`
- `market_short_interest_v1`
- `market_cash_dividend_v1`
- `market_ipo_v1`
- `market_security_float_v1`
- `market_stock_split_v1`

If these are needed by live trading, scanner setup, or model feature generation, they should be recreated in `q_live` using `CLICKHOUSE_LIVE_STORAGE_POLICY`.

## Target Table Groups

### 1. Source Control And Sync

These tables are required before any migration job writes data.

| Table | Purpose |
| --- | --- |
| `source_run_v1` | One row per migration/sync/gap-fill run. |
| `source_artifact_v1` | Raw file/API artifact inventory with hash, size, and status. |
| `sync_watermark_v1` | Per source job high-water marks. |
| `sync_validation_v1` | Validation checks, row counts, mismatch counts, and errors. |

These tables make sync jobs idempotent and inspectable.

### 2. Reference Dimensions

Small dimensions used by multiple domains:

| Table | Source Basis |
| --- | --- |
| `ref_country_v1` | `market_country_v1` |
| `ref_asset_class_v1` | `market_asset_class_v1` |
| `ref_exchange_v1` | `market_exchange_v1` |
| `ref_exchange_currency_v1` | `market_exchange_currency_v1` |
| `ref_ticker_type_v1` | `market_ticker_type_v1` |

These tables should stay small and frequently cacheable by the app.

### 3. Canonical Identity Graph

This is the most important correction.

| Table | Purpose |
| --- | --- |
| `id_issuer_v1` | Canonical issuer/company/person/fund identity. |
| `id_issuer_identifier_v1` | CIK, EIN, LEI, provider issuer ids, and other issuer-level identifiers. |
| `id_security_v1` | Canonical tradable/security instrument identity. |
| `id_security_identifier_v1` | ISIN, CUSIP, FIGI, conid, provider security identifiers. |
| `id_listing_v1` | Security listed on an exchange/currency. Includes IBKR conid when listing-specific. |
| `id_symbol_v1` | Provider ticker/symbol attached to a listing. |
| `id_source_mapping_v1` | Source-to-canonical mapping evidence and confidence. |
| `id_mapping_issue_v1` | Unresolved/ambiguous mapping issues. |
| `id_sec_market_bridge_v1` | Explicit bridge from SEC CIK/accession context to tradable listing/security/symbol. |

The bridge table is intentional. CIK-to-ticker mapping can be ambiguous over time, and one issuer can have multiple securities/listings/classes. We should not hide that complexity in a simple join.

### 4. Market Reference Publications

These tables feed scanner setup, live trading metadata, and model features.

| Table | Source Basis | Notes |
| --- | --- | --- |
| `market_security_classification_v1` | source table | Keep security-level classification. |
| `market_security_market_snapshot_v1` | source table | Latest shares/market-cap metadata, not live quotes. |
| `market_security_float_v1` | source table | Use SSD policy. |
| `market_short_interest_v1` | source table | Use SSD policy. |
| `market_short_volume_v1` | source table | Use SSD policy. |
| `market_stock_split_v1` | source table | Use SSD policy. |
| `market_cash_dividend_v1` | source table | Use SSD policy. |
| `market_ipo_v1` | source table | Use SSD policy. |
| `market_presentation_asset_v1` | source table | Logo and asset metadata only; binary files stay in artifact storage. |
| `massive_flatfile_source_file_v1` | source table | Raw SIP source-file inventory. |

These are publication tables. They should not contain raw provider payloads unless the payload is needed for reproducibility.

`market_financial_statement_snapshot_v1` is not part of step 3. It is deferred to the fundamentals/feature migration because it overlaps with SEC/XBRL-derived snapshots and should be reconciled before publication.

### 5. SEC Publications

SEC should be linked to canonical identities but still keep its own source truth.

| Table | Purpose |
| --- | --- |
| `sec_filing_v2` | Filing metadata with exact acceptance timestamp and accession URLs. |
| `sec_filing_document_v1` | One row per filing document. |
| `sec_filing_text_v1` | Extracted text, separated from document metadata. |
| `sec_xbrl_concept_v1` | Concept metadata from company facts. |
| `sec_xbrl_company_fact_v1` | Company facts by CIK/accession/tag/unit/period. |
| `sec_xbrl_frame_v1` | Frame metadata. |
| `sec_xbrl_frame_observation_v1` | Frame observations. |
| `sec_fundamental_snapshot_v1` | Derived features for runtime/model use. Rebuildable. |

`sec_filing_v2` replaces the source `sec_filing_v1` shape. Existing source rows can seed it, but exact accepted time must be backfilled from submissions bulk or SEC API/header fallback.

### 6. Derived Feature Tables

These are not direct copies from `trading_dashboard_dev`; they are optimized publications for app/model consumers.

| Table | Purpose |
| --- | --- |
| `feature_tradable_universe_v1` | Daily tradable universe with listing/security/symbol/conid and metadata. |
| `feature_scanner_static_v1` | Scanner setup fields such as float bucket, short pressure label, sector/classification. |
| `feature_fundamental_snapshot_v1` | Compact financial/fundamental features per issuer/security/date. |
| `feature_sec_event_market_bridge_v1` | SEC filing events joined to affected tradable listings for label generation. |

These should be rebuilt from canonical/publication tables. They are allowed to change as feature engineering improves.

## Required Relationships

### Canonical Market Graph

```text
id_issuer_v1
  -> id_security_v1
      -> id_listing_v1
          -> id_symbol_v1
```

### Provider Mapping

```text
id_source_mapping_v1
  source_system + source_entity_kind + source_entity_key
  -> mapped_entity_kind + mapped_entity_id
```

Examples:

```text
sec:cik:0000320193 -> issuer_id
ibkr:conid:265598 -> listing_id or security_id
massive:ticker:AAPL -> symbol_id/listing_id
```

### SEC To Market Bridge

```text
sec_filing_v2.cik/accession_number
  -> id_sec_market_bridge_v1
      -> issuer_id/security_id/listing_id/symbol_id
```

The bridge must include:

- confidence score
- mapping method
- validity interval
- ambiguity status
- evidence JSON

This avoids pretending that every CIK has exactly one tradable ticker.

## Migration Order

1. Create source control tables.
2. Migrate small reference dimensions.
3. Build canonical identity tables from issuer/security/listing/symbol sources.
4. Migrate provider identifier and source mapping tables.
5. Build SEC CIK-to-issuer mappings.
6. Build Massive ticker-to-symbol/listing mappings.
7. Build IBKR conid-to-listing/security mappings.
8. Build `id_sec_market_bridge_v1`.
9. Migrate market reference publications.
10. Migrate SEC filing and XBRL publications.
11. Backfill exact SEC acceptance timestamps.
12. Download selected accession text and populate document/text tables.
13. Build derived feature tables.
14. Run validation coverage and row-reconciliation checks.

## Validation Required Before Migration

For each target table:

- source row count
- target row count
- expected dedupe count
- duplicate target primary key count
- null/empty critical key count
- source hash coverage
- latest inserted timestamp
- storage policy check

For identity bridge tables:

- CIK-to-issuer coverage
- ticker-to-symbol coverage
- IBKR conid-to-listing coverage
- SEC filing accessions mapped to at least one tradable listing
- ambiguous CIK/ticker mappings retained, not dropped

For SEC:

- filings with missing `accepted_at_utc`
- accepted time source distribution
- source filing date vs accepted date distribution
- filing text coverage for selected forms/accessions

## Implementation Boundary

This phase is design only. It should not write to ClickHouse.

The next phase should implement:

1. `step_01_create_q_live_schema.py`
2. `step_02_migrate_reference_identity.py`
3. `step_03_migrate_market_publications.py`
4. `step_04_migrate_sec_publications.py`
5. `step_05_validate_q_live_migration.py`

Each migrator should be idempotent and write rows to `source_run_v1` and `sync_validation_v1`.
