# Strategy 1 live completed-boundary inputs

Status: required producer and consumer work; **not an order-admission permit**.
The current live Strategy 1 supervisor remains disabled.

The fixed Backtest's authority is the certified `arte.bars_v1`,
`arte.indicators_v1`, and `arte.liquidity_100ms_v1` product set. QMD's
`/stream/completed-indicators` already emits an exact closed bar/indicator pair
for each computed timeframe and terminates on subscriber lag. It can supply
completed MACD for 1s, 5s, 10s, and 30s and closed trade bars. It is not a
liquidity-bar or broker-match feed: `BarRow` exposes bid/ask closes and mean
displayed sizes, but not the latest causal quote timestamp and bid/ask sizes,
eligible execution volume, or the ARTE bucket-validity fields. The ordinary
Market Discovery refresh has a minimum 1-second cadence and cannot drive a
100 ms Strategy 1 decision clock.

QMD must publish a **new versioned, producer-owned completed 100 ms liquidity
product** from its ordered canonical compact-event lane. Do not change the
existing chart/scanner bar semantics or translate an aggregate bucket into
invented quote/trade events. For each event-bearing bucket, the product must
expose session date, ticker, bucket index/end, source sequence/watermark,
first/last event timestamp, event/trade/quote counts, delayed-report and
eligibility counters, price/extremes/volume validity, integer OHLC at 1e-4,
eligible trade volume and notional, execution-eligible volume and notional,
latest valid bid/ask integers and displayed sizes, quote timestamp and
validity, and causal cumulative execution VWAP. The field meanings and
eligibility must match `pipelines/market_sip/events/market_day_sql.py`; keep
the same source trade-reporting revision. In particular, delayed reports are
excluded, but otherwise eligible 04:00–04:05 ET trades are included. The
existing QMD default `TradeAggregationRules.resolve` and
`MarketEvent.is_excluded_from_derived_state` exclude that opening interval;
they cannot be reused unchanged for this product.

QMD compact event v6 now sets `trade_reporting_v1` evaluated/delayed bits
while the original participant-clock presence and exact condition codes are
available. Its `execution_timestamp_us` still substitutes SIP time when the
participant clock is absent, so liquidity consumers must use the v6 flags,
not timestamp equality. V5 and earlier trade rows have no classification and
cannot be interpreted as timely; no in-place backfill is permitted. The
strict market-day token resolver includes valid 04:00–04:05 trades and rejects
unknown tokens instead of silently dropping them. The completed liquidity
reducer, persistence, and publication remain to be implemented.

The consumer joins this completed liquidity row with the closed indicator
pairs by session, ticker, resolution, and completed boundary. A 100 ms
decision is eligible only after all required input products for that boundary
are present. Sparse/quote-only/price-bearing/missing buckets remain distinct;
no zero-volume candle is fabricated. A stream lag, rejected event, source
watermark gap, mismatched identity, or missing quote freshness fails closed
before another decision. Recovery may use certified canonical state, but may
not backdate a decision or submit an order for an already elapsed boundary.
Only the ordered portfolio/OMS actor mutates shared cash and orders.

Acceptance requires producer-vs-ARTE comparisons on representative 100 ms
buckets, including 04:00–04:05 trades, delayed reports, same-timestamp
quote/trade order, crossed/stale quotes, sparse and quote-only buckets,
executability and partial-fill budgets. Test worker-count determinism,
disconnect/lag recovery, a cold restart, and no-ClickHouse/SQLite/disk calls
on the hot decision path. Until these tests and typed journal/broker recovery
pass, Strategy 1 live order admission remains closed.
