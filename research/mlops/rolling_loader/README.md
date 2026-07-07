# Rolling Loader Package

`research.mlops.rolling_loader` is the training and serving data path for
stateful market-context models. The current recommended path is:

```text
ClickHouse events/context tables
  -> ticker/month SSD cache builder
  -> ticker/month rolling data loader
  -> trainer batches
```

The cache builder writes source-aligned ticker/month packages to SSD. The data
loader reads those packages, builds a shuffled sample plan, materializes only
the data groups requested by the trainer, and feeds batches from CPU while the
GPU trains.

The older in-memory replay loader and materialized-cache scripts remain in this
package for comparison and profiling, but new training work should start from
the ticker/month cache and loader.

## Components

| Component | File | Purpose |
| --- | --- | --- |
| Ticker/month cache builder | `run_build_ticker_month_cache.py` | Builds reusable SSD packages from ClickHouse. |
| Ticker/month cache audit | `audit_ticker_month_cache.py` | Audits completed SSD cache packages. |
| Ticker/month data loader | `ticker_month_dataset.py` | Reads SSD packages and materializes trainer batches. |
| Loader profiler | `run_profile_ticker_month_loader.py` | Measures loader speed, memory, and output shapes. |
| Loader batch audit | `audit_ticker_month_loader_batches.py` | Verifies materialized batches against SSD package files. |
| Loader batch inspection notebook | `notebooks/ticker_month_loader_batch_inspection.ipynb` | Loads an interactive batch and inspects identities, shapes, masks, labels, text embeddings, XBRL, and bars. |
| Cache design guide | `TICKER_MONTH_SSD_CACHE_DESIGN.md` | Detailed design rationale and storage contract. |
| Legacy stateful replay | `loader.py`, `initialize.py`, `run_training_profile.py` | Older production-style replay/profiling path. |

## Ticker/Month SSD Cache

Production compact events are stored in yearly physical tables
`market_sip_compact.events_YYYY`. Builder arguments still default to the
logical name `events`; the builder resolves it to the relevant `events_YYYY`
table for the requested month and uses a ClickHouse `merge(...)` source only
for rare cross-year lookback windows.

Each yearly event table is partitioned by month and ordered by
`(ticker, ordinal)`. The fastest natural unit is therefore:

```text
one ticker, one month
```

Each package stores raw compact events, origins, reusable index files, compact
intraday label sources, daily corporate-action labels, and context files. It
does **not** store encoded event chunks and does
**not** store fully materialized training batches.

### Layout

```text
cache_root/
  manifest.json
  train/
    month=YYYY-MM/
      global/
        manifest.json
        market_news_embeddings.parquet
        global_daily_bars.parquet
        category_references.parquet
      ticker_hash=XX/
        ticker=ABC/
          manifest.json
          events_part_00000.parquet
          origins_part_00000.parquet
          event_window_index_part_00000.parquet
          ranges_part_00000.parquet
          corporate_action_daily_labels_part_00000.parquet
          daily_bars.parquet
          ticker_news_embeddings.parquet
          sec_filing_embeddings.parquet
          xbrl.parquet
          corporate_actions.parquet
          intraday_base_bars.parquet
          intraday_condition_events.parquet
```

Very liquid tickers can be physically split into multiple ordinal-bounded
parts. These part boundaries are storage boundaries only. They are not model
or training boundaries.

### Keys

Two keys are used:

```text
origin_key = ticker_id + ordinal
cache_state_key = timestamp_us
```

`origin_key` uniquely identifies one training sample. `cache_state_key`
represents an as-of time; many tickers or origins can share it, so it is not a
sample identity.

### Origin Rule

Origins are only events inside the active trading session:

```text
04:00:00 America/New_York <= origin session time < 20:00:00 America/New_York
```

All timestamps in storage remain UTC microseconds. The New York session rule is
used only for session membership and session-relative features.

### Event Payload

Events are stored as raw compact rows plus cache-time event time features.

Core event columns:

```text
ticker_id
ticker
ordinal
event_meta
timestamp_us
price_primary_int
price_secondary_int
size_primary
size_secondary
exchange_primary
exchange_secondary
condition_token_1 ... condition_token_5
```

Cache-time event features:

```text
utc_second_of_day_sin/cos
utc_day_of_week_sin/cos
utc_day_of_year_sin/cos
years_since_2000
session_second
session_progress
is_regular_hours
is_premarket
is_afterhours
```

Origin-relative features are not stored because the same event can be reused by
many origins. The loader computes origin-relative deltas at materialization
time when a trainer actually asks for them.

### Time Feature Policy

All cached absolute time features use UTC. The only New York conversion is for
market-session membership and session-progress fields derived from event
timestamps.

The builder computes origin-independent absolute time features once and stores
them aligned with each source row:

```text
events:        utc_* calendar features plus session fields
news:          available_utc_* from published_at_utc / timestamp_us
SEC text:      available_utc_* from accepted_at_utc / timestamp_us
XBRL:          available_utc_* from timestamp_us
daily bars:    bar_start_utc_* from bar_start_ms
```

The loader computes origin-relative fields because age depends on the selected
origin:

```text
time_delta_seconds              source_timestamp_us - origin_timestamp_us
time_delta_seconds_log1p_signed signed log1p(abs(delta_seconds))
time_age_seconds_log1p          log1p(max(0, origin_timestamp_us - source_timestamp_us))
bar_age_days                    origin date/time minus selected bar start
bar_age_days_log1p              log1p(bar_age_days)
```

For text embeddings, time features are item-level. All chunks from the same
article or filing share the same item timestamp and item time features.

### Category Id Policy

Categorical ids are persistent reference-table ids, not batch-local or
cache-local ids. Id `0` is reserved for missing or unknown values.

The reference table is built by:

```powershell
python -m pipelines.market_sip.events.run_build_training_category_reference
```

Use `--rebuild-from-scratch` only for the initial full reference build. Normal
runs preserve existing `(domain, field_name, category_value) -> category_id`
mappings and append ids only for new values. This keeps ids fixed across cache
builds and training runs.

For new ticker-month caches, XBRL category ids are joined from the reference
table at build time and stored in each ticker `xbrl.parquet`. This includes
`fiscal_period_id` and `calendar_period_id`, plus taxonomy/tag/unit/form/row
kind/location ids. The monthly `global/category_references.parquet` snapshot is
still saved for auditability and backward compatibility with older caches.

The ticker-month cache builder checks this table at startup. If the table is
missing or empty, it runs the append-only category reference builder before
fetching month data. To refresh the table even when it already exists, pass:

```powershell
--force-category-reference-build
```

To bypass this startup check for an isolated/debug run, pass:

```powershell
--skip-category-reference-check
```

### Event Lookback

The builder separates cached history from the default training window index.

`--max-cached-event-lookback-rows` controls how many prior raw event rows are
stored before each physical part's first origin. The default is:

```text
8192 rows
```

This is not per origin. For one normal ticker/month part, it stores up to 8192
rows before the first eligible origin of the month. For split liquid tickers,
the overlap is up to 8192 rows per physical part boundary.

The default `event_window_index` is still written for the configured build
coverage, but the loader can request any event coverage that fits inside
`max_cached_event_lookback_rows`. If a later experiment needs more history, the
cache must be rebuilt with a larger value.

### Labels

Intraday labels are stored as grid-aligned `next_*` labels. The builder skips
the origin's current partial bucket and starts every label at the next bucket in
the selected base resolution. This bounds timestamp approximation error by the
base resolution while avoiding per-origin raw-event scans.

The SSD cache does not store dense intraday bar grids and does not store
`current_*` intraday labels. During a build, the script maintains a shared
ClickHouse intermediate table, `intraday_base_bars_by_time_ticker` by default,
with one row per `(local_date, ticker, resolution, bucket, bar_family)`. Missing
local-session days are built per ticker-month package, then all parts for that
ticker/month reuse those bars for labels and backward context. The builder does
not front-load every ticker for a whole day/month because the 100ms grid can be
hundreds of millions of rows per active market day. This replaces the older
behavior where each part query rebuilt the same bars from raw events.

Condition-event labels use the same shared-artifact pattern. Before package
workers start for a month, the builder ensures a sparse ClickHouse table,
`intraday_condition_events_by_time_ticker` by default. That table stores only
events that match forecastable condition groups such as halt/pause, resume,
news-risk, and LULD/limit-state flags. A small
`intraday_aux_build_status` table records that the month-level sparse condition
artifact is complete, including zero-row months. Package workers then read
bounded ticker/month slices from the sparse table instead of scanning raw
`events` repeatedly. This keeps condition-label integrity tied to the same
`event_condition_token_reference` rules while removing thousands of redundant
condition scans.

When building reusable ClickHouse intraday grids outside the package builder,
condition buckets stay in their own table,
`intraday_condition_bars_by_time_ticker`, instead of being added as a fourth
`bar_family` inside `intraday_base_bars_by_time_ticker`. Trade, quote-bid, and
quote-ask rows carry numeric OHLC/size/count semantics; condition bars carry
sparse binary flags and `condition_event_count`. Keeping them separate avoids
mixing incompatible schemas while still letting labels and audits join on
`(ticker, local_date, label_resolution_us, bucket_index)`.

By default, the cache does not write redundant
`intraday_forward_labels_part_*.parquet` files. It writes compact
`intraday_base_bars.parquet` and `intraday_condition_events.parquet` once per
ticker/month, then the loader materializes `intraday_labels` for the requested
batch from those compact sources. This keeps monthly builds linear in source
data size instead of multiplying work by `origins x horizons`.

For debugging or parity checks, `--materialize-intraday-forward-labels` writes
the older per-origin label files. In that mode,
`intraday_forward_labels_part_*.parquet` stores one row per origin and each
horizon-dependent field is a list column ordered by `horizon_us`. The canonical
future bar fields are family-specific:

Grid metadata list columns are emitted with the same horizon order:

```text
label_resolution_us
label_grid_start_timestamp_us
label_grid_end_timestamp_us
```

The grid window is `[label_grid_start_timestamp_us, label_grid_end_timestamp_us)`.

| Family | Price fields | Size/count fields |
| --- | --- | --- |
| `trade` | `trade_open`, `trade_close`, `trade_high`, `trade_low` | `trade_size_sum`, `trade_event_count`, `trade_available`, `trade_last_event_timestamp_us` |
| `quote_bid` | `quote_bid_open`, `quote_bid_close`, `quote_bid_high`, `quote_bid_low` | `quote_bid_size_open`, `quote_bid_size_close`, `quote_bid_size_high`, `quote_bid_size_low`, `quote_bid_event_count`, `quote_bid_available`, `quote_bid_last_event_timestamp_us` |
| `quote_ask` | `quote_ask_open`, `quote_ask_close`, `quote_ask_high`, `quote_ask_low` | `quote_ask_size_open`, `quote_ask_size_close`, `quote_ask_size_high`, `quote_ask_size_low`, `quote_ask_event_count`, `quote_ask_available`, `quote_ask_last_event_timestamp_us` |

Legacy list columns `price_primary_int`, `price_secondary_int`,
`size_primary_sum`, `size_secondary_sum`, `event_count`,
`last_event_timestamp_us`, and `available` are retained as compatibility
projections only. This keeps the ClickHouse result and parquet file
proportional to origin count instead of
`origin_count * horizon_count`.

All future bar prices are decoded `float32` price levels. The builder applies
the scale bits packed in each event's `event_meta` before writing label arrays.
Raw packed event-window prices remain stored as integer event columns because
the event path needs the original compact representation.

