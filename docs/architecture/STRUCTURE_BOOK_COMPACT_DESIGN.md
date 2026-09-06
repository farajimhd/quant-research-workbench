# Compact historical structure book: design and feasibility contract

Status: design/prototype with opt-in streaming score and interval primitives.
No production authority or consumer is changed; a full new JUNS build is pending.
Scope: user-approved steps 1–3, September 6, 2026; JUNS from January 2025
through the latest certified canonical source day. Full reconstruction, live
activation, and backtest integration remain later approval gates.

## Outcome and authority

One causal book must serve live strategy decisions and historical replay.
Historical batch calculation must preserve the effective input contract and
confirmation boundary of streaming computation. Higher-timeframe swings are
available only after confirmation. Developing buckets are continuation state,
not confirmed levels. Permanently retain closing-book changes, not every
intraday transient or full daily evidence snapshot. Reconstruct an intraday
`as_of` by loading a compatible prior close and replaying the event prefix.

Canonical historical event authority is `market_sip_compact.events_YYYY` after
ingestion certification. Never reopen flatfiles. Historical v18 explicitly uses
SIP order and canonical trade-condition eligibility; it does not require an
archive execution-clock sidecar. Live/historical equality therefore presupposes
equivalent effective inputs, not merely equal ticker and date.

The user stopped the old campaign, then requested deletion of past checkpoint
tables, then explicitly postponed deletion until the replacement is proven.
Keep the old tables during this feasibility study. A compact comparison reference
is not sufficient restart state or a complete v18 checkpoint.

## Current v18 behavior, traced to code

Source: `services/qmd-gateway/src/generic_structure.rs`, feature
`structural-prominence-v18`; historical driver:
`services/qmd_history_gateway/src/bin/structure_checkpoint_campaign.rs`.

1. Events advance in `(sip timestamp, ordinal)` order. Historical sessions
   verify exact count, first/last timestamps, ordinal contiguity, source revision
   and checkpoint chain. Quote events update bid/ask and spread. Last-price
   eligible trades drive reversals, timeframe buckets and level lifecycles.
   High/low reference eligibility is separate. Delayed live reports advance the
   audit cursor without revising structural state.
2. `TradeAggregationRules::resolve` intersects canonical condition rules.
   Form T condition 12 is price eligible outside 09:30–16:00 New York only when
   every additional condition is fully price eligible. Compact decoding ignores
   tokens not mapped to a trade condition; an empty decoded list means regular.
   A SQL implementation must reproduce this behavior, not reinterpret token 0
   as a blanket exclusion. Unknown/ignored-token counts should remain visible.
3. Trade-native reversal distance is
   `min(max(0.75*EWMA(abs move),0.75*EWMA(spread),2*tick),max(2%*price,2*tick))`.
   Zone half-width similarly uses `0.75*move`, `0.5*spread`, one tick, capped at
   `max(1%*price,tick)`. Engine ticks are 0.0001 below $1 and 0.01 otherwise;
   this is engine geometry, not an assertion about every venue's tick schedule.
   Compatible active levels reinforce without moving their original geometry.
4. Ten timeframes run independently of chart interval: 100ms, 1s, 5s, 10s,
   30s, 1m, 5m, 1h, 1d, 1w. Daily/weekly anchors use 04:00 New York, Monday
   for weekly. Intraday buckets use UTC epoch multiples. Each bucket preserves
   the last event at equal high/low. The local pivot detector uses three
   completed populated buckets. High: center >= left and center > right; low:
   center <= left and center < right. Completion is the first eligible trade
   of a fourth bucket. A gap greater than three horizons clears the neighborhood.
   No forced close
   at the end of an offline query is allowed. Correction from executable fixture
   review: clearing is followed by pushing the old current bucket. Consequently
   a gap between left and center is retained; only center-to-right and
   right-to-completion gaps suppress that triple. The SQL prototype reproduces
   this actual behavior rather than the stronger claim in the source comment.
5. Underlying Active/Crossed levels and unbroken active timeframe swings form
   candidates. V18 bypasses score-based candidate, level, track and source caps.
   Clustering sorts by role/price/confirmation/identity and bounds cluster span
   by `max(2*tick(reference),0.0005*reference)`. A confirmed >=1s timeframe swing
   can found a unified level; 100ms is corroboration only. Without such a swing,
   two distinct `(price key,pivot time)` underlying pivots are required.
