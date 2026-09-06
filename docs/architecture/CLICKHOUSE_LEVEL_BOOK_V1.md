# ClickHouse closing level book v1

Status: experimental full JUNS build, September 6, 2026. This implements the
user's revised historical-compute direction: ClickHouse constructs the book
from canonical events; already persisted v18 checkpoints are comparison data.
No Rust replay or raw-event transfer to Python. An explicit experimental
Backtest source now exists; ordinary backtests and live trading retain v18.
The earlier exact-v18 reconstruction gate is superseded for this experiment.

## Inputs and computation

`scripts/build_structure_book_clickhouse.py` orchestrates `INSERT SELECT`,
window functions, array transformations, and typed `arrayFold` transitions.
Python receives bounded certification metadata, counts, and profiles. Source
data stays in ClickHouse. Certified `market_sip_compact.events_YYYY` remains
the exclusive historical event authority. Counts, ordinal uniqueness/endpoints,
timestamp endpoints, trade-condition rules, split revisions, and baseline
certificate links are checked and pinned in `source_manifest.json`.

The new algorithm has explicit differences from v18:

1. Prices use the starting-period coordinate system. A trade is divided by
   cumulative split factors effective at its timestamp. Future factors never
   rescale an earlier trade. Output geometry is restored to the applicable
   session's price basis.
2. Ten timeframe bucket series retain pivot geometry and causal confirmation:
   100ms, 1s, 5s, 10s, 30s, 1m, 5m, 1h, 1d, 1w. Three populated buckets form a
   candidate; the first eligible trade of the fourth confirms it. Equal
   extrema and gap behavior follow the tested detector contract. There is no
   forced end-of-query confirmation. Only >=1s candidates found zones.
3. Deterministic cells use two starting-basis ticks: 0.0002 below $1, 0.02
   otherwise. Region plus cell identifies a level; the earliest confirmed
   pivot supplies its actual price, initial role, and fixed band with half-width
   `max(tick, price*0.0005)`. Cell rounding addresses the level; it does not
   quantize displayed geometry. Later candidates do not move the anchor.
   This replaces v18 adaptive reversals and source-based mutual-containment
   consolidation. It is not an exact v18 port.
4. Lifecycle observations are completed, populated **one-second closes** in
   SIP ordinal order. The creation second is excluded if it began before the
   level's confirmation. Two consecutive observations beyond the adverse band
   plus `max(tick, 0.25*range)` accept a crossing. A subsequent band contact and
   opposite-side departure beyond one tick confirm a role flip; a departure
   back to the original side rejects the retest. Pending levels remain visible.
   These simplified transitions differ from v18 tick/time/volume acceptance.
5. Volatility is the mean true range of the last 14 completed populated minute
   buckets, joined as of the second's completion time. The just-completed minute
   is available exactly at its end; a developing minute is never available.
   Frozen encounter volatility has a raw-tick floor transformed into the
   starting basis. With no completed minute yet, the scorer's fallback applies.
6. One `prominence` score accumulates favorable reactions: band contact freezes
   the range, favorable excursion is divided by that range, and a departure
   of at least one range followed by return starts another encounter. Accepted
   crossings and role changes finish/reset the current encounter. Output is
   `log(1 + completed reaction units + current best reaction units)`.
   This is structural evidence, not a calibrated probability or demonstrated
   trading predictor. There is currently no age decay or score-based deletion.

The absence of retirement is a remaining retention-design issue: survival to
close does not by itself prove strength. In particular, pending or rarely
retested zones can accumulate. Do not claim this experiment automatically
removes every unimportant level.

## Computation reductions and concurrency

Days advance sequentially, preserving the preceding scalar state. ClickHouse
processes independent levels using four threads per query; two independent
bucket/extraction queries may run concurrently. Limits are configurable and
bounded (1–8 threads, 1–4 concurrent queries, 2 GiB per query).

