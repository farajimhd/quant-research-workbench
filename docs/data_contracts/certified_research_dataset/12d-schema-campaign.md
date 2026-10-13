# Campaign plan, progress and seal schemas

[Schema overview](12-implemented-schemas.md) · [Design overview](../certified_research_dataset_design.md)

All tables use `MergeTree`, `live_market_ssd` and explicit lossless compression.

## research_campaign_unit_v1

**Order:** `dataset_id, unit_id`. **Partition:** `tuple()`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id` | `UUID` | `ZSTD(1)` |
| `unit_id` | `UInt64` | `T64,ZSTD(1)` |
| `unit_key` | `String` | `ZSTD(1)` |
| `product` | `LowCardinality(String)` | `ZSTD(1)` |
| `parents` | `Array(UInt64)` | `ZSTD(1)` |
| `arguments` | `String` | `ZSTD(1)` |
| `arguments_hash` | `FixedString(64)` | `ZSTD(1)` |
| `memory_bytes` | `UInt64` | `T64,ZSTD(1)` |

## research_campaign_state_v1

**Order:** `dataset_id, unit_id, sequence`. **Partition:** `tuple()`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id` | `UUID` | `ZSTD(1)` |
| `unit_id` | `UInt64` | `T64,ZSTD(1)` |
| `sequence` | `UInt64` | `Delta(8),ZSTD(1)` |
| `status` | `UInt8` | `ZSTD(1)` |
| `output_hash` | `FixedString(64)` | `ZSTD(1)` |
| `reason` | `LowCardinality(String)` | `ZSTD(1)` |
| `changed_at` | `DateTime64(6,'UTC')` | `ZSTD(1)` |

## research_campaign_seal_v1

**Order:** `dataset_id`. **Partition:** `tuple()`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id` | `UUID` | `ZSTD(1)` |
| `plan_hash` | `FixedString(64)` | `ZSTD(1)` |
| `source_hash` | `FixedString(64)` | `ZSTD(1)` |
| `root_hash` | `FixedString(64)` | `ZSTD(1)` |
| `units` | `UInt64` | `T64,ZSTD(1)` |
| `published_at` | `DateTime64(6,'UTC')` | `ZSTD(1)` |