6. Existing unified geometry remains fixed when reinforced. Candidate/track
   matching requires mutual anchor containment, with numerical epsilon only.
   Nearest and oldest tie-break rules differ between refresh and consolidation
   and must be preserved. Source identity is `(level_id,timeframe,source_kind)`;
   per-identity replacement chooses an equal/newer `last_test_at_ms`.
7. Two lifecycle machines exist. Underlying levels use a tentative cross,
   acceptance (two trades or 100ms), retest, and role flip. Unified tracks use
   adaptive penetration, 2/4 trades, and 100/350ms or volume evidence, followed
   by retest confirmation. Pending states are not silently deleted. Repeated
   calls can still matter without a changed price: elapsed time/counts advance.
8. Unified hold metrics derive from counters using Beta(2,2)/Wilson90. Relative
   quality uses a frozen role-specific distribution. Scores do not limit v18
   membership, but consumers can use them; dropping historical score output is
   a product-contract decision, not an equivalence-preserving optimization.
9. Splits causally rescale levels, tracks, developing buckets, quantities,
   footprints and baselines. Prices multiply by split_from/split_to. Stable
   identities and replay boundaries must survive. Whole-history back adjustment
   must not leak future coordinates into earlier queries.
10. Each session currently serializes the full engine, including every retained
    source list, then validates its hash and persists it. This is a persistence
    choice, not a requirement of the level definition.

History already attempts a persisted certified seed before cold rebuilding:
`cache.rs::structure_seed_checkpoint`. Live focus has daily seed/replay paths in
`structure_focus.rs`. This inspection is not proof of complete end-to-end parity
of current services, which remains a later integration test.

## Retain/remove decisions

| State | Permanent closing history | Continuation requirement |
|---|---|---|
| Book/build/security identity | Retain, immutable | Required |
| Level ID, price/band, role, pending lifecycle | Retain when changed between closes | Required |
| Origin/confirmation clocks, causal cursor | Retain with explicit meaning | Required |
| Developing buckets and active/previous swings | No display history | Required until superseded |
| Pending cross/retest timers and counts | Only selected visible state | Required for exact transitions |
| Bid/ask, EWMAs, directional-leg candidates | No display history | Required |
| Session profile and footprints | No embedded daily display history | Dependency audit required |
| Source lists | Do not embed in permanent closing rows | Cannot delete yet: dedup, merge, independent-pivot and aggregate dependencies exist |
| Hold/relative quality and baseline | Separate versioned attributes if needed | Required if preserving score-dependent strategy results |
| Applied splits and source lineage | Reference a compact immutable manifest | Required |
| Intraday-only level transitions | Temporary recovery/trade audit as needed | Replayable after certified close |

The proposed small closing table is NOT an exact restart snapshot. A sufficient
replacement for the source arrays has not been proven. Replacing lists with
aggregate sums alone loses replacement/deduplication semantics. This is a design
gate, not permission to drop data and accept changed results.

## Production persistence proposal

Use explicit workload policy `live_market_ssd` and verify table policy and actual
parts before writing. Do not introduce one partition per ticker/day.

### Closing-book changes (authoritative output)

Suggested columns: `book_version`, `security_id`, `ticker`, `level_id`,
`from_close_date`, `to_close_date`, `price`, `lower`, `upper`, `role`, `lifecycle`,
`pending_role`, `pivot_at`, `state_confirmed_at`, `source_cursor`, `revision`.
An absence/removal is explicit. Distinguish output-field changes from full-engine
state changes. A final observed interval endpoint is a coverage horizon, not a
claim that the level disappears tomorrow.

Measured identity issue: the JUNS v18 reference contains 778 excess occurrences
of `(session,level_id)` over 420 closing books, some with different geometry.
Therefore that pair is not a valid unique key for a lossless migration. The
prototype run-length encodes the full selected state tuple and its multiplicity;
it does not invent cross-session identity or discard duplicate occurrences.
Production requires an unambiguous episode-identity contract before adopting
ReplacingMergeTree keys. Existing IDs alone cannot safely deduplicate writes.

Initial physical design to benchmark:

