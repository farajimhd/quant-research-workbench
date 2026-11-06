# V68: missed eligible 1b opportunities

V68 restarts evolution from the exact original V67 founders. It rescores saved
training and display-only validation evidence without repeating the sixfold
market replay. There are no manual candidates or review pauses. Execution,
features, fees, account sizing, protective exits, networks and reproduction
operators retain V67 behavior. Stable storage/input schema identifiers retain
their original names; the experiment ID and source identity are V68.

## Objective

Preserve V67 label-aligned realized profit credit, all realized losses, blocked
request penalties, incorrect ENTRY/EXIT penalties and late-position charges.
Additionally charge $2.50 once for every missed eligible saved 1b pair.

A pair is eligible if at least one saved ENTRY candle has top-five membership,
a valid quote, bid above the minimum stop margin, ask within the fixed $1,000
entry budget, spread within the existing cap and at least one displayed share.
Eligibility is independent of candidate signals, inventory, cash and requested
prices. Missing labels, unselected pairs and opportunities outside that fixed
executable set are excluded. This is an isolated-entry eligibility definition;
it does not guarantee positive realized profit or simultaneous execution of
all opportunities.

Capture requires a filled initial entry order whose submission and first fill
both align to ENTRY for the same eligible pair. Reentries, partial records and
multiple branches cannot capture the pair more than once. Adds and unfilled
signals earn no coverage credit. Positions need not be manually corrected.

`absolute_score = V67_score - 2.50 * missed_pairs`.
`abstention_score = -2.50 * eligible_pairs`.
`score = absolute_score - abstention_score` is the evolutionary fitness.

This reference shift preserves comparisons between candidates evaluated on the
same fixed set while preventing session opportunity density from dominating
behavioral/concentration descriptors. Ticker specialists also receive the
corresponding pair capture credit. Fitness is distinct from raw P&L: a positive
fitness value is not evidence of profitability. Coverage and validation
activity must accompany every generation-10 review.

## Saved founder authority

`prepare_cost_restart.py` requires sealed V67 generation-zero receipts for all
150 training and 38 validation sessions, exact founder genomes, pinned code,
unchanged input and execution contracts, and namespace-only equality of the
replay implementation. It rejects reuse after execution/input code changes.

Each worker uses its resident session targets and scans each shared saved order
journal once with hash verification. Rescoring publishes complete derived
receipts under the new run, recording predecessor receipt/hash and cost source.
Original receipts remain untouched. Prepared summary caches verify those new
receipt hashes on restart. Validation remains excluded from reproduction;
repeated experiment review makes the existing validation set development data,
not an independent final holdout.

## Runtime and qualification

Use 1,024-candidate batches, 12 concurrent session lanes, all 188 sessions in RAM,
and the existing 128 GiB reserve. Startup prepares training then validation,
with one CPU loader per lane and four global startup slots, to bound transient
feature-build memory. Profiling admits all sessions before the benchmark, using
the same production admission method. Pair tables are prepared once per resident
session on CPU and are never candidate features or GPU inputs. Cost attribution
runs in the existing CPU result handoff; no extra market replay is required.

Run the launcher help with bytecode disabled:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v68.prepare_cost_restart --help
python -B -m research.vectorized_backtest.v68.run_train --help
```

After laptop validation, commit and push before deploying. The immutable
workstation deployment/job records the exact commit, source/file hashes,
original founder bindings, supervised dataset hash and resolved runtime roots.
Qualify full-session accounting parity, cost determinism, founder rescore/cache
reuse, all-session admission and warm throughput before starting the campaign.
The run-specific launch command and qualification evidence live under runtimes.
