# QMD Design Guide

## Purpose

QMD is the application's market-data authority. It provides the same canonical
event-derived products to live/manual trading, automatic trading, paper
trading, Replay, Backtest, Backtest Debug, charting, scanning, causal-model
training, and future production inference.

Two processes intentionally run independently:

- `qmd-gateway` serves Live and Paper from Massive WebSocket events, rolling
  `q_live.events`, and historical warm-up data.
- `qmd-history-gateway` serves Replay, Backtest, and Backtest Debug from
  read-only `market_sip_compact.events_YYYY` tables.

They compile against the same `qmd_core` Rust library target. Source adapters,
clocks, persistence permissions, and delivery pacing differ. Event decoding,
bar construction, condition classification, macro aggregation, indicators,
cache semantics, and schemas must not differ.

## Product authority

```text
compact canonical events
        |
        +-- intraday family bars: trade, quote_bid, quote_ask
        +-- intraday condition bars: halt, resume, news risk, LULD
        +-- macro family bars: 1d, 1w, 1y
        +-- enriched feature projection
        +-- causal indicators
        `-- universe scanner frames
```

The chart does not own another candle builder. Its primary candle is the
canonical `trade` family row. `quote_bid` and `quote_ask` provide quote overlays,
spread and midpoint context. The enriched bar payload remains a compatibility
and feature projection, but its OHLC, volume, and trade count are reconciled
against canonical trade-family rows before chart delivery.

## Canonical intraday family-bar schema

The shared semantic row aligns with
`market_sip_compact.intraday_base_bars_by_time_ticker`:

| Field | Type | Meaning |
|---|---|---|
| `local_date` | date/string at wire boundary | New York trading date |
| `ticker` | uppercase string | Point-in-time event ticker |
| `label_resolution_us` | `UInt64` | Fixed bucket width |
| `bucket_index` | `UInt64` | Bucket from New York midnight |
| `bar_family` | enum/string | `trade`, `quote_bid`, or `quote_ask` |
| `open`, `close`, `high`, `low` | `Float32` | Family price aggregates |
| `size_sum`, `size_open`, `size_close`, `size_high`, `size_low` | `Float64` | Size aggregates |
| `event_count` | `UInt64` | Eligible events in the family bucket |
| `first_event_timestamp_us`, `last_event_timestamp_us` | `UInt64` | Source-event boundaries |

The API adds `schema_version`, resolved UTC `bar_start` and exclusive `bar_end`,
`as_of`, monotonic `revision`, and `state` (`partial`, `closed`, or
`corrected`) without changing the training fields.

Sizes remain floating point because the active compact-event and causal-loader
contracts use floating-point trade sizes. Quote acquisition currently
normalizes sizes through `UInt32`, while trade acquisition retains `Float32`.
Changing all sizes to unsigned integers requires a representative source-data
integrality audit and one versioned event/bar/model cutover. Aggregated sizes
use `Float64` to avoid accumulated `Float32` precision loss.

Live durable family rows use `q_live.intraday_family_bars_v2`. Version 2 changes
size aggregates to `Float64`, counts and event timestamps to `UInt64`, and keeps
the same three-family key. `intraday_bars_v1` is not the new default.

## Fixed session grid

All intraday bars use `America/New_York` and the half-open extended session
`[04:00:00, 20:00:00)`. A bucket is determined only by:

```text
local trading date + resolution_us + floor(local_time_us / resolution_us)
```

Ticker activity does not define boundaries. Independent historical chunks can
therefore update different buckets concurrently or in any arrival order.

The production grid is `100ms, 1s, 5s, 10s, 30s, 1m, 5m, 1h`. The
training-required subset is `100ms, 1s, 5s, 30s, 1m`. Additional resolutions
are deterministic rollups of the same family algebra. No timeframe-specific
ClickHouse event query is required after a session entry has been built.

## Bar lifecycle and causality

Family buckets are independent. Bars may be populated from concurrent or
out-of-order source chunks because open/close use source timestamp/sequence
order and high/low/sizes are reducible aggregates.

Indicators, scanner state, strategy events, broker fills, and model sequences
are causal. They advance only through an oldest-to-newest frontier.

1. The first eligible event creates a `partial` row.
2. Eligible events update all configured resolutions immediately.
3. A row becomes `closed` when the wall/replay/debug clock reaches its exclusive
   end, even if no later event arrives.
4. A late event changing a closed row emits a higher-revision `corrected` row.
5. Empty intervals are not fabricated. Coverage distinguishes known-empty from
   not-loaded periods.

Live uses the exchange/wall clock. Replay uses a paced cursor. Backtest uses an
unthrottled run clock. Debug uses a stepped event/bar clock. Fast-forward changes
pacing, never market-data semantics.

## Condition bars

Condition rows use the training column names exactly:

- `condition_halt_pause_flag`
- `condition_resume_flag`
- `condition_news_risk_flag`
- `condition_luld_limit_state_flag`
- `condition_event_count`
- first/last event timestamps

The shared classifier follows `clickhouse_build_intraday_base_bars.py`.
Production uses these rows for chart annotations, tradability, automatic entry
blocking, risk checks, replay parity, and Market AI context. A missing condition
row means no configured condition was observed; it does not mean price coverage
is missing.

## Macro family bars

Macro rows preserve the three families and the same OHLC/size/count algebra.
Closed historical `1d`, `1w`, and `1y` rows may hydrate from
`market_sip_compact.macro_bars_by_time_symbol` when source revision matches.
The shared core also rolls current macro rows from cached intraday rows:

- current day: closed intraday rows plus the current partial row
- current week/year: closed daily context plus the current partial day

All macro responses are evaluated as-of the active clock. Replay cannot expose
the final macro close before the cursor reaches it. `1mo` is not in the durable
contract and requires an explicit schema migration.

## Cache identity and memory bounds

The logical session identity is:

```text
ticker + requested event window + source revision + product schema version
```

Timeframe is not part of the historical derived-cache key. A cold build reads
events once and constructs every configured resolution, indicators, conditions,
and macro inputs. Timeframe changes reuse the entry.

Chart delivery is a bounded projection over that entry, not another cache. The
projection returns only the candle fields required by the browser plus causal
indicators when the enriched timeframe supports them. It never clones the
entry's complete derived-update vector merely to serve one page.

### Live product cache

The live cache is sharded by stable ticker hash. Each shard has an independent
async mutex. Limits are divided across shards; configured values are
service-wide ceilings:

| Variable | Default | Purpose |
|---|---:|---|
| `QMD_PRODUCT_CACHE_MAX_BYTES` | 512 MiB | Estimated family/condition row memory |
| `QMD_PRODUCT_CACHE_MAX_ROWS` | 2,000,000 | Absolute row ceiling |
| `QMD_PRODUCT_CACHE_MAX_PARTITIONS` | 8,192 | Ticker-day ceiling |

Eviction removes complete least-recently-used ticker-day partitions, preserving
internally consistent coverage. `GET /snapshot/product-cache` exposes rows,
partitions, bytes, limits, and evictions.

### Historical derived cache

Historical requests are single-flight by event window, ticker, source revision,
and engine versions. A semaphore bounds independent cold builds.

| Variable | Default | Purpose |
|---|---:|---|
| `QMD_HISTORY_CACHE_MAX_BYTES` | 1 GiB | Total estimated derived-cache memory |
| `QMD_HISTORY_CACHE_MAX_ENTRIES` | 256 | LRU entry ceiling |
| `QMD_HISTORY_CACHE_MAX_CONCURRENT_BUILDS` | 4 | Simultaneous cold builds |
| `QMD_HISTORY_CACHE_MAX_CONCURRENT_FETCHES` | 8 | Service-wide ClickHouse chunk queries |
| `QMD_HISTORY_FETCH_CHUNK_HOURS` | 24 | Source-query chunk width |
| `QMD_HISTORY_CACHE_MAX_UPDATES_PER_ENTRY` | 500,000 | Enriched updates per entry |
| `QMD_HISTORY_PRODUCT_CACHE_MAX_ROWS_PER_ENTRY` | 2,000,000 | Family/condition rows per entry |
| `QMD_HISTORY_MAX_EVENTS_PER_REQUEST` | 10,000,000 | Defensive source-scan ceiling |

Each build reserves at most half the byte budget for enriched updates and half
for canonical products. All entries share an atomic service-wide byte budget;
allocations that would cross it fail explicitly, and an evicted entry retains
its reservation until the last active lease releases it. Authoritative data is
never silently truncated. Completed entries are evicted by LRU until entry and
byte limits are satisfied. `/snapshot/cache` and `/health` expose active builds,
estimated/configured bytes, hits, misses, builds, entries, and evictions.

## Concurrency and backpressure

- Live WebSocket batches are parsed asynchronously and routed through bounded
  Tokio channels.
- Products, enriched bars, indicators, scanner primitives, condition state, and
  persistence are sharded or independently queued.
- Canonical persistence is loss-intolerant. A failed queue is operational error,
  not permission to drop data silently.
- UI streams may coalesce snapshots. Canonical event and backtest feeds remain
  event-ordered.
- Historical cold builds are asynchronously single-flighted and globally
  semaphore-limited. Long windows are split into fixed time chunks; a bounded
  per-build prefetch window overlaps ClickHouse reads under a second
  service-wide semaphore, while consumers drain chunks oldest-to-newest.
- Product snapshot source horizons are clamped to `min(end, as_of)`. Future
  events never enter a point-in-time entry, so no downstream filter is trusted
  to repair lookahead after aggregation.

## APIs

QMD Live:

```text
GET /snapshot/family-bars/{ticker}?resolution=1m&family=trade&price_only=true&limit=1500
GET /snapshot/condition-bars/{ticker}?resolution=1m&limit=1500
GET /snapshot/macro-bars/{ticker}?timeframe=1d&limit=500
GET /snapshot/product-cache
WS  /stream/family-bars/{ticker}?resolution=1m&family=trade&price_only=true&emit=full_then_updates
WS  /stream/condition-bars/{ticker}?resolution=1m
WS  /stream/macro-bars/{ticker}?timeframe=1d
```

QMD History adds point-in-time bounds:

```text
GET /snapshot/chart-bars/{ticker}?start=...&end=...&as_of=...&before=...&timeframe=1m&limit=5000
GET /snapshot/family-bars/{ticker}?start=...&end=...&as_of=...&resolution=1m
GET /snapshot/condition-bars/{ticker}?start=...&end=...&as_of=...&resolution=1m
GET /snapshot/macro-bars/{ticker}?start=...&end=...&as_of=...&timeframe=1d
```

`/snapshot/chart-bars` is the browser history contract. `as_of` clamps the
source horizon before aggregation, `before` is an exclusive fixed-bar cursor,
and `has_more` plus `next_before` drive lazy backward paging inside one session.
Live family-bar chart consumers set `price_only=true`. That projection filters
size-only trade buckets before applying `limit`, so condition-excluded prints
remain available in the canonical three-family cache without becoming zero-price
candles or displacing valid price bars. QMD History applies the identical
projection in `/snapshot/chart-bars`.
The response supports the full production intraday grid. The enriched
timeframes return aligned causal indicators; `100ms` and `5s` currently return
the canonical trade-family candle projection with `indicators_available=false`.
The frontend retains all explicitly requested pages and cancels obsolete
ticker/timeframe requests instead of allowing stale responses to overwrite a
new selection.

Existing `/snapshot/bars` and `/stream/bars` remain enriched compatibility
endpoints. Live chart requests for `100ms` and `5s` use filtered canonical
trade-family snapshots and streams, normalized to the same lean candle wire
shape. Other intraday chart timeframes use the enriched endpoints, whose price
fields reconcile to the canonical trade-family authority.

Historical `/stream/derived` supports `full`, `updates`, and
`full_then_updates`, sequence resume, one-step playback, paced replay, and
unthrottled backtest delivery.

## Mode responsibilities

| Mode | QMD guarantee |
|---|---|
| Manual Live | Current quote/trade, partial bars, conditions, scanner state, freshness, gaps, chart warm-up |
| Automatic Live | Ordered events, closed/corrected bars, causal indicators, backpressure and explicit stale failure |
| Paper | Identical live market truth with paper/simulated execution |
| Replay | Historical products at a paced run clock with no lookahead |
| Backtest | Deterministic maximum-speed products with source/schema versions |
| Backtest Debug | Event/bar stepping with revisions and provenance |
| Market AI | Canonical events plus family, condition, and macro context; model cache remains in Market AI |
| Manual + Automatic | One shared market truth for operator and strategies |

QMD never owns accounts, orders, fills, positions, portfolio accounting, or
strategy decisions. Those remain broker/runtime responsibilities.

## Source composition and provenance

QMD History reads only historical compact events and cannot connect to Massive
or write live state. QMD Live composes non-overlapping coverage from closed
historical sessions, recent `q_live.events`, and the Massive tail. Source
revision, event order, coverage, schema version, and corrections must be visible
in response evidence and reproducibility manifests. Warm-up history is snapshot
state; it is not re-emitted as newly arrived live events.

Both services aggregate the decoded canonical compact-event contract. Live
products are updated after encoding/sanitization in `compact_event.rs`, not from
the pre-canonical WebSocket object; History uses the same decoder over
`events_YYYY` rows.

## Future-safe invariants

- Canonical prices remain raw; corporate-action adjustment is a point-in-time
  view.
- Scanner rankings require a universe watermark.
- Automatic trading cannot silently fall back to a semantically different
  provider.
- Late events produce revisions, never silent loss.
- Cache and queue pressure is bounded and observable.
- Source/schema revision invalidates derived entries.
- Replay/live parity compares the same window through both adapters.
- Model tensors may cast to `Float32` only at the tensor boundary.

## Validation requirements

1. `cargo test --lib` in `services/qmd-gateway`.
2. `cargo test` in `services/qmd_history_gateway`.
3. Compare a covered session across all three families and condition rows.
4. Verify live/history equality under one source revision.
5. Check no-lookahead at a mid-session boundary.
6. Test partial-to-closed and closed-to-corrected transitions.
7. Prove row, byte, partition, entry, and concurrency bounds under pressure.
8. Run launcher `-CheckOnly` and health/cache endpoint checks before production.
