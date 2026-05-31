# Compact Market Microstructure Representation

This document defines the compact byte-level representation used for market
microstructure experiments. It is intended to be implementation-ready: field
names, byte layout, bit widths, reference-table usage, extraction order, and
validation rules are specified here.

## Learning Unit

The atomic learning unit is an `EventsChunk`.

```text
EventsChunk
  header
  details
```

An `EventsChunk` is a fixed number of consecutive quote/trade events for one
ticker, sorted by market-event time. It is not a fixed wall-clock bar. Time is
represented as encoded features inside the chunk.

Default parameters:

```text
events_per_chunk = 128
event_bytes = 16
header_bytes = 14
price_delta_dtype = signed int16
size_bucket_bits = 8
time_bucket_bits = 10
anchor_bits = 20
spread_anchor_bits = 16
```

`events_per_chunk` is a configuration value. The default model representation
uses `128` events. If `events_per_chunk > 255`, count fields must be widened
from `uint8` to `uint16` and the header layout must be versioned.

## Why Semantic Bytes

The most compact logical bitstream for the previous 128-event design was about
`1.68 KB`, but it was not aligned to meaningful byte tokens. For neural
training, especially masked byte modeling, byte alignment matters:

- A model can consume `uint8` byte tokens through a 256-entry embedding table.
- BCE reconstruction can still predict the underlying bits of masked bytes.
- The GPU does not need to expand every bit to `float16`, which would waste
  memory.
- A byte should represent a coherent field or tightly related fields, not a
  random mix of unrelated bits.
- Storage and data transfer stay compact because each byte remains one byte
  until it reaches the model embedding/loss path.

Therefore the physical model input is a semantic byte stream:

```text
EventsChunk bytes = 14 header bytes + 128 * 16 event bytes
                  = 2,062 bytes
```

This is larger than the theoretically packed bitstream, but much more useful as
a model tokenization. Each byte token has stable semantics.

All multi-byte numeric fields use little-endian byte order. Reserved bits must
be written as `0`.

## Semantic Packing

Semantic packing means bytes are allocated by field meaning first, then by bit
compactness. This intentionally leaves some reserved bits where doing so keeps a
byte interpretable and stable across versions.

The packing rules are:

- Keep multi-byte numeric values byte-aligned, for example signed `int16` price
  deltas and `uint16` spread anchors.
- Pack only closely related boolean or small categorical fields into the same
  byte, for example event type, presence, and correction code.
- Keep every condition slot in its own byte so the condition ID and slot
  presence bit travel together.
- Keep quote/trade event rows identical in length and layout, even when a trade
  does not use `price_2_delta_ticks`, `size_2_bucket`, or `exchange_2_dense_id`.
- Use reserved bits for forward compatibility. Reserved bits must be zero in
  generated data and ignored by current models.

The default semantic groups are:

| Group | Bytes | Purpose |
|---|---:|---|
| Header price anchors | `H0-H4` | Decode local price deltas without storing full raw prices per event |
| Header timing/counts | `H5-H12` | Decode chunk duration, origin age, start gap, and quote/trade counts |
| Header flags | `H13` | Chunk-level quote/trade/tick-regime state |
| Event flags/time | `E0-E2` | Event type, presence, correction, and event-to-event time gap |
| Event prices | `E3-E6` | Two signed `int16` local tick deltas |
| Event sizes | `E7-E9` | Size buckets, small-size flags, and tape |
| Event venues | `E10-E11` | Exchange dense IDs |
| Event conditions | `E12-E15` | Four condition slots, each with dense ID plus presence bit |

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

The encoder must not combine events from different tickers. The event stream is
ticker-local.

## Event Ordering

For each ticker, quotes and trades are merged into one stream and sorted by:

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
to decode event-level price deltas.

Default header size:

```text
14 bytes = 112 bits
```

### Header Byte Layout

