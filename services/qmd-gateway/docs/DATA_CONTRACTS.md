# QMD Gateway Data Contracts

This file documents the values produced by `qmd-gateway`. A **formula** is the exact calculation where the code has one. A **proxy** is a practical substitute for a value that would need data we do not have, such as level 2 depth.

## Schema Version Policy

| Contract | Version Field | Current Version | Rule |
|---|---|---:|---|
| Live compact unified events | `schema_version` | `4` | Increment when the live unified event table semantics change. |
| Raw Massive trades | `schema_version` | `1` | Increment when durable raw table semantics change. |
| Raw Massive quotes | `schema_version` | `1` | Increment when durable raw table semantics change. |
| Bars | `schema_version` | `2` | Increment when bar fields or formulas change. |
| Bar indicators | `schema_version` | `3` | Increment when persisted indicator fields or formulas change. |
| Deterministic microstructure forecast | `schema_version` | `2` | Increment when horizon or unified-action fields or formulas change. |
| Scanner primitives | `schema_version` | `1` | Increment when primitive output contract changes. |
| Live abnormal market-state events | `schema_version` | `1` | Increment when abnormal state event semantics change. |

Once production data is written under a version, do not change that version's field meaning. Add a new version or field.

## Live Compact Unified Event Row

Table: `events`

This is the durable live ML-serving event surface. It mirrors the historical
`market_sip_compact.events_YYYY` row shape so downstream encoders
can build the same `header_uint8 + events_uint8` chunks from either historical
or live rows. The gateway emits compact rows immediately on
`/stream/compact-events` and keeps a bounded per-ticker memory buffer exposed by
`/snapshot/compact-events/{ticker}?limit=128`. The historical
`market_sip_compact.events_YYYY` tables remain flatfile-only; QMD live events are
not merged into it.

Raw quote/trade tables are optional debug/replay support. They are not the
primary model-serving contract.

| Field | Meaning |
|---|---|
| `event_date` | UTC date from SIP timestamp, used for partitioning. |
| `schema_version` | Live compact event contract version. |
| `ingest_ts` | Gateway receive/parse timestamp. |
| `arrival_sequence` | Gateway-local monotonically increasing sequence. Used only as a deterministic tie-breaker for equal timestamp/sequence rows. |
| `ticker` | Uppercase ticker. |
| `event_meta` | Compact row metadata: bit 0 event type (`0 = quote`, `1 = trade`), bit 1 primary price scale, bit 2 secondary price scale, bits 3-5 tape, bits 6-7 reserved. |
| `sip_timestamp_us` | SIP timestamp in UTC microseconds. Massive websocket timestamps are millisecond precision, so live rows currently land on millisecond boundaries. |
| `price_primary_int` | Quote: ask price integer. Trade: trade price integer. |
| `price_secondary_int` | Quote: bid price integer. Trade: `0`. |
| `size_primary` | Quote: ask size. Trade: trade size. |
| `size_secondary` | Quote: bid size. Trade: `0`. |
| `exchange_primary` | Quote: ask exchange. Trade: trade exchange. |
| `exchange_secondary` | Quote: bid exchange. Trade: `0`. |
| `condition_token_1` ... `condition_token_5` | Quote: first four conditions plus first indicator. Trade: first five conditions. Tokens use the canonical historical reference; absent/unknown values encode as `0`. Overflow/unknown input is logged and audited with full identity while the first five slots continue. |
| `source_sequence` | Massive sequence number from the original quote/trade event. |
| `issue_flags` | Reserved for future issue classification. Current compact writer drops structurally invalid events before emit/insert, so persisted rows use `0`. |

Live ordering contract:

```text
sip_timestamp_us, source_sequence, event_type, arrival_sequence
```

The in-memory and persisted live paths use the same ordering tuple. Persistence
keeps a short per-ticker reorder buffer and inserts `q_live.events` without a
durable ordinal. Cross-store consumers sort each segment under its own contract,
concatenate a non-overlapping boundary, and may assign a query-local ordinal.

## Canonical Intraday Bars

