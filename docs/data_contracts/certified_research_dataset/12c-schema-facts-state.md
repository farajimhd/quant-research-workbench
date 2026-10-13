# Facts, checkpoint and receipt schemas

[Schema overview](12-implemented-schemas.md) · [Design overview](../certified_research_dataset_design.md)

All tables use `MergeTree`, `live_market_ssd` and lossless per-column compression. Schemas are under implementation; the full publication pipeline is not yet complete.

## research_facts_v1

**Order:** `dataset_id, entity_kind, entity_key, attempt_id, metric_key, unit_key, basis_unit_key, taxonomy_key, available_at, period_end, revision`. **Partition:** `toYYYYMM(available_at)`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id` | `UUID` | `ZSTD(1)` |
| `attempt_id` | `UUID` | `ZSTD(1)` |
| `entity_kind` | `UInt8` | `ZSTD(1)` |
| `entity_key` | `UInt64` | `T64,ZSTD(1)` |
| `metric_key` | `UInt32` | `T64,ZSTD(1)` |
| `period_start` | `Nullable(Date)` | `ZSTD(1)` |
| `period_end` | `Nullable(Date)` | `ZSTD(1)` |
| `context_kind` | `UInt8` | `ZSTD(1)` |
| `fiscal_year` | `UInt16` | `ZSTD(1)` |
| `fiscal_period` | `LowCardinality(String)` | `ZSTD(1)` |
| `taxonomy_key` | `UInt8` | `ZSTD(1)` |
| `available_at` | `DateTime64(6,'UTC')` | `Delta(8),ZSTD(1)` |
| `revision` | `UInt32` | `T64,ZSTD(1)` |
| `value` | `Nullable(Float64)` | `ZSTD(1)` |
| `unit_key` | `UInt16` | `ZSTD(1)` |
| `basis_unit_key` | `UInt16` | `ZSTD(1)` |
| `status` | `UInt8` | `ZSTD(1)` |
| `precision` | `UInt8` | `ZSTD(1)` |
| `confidence` | `UInt8` | `ZSTD(1)` |
| `reason` | `LowCardinality(String)` | `ZSTD(1)` |
| `evidence_hash` | `FixedString(64)` | `ZSTD(1)` |

## research_short_pressure_v1

**Order:** `dataset_id, entity_key, attempt_id, available_at, window_sessions`. **Partition:** `toYYYYMM(available_at)`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id`, `attempt_id` | `UUID` | `ZSTD(1)` |
| `entity_kind` | `UInt8` | `ZSTD(1)` |
| `entity_key` | `UInt64` | `T64,ZSTD(1)` |
| `available_at` | `DateTime64(6,'UTC')` | `Delta(8),ZSTD(1)` |
| `window_sessions`, `observed_sessions` | `UInt16` | `T64,ZSTD(1)` |
| `window_end` | `Nullable(Date)` | `ZSTD(1)` |
| `ratio` | `Nullable(Float64)` | `ZSTD(1)` |
| `context_ready` | `UInt8` | `ZSTD(1)` |
| `reason` | `LowCardinality(String)` | `ZSTD(1)` |

This is CNMS short-volume pressure, not outstanding short interest or exchange-wide short activity. The producer publishes a snapshot at the range start and every original public input clock; corrections affect subsequent snapshots only. Downstream consumers select the latest certified snapshot whose `available_at` is at or before their decision time, and inspect `window_end` for freshness. Missing/invalid measurement sessions yield a null ratio. Complete zero-volume windows also yield null, with a denominator reason. The latest available measurement anchors the window; publication delays do not manufacture newer measurement dates.

Execution dependency: certified `short_volume` facts → `short_pressure`. Each bounded security/range uses one vectorized ClickHouse projection, an identically compressed private staging table, independent output fingerprints and the shared reference receipt/resume protocol. Window selection is part of the parent recipe. Neither original public times nor absent coverage are inferred. The full campaign launcher and historical source-clock acquisition remain unfinished; this producer is tested independently.

## research_metric_v1

**Order:** `catalog_hash, metric_key`. **Partition:** `tuple()`.

| Column | Type | Codec |
| --- | --- | --- |
| `catalog_hash` | `FixedString(64)` | `ZSTD(1)` |
| `metric_key` | `UInt32` | `ZSTD(1)` |
| `name` | `LowCardinality(String)` | `ZSTD(1)` |
| `unit_key` | `UInt16` | `ZSTD(1)` |
| `formula` | `String` | `ZSTD(1)` |

## research_unit_v1

**Order:** `unit_key`. **Partition:** `tuple()`.

| Column | Type | Codec |
| --- | --- | --- |
| `unit_key` | `UInt16` | `ZSTD(1)` |
| `symbol` | `LowCardinality(String)` | `ZSTD(1)` |
| `kind` | `UInt8` | `ZSTD(1)` |

## research_taxonomy_v1

**Order:** `taxonomy_key`. **Partition:** `tuple()`.

| Column | Type | Codec |
| --- | --- | --- |
| `taxonomy_key` | `UInt8` | `ZSTD(1)` |
| `name` | `LowCardinality(String)` | `ZSTD(1)` |

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

## research_reference_receipt_v1

**Order:** `dataset_id, entity_kind, entity_key, family, range_start, range_end, attempt_id`. **Partition:** `toYYYYMM(range_end)`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id` | `UUID` | `ZSTD(1)` |
| `entity_kind` | `UInt8` | `ZSTD(1)` |
| `entity_key` | `UInt64` | `T64,ZSTD(1)` |
| `family` | `LowCardinality(String)` | `ZSTD(1)` |
| `range_start` | `Date` | `ZSTD(1)` |
| `range_end` | `Date` | `ZSTD(1)` |
| `attempt_id` | `UUID` | `ZSTD(1)` |
| `status` | `UInt8` | `ZSTD(1)` |
| `output_rows` | `UInt64` | `T64,ZSTD(1)` |
| `output_hash` | `FixedString(64)` | `ZSTD(1)` |
| `parent_hash` | `FixedString(64)` | `ZSTD(1)` |
| `catalog_hash` | `FixedString(64)` | `ZSTD(1)` |
| `published_at` | `DateTime64(6,'UTC')` | `ZSTD(1)` |
