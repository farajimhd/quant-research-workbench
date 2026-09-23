# ClickHouse market-day builder

`scripts/build_market_day.py` builds persisted core market products without
transferring market rows to Python. Python submits SQL, validates bounded
metadata, and manages restart records. It does not run strategies, rebuild
structural books, or switch Backtest consumers.

## Commands

Run from the laptop source repository with the repository's Python environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/build_market_day.py --date 2026-09-18 --tickers AADX --plan-only
python -B scripts/build_market_day.py --date 2026-09-18 --tickers AADX
python -B scripts/build_market_day.py --start-date 2026-09-17 --end-date 2026-09-18 --tickers AADX,AAPL
```

Both range endpoints are **inclusive New York dates**. Do not combine `--date`
with range arguments. Closed calendar dates are explicitly recorded as excluded;
a range with no exchange sessions is rejected. Omit `--tickers` to select all
dated-tradable tickers that also have certified source events. The builder reads
`q_live.feature_tradable_universe_v1 FINAL` with `universe_date` equal to each
session and `is_tradable=1`. It never substitutes the latest snapshot for a
missing historical date. Duplicate admitted listing rows collapse to one ticker
for bar calculation; the complete dated snapshot is fingerprinted in the build.
Requested ticker-days without dated admission or canonical events fail preflight.
Missing source or population coverage also fails preflight for the seven calendar
days preceding the first requested date. Earlier warm-up bars are persisted only
on days when that ticker was tradable; a newly admitted ticker starts its EMA
from the first available price-bearing bar, with no fabricated history.

Connection settings come from environment variables or `--env-file`. The default
file is the existing workstation secrets `.env`; credentials are never printed or
copied. Supported names are QMD_CLICKHOUSE_*, REAL_LIVE_CLICKHOUSE_WRITE_*,
CLICKHOUSE_WORKSTATION_USER/PASSWORD, and CLICKHOUSE_URL/USER/PASSWORD.
Runtime manifests default to `D:/TradingML/runtimes/market-day`; `--runtime` must
remain under the available runtime root. The default output database is
`q_market_history`. Python needs `pandas_market_calendars`; interactive progress
uses Rich, with `--progress text` for plain output.

Default resource limits are one active ticker-day query, four ClickHouse threads,
2 GiB query memory and 600 seconds per query. They can be changed explicitly with
`--max-threads`, `--max-memory-gb`, and `--query-timeout`. No automatic write retries
or unbounded worker fan-out occurs.
Planning metadata is limited to 100,000 ticker-days by `--max-plan-units`;
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
3. Persist event indicator updates, then reduce those persisted rows into sparse
   100ms trade bars. Quotes outside 04:00–20:00 ET are not carried into the session.
   Excluded events and eligibility counts are exposed in the runtime report.
4. Read 100ms bars to build 1s, 5s, 10s and 30s together. Read 30s to build 1m,
   5m and 1h together. Bucket indices are offsets from New York midnight; all
   intervals are half-open. No daily/weekly/monthly aggregation is implied.
5. Calculate technical indicators from each timeframe's price-bearing bars:
   EMA **7, 9, 12, 15, 20, 26, 50**, MACD **12/26/9**, RSI **14**, ATR **14**.
   Native cumulative `exponentialMovingAverage` windows use an explicit first-value
   seed adjustment. MACD signal smoothing is a dependent SQL stage. Wilder
   calculations use a 14-sample arithmetic seed and then alpha=1/14 smoothing.

EMA/MACD warm-up uses the preceding seven calendar days of completed nonempty
bars, following the existing historical EMA horizon. Prior bars initialize the
previous close. Split references are frozen from `q_live.market_stock_split_v1`;
prior prices are adjusted inside SQL to the requested session's split basis.
Future splits are excluded and conflicting ratios fail closed. Persisted raw bars
remain unadjusted. RSI/ATR reset for the requested session; RSI's first change may
use that prior close, and ATR includes the prior-close gap. Before their seed is
ready they publish zero with readiness=false. Flat seeded RSI is 100, matching
the current QMD implementation. Intermediate historical RSI calculations do not
seed the requested session. No missing bars are fabricated. A price-bearing bar
without eligible extremes blocks technical publication rather than silently
dropping it or inventing ATR inputs.

The indicator definition, parameters, seeds, calculation source hashes, calendar,
input certificates and price scale are stored once per build. Floating indicator
outputs use Float64; price primitives use UInt64 at scale 10,000, because scaling
the canonical UInt32 cent representation can exceed UInt32. Volume remains
Float64 to preserve fractional canonical sizes. No JSON indicator payload is
repeated per bar/event.

## Tables and consumer contract

All five tables specify `storage_policy='live_market_ssd'`. Preflight checks the
policy's disks, the dated-universe table and its active parts, and existing
output column definitions; completion checks actual
active-part placement. Incorrect existing placement is a hard error, not a
setting-only repair or fallback to `default`.

| Table suffix (prefix `market_day_`, suffix `_v1`) | Purpose |
|---|---|
| `events` | Event-cursor updates: eligibility, causal NBBO, cumulative volume/notional, execution VWAP, spread |
| `bars` | Sparse integer OHLC, sums/counts, additive execution primitives and validity |
| `technical` | Typed timeframe indicator values, readiness and calculation state |
| `units` | Published attempt ID and source/output integrity for each ticker-day stage |
| `builds` | Shared definition and final `core_complete` status |

Large data tables use MergeTree with monthly session partitions. Registry tables
use ReplacingMergeTree and must be read with `FINAL`. Data rows are immutable,
keyed by build, instrument/session, attempt and event/bar coordinates. Failed
attempts may retain rows, but are **never consumer-visible authority**. Consumers
must first select a `core_complete` build, then join each product to its matching
`units FINAL` row with status=`complete`, including the exact `attempt_id`.
Reading an entire table without these predicates is incorrect.

An event value is available at its canonical event cursor. A technical value is
available only when its bar ends; a bar's bucket start is not its availability
time. The last technical row provides EMA/MACD and mature Wilder state; incomplete
days are recomputed, not resumed mid-recurrence. These outputs do not replace raw
events for fills or intrabar strategy/forming updates, nor the separate structural,
corporate-action, population, reference and signal authorities.

## Resume and certification

Rerun the same command to validate and skip published units. `--rebuild` creates
a distinct build ID without deleting existing data; resume that specific build
with `--build-id <printed-id>` and the original arguments/runtime. Source, rules,
code, parameters, range and runtime owner are pinned. A per-database OS lock
prevents simultaneous local controllers; separate runtime owners get different
build IDs. Ctrl+C cancels the active query and preserves completed units. A retry
uses a fresh attempt ID, so an uncertain partial INSERT cannot duplicate a
published result. There is no automatic garbage collection of abandoned attempts.

Verification checks source-day totals against continuity, ordinal uniqueness and
bounds, source fingerprints before/after calculation, event count conservation,
unique output keys, volume/notional/count conservation, direct-versus-hierarchical
OHLC equality, indicator coverage/finite values, and SSD placement. Resume rechecks
stored output hashes. Core certification is **not Backtest behavioral acceptance**;
the consumers remain unchanged pending decision/order/fill equivalence testing.

The calculation reads canonical events once to produce event-derived rows and
uses those persisted rows for bars. Integrity verification intentionally performs
additional canonical scans. The controller does not claim a single total disk
scan, or that the fastest all-universe implementation has been established.
`latest.json`, per-build files and immutable `runs/*.json` retain query IDs, client
elapsed times, and available query-log read/write/memory measurements. Query-log
publication is asynchronous; missing metrics are explicitly marked unavailable.

## Validation

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B tests/test_market_day_builder.py -q
$env:MARKET_DAY_CLICKHOUSE_TEST='1'
python -B tests/test_market_day_builder.py -q
```

The opt-in suite creates uniquely named SSD fixture databases, removes only those
databases, and retains local test manifests under the runtime root. It checks
1,000 recursive samples against independent sequential formulas, prior-day seeds,
session resets, equal-time quote/trade order, stale/outside-NBBO eligibility,
unknown conditions, sparse rollups, abandoned attempts and corruption rejection.
Its runnable interruption/range/resume test uses certified, dated-tradable AADX data for
September 17–18, 2026 plus warm-up. This is a bounded integration test, not a
full-market campaign.

On ClickHouse 26.3.25.2, the bounded AADX September 17–18 run plus seven-day
warm-up persisted 241,216 event updates, 145,849 bars, and 15,202 technical rows.
The measured build phases took approximately six seconds; the complete launcher
took approximately eight seconds. Its largest logged query used 96,109,054 bytes.
All active output parts were on `live_market_ssd`. These are a small-symbol
measurement, not a full-market throughput or Backtest-equivalence claim.