`/stream/intraday-bars` and required table `intraday_family_bars_v2` expose the
long-form families used by `research/mlops/packed_market`: `trade`,
`quote_bid`, and `quote_ask`. Rows use New York 04:00-20:00 local-session
buckets and contain `schema_version`, `ticker`, `local_date`,
`label_resolution_us`, `bucket_index`, `bar_family`, price OHLC, size
sum/open/close/high/low, event count, first/last event timestamps, and session
bar bounds. Invalid zeroed prices do not enter a family bar, and empty family
rows are not fabricated.

The base resolution is 100,000 microseconds (`100ms`). Every higher resolution
is rolled up from closed base bars: first open, last close, maximum high,
minimum low, summed size and event count, and first/last timestamps. Defaults
are `100ms`, `1s`, `5s`, `10s`, `30s`, `1m`, `5m`, and `1h`; the packed
training subset is `100ms`, `1s`, `5s`, `30s`, and `1m`.

Coverage manifest: `qmd_market_coverage_manifest_v1`

This table is coarse and run-scoped. It records startup live event audits,
recent REST repair attempts, and historical flatfile update plans. It is not the
fine-grained source of truth for live time gaps.

| Field | Meaning |
|---|---|
| `started_at` | Maintenance run start time. |
| `finished_at` | Maintenance run finish or plan record time. |
| `coverage_kind` | `q_live_recent_events` or `historical_flatfile_events`. |
| `status` | Recent-live statuses include `up_to_date`, `awaiting_live_symbols`, `no_symbols_available`, `repair_completed`, `partial_page_limit`, `partial_failed`, `repair_failed`, and `needs_manual_rebuild`. Historical statuses include `up_to_date`, `planned`, `launched`, and `launch_failed`. |
| `start_ts_utc` | UTC start of the audited or planned coverage range. |
| `end_ts_utc` | UTC end of the audited or planned coverage range. |
| `action` | Startup or periodic action that wrote the row. |
| `rows_written` | Rows routed through recent REST repair when applicable. The compact-event DB writer persists them asynchronously after fan-out. |
| `host_role` | `workstation` or `laptop` after host-role resolution. |
| `command` | Historical flatfile update command when one is planned. |
| `summary_json` | JSON summary of audit counts, command planning, and messages. |

Live event coverage manifest: `qmd_live_event_coverage_v1`

This table is the fine-grained recent q_live coverage source. It is maintained
by the compact-event writer, the intraday-bar writer, and REST repair. Live streaming
does not become covered from a single `running` row. Coverage is materialized
as:

- `compact_persisted` intervals from `events` inserts.
- `intraday_bars_persisted` intervals from closed 100ms bar inserts.
- the intersection of compact and bar intervals for the same run id.
- explicit `repair_completed` intervals after REST repair routes events through
  the same fan-out and verifies bar persistence for that interval.
- the `coverage_bootstrap` rows used only for bootstrapped historical contracts.

Rows with `failed`, `partial_failed`, `partial_page_limit`, or `running` are
diagnostic. They are not counted as covered intervals. This prevents a compact
insert failure or bar insert failure from hiding a q_live time gap.

| Field | Meaning |
|---|---|
| `coverage_kind` | `q_live_events` for live compact/bar coverage or `flatfile_events` in the flatfile table. |
| `coverage_id` | Stable id for the row. Live confirmations use `compact_<run_id>` and `intraday_<run_id>`. REST repair rows use `repair_<run_id>_<started_ms>_<interval_index>`. |
| `source` | Writer or repair source, such as `qmd_compact_event_writer`, `qmd_intraday_bar_writer`, or `massive_rest_gap_repair`. |
| `status` | `compact_persisted`, `intraday_bars_persisted`, `repair_completed`, or diagnostic statuses. |
| `coverage_start_utc`, `coverage_end_utc` | UTC interval covered or diagnosed. |
| `rows_written`, `event_rows`, `bar_rows` | Writer-specific row counts. Repair rows store per-interval counts, not one repeated global count. |
| `error_count` | Nonzero when a diagnostic row records a failed or partial interval. |
| `metadata_json` | Per-run metadata including excluded raw tables and repair interval details. |

## Live Abnormal Market-State Event Row

Table: `live_symbol_market_event_v1`

This table is an exception/audit stream, not a full state table. QMD keeps the
current live state in memory and writes a row only when a predefined abnormal
state opens or closes. Ordinary `normal` state is intentionally not
persisted.