Future event labels are separate binary targets in `intraday_labels`, not fields
inside compatibility `future_intraday_bars`. Condition flags are generated from the packed
event condition-token columns by resolving the current
`event_condition_token_reference` table at build time. External arrival flags
use the context availability timestamp from the news and SEC embedding tables.
These labels use intraday horizons. The default labels are:

```text
condition_halt_pause_flag
condition_resume_flag
condition_news_risk_flag
condition_luld_limit_state_flag
ticker_news_arrival_flag
sec_filing_arrival_flag
```

Daily horizons are reserved for labels whose meaningful cadence is not updated
inside the trading session, such as split/dividend/corporate-action labels.

Only conditions with direct trading value are included: halts or volatility
pauses, resumptions, news-risk states, and LULD or limit-state events. Ticker
news and SEC filing arrivals are included because they support event-risk
position sizing, entry avoidance, or order-risk controls. Routine sale/quote
condition metadata and opening-delay/no-open states are still available in raw
event windows but are not forecast as separate targets by default.

The current default horizons are:

```text
100ms,200ms,300ms,400ms,500ms,1s,2s,3s,5s,10s,15s,30s,
60s,120s,180s,300s,600s,900s,1200s,1800s,3600s,7200s,
3h,4h,5h,eod
```

The current grid policy is:

| Horizon | Base resolution | Max bucket count at upper bound |
| --- | ---: | ---: |
| `<=60s` | `100ms` | `600` |
| `60s..900s` | `1s` | `900` |
| `900s..3600s` | `5s` | `720` |
| `3600s..10800s` | `30s` | `360` |
| `>10800s` and `eod` | `1m` | `300` for 5h |

Custom numeric horizons must be divisible by their selected base resolution.
`eod` runs from the next selected bucket through the 20:00 ET extended-session
close.

Daily macro/future labels are based on daily bars and should be computed from
daily-bar sequences, not from independent weekly/monthly/yearly bars.

Corporate-action labels are daily future targets, not intraday labels. The
builder writes `corporate_action_daily_labels_part_*.parquet`, one row per
origin, with list columns ordered by `horizon_days`. Defaults are:

```text
1d,2d,3d,7d,28d
```

The emitted binary label fields are:

```text
future_split_flag
future_reverse_split_flag
future_forward_split_flag
future_dividend_ex_flag
future_special_dividend_ex_flag
future_any_corporate_action_flag
```

They are computed from q_live corporate-action reference tables using action
effective dates. Split context is conservative because the split table provides
execution date but no separate announcement timestamp; it becomes available at
the split effective session start. Dividend context uses declaration date when
present, otherwise ex-dividend date. Labels always look forward from the origin.

### Context Files

The builder reads precomputed Qwen embedding context tables. It does not query
raw text and does not run Qwen inference during cache creation.

Per-ticker optional context:

```text
ticker_news_embeddings
sec_filing_embeddings
xbrl
daily_bars
corporate_actions
```

Global context is stored once per month under `global/` where available.

Text and XBRL context is saved as `month rows + latest prior context`. The
prior context is count-based, not just day-lookback based, so early-month
origins still see the latest backward information available in ClickHouse.
Builder capacity and loader consumption are intentionally separate:

| Context | Builder parameter | Builder default | What is saved | Loader parameter | Loader default |
| --- | --- | ---: | --- | --- | ---: |
| Raw events | `--max-cached-event-lookback-rows` | `8192` | Extra raw event rows before each physical part so the first origins in the part can build backward context. | `event_stream_length` | `1024` |
| Ticker news | `--ticker-news-prior-items` | `64` | Latest prior logical ticker-news items before the month start, plus all items inside the month. | `ticker_news_max_items` | `8` |
| Market news | `--market-news-prior-items` | `512` | Latest prior logical market-news items before the month start, plus all items inside the month. | `market_news_max_items` | `16` |
| SEC filing text | `--sec-filing-prior-items` | `32` | Latest prior logical SEC filing items before the month start, plus all items inside the month. | `sec_filing_max_items` | `4` |
| XBRL facts | `--xbrl-prior-rows` | `4096` | Latest prior XBRL fact rows before the month start, plus all rows inside the month. | `xbrl_max_items` | `4096` |
| Daily ticker/global bars | `--macro-lookback-days`, `--label-lookahead-days` | `400`, `400` | Completed daily bars before the month and daily bars needed for forward labels after the month. | daily bar offsets / label horizons | see loader config |
| Corporate actions | `--corporate-action-lookback-days`, `--corporate-action-items` | `3650`, `128` | Historical available corporate actions and future effective actions needed for labels. | `corporate_action_max_items` | `128` |

For news and SEC, `items` means logical article or filing items, not embedding
chunk rows. The builder selects item identities first, then writes all chunk
rows for each selected item. The loader may request fewer items than the saved
capacity without rebuilding the cache. If a loader experiment needs more than
the saved builder capacity, rebuild or refresh the cache with larger prior
values.

Market news means all news from the embedding table, deduplicated by
article/chunk identity, then stored under `global/market_news_embeddings.parquet`
with ticker `__MARKET__`. It is not limited to rows that have no ticker.

Missing optional context is represented as empty/zero data plus explicit masks
at load time. For text/XBRL, padding should mean ClickHouse did not have enough
historical as-of rows/items for that origin, not that the builder clipped the
history window too tightly.

At load time, daily bar context is emitted only when requested in
`data_groups`. The loader uses completed daily bars only. It does not expose a
full current-day daily bar as context because that would include future
information for intraday origins.

## Data Lifecycle

This section describes the full value lifecycle from source data to trainer
batch. Market-event data starts from SIP quote/trade flatfiles. External
context starts from normalized ClickHouse context tables, embedding tables, and
reference tables that were built before the ticker/month cache builder runs.

### Atomic X Dimension Lifecycle

