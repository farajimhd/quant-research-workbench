# V20: session experts followed by global evolution

V20 is an isolated copy of V19's feature, genetic-operation and FP64 financial kernels. The cost and execution rules remain unchanged. This version changes search orchestration and parent selection.

Phase 1 evolves each of the 24 training sessions independently for 30 generations by default (configurable through 500). Each session starts with 1,024 unique random candidates. Two persistent worker processes each own one session task at a time. A task loads/uploads its session once and retains it across generations; candidates are replayed in vectorized batches of 512. Canonical result reuse and bounded graph caches remain enabled. No validation candles are loaded in this phase.

Local fitness is V19's local trade-trimmed profit minus drawdown, tail-loss, risk-duration, exposure and peak-position-risk costs. Its other-session loss term is zero because this phase has only one session; the contract records this explicitly. Local parents and archived experts require at least three completed positions, positive trade-trimmed profit and the unchanged financial/risk validity checks. Empty eligible pools produce fresh candidates without relaxing those gates.

Archives retain up to 64 experts per session, configurable up to 1,024. After exact effective-rule/policy deduplication, a bounded score shortlist (256 by default) feeds greedy selection weighted 70% normalized local score and 30% nearest-selected parameter distance. The descriptor includes reachable feature/operator presence, typed DAG coordinates, unit-scaled literals, policy genes and ages; inactive genes do not create diversity. This is inexpensive structural diversity, not proof of different trading behavior. Distances use a bounded shortlist-by-descriptor array, not a population-wide pairwise matrix. Selection defaults are engineering choices to profile after V19, not validated optimums.

Phase 2 draws unique experts round-robin across training sessions, with up to 75% expert slots and at least 25% unique fresh candidates in its 1,024-member initial population. Extra experts remain in their sealed archives. Local ancestry receives session-specific negative IDs, preserving mating exclusions across the phase boundary without serial collisions. Empty expert archives simply increase fresh exploration.

Phase 2 uses V19's global score and eligibility unchanged: positive net and session-trimmed profit, at least 20 completed positions and eight active training sessions. All parent routes, elites and crossover budgets are global; there are no local champions or session-parent paths. Mutation, unrelated mating and fresh fallback remain intact. Twelve later validation sessions observe a training-frozen shortlist and never affect archives, selection or RNG. Six validation sessions retain the existing provenance of prior V17 training exposure; this is not a pristine holdout.

Both phases enforce actual stop distance at most 20% and planned per-stock position risk at most $300, with V19's $0.005/share and $1 minimum-order fees. A risky stop gene is rejected by execution, not silently clamped. The enriched banks are reused byte-for-byte; no extraction or new whole-universe candle features are introduced.

## Completion gate and profiling

Every normal controller, local task and direct global entry point rejects execution until the current job-bound V19 campaign completes all 50 generations, publishes both final split seals and exit code zero, releases ownership and exits. The gate follows the current V19 job after migrations. A stopped, partial or failed campaign cannot authorize V20. Final checkpoint, dataset/source, session receipt and panel hashes are checked; workers use the controller's pinned completion proof. No standalone long qualification is introduced.

The default mode is a small real local profile: first two training sessions, three local generations, with cold-generation timing retained. It ends without starting Phase 2 or the full V20 campaign. `profile.json` distinguishes evaluation timings from complete generation wall times, including scoring, archiving, reproduction and checkpoint publication. GPU speed and memory acceptance remain unverified until this profile runs after V19 completion.

Source: `D:/TradingCodes/quant-research-workbench`. Workstation code is immutable beneath `D:/TradingML/codes`, synchronized only after commit/push. All outputs belong under `D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v20`.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u -m research.vectorized_backtest.v20.run_deferred `
  --job D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v20/queue/20261014-local-profile/job.json
```

`run_deferred --once` performs one lightweight readiness read and starts no compute. Its module does not import Torch before V19 exits. `run_train --check-only` performs the deeper readiness read without starting compute. Without it, the controller waits in 30-second intervals without CUDA initialization or session workers. `--mode full` must be explicitly selected after reviewing profiling; it runs all local sessions then global evolution. Use a separate output folder for full runs; profile checkpoints are not silently relabeled as full searches.

## Restart and evidence

Each local generation seals its receipt, score/archive summary and births before committing population, active ancestry, RNG and bounded archive state. Checkpoints never import V19 candidates or optimization results. Local source/data/execution/selection identity and every completed seal must match on restart. Phase 2 binds all local archive seals and freezes its seed pool; global restart retains full GA state.

Create `STOP` in the controller directory to stop new session dispatch and request active local tasks to stop after their sealed generation. During Phase 2, STOP must also be present in its `global` directory; the controller forwards it. Remove STOP files deliberately before resuming with the exact original command. Failures and interrupted sessions are explicit in status and local-results files; no session is silently omitted. CPU unit tests use deterministic fake session workers to validate lifecycle/resume and real CPU financial transitions for V19 parity. They do not establish CUDA throughput or large-session admission.

## Detailed Phase-1 observer

Run `python -B -u -m research.vectorized_backtest.v20.observe_local` from an immutable observer copy. It follows `active-campaign.json` and automatically switches to the existing financial observer when Phase 2 begins. Brackets select the session; J/K select a candidate; N/B page rankings; 1/2/3/4 sort by profit, saved local score, win rate or increasing risk. Financial ranks remain the original session profit ranks. F shows financial details, O the saved local score/eligibility, P policy, and R rules. E/X/A/D/T choose the lifecycle rule; U/I page long rule definitions. Q closes only the observer.

The observer uses one background reader at idle CPU/low I/O priority on one core. It projects only flat summary arrays from the selected session's sealed receipt, without decoding the completed-position ledger, and caches checkpoints by modification time. It retains at most two metric snapshots and two bounded genome caches. No observer output is fed back into optimization, and no campaign file is written. Completed final populations retain all candidate rules; for active sessions the observer retains genomes as generations advance. If an older non-archived genome was discarded before attachment, its rules are explicitly unavailable. The original policy and every reachable DAG node are accessible without expression truncation. Display-only changes are deployed to a separate immutable code copy; the running optimizer copy and its source identity remain unchanged.