| Field | Meaning |
|---|---|
| `schema_version` | Live abnormal market-state contract version. |
| `event_id` | Stable run-scoped id from run id, ticker, event type, status, and source timestamp. |
| `ticker` | Uppercase Massive ticker. |
| `event_type` | Abnormal state family, for example `estimated_luld_near_upper`, `estimated_luld_breach_lower`, `locked_crossed_quote`, or `condition_halt`. |
| `event_status` | `opened` or `closed`; `updated` is reserved for future explicit throttled update rows. |
| `event_start_utc` | Source timestamp that opened the active abnormal state. |
| `event_end_utc` | Source timestamp that closed the state, otherwise null. |
| `source_event_ts_utc` | Quote/trade/bar timestamp that caused this transition. |
| `source_event_type` | `trade`, `quote`, or `bar`. |
| `source_conditions` | Raw Massive condition ids from the source quote/trade, if any. |
| `source_indicators` | Raw Massive quote indicator ids from the source quote, if any. |
| `severity` | `warning` or `critical`. |
| `is_live_tradability_blocking` | `1` when the live state should block new orders while active. |
| `block_reason` | Short machine-readable reason when blocking. |
| `evidence_json` | Minimal evidence needed to explain the transition; not a raw event dump. |
| `source_run_id` | QMD run id. |
| `inserted_at_utc` | Gateway insert-row creation time. |

Current default event families:

- estimated LULD near/breach states from closed 1s bars
- locked/crossed quote states from closed 1s bars
- configured halt/resume condition ids from quote/trade events

API contracts:

```text
GET /snapshot/live-market-state?limit=250
GET /snapshot/live-market-state/{ticker}?limit=250
WS  /stream/live-market-state
```

The live order/scanner decision should combine this overlay with reference
tradability and broker/account checks:

```text
reference_tradable AND routing_valid AND no active live blocking state
```

Price integer scale:

```text
scale=0: price_int = round(price * 100)
scale=1: price_int = round(price * 10000)
```

The writer uses `scale=1` when the price is below `$1` or when the value is not
cent-exact; otherwise it uses `scale=0`. This preserves sub-cent prices without
promoting all prices to 64-bit floats.

Condition packing:

Each `condition_token_N` column is one independent `UInt8`; tokens are not
bit-packed. Quotes use condition slots 1-4 and the first quote indicator in slot
5. Trades use conditions 1-5.

Tokens are loaded from
`market_sip_compact.event_condition_token_reference`; tape ids are loaded from
`market_sip_compact.ref_stock_tapes` and converted from source `1..3` to encoded
`0..2`, exactly as `download_update_events.py` does. Missing/unknown codes encode
as `0` and produce a structured `qmd_compact_event_issue_v1` audit row. More
than five usable slots also produces a warning/audit row, persists the first
five, and continues.

## Raw Massive Trade Row

Table: `live_massive_trades`

| Field | Meaning |
|---|---|
| `session_date` | Date from trade timestamp, used for partitioning. |
| `schema_version` | Raw trade contract version. |
| `ts` | SIP trade timestamp. SIP means the consolidated market data feed timestamp. |
| `participant_ts` | Exchange participant timestamp when Massive provides it. |
| `trf_ts` | Trade reporting facility timestamp when provided. |
| `ingest_ts` | Gateway receive/parse time. |
| `sym` | Uppercase ticker. |
| `trade_id` | Massive trade id. |
| `seq` | Massive sequence number. |
| `exchange` | Massive exchange code. |
| `tape` | Massive tape code. |
| `price` | Trade price. |
| `size` | Trade size in shares. |
| `conditions` | Trade condition codes. |
| `trf_id` | Trade reporting facility id. |
| `raw` | Original row payload as text. |

## Raw Massive Quote Row

Table: `live_massive_quotes`

| Field | Meaning |
|---|---|
| `session_date` | Date from quote timestamp, used for partitioning. |
| `schema_version` | Raw quote contract version. |
| `ts` | SIP quote timestamp. |
| `ingest_ts` | Gateway receive/parse time. |
| `sym` | Uppercase ticker. |
| `seq` | Massive sequence number. |
| `bid_exchange`, `ask_exchange` | Exchange codes for displayed bid/ask. |
| `bid_price`, `ask_price` | NBBO bid/ask prices from Massive quote event. |
| `bid_size`, `ask_size` | Displayed bid/ask sizes. |
| `conditions`, `indicators` | Massive quote condition/indicator codes. |
| `tape` | Massive tape code. |
| `raw` | Original row payload as text. |

