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
forming-MACD previews. It replaces initial and rising swing protection with
one tick below the low of the immediately preceding completed price-bearing
30s bar. A missing/empty latest 30s bucket supplies no stop; it is not
silently carried forward. Each disjoint group of three accepted resistances
can also raise the stop under the lowest band in that group. Both paths only
raise protection, and a simultaneous qualifying update chooses resistance.
The target reuses the historical 3/2/1 overhead-resistance ordinal rule,
never moving downward. These selections are drafted in
`src/trading_runtime/strategy_one_contract.py` and are not registered.
`src/trading_runtime/strategy_one_columnar.py` implements a pure necessary-
condition entry mask using completed MACD, liquidity quote/VWAP, prior close,
and the last completed 30s low. It neither owns activation nor suppresses
management of an existing position. The read-only
`src/backend/backtest_strategy_one_loader.py` now projects one certified
ticker/session through bounded Arrow batches into this mask, including exact
indicator-row checks. Its candidates must still pass Candidate 350's
surviving stateful entry/lifecycle rules. No path yet dispatches this draft
from the application.
`src/trading_runtime/strategy_one_position.py` now owns a pure, deterministic
active-position protection reducer: entry requires the completed 30s stop and
third overhead target; accepted 1s resistance breaks are deduplicated and
ordered causally; three-break and 30s-low stop proposals ratchet upward with
resistance precedence; target re-ranking only follows a completed
price-bearing evaluation bar. It returns amendments for the coordinator and
does not submit orders or write a journal. Stateful activation, surviving
Candidate 350 entry gates, OMS wiring, and run dispatch remain unfinished.
The active fixed Backtest preflight also blocks on unfinished ClickHouse-only
runtime/journal recovery. These contracts must be delivered and verified
before Strategy 1 can become selectable. Do not bypass them by launching the
legacy event/SQLite path or synthesizing missing inputs.
