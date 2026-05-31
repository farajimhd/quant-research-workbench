# Compact Market Microstructure Representation

This document defines the compact event-level representation used for market
microstructure experiments. It is intended to be implementation-ready: field
names, bit widths, encoding formulas, reference-table usage, extraction order,
and validation rules are specified here.

## Naming

The atomic learning unit is an `EventsChunk`.

```text
EventsChunk
  header
  details
```

An `EventsChunk` is a fixed number of consecutive quote/trade events for one
ticker, sorted by market-event time. It is not a fixed wall-clock time bar. Time
is represented as encoded features inside the chunk.

Default parameters:

```text
events_per_chunk = 128
price_delta_bits = 9
size_bucket_bits = 8
time_bucket_bits = 10
anchor_bits = 20
spread_anchor_bits = 16
scale_bits = 16
```

`events_per_chunk` is a configuration value. The logical layout below works for
other values, but count-field bit widths must be derived from
`events_per_chunk`.

## Source Data

Use canonical quote and trade events, one ticker at a time.

Required quote columns:

```text
ticker
session_date
sip_timestamp
sequence_number
bid_price
ask_price
bid_size
ask_size
bid_exchange
ask_exchange
tape
conditions or condition fields
```

Required trade columns:

```text
ticker
session_date
sip_timestamp
sequence_number
price
size
exchange
tape
conditions or condition fields
correction
```

The encoder must not combine events from different tickers. Session boundaries
may be crossed only if the source stream is explicitly configured to carry
history across sessions. In all cases, the event order is ticker-local.

## Event Ordering

For each ticker, quotes and trades are merged into one event stream and sorted
by:

```text
sip_timestamp ASC
sequence_number ASC
event_type ASC
```

Event type mapping:

```text
quote = 0
trade = 1
```

If exact timestamp and sequence ties occur, quote events sort before trade
events because `event_type=0` precedes `event_type=1`.

## Reference Tables

Categorical fields use dense IDs from:

```text
research/market_references/massive/stock_exchanges.json
research/market_references/massive/stock_conditions.json
research/market_references/massive/stock_tapes.json
```

Current bit widths:

```text
exchange_dense_id: 5 bits
condition_dense_id: 7 bits
tape_dense_id: 3 bits
```

Rules:

- `dense_id=0` means missing, unknown, or not applicable.
- `dense_id_kind=actual` maps to a current provider row.
- `dense_id_kind=reserved_future` keeps capacity for future provider additions.
- New raw provider IDs not present in the current mapping encode as `0` until
  the reference table is refreshed and the raw value is assigned to a reserved
  slot.

## Tick Regime

Prices are converted to integer ticks.

```text
if ask_anchor_price >= 1.00:
    tick_size = 0.01
    tick_regime = 1
else:
    tick_size = 0.0001
    tick_regime = 0
```

The tick regime is stored in the header and applies to all price fields in that
chunk.

## Header

The header describes the whole `EventsChunk` and provides the information needed
to decode event-level deltas.

`count_bits` is derived from the configured event count:

```text
count_bits = ceil(log2(events_per_chunk + 1))
```

For the default `events_per_chunk=128`, `count_bits=8`.

| Field | Description | Bits |
|---|---|---:|
| `block_duration_us_bucket` | Time from first event to last event in the chunk | 10 |
| `age_to_origin_us_bucket` | Time from chunk end to prediction/origin time | 10 |
| `start_delta_us_bucket` | Time from previous chunk end to this chunk start | 10 |
| `quote_event_count` | Number of quote events in this chunk | `count_bits` |
| `trade_event_count` | Number of trade events in this chunk | `count_bits` |
| `has_quote_state` | Latest valid quote exists at or before chunk end | 1 |
| `has_trade_event` | At least one trade event exists in chunk | 1 |
| `tick_regime` | `1` for penny tick, `0` for sub-dollar tick | 1 |
| `ask_anchor_ticks` | Latest ask anchor in ticks | 20 |
| `spread_anchor_ticks` | Latest `(ask - bid)` anchor in ticks | 16 |
| `ask_delta_scale` | Scale used for quote ask deltas | 16 |
| `spread_delta_scale` | Scale used for quote spread deltas | 16 |
| `trade_delta_scale` | Scale used for trade price deltas | 16 |

Default header size:

```text
events_per_chunk = 128
count_bits = 8
header_bits = 133
```

Formula:

```text
header_bits = 30 + 2 * count_bits + 3 + anchor_bits + spread_anchor_bits + 3 * scale_bits
```

With defaults:

```text
30 + 16 + 3 + 20 + 16 + 48 = 133 bits
```

### Header Time Buckets

Use the same log bucket for all microsecond-duration header fields:

```text
bucket = round(log2(1 + duration_us) * 32)
bucket = clip(bucket, 0, 2^time_bucket_bits - 1)
```