## Memory-Only Enriched Bar Contract

The scanner, indicator, LULD-estimate, and `/snapshot/bars/{ticker}` path keeps
an enriched `BarRow` in memory. It is built directly from Massive events for
configured operational timeframes and is not persisted. The only durable bar
contract is `intraday_family_bars_v2` above. The fields below document the richer
operational row consumed inside QMD.

### Identity And Time

| Field | Source | Formula Or Rule |
|---|---|---|
| `schema_version` | constant | Current value is `2`. |
| `session_date` | bar start | `bar_start.date`. |
| `timeframe` | config | One of configured labels such as `1s`, `10s`, `1m`. |
| `sym` | event ticker | Uppercase Massive ticker. |
| `bar_start` | event timestamp | Floor event timestamp to timeframe boundary. |
| `bar_end` | bar start | `bar_start + timeframe_seconds`. |
| `is_closed` | bar lifecycle | True when emitted for persistence. |
| `first_event_ts` | events | First quote or trade timestamp seen in bar. |
| `last_event_ts` | events | Latest quote or trade timestamp seen in bar. |

### Trade OHLCV

| Field | Source | Formula Or Rule |
|---|---|---|
| `open` | trades | First valid trade price. |
| `high` | trades | Max valid trade price. |
| `low` | trades | Min valid trade price. |
| `close` | trades | Latest valid trade price. |
| `volume` | trades | `sum(size)`. |
| `dollar_volume` | trades | `sum(price * size)`. |
| `trade_count` | trades | Count of valid trade events. |
| `vwap` | trades | `dollar_volume / volume`. VWAP means volume-weighted average price. |
| `avg_trade_size` | trades | `volume / trade_count`. |
| `median_trade_size` | trades | Median of bounded sample, currently up to 512 trade sizes. |
| `max_trade_size` | trades | Max trade size. |
| `large_trade_count` | trades | Count where `size >= 10000` or `price * size >= 100000`. |
| `large_trade_volume` | trades | Sum of sizes for large trades. |
| `large_trade_notional` | trades | Sum of `price * size` for large trades. Notional means dollar value. |

Trade price and volume eligibility follows the consolidated Massive condition
rules loaded through `event_condition_token_reference`. Multiple conditions are
combined conservatively: a print updates a field only when every recognized
condition permits that field, and unknown conditions do not alter OHLCV. Form T
prints are price-eligible only outside the 09:30-16:00 New York regular session
and only when every additional condition is fully price-eligible, matching
Massive's extended-hours custom-bar behavior. A bucket with no
price-eligible trade is omitted from price-bar snapshots rather than rendered as
a zero or synthetic candle.

### Trade Rates And Movement

| Field | Source | Formula Or Rule |
|---|---|---|
| `trade_rate` | trades | `trade_count / timeframe_seconds`. |
| `volume_rate` | trades | `volume / timeframe_seconds`. |
| `dollar_volume_rate` | trades | `dollar_volume / timeframe_seconds`. |
| `price_change` | trades | `close - open`. |
| `price_change_pct` | trades | `(close - open) / open * 100`. |
| `high_low_range` | trades | `high - low`. |
| `high_low_range_pct` | trades | `(high - low) / open * 100`. |

### Quote OHLC

| Field | Source | Formula Or Rule |
|---|---|---|
| `bid_open`, `bid_high`, `bid_low`, `bid_close` | quotes | OHLC of valid bid prices. |
| `ask_open`, `ask_high`, `ask_low`, `ask_close` | quotes | OHLC of valid ask prices. |
| `mid_open`, `mid_high`, `mid_low`, `mid_close` | quotes | OHLC of midpoint, where `mid = (bid + ask) / 2`. |
| `spread_open`, `spread_high`, `spread_low`, `spread_close` | quotes | OHLC of spread, where `spread = ask - bid`. |
| `spread_mean` | quotes | `sum(spread) / quote_count`. |
| `spread_bps_mean` | quotes | Mean of `(ask - bid) / mid * 10000`. Bps means basis points. One basis point is 0.01 percent. |
| `spread_bps_close` | quotes | `spread_close / mid_close * 10000`. |
| `quoted_bid_size_mean` | quotes | `sum(bid_size) / quote_count`. |
| `quoted_ask_size_mean` | quotes | `sum(ask_size) / quote_count`. |
| `quote_count` | quotes | Count of valid quote events. |
| `quote_rate` | quotes | `quote_count / timeframe_seconds`. |
| `quote_update_intensity` | quotes/trades | `quote_count / max(trade_count, 1)`. |
| `locked_crossed_quote_count` | quotes | Count where `bid >= ask`. |

