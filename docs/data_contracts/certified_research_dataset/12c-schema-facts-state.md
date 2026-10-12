# Facts, checkpoint and receipt schemas

[Schema overview](12-implemented-schemas.md) · [Design overview](../certified_research_dataset_design.md)

All tables use `MergeTree`, `live_market_ssd` and lossless per-column compression. Schemas are under implementation; the full publication pipeline is not yet complete.

## research_facts_v1

**Order:** `dataset_id, entity_kind, entity_key, metric_key, available_at, period_end, revision`. **Partition:** `toYYYYMM(available_at)`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id` | `UUID` | `ZSTD(1)` |
| `entity_kind` | `UInt8` | `ZSTD(1)` |
| `entity_key` | `UInt64` | `T64,ZSTD(1)` |
| `metric_key` | `UInt32` | `T64,ZSTD(1)` |
| `period_start` | `Date` | `ZSTD(1)` |
| `period_end` | `Date` | `ZSTD(1)` |
| `available_at` | `DateTime64(6,'UTC')` | `Delta(8),ZSTD(1)` |
| `revision` | `UInt32` | `T64,ZSTD(1)` |
| `value` | `Nullable(Float64)` | `ZSTD(1)` |
| `unit_key` | `UInt16` | `ZSTD(1)` |
| `status` | `UInt8` | `ZSTD(1)` |
| `precision` | `UInt8` | `ZSTD(1)` |
| `confidence` | `UInt8` | `ZSTD(1)` |
| `reason` | `LowCardinality(String)` | `ZSTD(1)` |
| `evidence_hash` | `FixedString(64)` | `ZSTD(1)` |

## research_metric_v1

**Order:** `catalog_hash, metric_key`. **Partition:** `tuple()`.

| Column | Type | Codec |
| --- | --- | --- |
| `catalog_hash` | `FixedString(64)` | `ZSTD(1)` |
| `metric_key` | `UInt32` | `ZSTD(1)` |
| `name` | `LowCardinality(String)` | `ZSTD(1)` |
| `unit_key` | `UInt16` | `ZSTD(1)` |
| `formula` | `String` | `ZSTD(1)` |

## research_v7_checkpoint_v1

**Order:** `dataset_id, listing_key, session_date, boundary, attempt_id`. **Partition:** `toYYYYMM(session_date)`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id` | `UUID` | `ZSTD(1)` |
| `session_date` | `Date` | `ZSTD(1)` |
| `listing_key` | `UInt64` | `T64,ZSTD(1)` |
| `attempt_id` | `UUID` | `ZSTD(1)` |
| `boundary` | `UInt8` | `ZSTD(1)` |
| `boundary_us` | `UInt64` | `DoubleDelta,ZSTD(1)` |
| `last_input_us` | `UInt64` | `Delta(8),ZSTD(1)` |
| `parent_hash` | `FixedString(64)` | `ZSTD(1)` |
| `state_hash` | `FixedString(64)` | `ZSTD(1)` |
| `codec_version` | `UInt8` | `ZSTD(1)` |
| `payload` | `String` | `ZSTD(1)` |

## research_unit_receipt_v1

**Order:** `dataset_id, listing_key, session_date, product, attempt_id`. **Partition:** `toYYYYMM(session_date)`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id` | `UUID` | `ZSTD(1)` |
| `session_date` | `Date` | `ZSTD(1)` |
| `listing_key` | `UInt64` | `T64,ZSTD(1)` |
| `attempt_id` | `UUID` | `ZSTD(1)` |
| `product` | `LowCardinality(String)` | `ZSTD(1)` |
| `status` | `UInt8` | `ZSTD(1)` |
| `output_rows` | `UInt64` | `T64,ZSTD(1)` |
| `output_hash` | `FixedString(64)` | `ZSTD(1)` |
| `parent_hash` | `FixedString(64)` | `ZSTD(1)` |
| `reason` | `LowCardinality(String)` | `ZSTD(1)` |
| `published_at` | `DateTime64(6,'UTC')` | `ZSTD(1)` |