- `PARTITION BY cityHash64(security_id) % 32`: a bounded partition count and all
  versions of one level colocated. The bucket count is a benchmark parameter,
  not a justified final optimum yet. Ticker-to-security identity is pinned.
- `ORDER BY (book_version, security_id, from_close_date, level_id)` for one
  instrument and bounded historical starts. Add the known partition bucket
  predicate to permit pruning. Stable IDs, not ticker spelling alone, define
  identity. The validity end remains a residual filter.
- Benchmark date-first versus level-first sorting and an alternate projection
  on `(book_version,security_id,level_id,from_close_date)`. One-ticker tests do
  not establish market-wide partition or interval-index performance.
- Avoid partitioning long-lived intervals only by creation month and then
  filtering to the requested month: that would lose older surviving levels.
- Fixed typed columns; Float64 preserves existing geometry until an explicit
  numeric migration is approved. Do not quantize bands to save storage.

For immutable historical builds, insert closed intervals into an isolated
revision and atomically select the validated build via the registry. For live
closing publication, append revisioned changes; a deterministic read contract
must resolve replacements before filtering validity. Background merges are not
the correctness boundary. Publication includes a manifest digest and row counts.

### Coverage and continuation

Coverage is one row per `(book_version,security_id,session)`, with predecessor,
canonical source digest, condition/split manifest, published output revision,
continuation digest, processing cursor and status. Zero levels is distinct from
missing coverage. Mark only a complete session certified.

Continuation snapshots are separately versioned, periodic and restart-safe.
Their exact compact schema awaits dependency proof. Temporary live journals may
be released only after acknowledged state/output publication and verified replay
coverage. Trading audit evidence has separate retention.

## No-lookahead query contract

For session opening, load the last certified completed session before opening,
not a book retrospectively filtered by current-day survival. Replay to `as_of`
using ordered canonical/live events and the matching calculation contract.
Developing higher-timeframe state may load; future-confirmed levels may not.
Strategies never receive future `valid_to` or later scores. A late canonical
correction publishes a new build; reproducing an earlier backtest pins its build.

## Reaction prominence and split versioning implementation

The user selected a single reaction-prominence score instead of carrying the
old hold/relative-quality projections into the new compact book. This is an
output contract change; existing strategy filters are not silently redirected.

`structure_prominence.rs` implements `reaction-prominence-1`. The denominator
is the mean true range of up to 14 completed populated one-minute buckets,
floored by the tick at contact. At least one completed bucket is required.
Current-minute prices do not enter their own denominator. Missing minutes are
not fabricated; the completed-bucket queue carries across sessions. A split
scales that queue, the previous close and an active encounter's frozen range.

An observed trade inside the band begins an encounter when a denominator is
available. The favorable excursion is measured from the appropriate band edge.
Its running maximum divided by the frozen range contributes provisionally.
A departure of at least one frozen range qualifies a subsequent band return
to finalize the old encounter and begin a new one. Subthreshold contact noise
does not create additional encounters. An engine-accepted break or role change
finalizes the current contribution. A tentative cross alone does not finalize it.
The score is `ln(1 + completed_contributions + current_best_contribution)`.
It ranks evidence; it never removes levels or changes v18 construction.

The shared engine enables this through `enable_compact_book()` before the first
event. Opt-in continuation and per-level score state serialize with checkpoints;
disabled fields are omitted, preserving the old checkpoint representation.
Sequential episode identities distinguish separate tracks that share a v18 ID.
The counter is part of continuation state and must be scoped by immutable build
and security identity in storage. On consolidation the retained track retains
its score; incoming track scores are not summed because their encounters may
overlap. Source membership is still uncapped. Scoring starts at observed level
creation, not retrospectively at an earlier pivot timestamp.

`scripts/structure_prominence_sql.py` implements the same transition function
using a bounded ordered ClickHouse fold. It consumes ordered encounter inputs,
including lifecycle acceptance and causal volatility; it does not discover the
entire book or produce these inputs from raw events by itself. Synthetic SQL
prefixes are compared against the actual shared Rust scorer using
`structure_score_fixture`. These are equivalence fixtures, not a JUNS benchmark.