| X dimension | Source field | Cache/storage representation | Loader representation | Scale/mask rule |
| --- | --- | --- | --- | --- |
| `ticker` | Event ticker symbol | `origins_part_*.parquet`, string | batch identity array | Identity only by default. |
| `ticker_id` | Persistent ticker id | `origins_part_*.parquet`, integer | batch identity array | Identity only by default. |
| `origin_ordinal` | Origin event ordinal | `origins_part_*.parquet`, integer | batch identity array | Defines sample identity with ticker. |
| `origin_timestamp_us` | Origin event UTC timestamp | `origins_part_*.parquet`, int64 microseconds | batch identity array | Defines as-of time. |
| `event_type` | Quote/trade source row kind | not a separate cached column; packed in `event_meta` | derived from `bitAnd(event_meta, 1)` when needed | `event_meta` bit 0: `0=quote`, `1=trade`. |
| `event_meta` | Event codec metadata | raw event row, uint8 packed byte | raw event window column | Bits: 0 event type, 1 primary price scale, 2 secondary price scale, 3-5 tape, 6-7 reserved. |
| `primary_price_scale` | Price precision selected at ingest | not a separate cached column; packed in `event_meta` | derived from `bitAnd(event_meta, 2) != 0` when decoding | `event_meta` bit 1: `0=/100`, `1=/10000`. |
| `secondary_price_scale` | Price precision selected at ingest | not a separate cached column; packed in `event_meta` | derived from `bitAnd(event_meta, 4) != 0` when decoding | `event_meta` bit 2: `0=/100`, `1=/10000`. |
| `tape` | Source tape code | not a separate cached column; packed in `event_meta` | derived from `bitAnd(bitShiftRight(event_meta, 3), 7)` when needed | `event_meta` bits 3-5 store tape code; bits 6-7 reserved. |
| `ordinal` | Event ordinal | raw event row, uint64 | raw event window column unless suppressed | Used for continuity audit. |
| `timestamp_us` | Event SIP timestamp | raw event row, int64 microseconds | raw event window column unless suppressed | UTC. |
| `price_primary_int` | Quote ask price or trade price | raw event row, uint32 packed price | raw event window column | Scale byte location: `event_meta` bit 1; `0=/100`, `1=/10000`. |
| `price_secondary_int` | Quote bid price or zero for trade | raw event row, uint32 packed price | raw event window column | Scale byte location: `event_meta` bit 2; `0=/100`, `1=/10000`. |
| `size_primary` | Quote ask size or trade size | raw event row, float32 | raw event window column | No scale bit; raw event units. |
| `size_secondary` | Quote bid size or zero for trade | raw event row, float32 | raw event window column | No scale bit; raw event units. |
| `exchange_primary` | Ask exchange or trade exchange | raw event row, uint8 full-byte code | raw event window column | Byte bits 0-7 store the exchange code; not sub-bit packed. |
| `exchange_secondary` | Bid exchange or zero for trade | raw event row, uint8 full-byte code | raw event window column | Byte bits 0-7 store the exchange code; not sub-bit packed. |
| `condition_token_1` | First condition/indicator token slot | raw event row, uint8 full-byte dense token id | raw event window column | Byte bits 0-7 store one dense token id; `0` means missing/unknown. |
| `condition_token_2` | Second condition/indicator token slot | raw event row, uint8 full-byte dense token id | raw event window column | Byte bits 0-7 store one dense token id; `0` means missing/unknown. |
| `condition_token_3` | Third condition/indicator token slot | raw event row, uint8 full-byte dense token id | raw event window column | Byte bits 0-7 store one dense token id; `0` means missing/unknown. |
| `condition_token_4` | Fourth condition/indicator token slot | raw event row, uint8 full-byte dense token id | raw event window column | Byte bits 0-7 store one dense token id; `0` means missing/unknown. |
| `condition_token_5` | Fifth condition/indicator token slot | raw event row, uint8 full-byte dense token id | raw event window column | Byte bits 0-7 store one dense token id; `0` means missing/unknown. |
| `utc_second_of_day_sin` | Event timestamp | raw event row, float32 | raw event window column when requested | UTC absolute time feature. |
| `utc_second_of_day_cos` | Event timestamp | raw event row, float32 | raw event window column when requested | UTC absolute time feature. |
| `utc_day_of_week_sin` | Event timestamp | raw event row, float32 | raw event window column when requested | UTC absolute time feature. |
| `utc_day_of_week_cos` | Event timestamp | raw event row, float32 | raw event window column when requested | UTC absolute time feature. |
| `utc_day_of_year_sin` | Event timestamp | raw event row, float32 | raw event window column when requested | UTC absolute time feature. |
| `utc_day_of_year_cos` | Event timestamp | raw event row, float32 | raw event window column when requested | UTC absolute time feature. |
| `years_since_2000` | Event timestamp | raw event row, float32 | raw event window column when requested | UTC absolute time feature. |
| `session_second` | Event timestamp converted to New York session | raw event row, float32/int | raw event window column when requested | Session feature, not UTC calendar feature. |
| `session_progress` | Event timestamp converted to New York session | raw event row, float32 | raw event window column when requested | Session progress from 04:00 to 20:00 ET. |
| `is_regular_hours` | Event timestamp converted to New York session | raw event row, bool/uint8 | raw event window column when requested | Market-session flag. |
| `is_premarket` | Event timestamp converted to New York session | raw event row, bool/uint8 | raw event window column when requested | Market-session flag. |
| `is_afterhours` | Event timestamp converted to New York session | raw event row, bool/uint8 | raw event window column when requested | Market-session flag. |
| `raw_event_mask` | Event-window selection | not stored; derived by loader | `[B, event_stream_length]` or window mask | False for padded/missing positions. |
| `ticker_daily_bars.trade_values.open` | Daily trade bar table rows | `daily_bars.parquet`, float32 | `bar_inputs["ticker_daily_bars"]["trade_values"][..., open]` | Completed trade bars only; compatibility alias `values` points to trade. |
| `ticker_daily_bars.trade_values.close` | Daily trade bar table rows | `daily_bars.parquet`, float32 | `bar_inputs["ticker_daily_bars"]["trade_values"][..., close]` | Completed trade bars only. |
| `ticker_daily_bars.trade_values.high` | Daily trade bar table rows | `daily_bars.parquet`, float32 | `bar_inputs["ticker_daily_bars"]["trade_values"][..., high]` | Completed trade bars only. |
| `ticker_daily_bars.trade_values.low` | Daily trade bar table rows | `daily_bars.parquet`, float32 | `bar_inputs["ticker_daily_bars"]["trade_values"][..., low]` | Completed trade bars only. |
| `ticker_daily_bars.trade_values.size_sum` | Daily trade bar table rows | `daily_bars.parquet`, float32 | `bar_inputs["ticker_daily_bars"]["trade_values"][..., size_sum]` | Trade size sum; no dollar-volume/VWAP field is stored by default. |
| `ticker_daily_bars.trade_values.event_count` | Daily trade bar table rows | `daily_bars.parquet`, float32 | `bar_inputs["ticker_daily_bars"]["trade_values"][..., event_count]` | Trade-event count. |
| `ticker_daily_bars.quote_bid_values.open/close/high/low` | Daily quote bid bar rows | `daily_bars.parquet`, float32 | `bar_inputs["ticker_daily_bars"]["quote_bid_values"][..., price_field]` | Bid OHLC from quote events only. |
| `ticker_daily_bars.quote_bid_values.size_open/size_close/size_high/size_low` | Daily quote bid bar rows | `daily_bars.parquet`, float32 | `bar_inputs["ticker_daily_bars"]["quote_bid_values"][..., size_field]` | Bid size state fields from quote events only. |
| `ticker_daily_bars.quote_bid_values.event_count` | Daily quote bid bar rows | `daily_bars.parquet`, float32 | `bar_inputs["ticker_daily_bars"]["quote_bid_values"][..., event_count]` | Quote-event count for bid family. |
| `ticker_daily_bars.quote_ask_values.open/close/high/low` | Daily quote ask bar rows | `daily_bars.parquet`, float32 | `bar_inputs["ticker_daily_bars"]["quote_ask_values"][..., price_field]` | Ask OHLC from quote events only. |
| `ticker_daily_bars.quote_ask_values.size_open/size_close/size_high/size_low` | Daily quote ask bar rows | `daily_bars.parquet`, float32 | `bar_inputs["ticker_daily_bars"]["quote_ask_values"][..., size_field]` | Ask size state fields from quote events only. |
| `ticker_daily_bars.quote_ask_values.event_count` | Daily quote ask bar rows | `daily_bars.parquet`, float32 | `bar_inputs["ticker_daily_bars"]["quote_ask_values"][..., event_count]` | Quote-event count for ask family. |
| `ticker_daily_bars.*_mask` | Bar family availability | not stored; derived by loader | `[B, offsets]` | False when requested family/offset is unavailable. |
| `ticker_daily_bars.*_time_features` | Bar start timestamp and origin timestamp | computed/cached time features | `[B, offsets, bar_time_features]` | Zero where the matching family mask is false. |
| `ticker_intraday_bars.trade_values.open/close/high/low` | Same-session historical trade stream before the origin | `intraday_base_bars.parquet` compact source by default; optional `intraday_context_bars_part_*.parquet` only with `--materialize-intraday-context-bars` | `bar_inputs["ticker_intraday_bars"]["trade_values"][..., price_field]` | Backward intraday X context. The default cache does not persist this per origin because it is highly redundant; it is materialized from compact base bars by the loader path that requests `intraday_bars`. |
| `ticker_intraday_bars.trade_values.size_sum/event_count` | Same-session historical trade stream before the origin | `intraday_base_bars.parquet` compact source | `bar_inputs["ticker_intraday_bars"]["trade_values"][..., size_sum/event_count]` | Same 6-field schema as future trade bars, but used as context. |
| `ticker_intraday_bars.quote_bid_values.open/close/high/low` | Same-session historical quote bid stream before the origin | `intraday_base_bars.parquet` compact source | `bar_inputs["ticker_intraday_bars"]["quote_bid_values"][..., price_field]` | Bid-side backward intraday X context. |
| `ticker_intraday_bars.quote_bid_values.size_open/size_close/size_high/size_low/event_count` | Same-session historical quote bid stream before the origin | `intraday_base_bars.parquet` compact source | `bar_inputs["ticker_intraday_bars"]["quote_bid_values"][..., field]` | Same 9-field schema as future bid bars, but used as context. |
| `ticker_intraday_bars.quote_ask_values.open/close/high/low` | Same-session historical quote ask stream before the origin | `intraday_base_bars.parquet` compact source | `bar_inputs["ticker_intraday_bars"]["quote_ask_values"][..., price_field]` | Ask-side backward intraday X context. |
| `ticker_intraday_bars.quote_ask_values.size_open/size_close/size_high/size_low/event_count` | Same-session historical quote ask stream before the origin | `intraday_base_bars.parquet` compact source | `bar_inputs["ticker_intraday_bars"]["quote_ask_values"][..., field]` | Same 9-field schema as future ask bars, but used as context. |
| `ticker_intraday_bars.*_mask` | Family availability in each backward horizon | derived from compact base bars, or `*_available` list columns in opt-in materialized files | `bar_inputs["ticker_intraday_bars"]["{family}_mask"] [B,H]` | False when no event of that family exists in the backward horizon. |
| `ticker_intraday_bars.*_time_features` | Backward context grid start and origin timestamp | computed by loader from origin timestamp and grid policy | `[B,H,bar_time_features]` | UTC start-time features plus age from origin to context-window start; zero where the family mask is false. |
| `global_daily_bars.*_values` | Global daily bar family rows | `global/global_daily_bars.parquet`, float32 | `bar_inputs["global_daily_bars"]["{family}_values"][..., field]` | Same family schema as ticker bars, with symbol dimension. |
| `global_daily_bars.*_mask` | Global bar family availability | not stored; derived by loader | `[B, symbols, offsets]` | False when requested symbol/family/offset is unavailable. |
| `text_inputs["ticker_news"].embeddings` | Ticker news Qwen embedding table | `ticker_news_embeddings.parquet`, float32 tensor | `[B, ticker_news_max_items, chunks, text_embedding_dim]` | Zero where item/chunk mask is false. |
| `text_inputs["ticker_news"].chunk_mask` | Ticker news chunk availability | derived from chunk rows | `[B, ticker_news_max_items, chunks]` | True only for real embedded chunks. |
| `text_inputs["ticker_news"].item_mask` | Ticker news item availability | derived from as-of item selection | `[B, ticker_news_max_items]` | True only for selected historical items. |
| `text_inputs["ticker_news"].item_timestamp_us` | Ticker news publish/availability time | embedding parquet timestamp | `[B, ticker_news_max_items]` | Zero where item mask is false. |
| `text_inputs["ticker_news"].item_time_features` | Ticker news item timestamp and origin timestamp | absolute UTC plus relative features | `[B, ticker_news_max_items, text_time_features]` | Zero where item mask is false. |
| `text_inputs["market_news"].embeddings` | Market news Qwen embedding table | `global/market_news_embeddings.parquet`, float32 tensor | `[B, market_news_max_items, chunks, text_embedding_dim]` | Zero where item/chunk mask is false. |
| `text_inputs["market_news"].chunk_mask` | Market news chunk availability | derived from chunk rows | `[B, market_news_max_items, chunks]` | True only for real embedded chunks. |
| `text_inputs["market_news"].item_mask` | Market news item availability | derived from as-of item selection | `[B, market_news_max_items]` | True only for selected historical items. |
| `text_inputs["market_news"].item_timestamp_us` | Market news publish/availability time | embedding parquet timestamp | `[B, market_news_max_items]` | Zero where item mask is false. |
| `text_inputs["market_news"].item_time_features` | Market news item timestamp and origin timestamp | absolute UTC plus relative features | `[B, market_news_max_items, text_time_features]` | Zero where item mask is false. |
| `text_inputs["sec_filings"].embeddings` | SEC filing Qwen embedding table | `sec_filing_embeddings.parquet`, float32 tensor | `[B, sec_filing_max_items, chunks, text_embedding_dim]` | Zero where item/chunk mask is false. |
| `text_inputs["sec_filings"].chunk_mask` | SEC filing chunk availability | derived from chunk rows | `[B, sec_filing_max_items, chunks]` | True only for real embedded chunks. |
| `text_inputs["sec_filings"].item_mask` | SEC filing item availability | derived from as-of item selection | `[B, sec_filing_max_items]` | True only for selected historical items. |
| `text_inputs["sec_filings"].item_timestamp_us` | SEC filing accepted/availability time | embedding parquet timestamp | `[B, sec_filing_max_items]` | Zero where item mask is false. |
| `text_inputs["sec_filings"].item_time_features` | SEC filing item timestamp and origin timestamp | absolute UTC plus relative features | `[B, sec_filing_max_items, text_time_features]` | Zero where item mask is false. |
| `xbrl_inputs.mask` | XBRL row availability | derived from as-of row selection | `[B, xbrl_max_items]` | Controls all XBRL fields. |
| `xbrl_inputs.value` | XBRL fact value | `xbrl.parquet`, float32 | `[B, xbrl_max_items]` | Zero where mask is false. |
| `xbrl_inputs.fiscal_year` | XBRL fiscal year | `xbrl.parquet`, int16 | `[B, xbrl_max_items]` | Zero where mask is false. |
| `xbrl_inputs.age_days` | XBRL availability timestamp vs origin | derived by loader | `[B, xbrl_max_items]` | Zero where mask is false. |
| `xbrl_inputs.period_end_days` | XBRL period end date | `xbrl.parquet`, int32 epoch day | `[B, xbrl_max_items]` | Zero where mask is false. |
| `xbrl_inputs.fiscal_period_id` | Reference category id | `xbrl.parquet`, uint32 | `[B, xbrl_max_items]` | `0` means missing/unknown. |
| `xbrl_inputs.calendar_period_id` | Reference category id | `xbrl.parquet`, uint32 | `[B, xbrl_max_items]` | `0` means missing/unknown. |
| `xbrl_inputs.taxonomy_id` | Reference category id | `xbrl.parquet`, uint32 | `[B, xbrl_max_items]` | `0` means missing/unknown. |
| `xbrl_inputs.tag_id` | Reference category id | `xbrl.parquet`, uint32 | `[B, xbrl_max_items]` | `0` means missing/unknown. |
| `xbrl_inputs.unit_id` | Reference category id | `xbrl.parquet`, uint32 | `[B, xbrl_max_items]` | `0` means missing/unknown. |
| `xbrl_inputs.form_id` | Reference category id | `xbrl.parquet`, uint32 | `[B, xbrl_max_items]` | `0` means missing/unknown. |
| `xbrl_inputs.row_kind_id` | Reference category id | `xbrl.parquet`, uint32 | `[B, xbrl_max_items]` | `0` means missing/unknown. |
| `xbrl_inputs.location_id` | Reference category id | `xbrl.parquet`, uint32 | `[B, xbrl_max_items]` | `0` means missing/unknown. |
| `xbrl_inputs.mapping_confidence` | XBRL mapping confidence | `xbrl.parquet`, float32 | `[B, xbrl_max_items]` | Zero where mask is false. |
| `xbrl_inputs.time_features` | XBRL availability timestamp and origin timestamp | absolute UTC plus relative features | `[B, xbrl_max_items, xbrl_time_features]` | Zero where mask is false. |
| `xbrl_inputs.period_end_time_features` | XBRL period end date and origin timestamp | absolute period-end plus relative age features | `[B, xbrl_max_items, xbrl_period_time_features]` | Zero where mask is false. |
| `corporate_action_inputs.mask` | Corporate-action row availability | derived from as-of row selection | `[B, corporate_action_max_items]` | Controls all corporate-action context fields. |
| `corporate_action_inputs.action_type_id` | Corporate-action type | `corporate_actions.parquet`, uint32 | `[B, corporate_action_max_items]` | `0` means missing/unknown. |
| `corporate_action_inputs.dividend_type_id` | Dividend type | `corporate_actions.parquet`, uint32 | `[B, corporate_action_max_items]` | `0` means missing/unknown. |
| `corporate_action_inputs.currency_id` | Dividend currency | `corporate_actions.parquet`, uint32 | `[B, corporate_action_max_items]` | `0` means missing/unknown. |
| `corporate_action_inputs.frequency_id` | Dividend frequency | `corporate_actions.parquet`, uint32 | `[B, corporate_action_max_items]` | `0` means missing/unknown. |
| `corporate_action_inputs.available_timestamp_us` | Context availability time | `corporate_actions.parquet`, int64 | `[B, corporate_action_max_items]` | Must be `<= origin_timestamp_us`; zero where mask is false. |
| `corporate_action_inputs.effective_timestamp_us` | Action effective time | `corporate_actions.parquet`, int64 | `[B, corporate_action_max_items]` | Zero where mask is false. |
| `corporate_action_inputs.effective_epoch_day` | Action effective date | `corporate_actions.parquet`, int32 | `[B, corporate_action_max_items]` | Zero where mask is false. |
| `corporate_action_inputs.declaration_epoch_day` | Declaration date | `corporate_actions.parquet`, int32 | `[B, corporate_action_max_items]` | Zero when absent or masked. |
| `corporate_action_inputs.pay_epoch_day` | Dividend pay date | `corporate_actions.parquet`, int32 | `[B, corporate_action_max_items]` | Zero when absent or masked. |
| `corporate_action_inputs.record_epoch_day` | Dividend record date | `corporate_actions.parquet`, int32 | `[B, corporate_action_max_items]` | Zero when absent or masked. |
| `corporate_action_inputs.numeric_features.split_from` | Corporate-action numeric field | `numeric_features`, float32 | `[..., split_from]` | Zero where mask is false. |
| `corporate_action_inputs.numeric_features.split_to` | Corporate-action numeric field | `numeric_features`, float32 | `[..., split_to]` | Zero where mask is false. |
| `corporate_action_inputs.numeric_features.share_factor` | Corporate-action numeric field | `numeric_features`, float32 | `[..., share_factor]` | Zero where mask is false. |
| `corporate_action_inputs.numeric_features.price_factor` | Corporate-action numeric field | `numeric_features`, float32 | `[..., price_factor]` | Zero where mask is false. |
| `corporate_action_inputs.numeric_features.log_share_factor` | Derived numeric field | `numeric_features`, float32 | `[..., log_share_factor]` | Zero where mask is false. |
| `corporate_action_inputs.numeric_features.log_price_factor` | Derived numeric field | `numeric_features`, float32 | `[..., log_price_factor]` | Zero where mask is false. |
| `corporate_action_inputs.numeric_features.cash_amount` | Dividend cash amount | `numeric_features`, float32 | `[..., cash_amount]` | Zero where mask is false. |
| `corporate_action_inputs.numeric_features.log1p_cash_amount` | Derived dividend cash amount | `numeric_features`, float32 | `[..., log1p_cash_amount]` | Zero where mask is false. |
| `corporate_action_inputs.numeric_features.is_split` | Derived action flag | `numeric_features`, float32 | `[..., is_split]` | Zero where mask is false. |
| `corporate_action_inputs.numeric_features.is_forward_split` | Derived action flag | `numeric_features`, float32 | `[..., is_forward_split]` | Zero where mask is false. |
| `corporate_action_inputs.numeric_features.is_reverse_split` | Derived action flag | `numeric_features`, float32 | `[..., is_reverse_split]` | Zero where mask is false. |
| `corporate_action_inputs.numeric_features.is_dividend` | Derived action flag | `numeric_features`, float32 | `[..., is_dividend]` | Zero where mask is false. |
| `corporate_action_inputs.numeric_features.is_special_dividend` | Derived action flag | `numeric_features`, float32 | `[..., is_special_dividend]` | Zero where mask is false. |
| `corporate_action_inputs.time_features` | Available timestamp and origin timestamp | absolute UTC plus relative features | `[B, corporate_action_max_items, corporate_action_time_features]` | Zero where mask is false. |
| `corporate_action_inputs.effective_time_features` | Effective timestamp and origin timestamp | absolute UTC plus relative features | `[B, corporate_action_max_items, corporate_action_effective_time_features]` | Zero where mask is false. |
| `input_availability.event_context_available` | Event output mode | derived by loader | `[B]` bool | True when event output mode is not `none`. |
| `input_availability.intraday_labels_available` | Intraday label mask | derived by loader | `[B]` bool | True when any intraday horizon is available. |
| `input_availability.corporate_action_labels_available` | Corporate-action label arrays | derived by loader | `[B]` bool | True when any future corporate-action flag is true. |
| `input_availability.ticker_news_available` | Ticker news chunk mask | derived by loader | `[B]` bool | True when any ticker news chunk is valid. |
| `input_availability.market_news_available` | Market news chunk mask | derived by loader | `[B]` bool | True when any market news chunk is valid. |
| `input_availability.sec_filings_available` | SEC filing chunk mask | derived by loader | `[B]` bool | True when any SEC filing chunk is valid. |
| `input_availability.xbrl_available` | XBRL mask | derived by loader | `[B]` bool | True when any XBRL row is valid. |
| `input_availability.ticker_daily_bars_available` | Ticker daily bar mask | derived by loader | `[B]` bool | True when any ticker daily bar is valid. |
| `input_availability.global_daily_bars_available` | Global daily bar mask | derived by loader | `[B]` bool | True when any global daily bar is valid. |
| `input_availability.corporate_actions_available` | Corporate-action context mask | derived by loader | `[B]` bool | True when any corporate-action context row is valid. |

