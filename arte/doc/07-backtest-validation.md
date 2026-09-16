# Backtest, debugging, and validation

## Shared core

Use the same event normalization, bars, indicators, V7 streaming algorithm, detectors,
strategy, portfolio rules, and OMS contracts in Live and Backtest.
Inject source, clock, execution adapter, and persistence interfaces.
Do not build a second strategy implementation for historical runs.

Historical V7 extraction is a shared historical module. It prepares seeds for both
future Live sessions and historical runs. It is not the intraday streaming updater.

## Replay modes

| Mode | Input | Claim |
|---|---|---|
| Historical | Pinned reconciled REST generation | Behavior under available historical fields |
| Recorded-live | Original observation sequence and field availability | Reproduction of observed decisions |
| Fault simulation | Pinned input plus explicit fault schedule | Behavior under modeled failures |

Historical data cannot prove original local receive latency. An unavailable check is
reported as unavailable, not passed. Synthetic delays are labeled as simulated.
Corrections and REST enrichment must not appear before their declared availability.

## Run identity

Pin code release, strategy/configuration, algorithm versions, source generation,
reference generation, historical seed, clock model, fill model, and cost model.
Persist input capabilities and declared approximations.

The run contract includes decision, intent, rejection, order, fill, position, and
account-state schemas. Live and historical use the same schemas. Mode and execution
origin differ explicitly. Compare semantic hashes without wall-clock log timestamps
or broker-generated IDs.

## Fresh-session startup document

`arte.backtest-startup.v1` binds the run manifest hash, effective configuration
map, initial simulated accounts, price precision, fill model, cost model and
resource limits. Startup requires its independently supplied expected hash.
An accepted hash proves content identity, not strategy approval or broker cash.

The JSON document is limited to 16 MiB. Account and configuration maps contain
at most 4096 unique entries each. Duplicate keys and unknown top-level fields
are rejected. Field ordering and whitespace do not change the semantic hash.
The session constructor subsequently checks effective market/policy bindings
and account compatibility. It returns a paused session.

The startup adapter persists content-addressed 1 MiB chunks and publishes a
root only after chunk readback. One immutable slot per manifest prevents changed
startup inputs from replacing the original. Exact retries are idempotent.
Publication requires extraction and durability acceptance plus cooperative
ownership. The adapter is not a distributed compare-and-swap authority.

Migration 019 is unapplied. Run orchestration must publish this document before
advancing the session and retain its expected identity in recovery metadata.
Those orchestration and recovery links are not implemented yet. Storage readback
does not prove strategy acceptance or power-loss durability.

## Explicit simulated costs

The initial cost contract is `arte.per-fill-costs.v1`. Its hash must match the
run manifest. Playback rejects a cost binding from another manifest, even when
the run name is the same. No model is selected implicitly.

The model declares currency, currency precision, fixed charge per fill, per-share
rate and minimum charge per fill. Variable charges round upward to currency minor
units. Add the fixed charge, then apply the minimum. Each partial fill is charged
separately. Cancelled orders with no fills have no modeled fill charges.

These are hypothetical execution costs, not an IBKR commission schedule. The
contract does not model order-level minimums, rebates, taxes, FX or settlement
delays. A zero-cost experiment must explicitly pin zero rates. It is not evidence
of realistic trading performance. Account/instrument currency compatibility must
be certified before modeled cash settlement.

Charges accumulate only after fill-journal readback and position projection
succeed. Exact publication retries must not add fees twice. Gross P&L remains
distinct from costs. Net cash conversion rounds positive fractional minor units
downward and negative fractions toward minus infinity before subtracting fees.

## Fast repeated runs

- Prepare source arrays once and reuse immutable data by dependency fingerprint.
- Cache indicators and structure only when all their dependencies match.
- Parallelize independent instruments and parameter runs.
- Preserve ordered account reservations across ticker candidates.
- Support seeded deterministic randomness where the fill model requires it.
- Keep preparation, playback, journaling, and UI publication timings separate.
- Do not skip required pre-admission history to improve apparent speed.
- Do not capture full engine snapshots on ordinary status requests.

Historical replay should use explicit merged-event ordering and bounded lookahead
buffers. A display interval must not change decision semantics.

## Debug evidence

Every decision references its causal input boundary, feature values, V7 seed/state,
account state, rule results, and chosen action or rejection reason.
Deduplicate immutable evidence blocks. Do not dump the entire book per decision.

Debug supports pause, step, inspect, and resume for Backtest. Live inspection is
read-only unless an explicit authorized control command is submitted.
Saved Review reads published evidence. It does not restore the execution engine.

## Required tests

| Gate | Required evidence |
|---|---|
| Event codec | Precision, fractional sizes, conditions, source capabilities and round trips |
| Cross-transport identity | REST/WS overlap, duplicates, resets and corrections |
| Causality | Prefix invariance; no future seed, metadata or quote leakage |
| V7 port | Fit tolerance, level identity, transitions and discrete decision parity |
| State recovery | Continuous run equals checkpoint restore plus replay |
| Gap repair | Restart-safe pagination, overlap, derived rebuild and no stale orders |
| Portfolio | Concurrent tickers/accounts cannot overspend or share fill state |
| OMS | Duplicate submits, unknown replies, partial fills and bracket recovery |
| Isolation | Backtest cannot access live control/credentials or exceed resource budgets |
| Performance | Full-session and burst latency; cold and warm backtest throughput |
| UI independence | Closed/disconnected UI does not change execution results |
| Packaging | Build and test after copying only this project root |

Numerical tolerances do not permit unexplained changes to a trading decision.
V7 optimizer changes require both numerical and discrete-state comparison.

## Strategy acceptance

Use diverse sessions, realistic costs, unseen holdouts, and retained failed experiments.
Do not promote from a familiar ticker or a short profitable interval.
Correct engine behavior does not establish a profitable or robust strategy.
Live activation needs a separate explicit approval after engineering gates pass.