Whole-day ranges skip provably irrelevant transitions. Remaining observations
are grouped by unchanged lifecycle branch predicates; each run retains its
first three, last, minimum, and maximum observations in original order. This
preserves crossing counters, retests, frozen encounter ranges and reaction
maxima for the new machine. It is an optimization of the new contract, not a
claim that one-second sampling preserves v18 tick behavior. Long-run/prefix
fixtures and 345 complete JUNS sessions match uncompressed states exactly.

## Persistent representation and as-of semantics

The isolated `book` table stores only changes between closing states, with
additional split-boundary versions. Fields include ticker, book version,
level identity, price/band, role, pending lifecycle, prominence, birth and
confirmation time, and half-open `[valid_from_us, valid_to_us)` timestamps.
Ordinary output becomes effective at 20:00 America/New_York. This experiment
uses that extended-session cutoff consistently rather than 16:00.

`ReplacingMergeTree` deduplicates deterministic retries. Reads require `FINAL`.
The partition expression is `cityHash64(ticker)%32`; ordering is
`(book_version,ticker,valid_from_us,level_id)`. Bounded ticker-hash partitions
avoid an unbounded partition per symbol/day. Query both ticker and version.
For the experimental JUNS database, a prior-session query is:

```sql
WITH toUInt64(toUnixTimestamp64Micro(
  toDateTime64('2026-08-21 04:00:00',6,'America/New_York'))) AS cutoff
SELECT level_id,price,lower,upper,side,lifecycle,pending_side,prominence,
       born_us,confirmed_us
FROM structure_book_9b415eac470a.book FINAL
WHERE ticker='JUNS' AND book_version='clickhouse-closing-book-1'
  AND cityHash64(ticker)%32=cityHash64('JUNS')%32
  AND valid_from_us<=cutoff
  AND (valid_to_us IS NULL OR cutoff<valid_to_us)
```

Do not expose a future `valid_to_us` endpoint as a strategy feature. A morning
query excludes that day's closing observations. Reconstructing intraday state
requires replay of that day's causal prefix with the same algorithm. The book
alone omits developing buckets and in-progress encounter/crossing state and is
not a complete streaming restart checkpoint.

At a split boundary, the prior surviving version ends and an adjusted copy
starts with the same identity and unchanged score. `split_audit` records ratio,
source revision, affected rows, mismatch counts and before/after diagnostic
hashes. Historical pre-split versions remain unchanged. JUNS's August 7, 2026
75-for-1 price multiplier affected 4,149 carried levels with zero mismatches.
The audit preserves source ingestion time, which can be later than the split's
effective date. Validation establishes event-time price-basis causality; it
does not prove the live gateway received corporate-action metadata in time.
Live activation needs a separate readiness policy for that case.

All tables explicitly use `live_market_ssd`; policy and physical parts are
checked. The permanent serving representation is book plus split audit and
build provenance. Temporary trades, buckets, history, comparison rows, and
episodes remain isolated experiment staging pending review. They are not
required permanent evidence arrays. Validation prefix tables are removed after
successful checks; old v18 checkpoints are retained as requested.

## Reproduction and repair boundary

From the repository, use an available Python interpreter:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python scripts/build_structure_book_clickhouse.py --ticker JUNS `
  --start 2025-01-01 --end 2026-09-06 --threads 4 --workers 2 `
  --runtime D:\TradingML\runtimes\structure-validation\juns-clickhouse-book-20260906-v3
python scripts/validate_structure_book_clickhouse.py `
  --runtime D:\TradingML\runtimes\structure-validation\juns-clickhouse-book-20260906-v3 `
  --uncompressed-runtime D:\TradingML\runtimes\structure-validation\juns-clickhouse-book-20260906-v1