With `time_bucket_bits=10`, bucket values are `0..1023`.

This intentionally saturates very long gaps. There is no maximum block-duration
filter in this representation; long illiquid intervals remain valid but their
large time gaps are represented by saturated/high time buckets.

### Header Anchors

Anchor quote:

```text
latest valid quote with sip_timestamp <= chunk_end_sip_timestamp
```

Validity:

```text
ask_price > 0
bid_price > 0
ask_price >= bid_price
```

Anchor fields:

```text
ask_anchor_ticks = round(anchor_ask_price / tick_size)
spread_anchor_ticks = round((anchor_ask_price - anchor_bid_price) / tick_size)
```

If no valid quote exists at or before chunk end, the chunk is invalid for this
price representation and should be skipped. Do not create trade-only chunks
without quote state.

Validation:

```text
ask_anchor_ticks < 2^anchor_bits
spread_anchor_ticks < 2^spread_anchor_bits
```

If either check fails, skip the chunk or increase the corresponding bit width in
the experiment config. Do not silently wrap values.

## Detail Rows

Each `EventsChunk` has exactly `events_per_chunk` unified event detail rows.

Default:

```text
events_per_chunk = 128
```

Each detail row has the same logical layout for quote and trade events.

| Field | Quote Meaning | Trade Meaning | Bits |
|---|---|---|---:|
| `event_type` | `0` | `1` | 1 |
| `event_presence` | Real event vs pad | Real event vs pad | 1 |
| `event_delta_us_bucket` | Time since previous event | Time since previous event | 10 |
| `price_1_delta_bucket` | Ask delta | Trade price delta | 9 |
| `price_2_delta_bucket` | Spread delta | `0` | 9 |
| `size_1_bucket` | Bid size bucket | Trade size bucket | 8 |
| `size_1_small_flag` | `0 < bid_size < 100` | `0 < trade_size < 100` | 1 |
| `size_2_bucket` | Ask size bucket | `0` | 8 |
| `size_2_small_flag` | `0 < ask_size < 100` | `0` | 1 |
| `exchange_1_dense_id` | Bid exchange | Trade exchange | 5 |
| `exchange_2_dense_id` | Ask exchange | `0` | 5 |
| `tape_dense_id` | Tape | Tape | 3 |
| `condition_1_dense_id` | Condition slot 1 | Condition slot 1 | 7 |
| `condition_2_dense_id` | Condition slot 2 | Condition slot 2 | 7 |
| `condition_3_dense_id` | Condition slot 3 | Condition slot 3 | 7 |
| `condition_4_dense_id` | Condition slot 4 | Condition slot 4 | 7 |
| `condition_mask` | Which condition slots are present | Which condition slots are present | 4 |
| `correction_code` | `0` | Trade correction code | 4 |

Detail row size:

```text
104 bits per event
```

Default chunk detail size:

```text
128 * 104 = 13,312 bits
```

Default total chunk size:

```text
header_bits + detail_bits = 133 + 13,312 = 13,445 bits
13,445 bits = 1,680.625 bytes
```

## Price Delta Encoding

Price deltas are signed integer buckets relative to the chunk header anchors.

Signed 9-bit range:

```text
min_bucket = -256
max_bucket = 255
```

Quote event:

```text
ask_ticks = round(ask_price / tick_size)
spread_ticks = round((ask_price - bid_price) / tick_size)

ask_delta_ticks = ask_ticks - ask_anchor_ticks
spread_delta_ticks = spread_ticks - spread_anchor_ticks
```

Trade event:

```text
trade_ticks = round(trade_price / tick_size)
trade_delta_ticks = trade_ticks - ask_anchor_ticks
```

Per chunk scales:

```text
positive_max = 2^(price_delta_bits - 1) - 1

ask_delta_scale = max(1, ceil(max(abs(ask_delta_ticks)) / positive_max))
spread_delta_scale = max(1, ceil(max(abs(spread_delta_ticks)) / positive_max))
trade_delta_scale = max(1, ceil(max(abs(trade_delta_ticks)) / positive_max))
```

If a chunk has no quote events, `ask_delta_scale=1` and `spread_delta_scale=1`.
If a chunk has no trade events, `trade_delta_scale=1`.

Bucketization:

```text
bucket = round(delta_ticks / scale)
bucket = clip(bucket, -256, 255)
```

Decoding:

```text
reconstructed_delta_ticks = bucket * scale
```

Validation:

```text
scale < 2^scale_bits
```

If a required scale does not fit in `scale_bits`, skip the chunk or increase
`scale_bits`. Do not silently wrap the scale.

## Size Encoding

Sizes are share quantities.

For each size field:

