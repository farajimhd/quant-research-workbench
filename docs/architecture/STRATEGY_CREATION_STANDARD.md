# Strategy creation and numbering standard

Status: draft. Strategy 1 is not published or selectable until all admission
checks below pass. The legacy Candidate 350 remains a historical reproduction
identity, not the new user-facing Strategy number.

## One trading identity

`strategy_number` is the sole user-facing version of a complete trading
behavior. It covers activation, admission, entries, additions, exits, stop and
target selection, sizing, re-entry, rule precedence, required input clocks, and
the effective evaluation interval. A change to any of these after publication
requires the next number. Do not edit the prior specification or dispatch its
number to new behavior. Do not reuse a number after a rejected experiment.

Configuration-row revisions, implementation revisions, QMD product versions,
broker contracts, schema versions, and run IDs remain separate technical
identities. They must be pinned in the run, but never presented as competing
Strategy versions. A run records the strategy number and content digest on
every decision, order, fill, and journal projection. Historical runs keep
their original identities without a retroactive alias.

## Input and execution authority

Each Strategy declares typed input fields, source product, resolution,
effective/observed clock, readiness, freshness, and missing-data behavior.
QMD or an ingestion-owned producer creates bars, indicators, and structural
products; a Strategy only consumes them. If a new Strategy needs MACD at one
minute, QMD must publish and certify that input before the Strategy can be
admitted. Backtest SELECTs certified immutable products and may not create or
repair them. Missing required products fail preflight before workspace entry.
The Backtest market principal has no INSERT/ALTER/CREATE grants.

The default evaluation interval is completed 100 ms boundaries. An explicitly
selected event-mode Strategy can consume events. Every rule's input clock must
be causal at that interval; a completed 1-minute MACD is unavailable until the
minute closes. A forming higher-timeframe MACD at 100 ms boundaries is a
distinct QMD product, not a value silently calculated by Strategy code from a
trade or a later completed bar. Sparse buckets and quote-only boundaries must
not be turned into invented trades or candles.

## Reusable rules and bounded state

Stateless scanner, Signal Stream, Watchlist, and Strategy gates compile from
the same versioned typed rule-set catalog. Evaluate them in bounded columnar
batches across ticker/bucket arrays; produce Boolean candidate masks and
compact reason codes. Avoid row dictionaries, repeated deep copies, and
per-ticker Python evaluation for rejected rows. A rule-set implementation
change that alters decisions requires a new rule-set version and, for every
published consuming Strategy, a new Strategy number.

Only survivors enter the causal state machine for swing sequencing, position
management, stop/target ratchets, OMS, fills, and shared cash. Do not vectorize
across future time or reorder shared financial mutations. Global coordination
must be deterministic across worker counts. The broker consumes completed
persisted liquidity buckets in fixed mode; no aggregate row may be expanded
into fictional quote/trade event ordering. Journal publication is normalized,
ClickHouse-only, bounded, asynchronous from the hot engine, and uses the same
decision/fill contract for Backtest and live trading. Failure or backpressure
is explicit; no silent loss.

## Publication seal

Before publication, create a canonical manifest with strategy number, exact
rule-set revisions, input product/build contracts, interval, broker/fill
policy, code revision, and an approved human-readable rule specification.
Hash its canonical encoding and record the approval and code commit. A digest
is an integrity seal; call it a cryptographic signature only if an actual
signing key and verification authority exist. Runtime registration, Backtest
preflight, and live deployment must compare the installed manifest to the
approved seal and fail closed on mismatch. Never allow `replace=True` for a
published numbered Strategy. No saved configuration may mutate that seal.

Publication requires causal future-tail independence, missing-input rejection,
completed-boundary availability, exact rule-set and strategy unit tests,
live/Backtest decision parity where their input contracts match, broker
partial-fill and shared-liquidity tests, cross-ticker cash determinism,
checkpoint/recovery equality, and app launch-to-terminal-run validation. A
Backtest completing without an exception is implementation acceptance, not
profitability or live-release acceptance.

## Strategy 1 admission blockers

