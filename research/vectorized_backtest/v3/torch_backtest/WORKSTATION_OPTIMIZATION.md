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
- The copied source contract retains its declared LGHL research exclusion for
  broker identity/tradability ambiguity. Verify the complete pinned snapshot
  before applying it; exact excluded identities and reasons remain in provenance.
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

The `inplace` writer declares the complete append (rank, rows and scatter) as
an exclusive buffer mutation through a v3-owned Torch custom operation. This prevents compiler functionalization from
copying the entire ledger before both scatter updates and back afterward.
The full append boundary also avoids a reproduced Torch2.12 code-generation
failure when fusing flattened indices at833 tickers. A second reproduced compiler
bug underallocated a native scatter view after automatic layout padding. This
graph explicitly disables that padding; tensor shapes, financial math and
fullgraph/CUDA execution remain unchanged. Disabling padding without the full
append boundary did not fix the first bug. The implementation calls the same native Torch operations, with no additional
dependency, account-math change, host transfer or per-ticker Python loop.

`profile` prepares a real TRAINING session and compares the same candidates:
inline rules/atomic logging against inline rules/explicit in-place logging.
`--profile-all-modes` also measures inline rules/unique logging and precomputed
rules/unique logging. Earlier full-session measurements rejected the latter:
74.50s replay plus49.28s gate preparation gave123.78s/objective. It compares complete ledgers and financial/activity/holding
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

## Qualified replay and blocked campaign (2026-10-03 UTC)

Engine commit `a90b3921a60d77ab1516e57f0f4a03c58a6b51ae` passed 134 tests.
Real September 3 premarket qualification used 19,800 one-second slots, 833
admitted tickers and 64 candidates. Three complete replays per mode gave:

| Component | Original atomic ledger | Explicit in-place ledger |
| --- | ---: | ---: |
| Median prepared replay | 79.80 s | 45.71 s |
| Compilation/setup | 5.22 s (cached) | 49.60 s |
| Rule precomputation | 0 s | 0 s |

Complete fill-ledger and financial/activity/holding parity passed. The prepared
replay reduction is 42.7%. Source preparation was separately 240.17 s; it is not
included in replay timing and prepared inputs are reused in the search. The
historical v2 report was 69.62 s at the same market/population dimensions, but
policy differences make that historical comparison less controlled. This is the
fastest measured qualified variant, not a claim of a theoretical optimum.

The full random search was launched through SSH in a verified visible workstation
console with the progress panel. Its first training-day preflight stopped on
missing certified prior V7 coverage for `2026-07-30:BKYI`. No objective backtests
or genetic generations completed. The process exited and its task registration
was removed; the checkpoint, console receipts and failure evidence are preserved.

Job root:
`D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v3/optimization_jobs/20261003-a90b3921a-search`.

Do not resume until the source owner supplies and certifies the causal prior seed.
Never substitute future levels, fabricate an empty seed, exclude BKYI or shorten
the session list to bypass certification. A read-only catalogue-wide V7 coverage
metadata audit is stored as `source_dependency_audit.json` under the job root;
planned-population misses are potential dependencies, while the admitted-watchlist
preflight confirms actual required inputs. Other source gates must still pass.

## Exchange-only optimization scope

All v3 Session requests now default to `regular_us_exchanges_only=True`.
The complete pinned preopen snapshot hash and availability are checked first.
Then declared exclusions and a fixed allowlist of US stock exchange listing venues
select USD `stk` identities. OTC (`otclnkecn`), ATS routes (`arcaedge`, `ibeos`,
`t24x`), foreign/unknown venues, currencies and malformed identities are excluded.
The allowlist and every rejected identity are recorded in population eligibility
provenance. Missing identities and ambiguous eligible listings still fail closed.
This uses the pinned listing venue, not current reference metadata or a claim about
issuer domicile/primary listing. ADRs and stock-typed products are not separately
classified by the snapshot. The optimizer cannot mutate universe eligibility.

Filtering occurs before the ClickHouse price/squeeze scans, so excluded tickers
never enter the watchlist, execution certification or downstream data projection.
The Session flag participates in cache identity; source/code hashes force a new
campaign and qualification. The old BKYI failure remains valid evidence for the
previous broader universe, rather than a defect to repair in canonical data.

## Approved broker approximation

New campaigns use 25% of completed eligible interval volume shared by all
buy/sell orders and retries per account/ticker. This approximates the app.
The cap is fixed outside the genome. Requalify new source before optimization.
