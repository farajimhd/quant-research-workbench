# V67: approved 1b supervision over V66 execution

V67 is a fresh, source-isolated evolutionary campaign. It preserves V66 features,
networks, causal market observations, fills, whole-share sizing, fees, stops,
rank-loss liquidation, 20% score-independent exploration, and lossless receipt
storage. It does not resume V66 fitness. Manual offspring and review pauses are
rejected by the job contract.

## Objective

Only the approved saved 1b `action` column supplies supervision. Rejected pairs
remain WAIT. Neither original 1a actions nor reconstructed opportunity gains are
used. The immutable label dataset is pinned by `supervision.APPROVED_SHA`; its
historical review-only metadata is preserved, and the new job records acceptance.

Targets attach to the completed candle at the candidate's decision clock. An
entry must be aligned both at submission and fill. Its selected pair owns the
position's supervision through close. Additions earn credit only inside that
same pair's ENTRY window. Each closed episode contributes all negative net P&L
and positive net P&L multiplied by aligned buy notional / total buy notional.
This includes all actual fees and execution costs. Unaligned initial entries
cannot acquire credit by adding later.

The original ticker/session logarithmic concentration limits apply to this
supervised reward, with equal weights. Deduct:

- Existing $5 execution-blocked-request penalties.
- $5 for each distinct invalid ENTRY/ADD signal on a labelled candle.
- $5 for each distinct discretionary EXIT/REDUCE outside the owned pair's EXIT.
- 0.00001 times dollar-seconds of capital held past the owned pair's last saved
  EXIT candle ($0.01 per second per $1,000 of inventory at average cost).

Request penalties use rising edges of each action bit and pair, avoiding repeated
per-second charges for a persistent signal. Execution-blocked penalties describe
a separate execution failure and retain V66 behavior. Protective stop/target,
rank-loss, and terminal liquidations are exempt from discretionary-exit charges;
late exposure still counts until actual liquidation. Stop-update requests are
execution management, not an ENTRY/EXIT decision target. WAIT/HOLD earn no bonus.

Missing/invalid/warm-up labels are masked, not treated as WAIT; entries there
receive no positive credit. Saved actions outside the supervised premarket
horizon are never invented. No paired EXIT means no invented late deadline.
Raw P&L remains separately reported. Founder quality, session champions, ticker
specialists, and behavior equivalence consume the supervised objective/evidence.

## Runtime and qualification

The inherited configuration uses 1,024 candidates per batch, 12 concurrent
session lanes, all 150 training + 38 validation sessions resident in RAM, and
bounded host loading (2 workers/lane, depth 32, global admission limit inherited).
Initialization uses 6,144 fresh founders and selects 1,024 through full training
replay. Validation is reporting-only. The campaign runs 300 generations.

Hash-verified label arrays are loaded once alongside each resident session. They
remain CPU-only, absent from feature tensors, GPU transfers, calibration, and
candidate inputs. Scoring runs inside the existing Numba accounting loop. No
tick-level label I/O, external lookup, or extra candidate replay is used in a
normal run. Resident label memory is reported in quote-coverage receipts.

After committing, pushing, and hash-verifying an isolated workstation deployment:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v67.prepare_shard_campaign --source <V66-job.json> --root D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v67/<campaign> --code <deployed-code> --workers 12
python -B -m research.vectorized_backtest.v67.profile_global --job <job.json> --output <runtime-profile> --count 1024 --repeat-warm --days <training-days>
python -B -m research.vectorized_backtest.v67.run_train --job <job.json>
```

`--compare-v66-execution` is an instrumented profile-only option: it executes
identical request packets through V66, checks all accounting buffers and ledgers,
and records baseline/scored kernel times separately from first compilation. That
run is not an end-to-end throughput measurement. `--prefetch-all-sessions` checks
all-session RAM residency and requires a lane-aligned training-date prefix.
Use a separate ordinary warm profile for end-to-end throughput.

Source/label hashes, the explicit scoring settings, split identities, and job
configuration bind each run and its caches. Missing or changed targets fail
closed. Changes to penalty scales require a new objective identity and fresh
fitness; no financial interpretation or generalization is implied by tests.
