# Identity, membership and provenance schemas

[Schema overview](12-implemented-schemas.md) · [Design overview](../certified_research_dataset_design.md)

All tables use `MergeTree`, `live_market_ssd` and lossless per-column compression. Schemas are under implementation; the full publication pipeline is not yet complete.

## research_dataset_v1

**Order:** `dataset_id`. **Partition:** `tuple()`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id` | `UUID` | `ZSTD(1)` |
| `contract_hash` | `FixedString(64)` | `ZSTD(1)` |
| `schema_hash` | `FixedString(64)` | `ZSTD(1)` |
| `start_date` | `Date` | `ZSTD(1)` |
| `end_date` | `Date` | `ZSTD(1)` |
| `definition` | `String` | `ZSTD(1)` |
| `created_at` | `DateTime64(6,'UTC')` | `ZSTD(1)` |

## research_identity_v1

**Order:** `entity_kind, entity_key`. **Partition:** `tuple()`.

| Column | Type | Codec |
| --- | --- | --- |
| `entity_kind` | `UInt8` | `ZSTD(1)` |
| `entity_key` | `UInt64` | `T64,ZSTD(1)` |
| `source_id` | `String` | `ZSTD(1)` |

## research_provenance_v1

**Order:** `evidence_hash`. **Partition:** `tuple()`.

| Column | Type | Codec |
| --- | --- | --- |
| `evidence_hash` | `FixedString(64)` | `ZSTD(1)` |
| `source_system` | `LowCardinality(String)` | `ZSTD(1)` |
| `source_record` | `String` | `ZSTD(1)` |
| `source_hash` | `FixedString(64)` | `ZSTD(1)` |
| `proof` | `String` | `ZSTD(1)` |

## research_membership_v1

**Order:** `dataset_id, listing_key, valid_from`. **Partition:** `toYear(valid_from)`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id` | `UUID` | `ZSTD(1)` |
| `listing_key` | `UInt64` | `T64,ZSTD(1)` |
| `security_key` | `UInt64` | `T64,ZSTD(1)` |
| `issuer_key` | `UInt64` | `T64,ZSTD(1)` |
| `ticker` | `LowCardinality(String)` | `ZSTD(1)` |
| `conid` | `UInt64` | `T64,ZSTD(1)` |
| `valid_from` | `DateTime64(6,'UTC')` | `Delta(8),ZSTD(1)` |
| `valid_to` | `DateTime64(6,'UTC')` | `Delta(8),ZSTD(1)` |
| `eligible` | `UInt8` | `ZSTD(1)` |
| `reason` | `LowCardinality(String)` | `ZSTD(1)` |
| `evidence_hash` | `FixedString(64)` | `ZSTD(1)` |