Strategy 1 starts from Candidate 350's entry/lifecycle gates but uses only
completed MACD at the declared 1s/5s/10s/30s boundaries, not its event-time
forming-MACD previews. Candidate 350's $1 current-price purchase floor remains
a vectorized entry gate; a sub-$1 Early Squeeze stays watched without
authorizing a purchase. It replaces initial and rising swing protection with
one tick below the low of the immediately preceding completed price-bearing
30s bar. A missing/empty latest 30s bucket supplies no stop; it is not
silently carried forward. Each disjoint group of three accepted resistances
can also raise the stop under the lowest band in that group. Both paths only
raise protection, and a simultaneous qualifying update chooses resistance.
The target reuses the historical 3/2/1 overhead-resistance ordinal rule,
never moving downward. These selections are drafted in
`src/trading_runtime/strategy_one_contract.py` and are not registered.
`src/trading_runtime/strategy_one_columnar.py` implements a pure necessary-
condition entry mask using completed MACD, liquidity quote/VWAP, cumulative
eligible share/dollar volume, completed 10s/60s eligible-trade rates, executable
spread, prior close, and the last completed 30s low. Its liquidity windows
exclude trades at the exact expired boundary and read only pinned `arte`
liquidity rows. It neither owns activation nor suppresses
management of an existing position. The read-only
`src/backend/backtest_strategy_one_loader.py` now projects one certified
ticker/session through bounded Arrow batches into this mask, including exact
indicator-row checks. Its candidates must still pass Candidate 350's
surviving stateful entry/lifecycle rules. No path yet dispatches this draft
from the application.
Do not dispatch a sparse candidate into
`early_squeeze_momentum.evaluate`: that legacy evaluator authorizes its BOS
and resistance state from event-time `market_data_update` trades and computes
forming MACD previews, while Strategy 1 has completed-bar clocks and no
fictional trade ordering within a liquidity bucket. Port the surviving
activation, confirmed/supported BOS, late-HOD, frozen-gap, entry/add/reentry,
pending-order, and permission gates into a numbered Strategy 1 state machine
with explicit completed-bar and V7 inputs. The columnar mask is only a
necessary condition; its survivor must never submit an order by itself.
The draft completed-bar BOS transition is in `strategy_one_bos.py`. Its
confirmed swing input is not inferred from V7 geometry: a separate
producer-owned `strategy_one_pivot_interval_v1` product is specified with
one row per unique active pivot interval and a coverage-last seal. The
normalizer matches the shared detector's pivot visibility on recorded
completed candles; the producer derivation reads certified 1s ARTE bars.
The producer-side publisher inserts interval rows under an immutable attempt,
reads them back, and publishes coverage last; an uncertain insert cannot
authorize a Backtest read. Backtest now certifies selected ticker coverage,
source bar attempts, scalar content hashes, and causal visibility at preflight
and rechecks its token at launch. Operator provisioning and a full historical
campaign are not yet complete, so this product currently blocks Strategy 1.
`src/backend/backtest_strategy_one_preparation.py` now scans the full certified
universe for completed-bar Early Squeeze starts, loads only episode-bearing
tickers in bounded read-only lanes, and merges compact candidate cursors in
stable boundary/ticker order. This remains preparation, not strategy activation,
portfolio mutation, or a runnable Backtest controller.
For a single flat-start session, V7 prior seed coverage is required only for
tickers with at least one surviving columnar candidate boundary. This is a
necessary-condition reduction, not permission to skip an eligible ticker or
synthesize a missing seed. A producer-owned normalized candidate and coverage
product has been drafted in `arte`; Backtest now requires complete, exact
coverage and pins its content token at preflight, then rechecks that token at
execution. The candidate rule has its own technical digest, distinct from the
unpublished complete Strategy 1 release seal; this lets the producer certify
the expensive reusable mask without falsely claiming that Strategy 1 is live.
Coverage also pins the exact squeeze SQL hash for the requested session end,
so premarket and full-session products cannot be confused.
The producer campaign for the certified August 18, 2026 full session has
published and read-back-verified all 6,100 ticker-days, including empty
candidate sets. Other sessions still require their own exact coverage. The
published Strategy 1 release seal and executable runtime are not yet complete,
so this path currently fails closed. A multi-session
or position-carrying Strategy must define a broader V7 dependency contract.
The read-only full-session workstation profile found 62,072 candidate
boundaries across 957 tickers. Certified candidate lookup took 1.895 seconds;
the former all-boundary fixed market stream decoded 26,488,823 rows in
547.632 seconds. A bounded four-worker SELECT of the same 62,072 exact
candidate market rows took 6.833 seconds. These are data-read measurements,
not Backtest runtime or fill-equivalence results. Candidate rows may drive
entry decisions only; once an order or position exists, the causal coordinator
must continue reading the relevant liquidity and management windows until
that financial state is resolved. Skipping those windows would silently omit
fills, stops, targets, and exits.
`src/trading_runtime/strategy_one_position.py` now owns a pure, deterministic
active-position protection reducer: entry requires the completed 30s stop and
third overhead target; accepted 1s resistance breaks are deduplicated and
ordered causally; three-break and 30s-low stop proposals ratchet upward with
resistance precedence; target re-ranking only follows a completed
price-bearing evaluation bar. It returns amendments for the coordinator and
does not submit orders or write a journal. Stateful activation, surviving
Candidate 350 entry gates, OMS wiring, and run dispatch remain unfinished.
`src/trading_runtime/strategy_one_v7.py` checks each projected level against
its own preflight-pinned prior seed policy. This admits the explicitly approved
provisional V1 seed without relaxing legacy strategies or implicitly switching
to filtered V2; an empty prior seed uses the filtered runtime policy. A stale
projection supplies no entry geometry, while mixed policy or future geometry
fails closed.
The active fixed Backtest preflight also blocks on unfinished ClickHouse-only
runtime/journal recovery. These contracts must be delivered and verified
before Strategy 1 can become selectable. Do not bypass them by launching the
legacy event/SQLite path or synthesizing missing inputs.