`structure_book_intervals.rs` provides closing-row coalescing and split updates.
At the effective boundary it closes original survivor rows and opens adjusted
versions with the same episode identities. Scores stay unchanged. It records
split identity, effective timestamp, ratio, affected count and canonical
before/after hashes. Reapplication is a no-op only when identity and terms
agree. Changed terms or a late split require rebuilding the affected history.
Updates are prepared on a copy to avoid leaving partly adjusted state on error.
These are shared persistence primitives; the database publisher and full-run
repair orchestration are not yet wired to them.

End-of-day output does not suffice for exact engine continuation. V18 source
arrays remain in its working state; this implementation does not claim that
they have been replaced with sufficient compact aggregates.

## Repair contract

Certification by the canonical ingestion updater triggers coverage comparison.
Repair missing/incomplete/changed sessions from the last compatible certified
predecessor, propagate forward until full continuation-state convergence, and
publish atomically. No canonical source yet means an exposed gap, not a stale
book presented as current. No fallback to retained flatfiles.

## Feasibility experiment and limits

`scripts/prototype_structure_book_clickhouse.py` runs from the laptop; only
metadata/counts/profiles leave ClickHouse. Each query has a 2 GiB memory ceiling,
4 threads by default; two concurrent jobs are the initial bounded setting.
All outputs are in an isolated `structure_feasibility_*` database and the laptop
runtime root. No production consumer points at these outputs.

Run from the repository with its configured Python environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/prototype_structure_book_clickhouse.py --ticker JUNS --start 2025-01-01 --end 2026-09-06 --workers 2 --threads 4 --runtime D:\TradingML\runtimes\structure-validation\juns-clickhouse-review
```

Use a fresh runtime name for a new experiment. The workstation environment file
is the default credential source; `--env-file` can select another authorized
machine configuration. Credentials are never written to the experiment output.
`report.json`, `source-manifest.json` and the executed SQL files record the
measurements and pinned inputs. The September 6 accepted experiment and readable
`RESULTS.md` are under
`D:\TradingML\runtimes\structure-validation\clickhouse-juns-feasibility-20260906-e`.

Independent experiments:

1. Canonical events -> ten bucket tables -> SQL adjacent-bucket candidates.
   This measures the set-oriented portion, not adaptive reversal, candidate
   consolidation or the complete lifecycle engine. A SQL candidate is not a
   published level. JUNS has an August 7, 2026 reverse split; split-crossing
   neighborhoods are explicitly tagged diagnostic, not certified equivalent.
   Their exclusion from any accepted output would require separate treatment,
   not silently dropping them. Floating volume reduction is not bitwise parity.
2. Existing v18 daily checkpoint -> narrow closing geometry/role/lifecycle rows
   -> consecutive closing intervals. This measures storage independently. Exact
   bidirectional multiset reconstruction must match every selected closing row.
   It does not validate the omitted score fields, source arrays or restart state.

Source verification uses New York dates derived from SIP timestamps, not the
UTC `event_date` partition alone. Per-session actual row count, unique ordinals,
ordinal endpoints and timestamp endpoints must match certified continuity. The
SQL reader uses `merge()` restricted to explicitly selected canonical yearly
tables; it never reads any raw flatfile. Baseline certificate predecessor links
and source counts are checked, but original full-payload hashes are not freshly
recomputed by this narrow SQL extraction.

The initial run is intentionally rejected if invariants fail. Uncertain writes
are never blindly retried. A failed/interrupted run requires a fresh isolated
runtime; a completed run can revalidate without reinserting completed units.
This fail-closed prototype is not yet the resumable production repair runner.

The full v18 state machine cannot be replaced by adjacent-column comparisons.
A fully database-side exact implementation (e.g. compiled database extension or
explicit stateful transform) is not yet demonstrated. No external raw-event
fetching, extension installation or alternate detector is silently introduced.
The feasibility result must report this gate even when SQL and storage timings
are excellent. Running the controller on the workstation does not solve this
semantic dependency by itself.

## Acceptance before widening scope

- Exact causal book comparison on mature books, pending states and split dates.
- Exact resumed/uninterrupted continuation including higher timeframes.
- Authoritative count/order/identity/source-revision checks.
- As-of opening and intraday checks; no future endpoint/score exposure.
- Storage, row growth, query latency, memory and wall-time measurements with
  server concurrency recorded. CPU time summed over queries is not wall time.
- No full campaign or deletion of unrelated market/audit data by this prototype.
- User review of unresolved algorithm and retention decisions before steps 4–7.