### Tape Classification

The gateway classifies a trade as buyer-initiated if its price is at or above last ask, or at/above midpoint when ask test is not available. Otherwise it is seller-initiated. This is a quote-test proxy, not direct order-flow data.

| Field | Source | Formula Or Rule |
|---|---|---|
| `buy_trade_count` | trades + quotes | Count of buyer-initiated trades. |
| `sell_trade_count` | trades + quotes | Count of seller-initiated trades. |
| `buy_volume`, `sell_volume` | trades + quotes | Sum of sizes by classified side. |
| `buy_dollar_volume`, `sell_dollar_volume` | trades + quotes | Sum of `price * size` by classified side. |
| `tape_imbalance` | trades + quotes | `(buy_volume - sell_volume) / volume`. |
| `aggressive_buy_ratio` | trades + quotes | `buy_volume / volume`. |
| `aggressive_sell_ratio` | trades + quotes | `sell_volume / volume`. |
| `buy_sell_volume_delta` | trades + quotes | `buy_volume - sell_volume`. |
| `cumulative_delta` | current bar | Currently same as `buy_sell_volume_delta`; session carry is not yet implemented. |

### Liquidity And Friction Proxies

| Field | Source | Formula Or Rule |
|---|---|---|
| `effective_spread_mean` | trades + last midpoint | Mean of `2 * abs(trade_price - last_mid) / last_mid * 10000`. |
| `realized_spread_proxy` | current implementation | Same as `effective_spread_mean`; delayed post-trade matching is not implemented. |
| `price_impact_1s`, `price_impact_5s` | current implementation | Currently set to close-vs-VWAP percent distance. |
| `slippage_proxy_bps` | quote/trade proxy | `max(effective_spread_mean, spread_bps_close)`. |
| `depth_imbalance_proxy` | quotes | `(mean_bid_size - mean_ask_size) / (mean_bid_size + mean_ask_size)`. This is NBBO size, not level 2 depth. |
| `liquidity_score` | trades + spread | `dollar_volume / max(spread_bps_mean, 1)`. Higher means more notional flow per unit of spread. |
| `spread_volume_ratio` | quotes/trades | `spread_bps_mean / dollar_volume`. Lower is better. |

### Previous-Bar Features

These fields are set when a bar closes and previous bars exist for the same ticker/timeframe.

| Field | Source | Formula Or Rule |
|---|---|---|
| `return_1_bar` | previous bar | Percent change from previous close to current close. |
| `return_3_bar` | previous 3 bars | Percent change from close 3 bars ago to current close. |
| `return_5_bar` | previous 5 bars | Percent change from close 5 bars ago to current close. |
| `volume_accel` | previous bar | `current.volume - previous.volume`. |
| `trade_count_accel` | previous bar | `current.trade_count - previous.trade_count`. |
| `dollar_volume_accel` | previous bar | `current.dollar_volume - previous.dollar_volume`. |
| `quote_rate_accel` | previous bar | `current.quote_rate - previous.quote_rate`. |
| `tape_imbalance_accel` | previous bar | `current.tape_imbalance - previous.tape_imbalance`. |

### VWAP, Volatility, And Noise

| Field | Source | Formula Or Rule |
|---|---|---|
| `vwap_distance_pct` | trades | `(close - vwap) / vwap * 100`. |
| `mid_vwap_distance_pct` | quotes + trades | `(mid_close - vwap) / vwap * 100`. |
| `realized_volatility` | trades | `sqrt(mean(sequential_trade_return^2))`. |
| `micro_price_volatility` | current implementation | Same as midpoint volatility until NBBO-size-weighted micro-price is added. |
| `mid_price_volatility` | quotes | `sqrt(mean(sequential_mid_return^2))`. |
| `mean_abs_trade_return` | trades | `mean(abs(sequential_trade_return))`. |
| `direction_change_count` | trades | Count of sign changes in sequential trade returns. |
| `chop_score` | trades | `sum(abs(trade_return)) * close / high_low_range`. Higher means more back-and-forth movement. |