### Atomic Time Feature Components

| Time feature dimension | Appears in | Source timestamp | Representation | Mask rule |
| --- | --- | --- | --- | --- |
| `available_utc_second_of_day_sin` | text, XBRL, corporate-action available time | item/fact/action availability timestamp | float32 cyclic UTC feature | Zero when parent item mask is false. |
| `available_utc_second_of_day_cos` | text, XBRL, corporate-action available time | item/fact/action availability timestamp | float32 cyclic UTC feature | Zero when parent item mask is false. |
| `available_utc_day_of_week_sin` | text, XBRL, corporate-action available time | item/fact/action availability timestamp | float32 cyclic UTC feature | Zero when parent item mask is false. |
| `available_utc_day_of_week_cos` | text, XBRL, corporate-action available time | item/fact/action availability timestamp | float32 cyclic UTC feature | Zero when parent item mask is false. |
| `available_utc_day_of_year_sin` | text, XBRL, corporate-action available time | item/fact/action availability timestamp | float32 cyclic UTC feature | Zero when parent item mask is false. |
| `available_utc_day_of_year_cos` | text, XBRL, corporate-action available time | item/fact/action availability timestamp | float32 cyclic UTC feature | Zero when parent item mask is false. |
| `available_years_since_2000` | text, XBRL, corporate-action available time | item/fact/action availability timestamp | float32 years since 2000 | Zero when parent item mask is false. |
| `effective_utc_second_of_day_sin` | corporate-action effective time | action effective timestamp | float32 cyclic UTC feature | Zero when corporate-action mask is false. |
| `effective_utc_second_of_day_cos` | corporate-action effective time | action effective timestamp | float32 cyclic UTC feature | Zero when corporate-action mask is false. |
| `effective_utc_day_of_week_sin` | corporate-action effective time | action effective timestamp | float32 cyclic UTC feature | Zero when corporate-action mask is false. |
| `effective_utc_day_of_week_cos` | corporate-action effective time | action effective timestamp | float32 cyclic UTC feature | Zero when corporate-action mask is false. |
| `effective_utc_day_of_year_sin` | corporate-action effective time | action effective timestamp | float32 cyclic UTC feature | Zero when corporate-action mask is false. |
| `effective_utc_day_of_year_cos` | corporate-action effective time | action effective timestamp | float32 cyclic UTC feature | Zero when corporate-action mask is false. |
| `effective_years_since_2000` | corporate-action effective time | action effective timestamp | float32 years since 2000 | Zero when corporate-action mask is false. |
| `bar_start_utc_second_of_day_sin` | ticker/global daily bar time features | bar start timestamp | float32 cyclic UTC feature | Zero when bar mask is false. |
| `bar_start_utc_second_of_day_cos` | ticker/global daily bar time features | bar start timestamp | float32 cyclic UTC feature | Zero when bar mask is false. |
| `bar_start_utc_day_of_week_sin` | ticker/global daily bar time features | bar start timestamp | float32 cyclic UTC feature | Zero when bar mask is false. |
| `bar_start_utc_day_of_week_cos` | ticker/global daily bar time features | bar start timestamp | float32 cyclic UTC feature | Zero when bar mask is false. |
| `bar_start_utc_day_of_year_sin` | ticker/global daily bar time features | bar start timestamp | float32 cyclic UTC feature | Zero when bar mask is false. |
| `bar_start_utc_day_of_year_cos` | ticker/global daily bar time features | bar start timestamp | float32 cyclic UTC feature | Zero when bar mask is false. |
| `bar_start_years_since_2000` | ticker/global daily bar time features | bar start timestamp | float32 years since 2000 | Zero when bar mask is false. |
| `bar_age_days` | ticker/global daily bar time features | origin timestamp minus bar start | float32 nonnegative day age | Zero when bar mask is false. |
| `bar_age_days_log1p` | ticker/global daily bar time features | origin timestamp minus bar start | float32 `log1p(bar_age_days)` | Zero when bar mask is false. |
| `time_delta_seconds` | text, XBRL, corporate-action available/effective time | source timestamp minus origin timestamp | float32 signed seconds | Zero when parent item mask is false. |
| `time_delta_seconds_log1p_signed` | text, XBRL, corporate-action available/effective time | source timestamp minus origin timestamp | float32 signed `log1p(abs(delta))` | Zero when parent item mask is false. |
| `time_age_seconds_log1p` | text, XBRL, corporate-action available/effective time | origin timestamp minus source timestamp | float32 `log1p(max(0, age))` | Zero when parent item mask is false. |
| `period_end_utc_day_of_week_sin` | XBRL period-end time features | XBRL period end date | float32 cyclic UTC feature | Zero when XBRL mask is false. |
| `period_end_utc_day_of_week_cos` | XBRL period-end time features | XBRL period end date | float32 cyclic UTC feature | Zero when XBRL mask is false. |
| `period_end_utc_day_of_year_sin` | XBRL period-end time features | XBRL period end date | float32 cyclic UTC feature | Zero when XBRL mask is false. |
| `period_end_utc_day_of_year_cos` | XBRL period-end time features | XBRL period end date | float32 cyclic UTC feature | Zero when XBRL mask is false. |
| `period_end_years_since_2000` | XBRL period-end time features | XBRL period end date | float32 years since 2000 | Zero when XBRL mask is false. |
| `period_end_age_days` | XBRL period-end time features | origin timestamp minus period end date | float32 nonnegative day age | Zero when XBRL mask is false. |
| `period_end_age_days_log1p` | XBRL period-end time features | origin timestamp minus period end date | float32 `log1p(period_end_age_days)` | Zero when XBRL mask is false. |

