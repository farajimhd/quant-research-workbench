# V56: V53 graphs with a 1024-node budget and selection risk caps removed

V56 retains V53's five independent recurrent action graphs, rich operators,
structural mutation, mask crossover, OR/AND/XOR/XNOR composition, species and
ancestry rules. The version-local config raises both MAX_NODES (per action) and
MAX_STRATEGY_NODES (total) to 1024. Maximum depth remains 512 and fan-in remains
16. Existing V53 payloads retain their compatible schema and exact identities.
No shared V42 config or existing V53 source is changed.

Selection no longer rejects a candidate because recorded maximum position risk
exceeds $300 or recorded maximum stop distance exceeds 20%. This applies to both
the global aggregate and local objective. Finite/nonnegative financial metrics,
valid terminal liquidation, and the active-parent requirement remain enforced.
Fitness remains exactly net P&L - gross losing-position dollars - 2 * equity DD.
Risk and stop-distance measurements remain available as diagnostics.

The rerun also disables the $300/20% execution entry/add admission limits via
risk_limits_enabled=false. Stops, quote validity, commissions, $1000/$200 sizing
and terminal accounting remain intact. The protected CUDA financial kernel still
records risk diagnostics; its admission envelope becomes 100% distance and an
unbounded dollar budget, so valid positive stops cannot trigger the old limits.

The authorized laptop run is 2026-08-13 only, 50 total generations, seed 2021,
population 1024, and no validation. It restores the exact original V53 6144
pre-selection genomes and saved initialization RNG. All founders must be replayed
under the new execution: prior financial receipts are forbidden as fitness.
Original populations, receipts and earlier V56 campaign remain untouched.

The next population keeps 102 global elites, 205 fresh candidates and 717
crossover slots. A balanced schedule assigns approximately 25% of crossover
parent draws to ticker ranks and 75% to global ranks. Tickers rotate; only
active, terminal-valid candidates can parent. Species, ancestry, duplicate and
budget checks remain. Siblings share their selected pair; failed mating slots
use existing mutation fallbacks, so realized fractions are reported separately.
Ticker scores use P&L - gross losses - 2 * equity drawdown, with per-listing
mark-to-market peaks/drawdowns tracked every replay clock. Offspring still trade
the whole session. Global elites use the unchanged global objective.

Replay batches stay at 512. Extra ticker peak/drawdown buffers are bounded by
batch size and listing width; CPU and captured CUDA paths use the same accounting.
All five actions require at least one node, so a single action in a complete
1024-node individual can occupy at most 1020 nodes when the others use one each.

Generated deployments, profiles, compiler caches, source bindings, checkpoints
and results belong under D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56.
Use a committed immutable deployment and full_launch with --generations 50 and
--comparison-source. The scheduled runtime entry pins code and job hashes;
active-pilot.json identifies its actual paths. Earlier V56 output is preserved
and stopped before assigning the laptop GPU to the new run.


## Full 24/12 campaign

`full_launch --full` prepares a separate `v56-full-v1` job; it does not start it
or stop the pinned Aug13 pilot. Session stage: all 24 training days independently
run 50 generations, with one evolutionary session at a time on each host. The
certified source job's historical 9/15 laptop/workstation allocation is retained;
this is affinity reuse, not a newly measured V56 speed estimate. Each session
starts with 3072 templates and 3072 fresh candidates calibrated on that day only.
Training and validation manifests, all session probes and exact host ownership
are pinned. Validation never participates in this stage.

After all session exports are sealed, global stage runs only on the workstation
for 50 generations. Up to 128 active, terminal-valid candidates from each final
session population are promoted in round-robin order, with no positive-score
gate. Global initialization deduplicates programs and fills the 6144-candidate
screen with fresh random candidates (at least 3072 fresh). Source session IDs,
parents and ancestors are namespaced before mixing; promotion references and
initialization probes enter the checkpoint contract. Export bundles include the
final checkpoint, seal, evaluated population/pedigree, training scores and source
financial receipt. Global promotion rechecks their hashes and score reconciliation.
No session fitness is reused as a global score.

Two persistent process lanes replay independent sessions concurrently on the
workstation GPU. Each owns its session state and CUDA graph; they share no
recurrent or financial state. Lanes have separate memory admission, compiler
caches and bounded host residency. Training replays cover all 24 days for every
candidate. The unchanged global score sums daily P&L, gross losses and twice the
daily equity drawdowns. Ticker ranks likewise sum each listing's local costs
across training days before ranking. Approximately 25% of crossover parent draws
use these ticker ranks and 75% global ranks, subject to existing sibling, species,
ancestry and fallback rules. Elites remain globally ranked.

Every generation freezes successor genomes and RNG before replaying all 12
validation sessions on the workstation, also with two lanes. Validation reports
are display only. Paired mutation diagnostics keep separate outputs and do not
enter parent selection. Switching training/validation/diagnostic output closes
and recreates the lane pool; this favors isolation over retaining GPU residency
across those boundaries. Actual full-workload throughput must be profiled after
launch; two-lane process tests alone do not establish GPU speedup or memory fit.

From the pinned laptop code directory, prepare with:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v56.full_launch --full --source-job <certified-full-job.json> --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56/<full-run> --code <pinned-laptop-code> --workstation-code <pinned-workstation-code> --generations 50
python -B -m research.vectorized_backtest.v56.campaign_launch --job <full-run>/full-job.json
```

The second command writes hash-pinned node entries and `launch-plan.json`, without
starting tasks. Synchronize immutable code and the prepared job/entries to the
workstation after committing and pushing source. On each matching host, use the
exact command and working directory in `launch-plan.json` to install/start its
scheduled node. `campaign_launch --job ... --node <host> --preflight` checks the
contract and reports GPU memory without launching optimization. Run it on both
hosts before starting. Do not overlap this full job with an existing optimizer.

Per-node status and completed export bundles live under `exchange/`; per-session
status lives under `sessions/<day>/full-status.json`; global status lives under
`phase-2/full-status.json`. Node status includes active, queued, completed, skipped,
retried and failed units. Create `STOP` under the campaign or exchange to stop;
existing seals remain. Resume by auditing exact exited owners, clearing STOP,
and rerunning the same pinned node entry. Completed session exports are verified
and skipped; incomplete sessions/global evolution resume their sealed checkpoints.
Missing or conflicting evidence fails closed. Crash-left owner locks require an
explicit ownership audit before removal; they are never stolen by timeouts.