### Estimated LULD Proximity

These fields are local estimates for scanner and chart risk display. They are
not official SIP LULD bands and do not replace an official LULD feed. The
gateway uses valid Massive trade prices in a rolling five-minute window as a
proxy for the LULD reference price. It currently applies default Tier 2
parameters because Tier 1/ETP membership is not wired into QMD reference data.

| Field | Source | Formula Or Rule |
|---|---|---|
| `estimated_luld_active` | session clock | True only during regular 9:30-16:00 ET trading hours. |
| `estimated_luld_reference_price` | trades | Simple average of valid trade prices retained from the prior 300 seconds. |
| `estimated_luld_parameter_pct` | reference price | Default Tier 2 rule: `10` when reference is above `$3`, `20` from `$0.75` to `$3`, otherwise `min(0.15 / reference_price, 0.75) * 100`. The below-$3 parameter is doubled from 15:35-16:00 ET. |
| `estimated_luld_lower_price` | reference + parameter | `max(reference_price - reference_price * parameter_pct / 100, 0)`. |
| `estimated_luld_upper_price` | reference + parameter | `reference_price + reference_price * parameter_pct / 100`. |
| `estimated_luld_distance_to_upper_pct` | bar price + upper band | `(upper_price - current_price) / current_price * 100`, where current price is midpoint close when available, otherwise trade close. |
| `estimated_luld_distance_to_lower_pct` | bar price + lower band | `(current_price - lower_price) / current_price * 100`, using the same current price rule. |
| `estimated_luld_state` | derived | `inactive`, `unknown`, `inside`, `near_upper`, `near_lower`, `above_upper`, or `below_lower`. Near-band threshold is currently 1 percent. |

## Tick Indicator Contract

Tick indicators are in memory only. They are exposed inside `IndicatorSnapshot.tick`.

| Field | Formula | Streaming Method |
|---|---|---|
| `sym` | Uppercase ticker. | Stored per ticker. |
| `last_ts` | Latest quote or trade timestamp. | Updated on every accepted quote/trade. |
| `last_price` | Latest trade price. | Updated on trade. |
| `last_mid` | `(bid + ask) / 2`. | Updated on quote. |
| `spread_bps` | `(ask - bid) / mid * 10000`. | Updated on quote. |
| `quote_pressure` | `(sum_bid_size_60s - sum_ask_size_60s) / (sum_bid_size_60s + sum_ask_size_60s)`. | Uses quotes from the latest 60 seconds. |
| `trade_rate_10s` | `trade_count_10s / 10`. | Counts retained trades with age <= 10 seconds. |
| `trade_rate_60s` | `trade_count_60s / 60`. | Counts retained trades with age <= 60 seconds. |
| `trade_accel_10s_60s` | `trade_rate_10s - trade_rate_60s`. | Positive means short-window trade activity is faster than the 60-second baseline. |
| `quote_rate_10s` | `quote_count_10s / 10`. | Counts retained quotes with age <= 10 seconds. |
| `quote_rate_60s` | `quote_count_60s / 60`. | Counts retained quotes with age <= 60 seconds. |
| `quote_accel_10s_60s` | `quote_rate_10s - quote_rate_60s`. | Positive means quote activity is accelerating. |
| `rolling_vwap_60s` | `sum(price * size)_60s / sum(size)_60s`. | Uses trades from the latest 60 seconds. |
| `tape_imbalance_60s` | `sum(signed_volume)_60s / sum(volume)_60s`. | Signed volume uses the quote-test classification. |
| `buy_pressure_60s` | `buy_volume_60s / volume_60s`. | Uses classified buy trades. |
| `sell_pressure_60s` | `sell_volume_60s / volume_60s`. | Uses classified sell trades. |

The retained sample window defaults to 300 seconds. Fixed-horizon fields still use their named horizon.

## Bar Indicator Contract

