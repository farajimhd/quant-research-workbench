# Market data schemas

[Schema overview](12-implemented-schemas.md) · [Design overview](../certified_research_dataset_design.md)

All tables use `MergeTree`, `live_market_ssd` and lossless per-column compression. Schemas are under implementation; the full publication pipeline is not yet complete.

## research_bars_v1

**Order:** `dataset_id, listing_key, session_date, attempt_id, resolution_ms, bucket_index`. **Partition:** `toYYYYMM(session_date)`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id` | `UUID` | `ZSTD(1)` |
| `session_date` | `Date` | `ZSTD(1)` |
| `listing_key` | `UInt64` | `T64,ZSTD(1)` |
| `attempt_id` | `UUID` | `ZSTD(1)` |
| `resolution_ms` | `UInt32` | `T64,ZSTD(1)` |
| `bucket_index` | `UInt32` | `Delta(4),ZSTD(1)` |
| `open_int` | `UInt64` | `T64,ZSTD(1)` |
| `high_int` | `UInt64` | `T64,ZSTD(1)` |
| `low_int` | `UInt64` | `T64,ZSTD(1)` |
| `close_int` | `UInt64` | `T64,ZSTD(1)` |
| `volume` | `Float64` | `ZSTD(1)` |
| `trade_count` | `UInt64` | `T64,ZSTD(1)` |
| `notional` | `Float64` | `ZSTD(1)` |
| `execution_volume` | `Float64` | `ZSTD(1)` |
| `execution_notional` | `Float64` | `ZSTD(1)` |
| `price_valid` | `UInt8` | `ZSTD(1)` |
| `extremes_valid` | `UInt8` | `ZSTD(1)` |

## research_indicators_v1

**Order:** `dataset_id, listing_key, session_date, attempt_id, resolution_ms, bucket_index`. **Partition:** `toYYYYMM(session_date)`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id` | `UUID` | `ZSTD(1)` |
| `session_date` | `Date` | `ZSTD(1)` |
| `listing_key` | `UInt64` | `T64,ZSTD(1)` |
| `attempt_id` | `UUID` | `ZSTD(1)` |
| `resolution_ms` | `UInt32` | `T64,ZSTD(1)` |
| `bucket_index` | `UInt32` | `Delta(4),ZSTD(1)` |
| `ema_7` | `Float64` | `ZSTD(1)` |
| `ema_9` | `Float64` | `ZSTD(1)` |
| `ema_12` | `Float64` | `ZSTD(1)` |
| `ema_15` | `Float64` | `ZSTD(1)` |
| `ema_20` | `Float64` | `ZSTD(1)` |
| `ema_26` | `Float64` | `ZSTD(1)` |
| `ema_50` | `Float64` | `ZSTD(1)` |
| `macd_line` | `Float64` | `ZSTD(1)` |
| `macd_signal` | `Float64` | `ZSTD(1)` |
| `macd_histogram` | `Float64` | `ZSTD(1)` |
| `rsi_14` | `Float64` | `ZSTD(1)` |
| `atr_14` | `Float64` | `ZSTD(1)` |
| `previous_close` | `Float64` | `ZSTD(1)` |
| `avg_gain` | `Float64` | `ZSTD(1)` |
| `avg_loss` | `Float64` | `ZSTD(1)` |
| `rsi_ready` | `UInt8` | `ZSTD(1)` |
| `atr_ready` | `UInt8` | `ZSTD(1)` |
| `sample_count` | `UInt64` | `T64,ZSTD(1)` |

## research_liquidity_v1