| Byte(s) | Field | Encoding |
|---:|---|---|
| `H0-H2` | `ask_anchor_ticks` | 20-bit unsigned integer, lower 20 bits used, upper 4 bits reserved |
| `H3-H4` | `spread_anchor_ticks` | `uint16` |
| `H5-H6` | `block_duration_us_bucket` | 10-bit unsigned bucket, upper 6 bits reserved |
| `H7-H8` | `age_to_origin_us_bucket` | 10-bit unsigned bucket, upper 6 bits reserved |
| `H9-H10` | `start_delta_us_bucket` | 10-bit unsigned bucket, upper 6 bits reserved |
| `H11` | `quote_event_count` | `uint8`, number of quote events in the chunk |
| `H12` | `trade_event_count` | `uint8`, number of trade events in the chunk |
| `H13` | flags | bit-packed flags |

`H13` flags:

| Bit | Field |
|---:|---|
| 0 | `has_quote_state` |
| 1 | `has_trade_event` |
| 2 | `tick_regime` |
| 3-7 | reserved, must be `0` |

For the default `events_per_chunk=128`, both count fields fit in `uint8`.

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

Header time fields:

```text
block_duration_us_bucket = time from first event to last event in the chunk
age_to_origin_us_bucket = time from chunk end to prediction/origin time
start_delta_us_bucket = time from previous chunk end to this chunk start
```

For standalone reconstruction pretraining where no prediction origin exists,
`age_to_origin_us_bucket` should be `0`.

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
ask_anchor_ticks < 2^20
spread_anchor_ticks < 2^16
```

If either check fails, skip the chunk or explicitly create a new version with
wider anchor fields. Do not silently wrap values.

## Event Details

Each `EventsChunk` has exactly `events_per_chunk` unified event detail rows.

Default:

```text
events_per_chunk = 128
event_bytes = 16
```

Each event row has the same 16-byte layout for quote and trade events.

### Event Byte Layout

| Byte(s) | Field | Quote Meaning | Trade Meaning |
|---:|---|---|---|
| `E0` | flags/correction | type/presence/correction | type/presence/correction |
| `E1-E2` | `event_delta_us_bucket` | time since previous event | time since previous event |
| `E3-E4` | `price_1_delta_ticks` | ask delta ticks | trade price delta ticks |
| `E5-E6` | `price_2_delta_ticks` | spread delta ticks | `0` |
| `E7` | `size_1_bucket` | bid size bucket | trade size bucket |
| `E8` | `size_2_bucket` | ask size bucket | `0` |
| `E9` | size flags + tape | size flags + tape | size flags + tape |
| `E10` | `exchange_1_dense_id` | bid exchange | trade exchange |
| `E11` | `exchange_2_dense_id` | ask exchange | `0` |
| `E12` | condition slot 1 | condition 1 + mask bit 1 | condition 1 + mask bit 1 |
| `E13` | condition slot 2 | condition 2 + mask bit 2 | condition 2 + mask bit 2 |
| `E14` | condition slot 3 | condition 3 + mask bit 3 | condition 3 + mask bit 3 |
| `E15` | condition slot 4 | condition 4 + mask bit 4 | condition 4 + mask bit 4 |

`E0` flags:

| Bit(s) | Field |
|---:|---|
| 0 | `event_type`, `0=quote`, `1=trade` |
| 1 | `event_presence`, `1=real event`, `0=padding` |
| 2-5 | `correction_code`, trade-only; quote rows use `0` |
| 6-7 | reserved, must be `0` |

`E1-E2`:

```text
event_delta_us_bucket: lower 10 bits
upper 6 bits: reserved, must be 0
```

`E3-E4` and `E5-E6`:

```text
signed int16, little-endian, two's complement
```

`E9`:

| Bit(s) | Field |
|---:|---|
| 0 | `size_1_small_flag` |
| 1 | `size_2_small_flag` |
| 2-4 | `tape_dense_id` |
| 5-7 | reserved, must be `0` |

`E10` and `E11`:

```text
lower 5 bits: exchange_dense_id
upper 3 bits: reserved, must be 0
```

`E12-E15`:

```text
lower 7 bits: condition_dense_id
bit 7: condition slot presence mask bit
```

### Quote Mapping

For a quote event:

```text
event_type = 0
event_presence = 1
correction_code = 0
price_1_delta_ticks = ask_delta_ticks
price_2_delta_ticks = spread_delta_ticks
size_1_bucket = bid_size_bucket
size_1_small_flag = bid_small_size_flag
size_2_bucket = ask_size_bucket
size_2_small_flag = ask_small_size_flag
exchange_1_dense_id = bid_exchange_dense_id
exchange_2_dense_id = ask_exchange_dense_id
tape_dense_id = tape_dense_id
condition slots = up to 4 quote conditions
```

### Trade Mapping

For a trade event:

```text
event_type = 1
event_presence = 1
correction_code = encoded trade correction
price_1_delta_ticks = trade_delta_ticks
price_2_delta_ticks = 0
size_1_bucket = trade_size_bucket
size_1_small_flag = trade_small_size_flag
size_2_bucket = 0
size_2_small_flag = 0
exchange_1_dense_id = trade_exchange_dense_id
exchange_2_dense_id = 0
tape_dense_id = tape_dense_id
condition slots = up to 4 trade conditions
```

## Price Delta Encoding

Price deltas are signed integer tick deltas relative to the chunk header
anchors. They are stored directly as signed `int16`; there is no per-chunk price
scale field.

Signed `int16` range:

```text
min_delta = -32768 ticks
max_delta = 32767 ticks
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