### Atomic Y Dimension Lifecycle

| Y dimension | Source field/window | Cache/storage representation | Loader representation | Loss/mask rule |
| --- | --- | --- | --- | --- |
| `intraday_labels.label_resolution_us` | Builder horizon routing policy | uint64 list column | `[B, H]` uint64 | Metadata only; defines target grid resolution. |
| `intraday_labels.label_grid_start_timestamp_us` | Next full grid bucket after origin | int64 UTC microsecond list column | `[B, H]` int64 | Metadata/audit only; label window start is inclusive. |
| `intraday_labels.label_grid_end_timestamp_us` | End of final selected future grid bucket | int64 UTC microsecond list column | `[B, H]` int64 | Metadata/audit only; label window end is exclusive. |
| `future_bar_values["trade"].open/close/high/low` | Future trade events in `[label_grid_start_timestamp_us, label_grid_end_timestamp_us)` inside same session | decoded float32 list columns `trade_open`, `trade_close`, `trade_high`, `trade_low` | `[B, H, 4]` float32 slice | Convert to normalized trade-price deltas; mask with `future_bar_masks["trade"]`. |
| `future_bar_values["trade"].size_sum` | Future trade events in the grid window | float32 list column `trade_size_sum` | `[B, H]` float32 slice | Train with `log1p`/scale normalization; mask with `future_bar_masks["trade"]`. |
| `future_bar_values["trade"].event_count` | Future trade events in horizon | uint64 list column `trade_event_count` | `[B, H]` float32 slice | Count loss; mask with `future_bar_masks["trade"]`. |
| `future_bar_values["quote_bid"].open/close/high/low` | Future quote bid stream in the grid window | decoded float32 list columns `quote_bid_open`, `quote_bid_close`, `quote_bid_high`, `quote_bid_low` | `[B, H, 4]` float32 slice | Convert to normalized bid-price deltas; mask with `future_bar_masks["quote_bid"]`. |
| `future_bar_values["quote_bid"].size_open/size_close/size_high/size_low` | Future quote bid sizes in the grid window | float32 list columns `quote_bid_size_*` | `[B, H, 4]` float32 slice | Train with `log1p`/scale normalization; mask with `future_bar_masks["quote_bid"]`. |
| `future_bar_values["quote_bid"].event_count` | Future quote events in horizon | uint64 list column `quote_bid_event_count` | `[B, H]` float32 slice | Count loss; mask with `future_bar_masks["quote_bid"]`. |
| `future_bar_values["quote_ask"].open/close/high/low` | Future quote ask stream in the grid window | decoded float32 list columns `quote_ask_open`, `quote_ask_close`, `quote_ask_high`, `quote_ask_low` | `[B, H, 4]` float32 slice | Convert to normalized ask-price deltas; mask with `future_bar_masks["quote_ask"]`. |
| `future_bar_values["quote_ask"].size_open/size_close/size_high/size_low` | Future quote ask sizes in the grid window | float32 list columns `quote_ask_size_*` | `[B, H, 4]` float32 slice | Train with `log1p`/scale normalization; mask with `future_bar_masks["quote_ask"]`. |
| `future_bar_values["quote_ask"].event_count` | Future quote events in horizon | uint64 list column `quote_ask_event_count` | `[B, H]` float32 slice | Count loss; mask with `future_bar_masks["quote_ask"]`. |
| `intraday_labels.last_event_timestamp_us` | Last future event timestamp in any family horizon | int64 list column | `[B, H]` int64 | Compatibility/diagnostic by default; zero when unavailable. |
| `intraday_labels.available` | Any future family exists and horizon stays inside session | bool list column | `[B, H]` bool | Compatibility mask; family masks are preferred for bar losses. |
| `intraday_labels.condition_halt_pause_flag` | Any selected halt/pause token in future horizon | bool list column | `[B, H]` bool | BCE-with-logits; mask with `available`. |
| `intraday_labels.condition_resume_flag` | Any selected resume token in future horizon | bool list column | `[B, H]` bool | BCE-with-logits; mask with `available`. |
| `intraday_labels.condition_news_risk_flag` | Any selected news-risk token in future horizon | bool list column | `[B, H]` bool | BCE-with-logits; mask with `available`. |
| `intraday_labels.condition_luld_limit_state_flag` | Any selected LULD/limit-state token in future horizon | bool list column | `[B, H]` bool | BCE-with-logits; mask with `available`. |
| `intraday_labels.ticker_news_arrival_flag` | Any ticker news item arrives in future horizon | bool list column | `[B, H]` bool | BCE-with-logits; mask with `available`. |
| `intraday_labels.sec_filing_arrival_flag` | Any SEC filing item arrives in future horizon | bool list column | `[B, H]` bool | BCE-with-logits; mask with `available`. |
| `future_intraday_bars.open/close/high/low/volume` | Loader compatibility projection from the trade family | not stored separately | `future_intraday_bars [B,H,5]` | Kept for older callers; v3 should train on `future_bar_values` instead. |
| `future_intraday_bar_mask` | Trade-family availability compatibility mask | not stored separately | `[B, H]` bool | Same as `future_bar_masks["trade"]`. |
| `corporate_action_labels.future_split_flag` | Split effective date in future daily horizon | bool list column ordered by `horizon_days` | `[B, D]` bool | BCE-with-logits over daily horizons. |
| `corporate_action_labels.future_reverse_split_flag` | Reverse split effective date in future daily horizon | bool list column ordered by `horizon_days` | `[B, D]` bool | BCE-with-logits over daily horizons. |
| `corporate_action_labels.future_forward_split_flag` | Forward split effective date in future daily horizon | bool list column ordered by `horizon_days` | `[B, D]` bool | BCE-with-logits over daily horizons. |
| `corporate_action_labels.future_dividend_ex_flag` | Dividend ex-date in future daily horizon | bool list column ordered by `horizon_days` | `[B, D]` bool | BCE-with-logits over daily horizons. |
| `corporate_action_labels.future_special_dividend_ex_flag` | Special dividend ex-date in future daily horizon | bool list column ordered by `horizon_days` | `[B, D]` bool | BCE-with-logits over daily horizons. |
| `corporate_action_labels.future_any_corporate_action_flag` | Any supported corporate action in future daily horizon | bool list column ordered by `horizon_days` | `[B, D]` bool | BCE-with-logits over daily horizons. |
| `corporate_action_label_days` | Builder configuration | package metadata/list column order | tuple of day horizons | Defines `D`; default `1,2,3,7,28`. |

### X Input Lifecycle

| Data piece | Source | Builder/cache representation | Loader output | Model/trainer handling |
| --- | --- | --- | --- | --- |
| Sample identity | Event origin from `market_sip_compact.events` | `origins_part_*.parquet` with `ticker_id`, `ticker`, `origin_ordinal`, `origin_timestamp_us`, origin session fields, and source part identity | `ticker`, `ticker_id`, `origin_ordinal`, `origin_timestamp_us`, `source_part_key`, row/source indexes | Used for audit, checkpoint accounting, reproducible shuffling, and joining labels/context. Not a default model feature unless explicitly requested. |
| Raw quote/trade events | SIP quote/trade flatfiles ingested into `events` | `events_part_*.parquet`; quote rows use primary=ask and secondary=bid; trade rows use primary=trade and secondary=0 | Raw event windows when `event_output_mode=raw_windows`: `[B, C, W]` structured arrays/columns | Event encoder consumes the selected columns. Suppressed columns such as ticker id, ordinal, or timestamp can be dropped from model input while still remaining available for audit. |
| Event prices | Flatfile bid/ask/trade price | Stored as compact integer prices: `price_primary_int`, `price_secondary_int`; scale bits are packed in `event_meta` | Raw event windows keep the integer prices plus `event_meta` | Event encoder can learn from compact representation or decode internally if configured. Future price labels are decoded separately by the builder; do not assume event-window prices are float price levels. |
| Event sizes | Flatfile bid size, ask size, trade size | Stored as `float32` `size_primary` and `size_secondary`; quote primary=ask size, quote secondary=bid size, trade primary=trade size, trade secondary=0 | Raw event windows include `size_primary`, `size_secondary` | No event-codec scale bit exists for size. Apply model-side normalization such as `log1p`, clipping, or standardization if needed. |
| Event type, exchanges, conditions | Flatfile quote/trade row, exchange fields, conditions, indicators | `event_type`, `exchange_primary`, `exchange_secondary`, and dense condition-token columns `condition_token_1..5` from `event_condition_token_reference` | Raw event windows include these categorical/token fields | Encoded with categorical/token embeddings. Unknown/missing condition token is `0`. |
| Event absolute time features | Event `timestamp_us` | UTC cyclic calendar features plus session fields are stored next to each event | Included as event columns when requested and not suppressed | Used by event encoder as ordinary numeric features. Origin-relative event age is computed by the loader only when the requested event mode needs it. |
| Event-window index | Origin ordinal and configured coverage parameters | `event_window_index_part_*.parquet` plus cached event lookback rows | Loader selects contiguous windows by origin and requested coverage | Integrity check requires ordinal-contiguous windows. Coverage can be changed at loader time only within cached lookback capacity. |
| Daily ticker bars | Daily bar ClickHouse table built after event ingestion | `daily_bars.parquet` contains only completed historical daily bars needed by cache parameters | `daily_bar_inputs` / bar context arrays when `daily_bars` is requested | Used by bar encoder. Loader does not expose an incomplete current-day daily bar as context. |
| Intraday backward trade bars | Same-session trade events before each origin | `intraday_base_bars.parquet` compact sparse bars; optional redundant `intraday_context_bars_part_*.parquet` only with `--materialize-intraday-context-bars` | `bar_inputs["ticker_intraday_bars"]["trade_values"] [B,H,6]`, mask `[B,H]` | X context only. Default cache stores compact bars once per ticker/month so the loader can materialize requested backward horizons without writing origin-level duplicates. |
| Intraday backward quote bid bars | Same-session quote events before each origin | `intraday_base_bars.parquet` compact sparse bars | `bar_inputs["ticker_intraday_bars"]["quote_bid_values"] [B,H,9]`, mask `[B,H]` | Same schema as future bid bars, clipped backward to session start with no lookahead. |
| Intraday backward quote ask bars | Same-session quote events before each origin | `intraday_base_bars.parquet` compact sparse bars | `bar_inputs["ticker_intraday_bars"]["quote_ask_values"] [B,H,9]`, mask `[B,H]` | Same schema as future ask bars, clipped backward to session start with no lookahead. |
| Global daily bars | Global/macro daily bar ClickHouse tables | `global/global_daily_bars.parquet` | Global bar context arrays when `global_daily_bars` is requested | Used by global/bar encoder. These are cheap and stored once per month. |
| Ticker news embeddings | Precomputed Qwen embedding table for ticker-linked news | `ticker_news_embeddings.parquet` with month rows plus latest prior items; item timestamps and time features are stored | `text_inputs["ticker_news"]` with embeddings, item/chunk masks, timestamps, and time features | Text encoder consumes embeddings directly. Missing history is zero padded and masked. No raw text or Qwen inference happens in the loader. |
| Market news embeddings | Precomputed Qwen embedding table for all market news | `global/market_news_embeddings.parquet` with ticker `__MARKET__`; month rows plus latest prior items | `text_inputs["market_news"]` with embeddings, masks, timestamps, and time features | Same embedding path as ticker news. Market news means all news, not only rows without tickers. |
| SEC filing embeddings | Precomputed Qwen embedding table for SEC filing text chunks | `sec_filing_embeddings.parquet` with month rows plus latest prior filing items | `text_inputs["sec_filings"]` with embeddings, masks, timestamps, and time features | Text encoder consumes embeddings. Missing history is masked and zero padded. |
| XBRL facts | Normalized SEC/XBRL tables joined with category reference ids | `xbrl.parquet` with latest prior fact rows plus month rows; categorical ids are fixed reference-table ids | `xbrl_inputs` arrays: value/numeric fields, category ids, masks, available-time features, and period-end time features | XBRL encoder consumes numeric facts, categorical embeddings, and masks. Id `0` means missing/unknown. |
| Corporate-action context | `q_live` corporate-action reference tables | `corporate_actions.parquet` with available/effective timestamps, action ids, numeric fields, and time features | `corporate_action_inputs` arrays with masks, ids, numeric features, available/effective timestamps, and time features | Corporate-action encoder consumes as-of context only where `available_timestamp_us <= origin_timestamp_us`. |
| Availability masks | Derived from context row availability and padding | Empty or short context is represented by fewer rows plus metadata | Loader emits explicit masks for text, XBRL, corporate actions, bars, and labels | Model must use masks so zero padding is not interpreted as real data. |