Table: `live_market_indicators`, only when `QMD_PERSIST_INDICATORS=true`.

| Field | Formula | Streaming Method |
|---|---|---|
| `schema_version` | Current value is `3`. | Constant per row. |
| `session_date`, `timeframe`, `sym`, `bar_start`, `bar_end` | Copied from closed bar. | One row per closed bar. |
| `close`, `volume` | Copied from closed bar. | Inputs for chart and indicator display. |
| `vwap` | Session-anchored cumulative `sum(hlc3 * volume) / sum(volume)`. Premarket has its own accumulation and the regular-session benchmark resets at the exchange's 09:30 New York open; after-hours continues the regular-session anchor. The canonical bar's own `vwap` remains its event-level `dollar_volume / volume`. | Keep cumulative typical-price notional and volume per ticker/timeframe/market-session anchor. |
| `ema_9`, `ema_20`, `ema_50` | `EMA_t = alpha * close_t + (1 - alpha) * EMA_{t-1}`, `alpha = 2 / (period + 1)`. | Keep last EMA value per ticker/timeframe. |
| `rsi_14` | Wilder RSI: `100 - 100 / (1 + avg_gain / avg_loss)`. | Seed first 14 changes, then update Wilder averages. |
| `atr_14` | Wilder average of true range. True range is `max(high-low, abs(high-prev_close), abs(low-prev_close))`. | Seed first 14 true ranges, then update Wilder average. |
| `macd_line` | `ema_12 - ema_26`. | Keep EMA 12 and EMA 26 state. |
| `macd_signal` | EMA 9 of `macd_line`. | Keep EMA state of MACD line. |
| `macd_histogram` | `macd_line - macd_signal`. | Derived after signal update. |
| `bollinger_mid_20` | 20-period simple moving average of close. | Rolling sum over last 20 closes. |
| `bollinger_upper_20` | `bollinger_mid_20 + 2 * stddev_20`. | Rolling sum and sum of squares. |
| `bollinger_lower_20` | `bollinger_mid_20 - 2 * stddev_20`. | Rolling sum and sum of squares. |
| `bollinger_std_20` | Standard deviation of last 20 closes. | Rolling sum and sum of squares. |
| `close_sma_20` | Average of last 20 closes. | Rolling sum. |
| `volume_sma_20` | Average of last 20 volumes. | Rolling sum. |
| `return_1_bar` | `(close - previous_close) / previous_close * 100`. | Uses previous close per ticker/timeframe. |
| `price_vs_ema20_pct` | `(close - ema_20) / ema_20 * 100`. | Derived after EMA update. |
| `price_vs_vwap_pct` | `(close - session_vwap) / session_vwap * 100`. | Derived from the cumulative session VWAP. |
| `trend_score` | Fraction of 5 checks that pass: `close > ema_20`, `ema_9 > ema_20`, `ema_20 > ema_50`, `rsi_14 >= 50`, `macd_histogram > 0`. | Updated per closed bar. Range is 0 to 1. |
| `microstructure_fast_signal`, `microstructure_confirm_signal`, `microstructure_context_signal` | Canonical deterministic microstructure scores for the latest 25, 100, and 500 quote/trade events. Range is -1 to +1. | Event state is sampled causally at `bar_end`; later events are excluded. |
| `microstructure_fast_confidence`, `microstructure_confirm_confidence`, `microstructure_context_confidence` | Evidence coverage and quality for each event horizon. Range is 0 to 100. | Uses the same canonical forecast snapshot as the score. |
| `microstructure_unified_signal` | Confidence-weighted mean of the three ready horizon scores with fixed priors 50%, 30%, and 20%. Range is -1 to +1. | Updated from the causal horizon snapshot at every closed bar. |
| `microstructure_unified_confidence` | Prior-weighted horizon confidence discounted by cross-horizon disagreement. Range is 0 to 100. | Missing horizons and disagreement reduce confidence. |
| `microstructure_unified_action` | `buy` when score is positive, confidence is at least 35%, and absolute score is at least 0.15; `sell` under the symmetric negative rule; otherwise `wait`. | Deterministic strategy gate, not an order instruction. |

## Indicator Persistence Policy