**Order:** `dataset_id, listing_key, session_date, attempt_id, resolution_ms, bucket_index`. **Partition:** `toYYYYMM(session_date)`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id` | `UUID` | `ZSTD(1)` |
| `session_date` | `Date` | `ZSTD(1)` |
| `listing_key` | `UInt64` | `T64,ZSTD(1)` |
| `attempt_id` | `UUID` | `ZSTD(1)` |
| `resolution_ms` | `UInt32` | `T64,ZSTD(1)` |
| `bucket_index` | `UInt32` | `Delta(4),ZSTD(1)` |
| `open_int` | `UInt64` | `T64,ZSTD(1)` |
| `high_int` | `UInt64` | `T64,ZSTD(1)` |
| `low_int` | `UInt64` | `T64,ZSTD(1)` |
| `close_int` | `UInt64` | `T64,ZSTD(1)` |
| `volume` | `Float64` | `ZSTD(1)` |
| `trade_count` | `UInt64` | `T64,ZSTD(1)` |
| `notional` | `Float64` | `ZSTD(1)` |
| `execution_volume` | `Float64` | `ZSTD(1)` |
| `execution_notional` | `Float64` | `ZSTD(1)` |
| `first_event_us` | `UInt64` | `Delta(8),ZSTD(1)` |
| `last_event_us` | `UInt64` | `Delta(8),ZSTD(1)` |
| `quote_timestamp_us` | `UInt64` | `Delta(8),ZSTD(1)` |
| `event_count` | `UInt64` | `T64,ZSTD(1)` |
| `source_trade_count` | `UInt64` | `T64,ZSTD(1)` |
| `quote_event_count` | `UInt64` | `T64,ZSTD(1)` |
| `reporting_delayed_trades` | `UInt64` | `T64,ZSTD(1)` |
| `reporting_unknown_trades` | `UInt64` | `T64,ZSTD(1)` |
| `reporting_invalid_trades` | `UInt64` | `T64,ZSTD(1)` |
| `volume_ineligible_trades` | `UInt64` | `T64,ZSTD(1)` |
| `price_ineligible_trades` | `UInt64` | `T64,ZSTD(1)` |
| `execution_ineligible_trades` | `UInt64` | `T64,ZSTD(1)` |
| `pre_0405_trades` | `UInt64` | `T64,ZSTD(1)` |
| `pre_0405_volume_eligible_trades` | `UInt64` | `T64,ZSTD(1)` |
| `invalid_trade_values` | `UInt64` | `T64,ZSTD(1)` |
| `bid_int` | `UInt64` | `T64,ZSTD(1)` |
| `ask_int` | `UInt64` | `T64,ZSTD(1)` |
| `bid_size` | `Float64` | `ZSTD(1)` |
| `ask_size` | `Float64` | `ZSTD(1)` |
| `cumulative_volume` | `Float64` | `ZSTD(1)` |
| `cumulative_notional` | `Float64` | `ZSTD(1)` |
| `cumulative_execution_volume` | `Float64` | `ZSTD(1)` |
| `cumulative_execution_notional` | `Float64` | `ZSTD(1)` |
| `price_valid` | `UInt8` | `ZSTD(1)` |
| `extremes_valid` | `UInt8` | `ZSTD(1)` |
| `volume_valid` | `UInt8` | `ZSTD(1)` |
| `quote_valid` | `UInt8` | `ZSTD(1)` |

## research_activity_v1

**Order:** `dataset_id, session_date, listing_key, attempt_id, resolution_ms, window_seconds, bucket_index`. **Partition:** `toYYYYMM(session_date)`.

| Column | Type | Codec |
| --- | --- | --- |
| `dataset_id` | `UUID` | `ZSTD(1)` |
| `session_date` | `Date` | `ZSTD(1)` |
| `listing_key` | `UInt64` | `T64,ZSTD(1)` |
| `attempt_id` | `UUID` | `ZSTD(1)` |
| `resolution_ms` | `UInt32` | `T64,ZSTD(1)` |
| `bucket_index` | `UInt32` | `Delta(4),ZSTD(1)` |
| `window_seconds` | `UInt32` | `T64,ZSTD(1)` |
| `volume` | `Nullable(Float64)` | `ZSTD(1)` |
| `notional` | `Nullable(Float64)` | `ZSTD(1)` |
| `trade_count` | `Nullable(UInt64)` | `ZSTD(1)` |
| `execution_volume` | `Nullable(Float64)` | `ZSTD(1)` |
| `execution_notional` | `Nullable(Float64)` | `ZSTD(1)` |
| `context_ready` | `UInt8` | `ZSTD(1)` |

One row per observed 100ms bucket and configured elapsed-time window. Volumes, notionals and trade counts remain null while context is incomplete (`context_ready=0`). Missing exchange-session parents block computation. `research_stage_activity_v1` is a private compressed table with the same columns/order, used to calculate once before independent publication readback.