Validation:

```text
-32768 <= ask_delta_ticks <= 32767
-32768 <= spread_delta_ticks <= 32767
-32768 <= trade_delta_ticks <= 32767
```

If a delta does not fit in signed `int16`, skip the chunk or create a new
version with wider price deltas. Do not silently clip. The reason for keeping
anchors even with `int16` deltas is that raw 16-bit price ticks cannot safely
cover all market prices, while local anchored deltas cover the expected
microstructure range and preserve scale information through the header anchor.

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

Rules:

- Preserve provider condition order if the canonical event stores ordered
  condition arrays.
- If canonical data only stores individual condition columns, use that canonical
  order.
- Missing condition slots encode as `0`.
- Each condition byte stores the 7-bit dense condition ID plus its own presence
  bit.
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
3. Normalize condition arrays to at most four dense condition IDs with per-slot
   presence bits.
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
10. Compute the header anchors, tick regime, counts, and duration buckets.
11. Encode each event into the unified 16-byte detail layout.
12. Validate that anchors and signed price deltas fit their configured widths.
13. Write the chunk plus metadata.

Required metadata per materialized dataset:

```text
events_per_chunk
event_bytes
header_bytes
price_delta_dtype
size_bucket_bits
time_bucket_bits
anchor_bits
spread_anchor_bits
reference_table_versions or generated_at_utc values
source canonical root
date range
ticker universe
```

## Targets

Targets should use the same representation as inputs.

For masked reconstruction:

```text
target = original header/detail bytes for masked parts
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

## Model Input Contract

The preferred byte-model input is:

```text
uint8 tensor shape = [batch, 2062]
```

For `events_per_chunk=128`:

```text
header bytes = bytes[0:14]
event i bytes = bytes[14 + i * 16 : 14 + (i + 1) * 16]
```

Masked-byte pretraining can:

1. Mask selected byte positions.
2. Feed visible byte IDs through a 256-entry byte embedding table.
3. Decode masked byte logits.
4. Apply BCE to the 8 target bits of each masked byte, or cross-entropy over
   the 256 byte values.

If using BCE, bytes are unpacked only inside the loss for masked positions.
Do not expand the full input into `float16` bits unless a specific experiment
requires that representation.

## Physical Storage

This document defines the semantic byte representation.

Recommended storage:

```text
uint8 array: [num_chunks, 2062]
```

For large datasets, use contiguous shard files with a manifest:

```text
manifest.json
events_chunks_uint8_000000.bin
events_chunks_uint8_000001.bin
...
```

Each shard should record:

```text
num_chunks
row_bytes
events_per_chunk
date range
ticker range or ticker list
reference table versions
```

The storage format must preserve the byte values exactly. If an implementation
also stores decoded integer columns for debugging, the byte representation
remains the source of truth for byte-model experiments.

## Default Size Summary

With `events_per_chunk=128`:

```text
header:        14 bytes
event row:     16 bytes
details:    2,048 bytes
total:      2,062 bytes
```

With `events_per_chunk=256` and `uint16` count fields:

```text
header:        16 bytes
event row:     16 bytes
details:    4,096 bytes
total:      4,112 bytes
```
