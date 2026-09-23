# ClickHouse market-day builder

`scripts/build_market_day.py` builds persisted core market products without
transferring market rows to Python. Python submits SQL, validates bounded
metadata, and manages restart records. It does not run strategies, rebuild
structural books, or switch Backtest consumers.

The output database is `arte` by user choice. This builder still reads the
existing `market_sip_compact` and `q_live` authorities; placing its derived
tables in `arte` does not turn them into ARTE-native REST-ingested source data
or authorize ARTE production consumers to bypass their separate source contract.

## Commands

Run from the laptop source repository with the repository's Python environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/build_market_day.py --date 2026-09-18 --tickers AADX --plan-only
python -B scripts/build_market_day.py --date 2026-09-18 --tickers AADX
python -B scripts/build_market_day.py --start-date 2026-09-17 --end-date 2026-09-18 --tickers AADX,AAPL
python -B scripts/build_market_day.py --start-date 2026-08-19 --end-date 2026-09-18 --workers 32 --allow-carried-forward-universe --plan-only
```

Both range endpoints are **inclusive New York dates**. Do not combine `--date`
with range arguments. Closed calendar dates are explicitly recorded as excluded;
a range with no exchange sessions is rejected. Omit `--tickers` to select all
dated-tradable tickers that also have certified source events. The builder reads
`q_live.feature_tradable_universe_snapshot_v2` through a matching certified
`q_live.feature_tradable_universe_snapshot_coverage_v2` row. The snapshot must
have been captured and published before 04:00 ET on its XNYS session date, and the builder
checks its row count and hash before accepting `is_tradable=1` members. It never
substitutes a later snapshot for a missing historical session. An explicitly
marked carry-forward uses the latest **earlier exact certified** list. It is
available only with `--allow-carried-forward-universe`; the builder checks its
source certificate and copy integrity and prints each affected session. This
causal fallback can omit tickers that became tradable after the source capture
or include tickers that stopped being tradable. It is not an exact pre-open
list for the missing session. Duplicate
admitted listing rows collapse to one ticker for bar calculation; the complete
dated snapshot and its certificate are fingerprinted in the build.
Requested ticker-days without dated admission or canonical events fail preflight.
Only requested sessions need canonical and dated-population coverage. No earlier
sessions are built automatically.

The Reference Gateway publishes current-graph snapshots only for the next
session whose 04:00 ET cutoff has not passed. Its session assignment uses the
XNYS calendar, so an after-close Friday publication targets Monday (or the
next exchange session), including holidays and daylight-saving changes. A
retained V1 publication may be copied into the immutable V2 snapshot only if
its actual `inserted_at` and retained Step 06 completion record prove it was
captured and available before that session's cutoff.
The read-only audit and bounded certification command is:

```powershell
python -B scripts/audit_tradable_snapshots.py --start-date 2026-08-18 --end-date 2026-09-18
python -B scripts/audit_tradable_snapshots.py --start-date 2026-08-18 --end-date 2026-09-18 --execute
python -B scripts/audit_tradable_snapshots.py --start-date 2026-08-18 --end-date 2026-09-18 --execute --carry-forward-missing
```

The second command copies only retained historical publications and writes
coverage certificates on `live_market_ssd`; unresolved sessions remain recorded
and cannot be built. The third command copies the latest earlier exact
certificate into missing sessions with a distinct `carried_forward` revision,
preserving source capture and availability timestamps. It never stamps today's
identity graph with a past date. The Aug 18–Sep 18 audit certified 12 exact
sessions and marked 11 carried forward; the source date and status are in the
runtime audit manifest.
Previously built `arte` days retain their original build/population identity;
they require a new build under the V2 authority before Backtest consumer cutover.

Connection settings come from environment variables or `--env-file`. The default
file is the existing workstation secrets `.env`; credentials are never printed or
copied. Supported names are QMD_CLICKHOUSE_*, REAL_LIVE_CLICKHOUSE_WRITE_*,
CLICKHOUSE_WORKSTATION_USER/PASSWORD, and CLICKHOUSE_URL/USER/PASSWORD.
Runtime manifests default to `D:/TradingML/runtimes/market-day`; `--runtime` must
remain under the available runtime root. The default output database is
`arte`. Market-day output tables retain their independent `_v1` schema suffix;
the `market-day-core-v5` string is the calculation revision, not a table version.
Python needs `pandas_market_calendars`; interactive progress
uses Rich, with `--progress text` for plain output.

The build starts four ticker workers by default (`--workers 1` through `32`).
Each worker owns one ticker's dates in chronological order and uses its own
ClickHouse client. The ordinary per-query limits are four ClickHouse threads,
2 GiB memory, and 600 seconds. A canonical ticker-day with at least four million
events uses an 8 GiB cap for its ordered 100 ms liquidity query, with at most
four such queries running concurrently; it does not spill temporary sort data
to the `default` disk. At 32 workers, these caps allow up to 88 GiB of
concurrent query memory and 128 ClickHouse threads; choose that setting only
when the host has headroom. Tune
`--workers`, `--max-threads`, `--max-memory-gb`, and `--query-timeout` for the host.
No automatic write retries
or unbounded worker fan-out occurs. A worker failure stops new ticker dispatch,
cancels active builder queries, and leaves published ticker-day stages resumable.
Each worker reuses one persistent ClickHouse HTTP connection; cancellation uses
a separate connection so it cannot wait behind an active query. This avoids
exhausting Windows ephemeral ports during large ticker campaigns.

Progress counts durable bars and technical ticker-days and shows active ticker
stages. Plain text mode emits bounded snapshots; interactive mode keeps a live
worker panel. The manifest records bounded recent query samples and aggregate
query timings. Completed per-ticker metrics append to the build's `units.jsonl`
in the runtime root, avoiding repeated writes of a growing report.
Planning metadata is limited to 250,000 ticker-days by `--max-plan-units`;
larger requests fail explicitly rather than truncating the requested range.

## Calculation contract

1. Read `market_sip_compact.events_YYYY` in `(sip_timestamp_us, ordinal)` order.
   Decode the two canonical integer price scales losslessly to fixed 1e-4 units.
   Apply independent last/high-low/volume condition eligibility, extended-hours
   Form T rules, and the canonical delayed-trade flag (`event_meta` bit `0x80`).
   Eligible trades from 04:00–04:05 ET are retained; a delayed trade is
   excluded at any time of day. Trades without the delayed bit remain subject
   to the ordinary condition and value rules, including those whose reporting
   clock was unknown at ingestion.
2. Carry the last valid NBBO causally through trades. Compute cumulative eligible
   volume/notional and execution-eligible volume/notional. Execution VWAP accepts
   trades inside the prevailing non-crossed NBBO using QMD's relative epsilon and
   truncated-millisecond age <= 1,000 ms. No future or equal-time later quote is
   visible to an earlier trade. Quote observation timestamps remain available so
   consumers can reevaluate freshness during event gaps.
3. In one ordered ClickHouse event pass, reduce trades and quotes into one
   `liquidity_100ms_v1` row per nonempty 100 ms bucket. This row retains the
   latest causal quote and its observation time, bid/ask sizes, eligible trade
   volume, event counts, and cumulative execution VWAP. There is no persisted
   per-event duplicate. Reduce these rows into sparse 100 ms trade bars.
   Quotes outside 04:00–20:00 ET are not carried into the session.
   Excluded events and eligibility counts are exposed in the runtime report.
4. Read 100ms bars to build 1s, 5s, 10s and 30s together. Read 30s to build 1m,
   5m and 1h together. Bucket indices are offsets from New York midnight; all
   intervals are half-open. No daily/weekly/monthly aggregation is implied.
5. Calculate technical indicators from each timeframe's price-bearing bars:
   EMA **7, 9, 12, 15, 20, 26, 50**, MACD **12/26/9**, RSI **14**, ATR **14**.
   Native cumulative `exponentialMovingAverage` windows use an explicit first-value
   seed adjustment. MACD signal smoothing is a dependent SQL stage. Wilder
   calculations use a 14-sample arithmetic seed and then alpha=1/14 smoothing.

For EMA/MACD continuity, the builder reads the preceding exchange session's
certified terminal technical row and close from persisted ClickHouse tables.
If that session has only quote-only bars and zero technical rows, it follows
the certified seed chain to the last earlier price-bearing session. A quote-only
chain with no prior price state bootstraps at the first later eligible bar.
It prefers the current build, then a completed compatible V5 build. The state
must match the calculation source and trade-rule hash; its published units and
output hashes are revalidated. If no compatible state exists, that ticker-day
uses a **first-bar bootstrap**, recorded in the runtime unit log and seed counts.
Such a bootstrap is a defined new-series boundary, not a claim of full-history
EMA continuity. A missing or uncertified preceding session is never silently
replaced by an older one. Split references are frozen from `q_live.market_stock_split_v1`;
carried price and MACD states are adjusted for splits effective between sessions.
Future splits are excluded and conflicting ratios fail closed. Persisted raw bars
remain unadjusted. RSI/ATR reset for each requested session; RSI's first change
may use the carried prior close, and ATR includes the prior-close gap. Before
their 14-sample seed is ready they publish zero with readiness=false. Flat seeded
RSI is 100, matching QMD. No missing bars are fabricated. A price-bearing bar
without eligible extremes blocks technical publication.

The indicator definition, parameters, seeds, calculation source hashes, calendar,
input certificates and price scale are recorded in the runtime build manifest and
the compact runtime SQLite certification ledger. Floating indicator
outputs use Float64; price primitives use UInt64 at scale 10,000, because scaling
the canonical UInt32 cent representation can exceed UInt32. Volume remains
Float64 to preserve fractional canonical sizes. No JSON indicator payload is
repeated per bar or broker bucket.

## Tables and consumer contract

All three ClickHouse tables specify `storage_policy='live_market_ssd'`. Preflight checks the
policy's disks, the dated-universe table and its active parts, and existing
output column definitions; completion checks actual
active-part placement. Incorrect existing placement is a hard error, not a
setting-only repair or fallback to `default`.

| Table in `arte` | Purpose |
|---|---|
| `bars_v1` | Sparse integer OHLC, sums/counts, additive execution primitives and validity, keyed by resolution |
| `indicators_v1` | Typed indicator values, readiness and calculation state, keyed by resolution |
| `liquidity_100ms_v1` | One aggregate per event-bearing 100 ms bucket: quote depth and timestamp, spread, eligible trade volume/count, cumulative volume/notional and execution VWAP |

Data tables use MergeTree with monthly session partitions. Rows are immutable,
keyed by build, ticker/session, attempt and resolution/bucket. The runtime
`build-ledger-v2.sqlite3` stores build status, certified stage row counts/hashes,
attempt IDs and seed provenance. Failed attempts may retain ClickHouse rows, but
are **never consumer-visible authority**. Consumers must select a `core_complete`
build from the runtime manifest/ledger and constrain each table to its certified
attempt ID. The Backtest consumer has not yet been changed to read these tables.

A 100 ms liquidity row and its bar become available only at the bucket end;
`bucket_index` denotes the start, not the decision time. Quote freshness for a
fill must be checked against `quote_timestamp_us` at the actual fill time.
Displayed ask size bounds aggressive buy liquidity; bid size bounds aggressive
sell liquidity. Eligible trade volume can inform a conservative passive-fill
budget, but the table does not infer aggressor side or prove queue position.
Multiple orders must share a bucket's budget rather than each consuming it in
full. Empty 100 ms buckets have no row. Exact event replay remains the canonical
authority for sub-bucket fill timing and stop triggers. Indicators become
available only when their bars end. Backtest broker integration remains separate.

## Resume and certification

Rerun the same command on the same host/runtime to validate and skip published units. `--rebuild` creates
a distinct build ID without deleting existing data; resume that specific build
with `--build-id <printed-id>` and the original arguments/runtime. Source, rules,
code, parameters, range and runtime owner are pinned. A per-database OS lock
prevents simultaneous local controllers; separate runtime owners get different
build IDs. Ctrl+C cancels the active query and preserves completed units. A retry
uses a fresh attempt ID, so an uncertain partial INSERT cannot duplicate a
published result. The ledger is host-local and must be preserved with the
runtime manifests; copying the ClickHouse tables alone does not transfer
certification. There is no automatic garbage collection of abandoned attempts.
Read-only plans
write `last-plan.json` so they do not replace the failed build's `latest.json`.

Verification checks source-day totals against continuity, ordinal uniqueness and
bounds, source fingerprints before/after calculation, event count conservation in
the 100 ms liquidity aggregates,
unique output keys, volume/notional/count conservation, direct-versus-hierarchical
OHLC equality, indicator coverage/finite values, and SSD placement. Resume rechecks
stored output hashes. Core certification is **not Backtest behavioral acceptance**;
the consumers remain unchanged pending decision/order/fill equivalence testing.

The calculation reads canonical events once to produce the 100 ms liquidity rows
and uses those persisted rows for bars. Integrity verification intentionally performs
additional canonical scans. The controller does not claim a single total disk
scan, or that the fastest all-universe implementation has been established.
`latest.json`, per-build files and immutable `runs/*.json` retain aggregate
client timings, recent query IDs, and available query-log read/write/memory
measurements. Query-log publication is asynchronous; missing metrics are
explicitly marked unavailable. A controller code change produces a new build
identity, so stages from a prior controller revision do not silently resume.

## Validation

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B tests/test_market_day_builder.py -q
$env:MARKET_DAY_CLICKHOUSE_TEST='1'
python -B tests/test_market_day_builder.py -q
```

The opt-in suite creates uniquely named SSD fixture databases, removes only those
databases, and retains local test manifests under the runtime root. It checks
1,000 recursive samples against independent sequential formulas, carried prior
state, session resets, equal-time quote/trade order, stale/outside-NBBO eligibility,
unknown conditions, sparse rollups, abandoned attempts and corruption rejection.
Its runnable interruption/range/resume test uses certified AADX and AEG data for
September 17–18, 2026, then builds September 18 alone from the preceding
completed build's persisted state. This is a bounded integration test, not a
full-market throughput or Backtest-equivalence claim.
