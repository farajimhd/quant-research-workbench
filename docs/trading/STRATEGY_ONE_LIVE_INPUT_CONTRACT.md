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
unknown tokens instead of silently dropping them. A typed per-ticker 100 ms
reducer now calculates completed bucket values from QMD's ordered per-ticker
lane and publishes them on `/stream/strategy-one-liquidity`. Queue loss
broadcasts a terminal `invalidated` update and stops further liquidity
projection until restart; stream lag closes the subscriber. The stream has no
durable replay/snapshot contract yet, and sparse final buckets lack a source
watermark publication path. It is therefore not an order-admission permit;
live order admission remains disabled.

The existing `/snapshot/compact-events/{ticker}` and market-page snapshots
read a bounded in-memory buffer and explicitly report cursor expiration; they
are not restart authority. `q_live.events` is the durable canonical live-event
table, but the current QMD coverage ledger aggregates a session/partition,
not a contiguous per-ticker source cursor. A cold SELECT of events can rebuild
a reducer, but cannot by itself prove that no source event was omitted before
its endpoint. The reorder worker also has a wall-time forced flush; that is
not a source completeness watermark and must not be used to finish sparse
100 ms buckets. The reducer exposes only event-closed output today.

The producer must add a normalized, per-ticker durable progress certificate
or use an equally authoritative upstream ordered watermark. Recovery then
SELECTs canonical events only through that certificate, replays the same
reducer off the realtime path, joins the stream from the certified cursor
without a gap, and reconciles typed journal, OMS, portfolio, and broker heads
before admitting orders. Do not infer completeness from wall clock, a recent
event, a session-wide count, or the in-memory snapshot. Backtest remains
SELECT-only on its separate certified `arte` products.

QMD now has a bounded, keyset-paged read-only `q_live.events FINAL` cold reader
for v6 ticker/range diagnostics. It preserves the reducer's canonical sort
key and rejects wrong-version, out-of-range, duplicate, or disordered rows.
Each page can be reduced through the same typed `LiquidityReducer` used by the
live stream; a split-page test checks the completed rows against uninterrupted
streaming reduction. The reader and reducer keep only a bounded page at once.
This reader is deliberately not wired to order admission: no source-complete
per-ticker certificate or late-write fence exists yet. Page exhaustion means
only that the current query returned no more rows, not that the source is
complete.

The upstream `q` sequence is **not** such a watermark: the provider explicitly
documents increasing but non-consecutive per-ticker values for both
[stock trades](https://massive.com/docs/websocket/stocks/trades) and
[stock quotes](https://massive.com/docs/websocket/stocks/quotes). A numerical
gap does not identify a missing packet, and a later `q` does not certify that
an otherwise silent 100 ms bucket is complete. The current QMD sort key only
orders events it has observed. Any producer certificate must instead bind an
acknowledged ingest prefix to persisted canonical rows and address late or
omitted source delivery explicitly; do not treat a locally assigned arrival
counter as an upstream completeness proof.

The existing `q_live` coverage ledger is **not** this certificate. It groups
by session/partition, has no contiguous per-ticker or global arrival prefix,
and places source metadata in `metadata_json`. Strategy 1 must not parse that
JSON as recovery state. The producer-owned successor must use typed scalar
columns in new, explicitly SSD-placed tables, partitioned by session month and
ordered for exact `(session_date, producer_epoch, ticker, through_sequence)`
reads. At minimum, an acknowledged batch receipt identifies a unique producer
epoch, global first/last locally assigned arrival sequence, exact event count,
canonical-row digest, and completed ClickHouse INSERT attempt. A separate
per-ticker head identifies its last canonical sort key, last included arrival
sequence, count, and digest. The publisher advances a Keeper-selected committed
global prefix only after **all** contributing INSERTs and receipts through that
sequence are acknowledged; parallel writer completion cannot advance it past a
gap. Cold recovery rechecks the exact persisted rows against those receipts,
then projects per-ticker reducer state only within the verified prefix.
The per-ticker reorder lane can place nonconsecutive global arrival sequences
in one ClickHouse batch. A receipt must therefore identify the exact member
sequences (or an equivalent normalized membership relation), not merely its
minimum, maximum, and count. The bounded in-memory acknowledgement tracker
accepts sparse sets and advances only through individually acknowledged
sequences; it is not connected to publication or live admission yet.
QMD also defines inactive typed `q_live.strategy_one_source_batch_v1` and
`q_live.strategy_one_source_member_v1` contracts. A batch row carries its
producer epoch, date, identity, exact member count, extrema, and acknowledgement
clock; each member row carries one arrival sequence, ticker, and canonical
scalar-row digest. Both schemas explicitly use `live_market_ssd` and contain
no JSON, arrays, or blobs. Defining the schema and deterministic membership
hashes does not yet create or publish these tables: producer writes, readback,
part-placement checks, Keeper selection, and delayed-write fencing must be
implemented before they can be used for recovery or admission.

Each process incarnation needs a fresh producer epoch in the canonical row and
receipt identity. A delayed INSERT from an older incarnation must never be
mistaken for the new epoch's source; reading `max(arrival_sequence)` from
`q_live.events` alone does not provide this fence. The certificate proves what
QMD accepted and durably published, **not** that the upstream provider
delivered every market packet. If a crash leaves an uncertified tail and the
provider cannot replay it with an authoritative continuity proof, Strategy 1
must not resume order admission for that affected session. A local wall clock,
silent bucket, provider `q` gap, or ClickHouse query returning no more rows is
not a substitute for such a proof.

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