### Y Label Lifecycle

| Label group | Source | Builder/cache representation | Loader output | Loss/model handling |
| --- | --- | --- | --- | --- |
| Intraday future trade bars | Future trade events in `events` for the same ticker and same New York session day | `intraday_forward_labels_part_*.parquet` list columns `trade_open`, `trade_close`, `trade_high`, `trade_low`, `trade_size_sum`, `trade_event_count`, `trade_available` | `future_bar_values["trade"] [B,H,6]`, `future_bar_masks["trade"] [B,H]`, plus compatibility `future_intraday_bars` | Trainer converts decoded trade prices to normalized deltas and sizes/counts to scale-stable targets. |
| Intraday future quote bid bars | Future quote events in the same horizon | List columns `quote_bid_open`, `quote_bid_close`, `quote_bid_high`, `quote_bid_low`, `quote_bid_size_open`, `quote_bid_size_close`, `quote_bid_size_high`, `quote_bid_size_low`, `quote_bid_event_count`, `quote_bid_available` | `future_bar_values["quote_bid"] [B,H,9]`, `future_bar_masks["quote_bid"] [B,H]` | Bid-side price, size, and count losses use the bid family mask. |
| Intraday future quote ask bars | Future quote events in the same horizon | List columns `quote_ask_open`, `quote_ask_close`, `quote_ask_high`, `quote_ask_low`, `quote_ask_size_open`, `quote_ask_size_close`, `quote_ask_size_high`, `quote_ask_size_low`, `quote_ask_event_count`, `quote_ask_available` | `future_bar_values["quote_ask"] [B,H,9]`, `future_bar_masks["quote_ask"] [B,H]` | Ask-side price, size, and count losses use the ask family mask. |
| Intraday label availability | Session boundary and presence of any future family event in horizon | `available` compatibility list column; false when horizon crosses the 20:00 New York session end or no event exists | `intraday_labels["available"] [B,H]` | Compatibility mask. Prefer family masks for family bar losses. |
| Intraday future event-state flags | Future event condition tokens resolved from `event_condition_token_reference` | Binary list columns for halt/pause, resume, news-risk, and LULD/limit-state flags | `intraday_labels["condition_*_flag"] [B,H]` | BCE-with-logits classification heads, masked by `available`; positive weights should be configurable because events are sparse. |
| Future news/SEC arrival flags | Ticker news and SEC embedding/context tables using availability timestamps | Binary list columns `ticker_news_arrival_flag`, `sec_filing_arrival_flag` for whether at least one item arrives in the future horizon | `intraday_labels["ticker_news_arrival_flag"] [B,H]`, `intraday_labels["sec_filing_arrival_flag"] [B,H]` | BCE-with-logits event-risk heads, masked by `available`. Counts are intentionally not emitted by default. |
| Future intraday bar projection | Trade-family intraday label columns | Not stored separately; derived by loader from `future_bar_values["trade"]` | `future_intraday_bars [B,H,5]` with order `open, close, high, low, volume` | Compatibility tensor only. New v3 heads should use `future_bar_values`. |
| Corporate-action daily labels | Corporate-action reference tables and effective dates | `corporate_action_daily_labels_part_*.parquet` list columns ordered by `horizon_days` | `corporate_action_labels[field] [B,D]` and `corporate_action_label_days` | Daily BCE-with-logits heads for split, reverse split, forward split, dividend ex-date, special dividend ex-date, and any corporate action. |

The cache deliberately separates source-preserving storage from model-specific
normalization. Builder and loader outputs should remain auditable against
ClickHouse and source files; model-side transforms such as price-delta
conversion, `log1p(size)`, clipping, or standardization belong in the trainer or
versioned model data adapter.

## Build Cache

### Streaming CPU Fast Path

The preferred experimental builder for large workstation runs is:

```powershell
python -m research.mlops.rolling_loader.run_build_ticker_month_cache_streaming `
  --month 2019-09 `
  --cache-id train_201909_ticker_month_streaming `
  --resume
```

For a period, the script builds only complete months inside the requested
range:

```powershell
python -m research.mlops.rolling_loader.run_build_ticker_month_cache_streaming `
  --start-utc 2019-02-01T00:00:00Z `
  --end-utc 2020-01-01T00:00:00Z `
  --cache-id train_201902_201912_ticker_month_streaming `
  --resume
```

This script is built around the physical layout of yearly `events_YYYY` tables:

| Stage | What happens |
| --- | --- |
| Month plan | Reads `events_ticker_day_index`, dedupes `ReplacingMergeTree` rows with `argMax(..., built_at)`, and creates one ticker/month plan. |
| Warmup plan | For each ticker chunk, subtracts `max_cached_event_lookback_rows` from the first origin ordinal and uses the day index to find every source day needed for that ordinal range. Warmup can cross month boundaries. |
| Event fetch | Fetches many small `ticker + ordinal BETWEEN` chunks from ClickHouse using the event-date bounds from the day index and the resolved yearly event table. |
| CPU processing | Builds origins and raw event-window indices in Python/Polars/NumPy after each small event frame arrives. |
| Context fetch | Fetches ticker news, SEC embeddings, XBRL, daily bars, and corporate actions as package-level time-ordered streams. These streams include prior history and are not materialized per origin. |
| Finalize | Concats and deduplicates fetched event rows for the ticker package, builds compact intraday base bars and sparse condition-event streams in Polars, writes package manifests, and atomically moves the package into place. |

Context semantics are important: the builder stores streams; the loader resolves
context per origin with as-of logic. A context row is available for an origin
only when its availability timestamp is not after `origin_timestamp_us`. Missing
or short context is represented by masks and zero padding at loader output time.

The default concurrency is workstation-oriented:

```text
fetch workers                  8
process workers               48
context workers               24
finalize workers              16
max inflight fetches          48
max inflight process tasks   128
target origin rows/fetch  500000
ClickHouse max_threads         8 per query
ClickHouse memory cap        120G per query
```

Use `--ticker-limit` or `--tickers` for quick tests. The Rich terminal uses the
same non-blinking dashboard renderer as the original ticker/month builder and
shows the `plan`, `fetch`, `process`, `context`, and `finalize` lanes. Ctrl+C
logs an interrupt message, cancels tracked ClickHouse query ids for this
process, writes an interrupted root manifest, and leaves completed packages
intact.

Workstation form:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\run_build_ticker_month_cache.py `
  --month 2019-02 `
  --cache-id train_201902_ticker_month
```

Laptop/module form:

```powershell
python -m research.mlops.rolling_loader.run_build_ticker_month_cache `
  --month 2019-02 `
  --cache-id train_201902_ticker_month
```

Build every complete month inside a period:

```powershell
python -m research.mlops.rolling_loader.run_build_ticker_month_cache `
  --start-utc 2019-01-01T00:00:00Z `
  --end-utc 2019-04-15T00:00:00Z `
  --cache-id train_201901_201903_ticker_month
```

Partial months at period boundaries are ignored.

Refresh only text-embedding/XBRL context for an existing cache:

```powershell
python -m research.mlops.rolling_loader.run_build_ticker_month_cache `
  --month 2019-02 `
  --cache-id train_201902_ticker_month `
  --refresh-context-only `
  --skip-final-audit