```text
size_units = size / 100
size_bucket = round(log2(1 + size_units) * 16)
size_bucket = clip(size_bucket, 0, 255)
small_size_flag = 1 if 0 < size < 100 else 0
```

Rationale:

- `size_bucket` captures large liquidity changes compactly.
- `small_size_flag` preserves the distinction between zero, sub-100-share, and
  100-share-or-larger quantities.
- This is not an odd-lot flag. Quote and trade sizes are encoded as share
  quantities.

## Event Time Encoding

For each event detail row:

```text
event_delta_us = current_event_sip_timestamp - previous_event_sip_timestamp
```

For the first event in a chunk:

```text
event_delta_us = 0
```

Bucket:

```text
event_delta_us_bucket = round(log2(1 + event_delta_us) * 32)
event_delta_us_bucket = clip(event_delta_us_bucket, 0, 2^time_bucket_bits - 1)
```

With `time_bucket_bits=10`, values are `0..1023`.

## Conditions

Each event gets four condition slots.

```text
condition_1_dense_id
condition_2_dense_id
condition_3_dense_id
condition_4_dense_id
condition_mask
```

Rules:

- Preserve provider condition order if the canonical event stores ordered
  condition arrays.
- If canonical data only stores individual condition columns, use that canonical
  order.
- Missing condition slots encode as `0`.
- `condition_mask` is a 4-bit mask. Bit `i` is `1` when condition slot `i` is
  present and non-zero.
- If more than four conditions exist, keep the first four in provider/canonical
  order and drop the rest. This must be counted in data-quality metrics, but the
  compact event row does not currently include a condition-overflow flag.

Quotes and trades both use all four slots.

## Correction Code

Correction is trade-only.

```text
0 = no correction, missing, or not applicable
1..14 = correction code
15 = unknown, reserved, or overflow
```

Quote rows always store:

```text
correction_code = 0
```

## Padding and Partial Chunks

Preferred training extraction drops incomplete tail chunks.

If an experiment keeps partial chunks, pad remaining rows with:

```text
event_presence = 0
all other detail fields = 0
```

Header counts must count only real events.

## Extraction Algorithm

For each ticker:

1. Load canonical quote events and canonical trade events.
2. Normalize categorical raw IDs to dense IDs using the market reference tables.
3. Normalize condition arrays to at most four dense condition IDs plus a 4-bit
   mask.
4. Merge quotes and trades into one stream.
5. Sort by `sip_timestamp`, `sequence_number`, `event_type`.
6. Split the sorted stream into `EventsChunk` records of `events_per_chunk`
   consecutive events. At this stage, no stride or overlap policy is part of the
   representation contract. Training code may later choose non-overlapping,
   overlapping, or sampled chunks, but that policy must be recorded separately
   from this encoding spec.

```text
start = chunk_start_event_index
end = start + events_per_chunk
```

7. Drop the chunk if it has fewer than `events_per_chunk` events, unless the
   experiment explicitly enables padding.
8. Find the latest valid quote at or before the chunk end.
9. If no valid quote state exists, skip the chunk.
10. Compute the header anchors, tick regime, counts, duration, and scales.
11. Encode each event into the unified 104-bit detail layout.
12. Validate that anchors and scales fit their configured bit widths.
13. Write the chunk plus metadata.

Required metadata per materialized dataset:

```text
events_per_chunk
price_delta_bits
size_bucket_bits
time_bucket_bits
anchor_bits
spread_anchor_bits
scale_bits
reference_table_versions or generated_at_utc values
source canonical root
date range
ticker universe
```

## Targets

Targets should use the same representation as inputs.

For masked reconstruction:

```text
target = original header/detail fields for masked parts
```

For next-event-chunk prediction:

```text
input = EventsChunk[k]
target = EventsChunk[k + horizon_chunks]
```

For rolling future-event prediction:

```text
input = EventsChunk ending at origin event index t
target = next EventsChunk or selected future event fields encoded with the same schema
```

Do not create higher-precision targets such as midpoint, bps return, or raw
float prices unless the experiment explicitly defines a separate target head.
The default target precision is the same compact representation used for input.

## Physical Storage

This document defines the logical bit representation.

Implementations may store the data as:

```text
packed bits
uint8/uint16 integer columns
tensor arrays with one integer per field
binary {0,1} tensors for bit-level models
```

The storage format must preserve the logical values exactly. If data is stored
unpacked for speed, the dataset manifest must still record the logical bit
widths from this document.

## Default Bit Summary

With `events_per_chunk=128`:

```text
header:      133 bits
event row:   104 bits
details:  13,312 bits
total:    13,445 bits
bytes:     1,680.625
```

With `events_per_chunk=256` and the same field widths:

```text
count_bits = 9
header:      135 bits
details:  26,624 bits
total:    26,759 bits
bytes:     3,344.875
```
