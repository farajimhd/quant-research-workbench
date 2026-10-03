# V3 multi-session workstation optimization

This package is standalone: v1/v2 imports and their runtime directories are not
required. Canonical certification and V7 numerical authorities remain shared
under `src`; copied v3 source/encoding/resource modules are owned by v3. V2 is
preserved. `semantic-squeeze-search-v3-3` supersedes the earlier two-phase search.

## Frozen experiment

- One phase, initialized randomly; no default-policy injection. Each candidate
  runs every training session before selection. The objective uses complete
  training outcomes, never future evidence inside a decision.
- Discover all published V5 dates with matching producer certificates. Catalogue
  presence alone is insufficient: full liquidity, indicators, identity, causal
  V7 seed and Keeper preflight still run. Missing expected trading days fail.
- Premarket is exchange-calendar 04:00 to opening time, New York timezone/DST.
  The last six available dates form chronological evaluation; the remaining
  dates train. A previously observed evaluation set must be labelled as such.
- Initial cash resets to $10,000 on each session. Cash is shared among that
  candidate's positions and evolves causally during the session. Cash/positions
  never carry across dates or between candidates.
- Default budget: 64 candidates, 50 generations, seed20261003. Population and
  generation limits are explicit; immigration cannot extend the budget.
- Three-second discretionary hold floor, measured from each child's first fill.
  Stops and terminal exits are exempt. A target/replacement decision at age3
  cannot execute retroactively; its first execution interval follows that
  decision. Entry-mode hold time is a different pre-entry condition.
- At least one **filled ticker acquisition batch on every training date**.
  Children/additions/partial-fill events do not manufacture activity. A soft
  maximum of20 batches/session discourages excessive activity. Null fitness
  rejects missing activity and terminal exposure, regardless of profit elsewhere.
- Long-hold penalty begins at300s: integrate marked USD exposure beyond the
  threshold, normalize by $10,000×3600, penalize0.01 per resulting capital-hour.
  Drawdown weight0.5, return dispersion0.25, excess-activity weight0.05. These
  constraints/weights are experiment configuration, not mutable strategy genes.
- Feasible candidates dominate infeasible ones. Among infeasible candidates,
  normalized activity deficits and nonflat-session fractions guide search.
  These ranks are not financial objective scores. Only a feasible policy can
  be frozen/reported as the winner. Exhausting the budget without one fails.
- Wide nonnegative price/activity thresholds use declared log1p random sampling
  over the same ranges. All operation/input/policy IDs remain random/searchable.
  This improves coverage of small premarket values without an injected policy.

Default financial objective, averaged over the fixed training sessions:

```
mean(net_PnL / 10000)
− 0.50 × mean(maximum_drawdown / 10000)
− 0.25 × std(net_PnL / 10000)
− 0.05 × mean(max(filled_batches − 20, 0) / 20)
− 0.01 × mean(long_hold_dollar_seconds / (10000 × 3600))
```

## GPU execution and profiling

Account time remains sequential; ticker/candidate/position work is tensorized.
Source tapes are prepared once. Host residency is bounded at320GiB; immutable GPU
residency defaults to48GiB, preserving driver/compiler/state headroom. A padded
working tape copies only one session at a time. Device start/end buffers and
fixed field pointers allow CUDA graph reuse across dates and ticker counts.
Every bind resets account/history state. Padding slots are permanently inactive.

Atomic rules depend only on completed market inputs. Optionally compile their
boolean `[T,B,N]` gates in bounded128-slot chunks before portfolio replay. A lag
or reduction at t only selects completion timestamps≤t. Full-session storage is
not future visibility. Account-dependent actions, cash, reservations, fills,
protection and rotation remain inside the causal tick. This optimization is not
allowed for an atomic input that depends on account state without a new contract.

Original logging atomically adds zero for inactive slots into repeated ledger
locations. The unique-destination writer assigns those inactive slots private
scratch locations; active prefix ranks overwrite distinct ledger rows. Overflow
still fails before publishing results. No fill row is dropped or compressed away.