```

This mode discovers existing ticker packages for the month and rewrites only:

```text
global/market_news_embeddings.parquet
global/category_references.parquet
ticker_news_embeddings.parquet
sec_filing_embeddings.parquet
xbrl.parquet
```

It preserves event, origin, event-window, label, and daily-bar files.

### Builder Defaults

Defaults are tuned for the 128-core / 512GB workstation and target practical
throughput without flooding ClickHouse:

```text
package workers                    64
max inflight packages              96
event fetch workers                 6
context fetch workers              16
label fetch workers                 6
CPU workers                        16
write workers                       8
audit workers                       2
ClickHouse max_threads              8 per query
ClickHouse memory cap             120G per query
max cached event lookback rows   8192
max origin events per part     500000
ClickHouse query retries            2 for transient HTTP read failures
ticker news prior items            64
market news prior items           512
SEC filing prior items             32
XBRL prior rows                  4096
macro lookback days               400
label lookahead days              400
corporate action lookback days   3650
corporate action items            128
```

Large liquid tickers are split by ordinal into physical parts. The default part
size is intentionally below the old 2,000,000-origin setting because intraday
label queries can return very large Arrow responses. Keeping parts smaller
reduces the chance of broken HTTP reads and makes retries cheaper.

If the workstation is quiet and ClickHouse has headroom, the first override to
test is usually:

```powershell
--label-fetch-workers 8
```

The default intraday base-bar table can be overridden with:

```powershell
--intraday-base-bars-table intraday_base_bars_by_time_ticker
```

The default sparse condition-event and auxiliary status tables can be
overridden with:

```powershell
--intraday-condition-events-table intraday_condition_events_by_time_ticker
--intraday-aux-build-status-table intraday_aux_build_status
```

Use `--skip-intraday-base-bar-build` only when that table is already populated
for every local-session day in the requested month. Label and intraday-context
queries still read the table either way.

The Rich terminal separates high-level work into lanes. The most important
builder lanes are:

| Lane | Meaning |
| --- | --- |
| `event` | Raw event part fetches for ticker/month packages. |
| `context` | Text embedding, XBRL, daily bar, and corporate-action context reads. |
| `bar` | Shared intraday base-bar package reads. |
| `condition` | Month-level sparse condition-event build and package condition-event reads. |
| `label` | Optional materialized/debug label work and per-ticker base-bar build checks. |
| `cpu` | In-memory origin/window index construction. |
| `write` | Parquet package writes. |

### Builder Logging

The builder writes these files under the cache root:

```text
terminal.log
builder_events.jsonl
builder_profile_events.jsonl
errors.jsonl
train_progress.json
manifest.json
```

The Rich terminal is heartbeat-driven, so progress continues updating while
the main thread waits on long ClickHouse futures. Ctrl+C requests a graceful
stop, cancels tracked active ClickHouse query ids, writes an interrupted
manifest, and keeps completed package directories intact.

### Rerun And Resume

Normal rerun:

```text
rebuild and atomically replace existing ticker packages
```

Use `--resume` only when you intentionally want to reuse completed package
directories from a compatible interrupted build.

### Builder Audits

Two audit layers are active by default.

Inline part audit:

```text
--inline-audit-samples-per-part 2
```

This checks two deterministic random origins from each part using only data
already loaded in memory. It catches origin/window mismatch, ordinal gaps,
missing labels, wrong label-origin alignment, and forward-label horizon
violations before files are written.

Final audit:

```text
audit_ticker_month_cache.py
```

This runs after all requested month packages finish unless
`--skip-final-audit` is passed.

## Rolling Data Loader

The loader reads the ticker/month SSD cache and emits trainer-ready batches.
It is implemented in:

```text
ticker_month_dataset.py
```

Main classes:

```text
TickerMonthLoaderConfig
TickerMonthCacheIndex
TickerMonthPartReader
TickerMonthBatchMaterializer
AsyncTickerMonthBatchLoader
TickerMonthTrainingBatch
```

### Loader Flow

1. Read the cache root manifest.
2. Discover complete `(month, ticker, part)` packages.
3. Read only the origin index for a small group of packages.
4. Build local sample refs over `(month, ticker, part_id, origin_row)`.
5. Filter origins by requested training period when `start_utc/end_utc` are
   provided.
6. Apply deterministic dataset sampling or hash buckets.
7. Load event, label, and context payload files only for packages that have
   selected origins.
8. Shuffle inside the loaded group for non-stream modes. In `raw_stream` mode,
   keep each package's origins in ordinal order so the trainer sees continuous
   sliding samples from the loaded part.
9. Materialize CPU batches with a bounded worker queue.
10. Yield batches to the trainer.

This gives useful training randomness while keeping SSD reads mostly
sequential and package-local.

The origin-first path matters for small repeatable benchmark sets. A sparse
`sample_fraction` should not force the loader to read large event parquet files
for packages that contribute no sampled origins.

### Loader Inputs

Important `TickerMonthLoaderConfig` fields:

```text
cache_root
split
start_utc
end_utc
months
tickers
batch_size
seed
data_groups
event_output_mode
events_per_window
event_stream_length
event_stream_chunk_size
context_chunks
context_stride_events
flat_coverage_events
loaded_parts_per_group
read_workers
materialize_workers
max_batches
event_columns
suppress_event_columns
dataset_id
randomize_seed
sample_fraction
sample_hash_modulus
sample_hash_buckets
max_origins_per_epoch
materialize_chunk_size
drop_last_batch
preserve_batch_order
```

For the stateful dataset-plan and checkpoint contract, see:

```text
STATEFUL_TICKER_MONTH_LOADER_GUIDE.md
```

### Data Groups

Use `data_groups` to avoid loading files the objective does not need.

Common values:

```text
events
intraday_labels
ticker_news_embeddings
market_news_embeddings
sec_filing_embeddings
xbrl
daily_bars
global_daily_bars
intraday_bars
corporate_actions
corporate_action_labels
```

Examples:

```text
events
events,intraday_labels
ticker_news_embeddings,market_news_embeddings,sec_filing_embeddings
events,intraday_labels,corporate_action_labels,intraday_bars,daily_bars,global_daily_bars
```

For event-only pretraining, use:

```powershell
--data-groups events --event-output-mode raw_stream
```

For identity/context experiments with no event tensor:

```powershell
--event-output-mode none
```

### Event Output Modes

The default is raw continuous streams, not encoded chunks.

```text
raw_stream
  one dense float32 tensor shaped [B, event_stream_length, F]
  each row is the continuous event sequence ending at the origin event

raw_windows
  per-column arrays shaped [B, context_chunks, events_per_window]

raw_flat
  per-column arrays shaped [B, coverage_events]

encoded_uint8
  compatibility path:
  headers_uint8 [B, context_chunks, 14]
  events_uint8  [B, context_chunks, 128, 16]

none
  no event tensor materialization
```

Use `encoded_uint8` only for older trainer paths that still consume the old
market-encoder byte tensors.

`raw_stream` is the preferred training path. The builder saves ordered event
tables and an origin index. The loader reads origins from the origin index,
not from every event row, because some events are not training origins. For each
origin it uses `event_row_offset` to gather:

```text
events[event_row_offset - event_stream_length + 1 : event_row_offset + 1]
```

The loader validates that the last gathered event ordinal equals
`origin_ordinal` and that the stream is ordinal-contiguous. If either check
fails, the batch fails instead of training on misaligned data.

Raw event outputs are projected at loader time, not build time. The cache keeps
the richer reusable event table, while each trainer can choose the columns it
needs. By default the raw loader suppresses debug/identity fields that are
already exposed separately:

```text
ticker_id
ordinal
timestamp_us
```

Use `event_columns` for an exact allow-list, or `suppress_event_columns` to
remove a few cached columns from the default set. If `event_columns` is set, the
allow-list is emitted exactly and the suppression list is ignored. For example:

```powershell
--event-columns event_meta,price_primary_int,price_secondary_int,size_primary,size_secondary,exchange_primary,exchange_secondary,condition_token_1,condition_token_2,condition_token_3,condition_token_4,condition_token_5,session_second
```

### Coverage Compatibility

The loader validates requested coverage against the package manifest.

For raw/encoded event modes:

```text
requested_coverage <= max_cached_event_lookback_rows
```

If requested coverage is larger, the loader fails fast and tells you to rebuild
the cache with a larger `--max-cached-event-lookback-rows`.

### Loader Output

`TickerMonthTrainingBatch` contains:

```text
ticker
origin_ordinal
origin_timestamp_us
event_output_mode
raw_event_windows
raw_event_flat
raw_event_stream
raw_event_stream_feature_names
raw_event_mask
headers_uint8
events_uint8
intraday_labels
corporate_action_labels
corporate_action_label_days
future_intraday_bars
future_intraday_bar_mask
input_availability
text_inputs
xbrl_inputs
corporate_action_inputs
bar_inputs
external_context
profile
```

Only fields requested by `data_groups` and `event_output_mode` are populated.

When text embedding data groups are requested, `text_inputs` contains:

```text
text_inputs["ticker_news"]["embeddings"]     [B, ticker_news_max_items, ticker_news_token_chunks, text_embedding_dim]
text_inputs["market_news"]["embeddings"]     [B, market_news_max_items, market_news_token_chunks, text_embedding_dim]
text_inputs["sec_filings"]["embeddings"]     [B, sec_filing_max_items, sec_filing_token_chunks, text_embedding_dim]
text_inputs[*]["chunk_mask"]                 [B, max_items, token_chunks]
text_inputs[*]["item_mask"]                  [B, max_items]
text_inputs[*]["item_timestamp_us"]          [B, max_items]
text_inputs[*]["item_time_features"]         [B, max_items, text_time_features]
text_inputs[*]["item_time_feature_names"]    names for item_time_features
```

Selection is as-of each origin timestamp. The loader takes the latest embedded
items with `timestamp_us <= origin_timestamp_us`, fills available chunks by
`token_chunk_index`, and leaves missing items/chunks as zero with false masks.
Default limits are:

```text
ticker_news_max_items: 8
market_news_max_items: 16
sec_filing_max_items: 4
xbrl_max_items: 4096
text_embedding_dim: 1024
```

When `xbrl` is requested, `xbrl_inputs` contains one array per XBRL attribute:

```text
xbrl_inputs["mask"]                         [B, xbrl_max_items]
xbrl_inputs["value"]                        [B, xbrl_max_items]
xbrl_inputs["fiscal_year"]                  [B, xbrl_max_items]
xbrl_inputs["period_end_days"]              [B, xbrl_max_items]
xbrl_inputs["fiscal_period_id"]             [B, xbrl_max_items]
xbrl_inputs["calendar_period_id"]           [B, xbrl_max_items]
xbrl_inputs["taxonomy_id"]                  [B, xbrl_max_items]
xbrl_inputs["tag_id"]                       [B, xbrl_max_items]
xbrl_inputs["unit_id"]                      [B, xbrl_max_items]
xbrl_inputs["form_id"]                      [B, xbrl_max_items]
xbrl_inputs["row_kind_id"]                  [B, xbrl_max_items]
xbrl_inputs["location_id"]                  [B, xbrl_max_items]
xbrl_inputs["mapping_confidence"]           [B, xbrl_max_items]
xbrl_inputs["time_*"]                       [B, xbrl_max_items]
xbrl_inputs["time_features"]                [B, xbrl_max_items, xbrl_time_features]
xbrl_inputs["time_feature_names"]           names for time_features
xbrl_inputs["period_end_time_features"]     [B, xbrl_max_items, xbrl_period_time_features]
xbrl_inputs["period_end_time_feature_names"] names for period_end_time_features
```

Selection is as-of each origin timestamp, using the latest XBRL rows with
`timestamp_us <= origin_timestamp_us`. Missing rows are zero-filled and masked
with `xbrl_inputs["mask"] == False`. New caches read categorical ids directly
from ID columns stored in `xbrl.parquet`; older caches fall back to mapping
string fields through monthly `global/category_references.parquet`. Id `0`
means missing or unknown. Existing scalar `time_*` fields may remain for
compatibility, but new model code should prefer the consolidated
`time_features` tensor.

XBRL has two temporal meanings and they must stay separate:

```text
timestamp_us / time_features          when the fact became available from the source
period_end_date / period_end_features what accounting period the fact describes
```

When `corporate_actions` is requested, `corporate_action_inputs` contains the
latest as-of corporate-action rows with `available_timestamp_us <=
origin_timestamp_us`:

```text
corporate_action_inputs["mask"]                    [B, corporate_action_max_items]
corporate_action_inputs["action_type_id"]          [B, corporate_action_max_items]
corporate_action_inputs["dividend_type_id"]        [B, corporate_action_max_items]
corporate_action_inputs["currency_id"]             [B, corporate_action_max_items]
corporate_action_inputs["frequency_id"]            [B, corporate_action_max_items]
corporate_action_inputs["numeric_features"]        [B, corporate_action_max_items, corporate_action_numeric_features]
corporate_action_inputs["available_timestamp_us"]  [B, corporate_action_max_items]
corporate_action_inputs["effective_timestamp_us"]  [B, corporate_action_max_items]
corporate_action_inputs["time_features"]           [B, corporate_action_max_items, corporate_action_time_features]
corporate_action_inputs["effective_time_features"] [B, corporate_action_max_items, corporate_action_effective_time_features]
```

`action_type_id`, `dividend_type_id`, `currency_id`, and `frequency_id` come
from the append-only `training_category_reference` table under domain
`corporate_actions`; id `0` means missing or unknown. Numeric features include
split factors, log split factors, cash amount, log cash amount, and action-type
indicator bits. Available time controls no-lookahead selection. Effective time
describes when the split or ex-dividend event applies.

When `corporate_action_labels` is requested, `corporate_action_labels` contains:

```text
future_split_flag
future_reverse_split_flag
future_forward_split_flag
future_dividend_ex_flag
future_special_dividend_ex_flag
future_any_corporate_action_flag
```

Each field is shaped `[B, D]`, where `D == len(corporate_action_label_days)`.

No-lookahead selection uses only `timestamp_us`. `period_end_date`,
`fiscal_period`, and `calendar_period_code` are descriptive context and are not
used to decide whether the fact was available.

When daily/global bar groups are requested, `bar_inputs` contains:

```text
bar_inputs["ticker_daily_bars"]["trade_values"]     [B, ticker_daily_bar_offsets, 6]
bar_inputs["ticker_daily_bars"]["quote_bid_values"] [B, ticker_daily_bar_offsets, 9]
bar_inputs["ticker_daily_bars"]["quote_ask_values"] [B, ticker_daily_bar_offsets, 9]
bar_inputs["ticker_daily_bars"]["trade_mask"]       [B, ticker_daily_bar_offsets]
bar_inputs["ticker_daily_bars"]["quote_bid_mask"]   [B, ticker_daily_bar_offsets]
bar_inputs["ticker_daily_bars"]["quote_ask_mask"]   [B, ticker_daily_bar_offsets]
bar_inputs["global_daily_bars"]["trade_values"]     [B, global_symbols, global_daily_bar_offsets, 6]
bar_inputs["global_daily_bars"]["quote_bid_values"] [B, global_symbols, global_daily_bar_offsets, 9]
bar_inputs["global_daily_bars"]["quote_ask_values"] [B, global_symbols, global_daily_bar_offsets, 9]
bar_inputs["global_daily_bars"]["trade_mask"]       [B, global_symbols, global_daily_bar_offsets]
bar_inputs["global_daily_bars"]["quote_bid_mask"]   [B, global_symbols, global_daily_bar_offsets]
bar_inputs["global_daily_bars"]["quote_ask_mask"]   [B, global_symbols, global_daily_bar_offsets]
bar_inputs["ticker_daily_bars"]["time_features"] [B, ticker_daily_bar_offsets, bar_time_features]
bar_inputs["global_daily_bars"]["time_features"] [B, global_symbols, global_daily_bar_offsets, bar_time_features]
bar_inputs[*]["time_feature_names"]          names for time_features
bar_inputs[*]["offsets"]                     completed daily-bar row offsets
bar_inputs[*]["feature_names"]               compatibility union: open, close, high, low, size_sum, size_open, size_close, size_high, size_low, event_count
bar_inputs[*]["{family}_feature_names"]      canonical family-specific feature names
```

`bar_inputs[*]["values"]` and `bar_inputs[*]["mask"]` remain compatibility
aliases for the `trade` family using the 10-column union layout.

The default ticker context offsets are `1,2,3,7,14,28,40,200`. The default
global context offsets are `1,2,7`. These are X/context lookback offsets, not
daily price-label horizons. Bars are selected with a completion lag before they
become eligible, so a full current-day daily bar is not used for an intraday
origin.

Pivoted intraday label files are expected to have one row per saved origin.
The loader first checks whether label rows are already origin-row aligned. If
they are not, it builds a strict origin-ordinal to label-row map and gathers
only the label rows requested by the current materialization chunk.

## Profile Loader

Workstation form:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\run_profile_ticker_month_loader.py
```

