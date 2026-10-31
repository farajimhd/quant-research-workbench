# V60: market-driven feed-forward evolution

Implementation in progress. This package is **not yet qualified for a campaign**.
The governing specification is `docs/trading/v59-strategy-execution-audit.md`.
V59 was gracefully stopped before implementation; existing receipts are retained.

## Preserved experiment

Global-only, 24 training / 12 display-only validation sessions, 6,144 fresh
founders, 1,024 successors, 200 generations, two workstation replay lanes.
Total/per-action node budgets remain 1,024. Parenting retains 25% ticker
specialists and 75% global ranks. Validation does not affect evolution.

The objective remains half the ticker-date logarithmic reward plus half the
session logarithmic reward, minus account drawdown. Targets remain $2,000 and
$5,000 respectively, with logarithmic coefficient 0.1.

## Construction authority

`feature_registry.json` pins all 957 source IDs, their permitted status,
construction bounds, basis, history dependencies and readiness conditions.
`authority.py` exposes only the 834 permitted inputs to constructors. Raw market
values are not clipped to construction bounds. Engineering caps still require
the training-only coverage qualification before a campaign can start.

The registry restores price-based indicators, EMAs, ATRs and bid/ask/spread.
V7 levels and the agreed explicit market-time/date-event features are excluded.
Actual position state remains available without recurrent edges. Order feedback
and general elapsed-clock inputs are excluded. Reserved storage slots are zeroed
and cannot be referenced by a valid genome.

## Minimal execution decisions

The deterministic controller owns a standing remainder until filled or superseded
by an exit, protective stop, or terminal liquidation. Repeated buy signals do not
increase an outstanding order. Exit takes precedence over a pending buy. An
unfilled initial buy whose stop is no longer below its executable ask is cancelled
by strategy admission with an explicit reason. No arbitrary replacement stop is
inserted. Re-entry waits three seconds after the final exit fill.

Candidate-defined buy/sell limits use the current causal closing quote as their
reference. Stop and target prices use candidate parameters and causal history.
Protective exits use the candidate sell-limit rule; terminal liquidation uses a
deterministic marketable closing-quote limit. Targets retain the candidate's
reduction fraction. Partial fills are reported through actual position state.
Only subsequent candle closes may fill a remainder; liquidity from one quote
snapshot is consumed once. Missing quotes do not fabricate fills or remove a
candidate from fitness. An incomplete terminal ledger is an explicit data failure.

## Acceptance gates still required

- Remove all runtime dependencies on prior research versions, including dynamic
  source references; exercise the isolated deployment.
- Wire the construction authority and full unique-population builder into every
  production path and verify deterministic resume.
- Implement and parity-test the CPU and GPU minimal execution contracts.
- Reconcile every F01–F19 finding with executable tests and diagnostic evidence.
- Preserve and test the copied V59 terminal features, including validation,
  candidate/session/positions navigation and responsive background reads.
- Profile real 1,024-candidate batches, two concurrent sessions, reproduction,
  publication, loading and full generations on workstation. Target <45 seconds
  per batch without changed network budgets or omitted work.

No V60 campaign or timing claim is authorized by this README alone.
