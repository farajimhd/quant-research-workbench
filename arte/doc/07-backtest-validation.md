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