The no-arg default is a repeatable sliding-stream benchmark over
`train_201902_201907_ticker_month`, month `2019-02`, with:

```text
dataset_id: bench_small_201902_v1
data_groups: events,intraday_labels,corporate_action_labels,intraday_bars,daily_bars,global_daily_bars,ticker_news_embeddings,market_news_embeddings,sec_filing_embeddings,xbrl,corporate_actions
event_output_mode: raw_stream
event_stream_length: 1024
sample_fraction: 1.0
max_origins_per_epoch: 1,000,000
batch_size: 4096
batches: 16
loaded_parts_per_group: 8
read_workers: 4
materialize_workers: 16
materialize_chunk_size: 512
report_path: D:\market-data\prepared\data_provider_profiles\ticker_month_loader_full_xy_xbrl_profile.jsonl
state_path: D:\market-data\prepared\data_provider_profiles\ticker_month_loader_full_xy_xbrl_state.json
audit_report_path: D:\market-data\prepared\data_provider_profiles\ticker_month_loader_full_xy_xbrl_batch_audit.json
```

Module form:

```powershell
python -m research.mlops.rolling_loader.run_profile_ticker_month_loader
```

Profile encoded compatibility mode:

```powershell
python -m research.mlops.rolling_loader.run_profile_ticker_month_loader `
  --cache-id train_201902_ticker_month `
  --month 2019-02 `
  --event-output-mode encoded_uint8 `
  --batch-size 4096 `
  --batches 8
```

The profiler prints:

```text
profile_seconds.loader_init_seconds
profile_seconds.origin_load_seconds
profile_seconds.sample_refs_seconds
profile_seconds.payload_load_seconds
profile_seconds.identity_seconds
profile_seconds.event_seconds
profile_seconds.raw_stream_validate_seconds
profile_seconds.raw_stream_matrix_seconds
profile_seconds.raw_stream_gather_seconds
profile_seconds.label_seconds
profile_seconds.xbrl_seconds
profile_seconds.context_seconds
profile_seconds.materialize_wait_seconds
profile_seconds.ready_concat_seconds
```

These timings are intentionally block-level so bottlenecks can be assigned to
SSD reads, origin filtering, stream matrix conversion, sliding-window gather,
label materialization, worker wait, or ready-buffer concatenation.

```text
discovered parts
batches
samples
elapsed seconds
samples/sec
materialization seconds
profile_seconds
max RSS
first batch shape summary
loader state summary
```

After the timing pass, the profiler runs the loader batch audit by default.
That means one no-arg profiler run now measures throughput and then checks the
emitted batches against SSD package files plus a small ClickHouse source sample.
The profiler appends a second JSONL record with `event: post_profile_audit`,
`audit_status`, `audit_ok`, and the audit report path.

Use profiling-only mode when measuring a hot loop repeatedly:

```powershell
--skip-audit
```

Keep the SSD-package audit but skip ClickHouse source queries:

```powershell
--skip-audit-source-clickhouse
```

The package-level cache audit validates future labels in three layers:

```text
deterministic fixture test for horizon/session-boundary behavior and final flag names
parquet-only invariants for compact label width, sorted horizons, binary flags, and no cross-session flags
sampled independent ClickHouse source checks for price/size/event labels, condition flags, ticker-news arrivals, and SEC-filing arrivals
```

The source-label checks intentionally use simple future-window filters instead
of the optimized cumulative/ASOF builder query. This keeps the audit independent
enough to catch label lookahead, horizon-boundary, condition-token, and
external-arrival alignment bugs.

Tune the post-profile audit size:

```powershell
--audit-batches 2 `
--audit-samples-per-batch 4 `
--audit-source-clickhouse-samples-per-batch 10
```

`profile_seconds` breaks emitted materialization time into
`identity_seconds`, `event_seconds`, `label_seconds`, `xbrl_seconds`, and
`context_seconds`.
Use it to identify whether the trainer is waiting on event-window gather,
intraday label gather, XBRL as-of materialization, or optional context work.

For fraction-only benchmark sampling, the loader uses a deterministic
vectorized per-part mask instead of hashing every origin in Python. Hash-bucket
splits still use the exact origin-hash path so train/validation membership stays
non-overlapping.

By default the profiler appends JSONL summaries to:

```text
D:\market-data\prepared\data_provider_profiles\ticker_month_loader_full_xy_xbrl_profile.jsonl
```

Override the report path with:

```powershell
--report-path D:\market-data\prepared\data_provider_profiles\ticker_month_loader_profile.jsonl
```

Disable report writing only when intentionally profiling terminal output:

```powershell
--no-report
```

Save a replayable loader checkpoint with:

```powershell
--save-state-path D:\market-data\prepared\data_provider_profiles\loader_state.json
```

The no-arg default already saves state to:

```text
D:\market-data\prepared\data_provider_profiles\ticker_month_loader_full_xy_xbrl_state.json
```

Resume the same dataset plan and cursor with:

```powershell
--load-state-path D:\market-data\prepared\data_provider_profiles\loader_state.json
```

The loader updates state before yielding each batch. A checkpoint saved
immediately after receiving a batch resumes after that batch, not at the same
origin again. If a batch completes a package group, `origin_cursor` may equal
the number of selected origins in that group until the iterator is resumed; on
resume the loader slices to empty, advances `package_position`, and continues
without repeating samples.

## Audit Loader Batches

Run a focused audit of emitted loader batches against the SSD package files:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\audit_ticker_month_loader_batches.py
```

The audit checks:

```text
batch shape consistency
duplicate sample identities
origin identity and timestamp against origins parquet
origin event_row_offset against events parquet
raw_stream values against source event rows
raw_stream ordinal continuity
intraday labels against label parquet
future_intraday_bars projection from labels
text embedding as-of selection
text embedding values against embedding parquet
text embedding item/chunk masks and zero padding
deterministic first batch for same config/seed
resume-from-state next batch against uninterrupted loading
```

Use small settings for a quick smoke audit:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\audit_ticker_month_loader_batches.py `
  --cache-id train_201902_201907_ticker_month `
  --month 2019-02 `
  --batch-size 1024 `
  --batches 2 `
  --samples-per-batch 4
```

By default the audit includes `ticker_news_embeddings`,
`market_news_embeddings`, and `sec_filing_embeddings`, so the no-arg audit
validates embedding tensor materialization as well as event and label
materialization.

## No-Lookahead Rules

The builder and loader must preserve these invariants:

- origins are inside the active session
- event streams/windows end at or before the origin
- event streams/windows never cross ordinal gaps
- text/SEC/XBRL context is selected as-of `origin_timestamp_us`
- labels are strictly future targets
- optional missing context is zero/masked, not silently treated as real data
- requested loader coverage must fit inside cached lookback
- invalid event windows are filtered or fail, not masked into training

## Programmatic Use

```python
from pathlib import Path

from research.mlops.rolling_loader import (
    AsyncTickerMonthBatchLoader,
    TickerMonthLoaderConfig,
)

config = TickerMonthLoaderConfig(
    cache_root=Path("D:/market-data/prepared/rolling_ticker_month_cache/train_201902_ticker_month"),
    split="train",
    months=("2019-02",),
    batch_size=4096,
    data_groups=("events", "intraday_labels", "ticker_news_embeddings", "market_news_embeddings", "sec_filing_embeddings"),
    event_output_mode="raw_stream",
    event_stream_length=1024,
)

loader = AsyncTickerMonthBatchLoader(config)
for batch in loader.iter_batches():
    train_step(batch)
```

## Legacy Stateful Replay

The original `RollingContextLoader` path is still available. It warms bounded
in-memory caches, replays chronological ClickHouse events, creates
`RollingSamplePointer` ids, and materializes batches at the final step. It is
useful as a production-semantics reference and for profiling live-like replay,
but it is not the recommended high-throughput historical training path.

Legacy profile:

```powershell
python -m research.mlops.rolling_loader.run_training_profile `
  --database market_sip_compact `
  --events-table events `
  --index-table train_2019_to_2025 `
  --batch-size 4096 `
  --batches 4 `
  --replay-mode time-window `
  --replay-window-us 200000 `
  --context-chunks 32 `
  --context-chunk-stride-events 64 `
  --sample-stride-events 1 `
  --start-utc 2019-01-05T00:00:00Z `
  --max-threads 8 `
  --max-memory-usage 80G `
  --materialize-external-payloads
```

## Smoke Tests

Package smoke:

```powershell
python -m research.mlops.rolling_loader.test_smoke
```

Direct workstation form:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\test_smoke.py
```

Use the ticker/month loader profiler for real cache throughput and shape
validation. Use the legacy smoke only for low-level in-memory cache mechanics.