Tick indicators are memory-first and are not persisted continuously. Closed bar-level indicators are also memory-first by default because the current set can be recomputed from compact events and `intraday_family_bars_v2`. Set `QMD_PERSIST_INDICATORS=true` only when a run needs a materialized indicator table for chart-load speed or audit.

## Indicator Catalog Summary

Endpoint:

```text
GET /indicator-catalog
```

The catalog is broader than the code currently computes. `implemented` means the gateway calculates the family today. Other statuses are review contracts.

| Family | Category | Priority | Status | Normal Compute Mode | Persistence Policy | Purpose |
|---|---|---|---|---|---|---|
| `core_bars` | core | P0 | implemented | realtime tick | always | Trade OHLCV, volume, VWAP, and basic movement. |
| `quote_mid_spread_bars` | core | P0 | implemented | realtime tick | always | Bid/ask, midpoint, spread, and NBBO context. |
| `session_context` | session | P0 | planned realtime | bar close | if signal uses | Time of day, session phase, day high/low, and gap context. |
| `opening_range` | session | P0 | planned realtime | bar close | if signal uses | Opening range levels and breakout state. |
| `tape_rates` | tape microstructure | P0 | implemented | realtime tick | signal snapshot only | Trade/quote event rates and acceleration. |
| `tape_pressure` | tape microstructure | P0 | implemented | realtime tick | signal snapshot only | Rolling VWAP, buy/sell pressure, and tape imbalance. |
| `large_trade_activity` | tape microstructure | P0 | implemented | realtime tick | always in bars | Large prints and trade-size behavior. |
| `nbbo_liquidity` | NBBO liquidity | P0 | implemented | realtime tick | signal snapshot only | Spread, quote pressure, liquidity score, and slippage proxies. |
| `volume_relative` | volume/liquidity | P0 | planned realtime | bar close | if signal uses | Relative volume and relative dollar volume. |
| `volume_classic` | volume/liquidity | P1 | planned realtime | bar close | if signal uses | OBV, CMF, MFI, force index, and related volume confirmations. |
| `momentum_core` | momentum | P1 | implemented | bar close | if signal uses | RSI, MACD, one-bar return, and VWAP distance. |
| `momentum_extended` | momentum | P2 | strategy specific | bar close | if signal uses | Wider oscillator set such as ROC, CCI, Stoch, TRIX, and PPO. |
| `trend_moving_averages` | trend overlap | P1 | implemented | bar close | if signal uses | EMA/SMA trend state and moving-average alignment. |
| `trend_directional` | trend overlap | P1 | planned realtime | bar close | if signal uses | ADX, DI, Supertrend, PSAR, and Ichimoku-style confirmation. |
| `volatility_core` | volatility | P1 | implemented | bar close | if signal uses | ATR, Bollinger Bands, and realized volatility. |
| `volatility_extended` | volatility | P2 | strategy specific | bar close | if signal uses | Keltner, Donchian, Parkinson, Garman-Klass, and other channel/volatility tools. |
| `price_action` | price action | P1 | planned realtime | bar close | if signal uses | Candle body, wick, close-location, inside/outside bars, and range expansion. |
| `market_structure` | market structure | P1 | planned realtime | bar close | if signal uses | Rolling highs/lows, swings, VWAP reclaim/break, and day-high breaks. |
| `shock_features` | shock | P0 | planned realtime | bar close | signal snapshot only | Return, volume, spread, and event-rate z-scores for unusual activity. |
| `cross_timeframe_confirmation` | cross timeframe | P2 | strategy specific | in memory | signal snapshot only | Alignment between 1m, 5m, and 1h states. |
| `statistics` | statistics | P3 | offline only | Polars on demand | no default | Rolling statistics, correlations, entropy, Hurst, and regression features. |
| `cycles` | cycles | P3 | offline only | Polars on demand | no default | Hilbert-transform cycle indicators for research. |
| `candlestick_patterns` | candles | P3 | offline only | Polars on demand | no default | Broad candle pattern recognition. |
| `performance` | performance | P3 | offline only | Polars on demand | no default | PnL, drawdown, Sharpe, exposure, and portfolio reports. |
| `reference_context` | reference context | P0 | reference only | reference load | reference snapshot | `conid`, float, short labels, news flags, and other non-streaming context. |

Use this table to decide what belongs in Rust live streaming versus what should stay in the app backend or Polars research code.