`profile` prepares a real TRAINING session and compares the same candidates:
inline rules/atomic logging, inline rules/unique logging, and precomputed
rules/unique logging. It compares complete ledgers and financial/activity/holding
metrics, then measures three full-session replays per mode. Compilation, source
preparation and rule preparation are reported separately. The selected mode
minimizes warm objective time, not replay time alone. A bounded GPU trace identifies
remaining kernels. The previous September3 v2 64-lane run took69.62s over19,800
slots and833 tickers; its different grid is historical context, not a controlled
policy-equivalent benchmark. Same-input inline v3 is the controlled baseline.

Full search requires a successful real-session `qualification.json` with matching
code hash, population and graph-step shape. Source changes invalidate qualification
and checkpoint identity; do not patch a running immutable campaign.

## Visible desktop launch and ownership

Validate locally, commit/push, then run v3 `sync_workstation.py`. Its archive
contains only committed source plus shared reader packages, never secrets, notebooks
or generated outputs. Each deployment is isolated and every payload hash is verified.
Install the pinned compiler through that deployment's `setup_gpu.py`; its target
belongs to the v3 runtime, not v2 or the shared Python environment.

`launch_remote.py` dispatches over SSH to an InteractiveToken scheduled task for
the currently logged-in workstation user. That task runs a visible PowerShell
console titled **GPU STRATEGY SEARCH - v3**. SSH-only console launch cannot assure
desktop visibility. No passwords are requested or copied. A unique task name,
launch script, `launch.json` and `exit.json` live in the task-owned runtime job.
An already-owned job refuses a second launcher. Remove the named scheduled task
after its worker finishes; never stop unrelated workstation tasks.

Laptop invocation after substituting the actual hash-verified deployment/job:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
# First use --command profile. Then use --command run and the saved qualification.
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B `
  -m research.vectorized_backtest.v3.torch_backtest.launch_remote `
  --checkout <verified-v3-deployment> --job <new-v3-runtime-job> `
  --command profile -- --population 64 --generations 50
```

The dashboard distinguishes completed generations, objective candidate evaluations,
candidate-session replay results, unique tensors, current clock cursor and source
preparation. Completed best metrics show aggregate/worst-session P&L, worst drawdown,
filled batches, child positions, fill events, open positions, sold-share-weighted
holding age and overdue capital-hours. Without a feasible winner it says so; lane0's
last completed-session metrics are observations, not partial fitness. GA configuration,
rejections, memory, compilation/rule/replay/bind timing, checkpoint and ETA remain visible.
Refresh is1Hz, diagnostics are redacted, redirected output is plain, and compact
terminals retain status/progress/outcome before secondary detail.

`monitor --resume <job>/experiment` reopens the same display read-only. Exiting the
monitor never changes worker status. Final status, full receipts, errors and resumable
state remain in runtime artifacts even if the desktop window closes.

## Restart and evaluation boundaries

Save the initial random population/RNG before replay. Each completed session has
an atomic receipt with its population and source fingerprints. An interrupted
generation reuses matching session receipts; no partial generation is selected.
Completed generations seal fitness, feasibility, repairs and next-population RNG.
`--resume` requires the exact immutable configuration/source/code identity.
Prepared CPU tapes are published as job-owned hash-verified snapshots after full
source certification. Resume rechecks the producer build certificate and exact
code/request/manifest identity before loading those same pinned inputs. Atomic
directory publication preserves interrupted staging files; corruption fails
explicitly rather than silently rebuilding or changing source authority.

After `winner.json` is frozen, compare `[default, optimized]` on training and the
chronological evaluation set. Evaluation data/P&L never feeds mutation, selection,
stopping, objective weights or constraint adjustment. Validation results diagnose
generalization, not profitability or live readiness certification.

For all timestamp details see [TIME_AND_SEARCH_CONTRACT.md](TIME_AND_SEARCH_CONTRACT.md).