```

The runtime must be under the required operational root. Connection settings
default to workstation secrets and are never emitted. No workstation Python
process is needed: the laptop controller submits SQL to workstation ClickHouse.

Each completed SQL unit is journaled. Resume checks source fingerprint and
controller hash, skips completed work and requires identical SQL for every
completed unit. `--resume-compatible` explicitly permits controller corrections
only if those SQL checks pass. Place a `STOP` file in the runtime to stop at the
next daily boundary; remove it before resuming. Failed writes are not blindly
retried. Deterministic replacement keys and `FINAL` protect logical retry
results; there is no production publication pointer.

An imported/corrected source revision requires a new isolated successor build.
This is an executable historical rebuild primitive, not yet an automatic
ingestion-triggered repair service. By default construction requires complete
persisted v18 coverage for comparison. `--without-v18-comparison` explicitly
builds from certified canonical events without that reference; validation marks
v18 parity untested while retaining causal-prefix, interval, split, retry and
physical-storage checks. Merging and p_norm normalization remain load-time only.
Split deliveries are deduplicated by effective date and identical ratio before
construction. Conflicting ratios fail closed; the report retains all source rows
and the duplicate count. Prefix validation uses the actual symbol's split dates.

## Results and acceptance

### Experimental backtest integration

The Backtest launch page offers **Level book → Experimental ClickHouse** for
validated builds discovered under the structural-validation runtime root.
Choose the covered ticker and a covered date (for JUNS, e.g. August 21, 2026).
The selection and source fingerprint persist in the run definition and are
checked again on resume. No global History/Live checkpoint selection changes.

`src/backend/experimental_structure_book.py` loads the prior day's compact
scalar state from the validated build's `history` table, level geometry and
confirmation ordinals from `levels`/`candidates`, and completed-second
observations from `observations`. These diagnostic continuation tables must
remain available while the experimental run is in use. It fetches no raw SIP
events. This is currently a backtest provider over certified observations,
not a live gateway detector or automatic historical repair service.

NumPy advances the same v1 state machine. Independent frame and trade cursors
prevent prefetch from leaking a later snapshot into an earlier decision.
Equal-timestamp trade decisions also check the candidate's canonical ordinal.
Session changes restore the preceding close and apply that day's causal price
basis; rewinding reconstructs the prefix deterministically. Continuation fields
remain scalar rather than accumulating evidence arrays.

The chart requests the selected run's provider and clamps levels to the run
cursor. It uses the same continuation, emits an initial snapshot plus deltas,
and preserves UInt64 identities as strings. Prominence labels are `P…`; legacy
probability/relative-quality labels and filters are not applied to those zones.
The initial experimental strategy contract was `clickhouse-point-level-prominence-4-v1` (superseded for new runs by the load-time contract below).
Only causally confirmed levels with prominence >= 4 enter strategy observations.
Entry, support stops and resistance targets use exact `price` and producer role;
overlapping shading bands do not merge distinct identities. This version replaces
legacy level probability/relative-quality eligibility with prominence, without
changing market entry conditions, ordinal selection or risk caps. Chart filters
remain independent and may show weaker levels. Current v18 behavior is unchanged.
Session high is maintained separately from canonical completed one-second bar
highs and price-eligible trades, including pre-activation warmup. It resets each
session and persists in the restart state. Older experimental restart states
without this contract require a new run; they cannot resume under changed rules.

Validation covers every scalar-reference prefix, confirmation sequence ties,
independent strategy cursors, API cursor clamping, and real JUNS August 6/7/21
closing states plus chart-delta reconstruction. Price and role comparisons
remain strict; the logarithmic score uses absolute tolerance 1e-8 because
ClickHouse's vector log differs from NumPy log1p by up to 1.6e-9 in these tests.

JUNS: 420 certified sessions, January 2, 2025–September 4, 2026;
3,372,215 source events; 4,284 final level identities; 823,720 level-day states;
46,426 compressed interval rows. Initial single-copy book size was 1,370,032
bytes; use `validation.json` for final merged physical size. The 420 lifecycle
queries totaled 168.80 seconds. Existing-v18 extraction separately took 57.99
seconds. These phase measurements exclude development attempts and are not an
end-to-end strategy backtest benchmark.

All closing states reconstruct exactly from intervals. No overlapping/gapped
versions, future confirmations or out-of-session observations were found.
Truncated event-prefix detector and volatility checks passed before and during
the split session; deterministic retry and physical storage checks passed.
Twelve focused SQL/scalar tests passed without executing Rust.

Comparison preserves all 697,168 persisted v18 rows, including duplicate level
identities. At tolerance `max(reference half-band, two raw ticks)`, 99.54% have
a nearby new price; 78.07% have a nearby new price with the same role. Reverse
coverage is 99.04% by price and 78.41% with the same role. These are many-to-one
nearest-price metrics, not exact identities, band equivalence or lifecycle
parity. On the last day, same-role v18 coverage is 2,722/3,684 (73.89%).

The speed/storage experiment succeeded, but the role difference is material.
Status remains `built_pending_quality_acceptance`. Live streaming detection and
persistence, retention and score-policy decisions, automatic repair and a
production consumer cutover remain unfinished. Use only the explicit
experimental Backtest selection for visual acceptance.


### Load-time merge and normalized prominence proof of concept

New experimental backtests use `merged-point-minmax-v1`; persisted source tables
are unchanged. The loader sorts supports and resistances separately and merges
transitively overlapping bounds. Each group retains the union bounds, arithmetic
mean of every original price and prominence, a deterministic member/role hash,
and member count. Opposite roles never merge. This intentionally has no maximum
width cap; chain width is a quality diagnostic for this proof of concept.

The population is merged before filtering by representative price into
`[max(0, C-r*C), C+r*C]`, with r=1 and C the preceding certified session's last
completed regular-session observation (through 16:00 New York), translated to
the current causal split basis. The prior-session book at 04:00 sets frozen
min/max prominence bounds. Current causal snapshots are merged again as the
source advances; p_norm is clamped min-max normalization using those frozen
bounds. Equal bounds yield 0.5. An empty seed yields null p_norm and cannot
qualify for strategy selection; missing prior close fails closed.

The Backtest launch form exposes `Strategy minimum p_norm` (0-1, default 0.85).
This run-scoped value is persisted in the definition and attached to strategy
levels; raw prominence 4 is not an additional gate for this contract. The chart
indicator form exposes an independent `Minimum p_norm` display filter. Merged
identities and score changes form causal chart segments; a later score does not
rewrite an earlier segment. Old experimental checkpoints require a new run.
This is a backtest/load-time experiment, not a Live producer or persistence
migration. Relative normalized scores are not calibrated success probabilities.


### SUGP visual-validation build, September 6, 2026

`structure_book_16b0d0af05e7` covers 420 certified sessions, January 2, 2025
through September 4, 2026, and 6,503,878 canonical source events. Construction
used two concurrent queries and two threads per query. Cumulative query wall
work was 192.33 seconds, including 187.01 seconds of sequential daily lifecycle
work; concurrent query durations are not elapsed controller time.

The book contains 3,709 level identities and 55,341 interval rows, occupying
1,695,491 bytes. All retained build, validation and continuation tables total
108,851,149 bytes on live_market_ssd. The first draft was stopped after detecting
a duplicate 10-for-1 split delivery and replaced by a fresh immutable build.
The two distinct split actions (August 25, 2025 and August 6, 2026) affected
1,828 and 3,617 carried levels, with zero adjustment mismatches.

Interval, causal-prefix, retry and storage validation passed. Five full-day
consumer continuations, including both split dates, match stored SQL states;
chart snapshots and rewinding also match. At August 21, 2026 07:10 ET, the
load-time book has 269 merged levels and 39 eligible at p_norm >= 0.85.
No SUGP v18 reference was available; this is not a v18 parity claim.

Runtime evidence: `D:\TradingML\runtimes\structure-validation\sugp-clickhouse-book-20260906-v2`
contains `report.json`, `validation.json`, and `consumer-validation.json`.
Choose Experimental ClickHouse / SUGP in the Backtest form. Construction retains
separate source levels; same-role overlap merging and normalization occur only
at load time. The initial form must be refreshed to discover a newly built book.
