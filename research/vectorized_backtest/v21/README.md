# V21: session discovery and global score-only evolution

V21 is an isolated copy of V20. The user authorized stopping V20 at a sealed boundary, bounded profiling/optimization and launching V21 on 2026-10-15; lengthy qualifications are excluded. No automatic job is installed. `run_train` requires `--authorize-run` for compute; `--check-only` reads readiness only. Predecessor/ownership gates remain in force. DESIGN.md contains the approval ledger and baseline inventory.

## Search and execution

Phase 1 evolves each training session independently. Default generation 1 has 6,144 candidates, evaluated in fixed batches of 512 on the same complete session. Selection sees the entire initial pool. Later generations have 1,024 candidates. `--initial-multiplier` controls the first generation only; checkpoint resume never repeats initialization. Default local generations: 30. Two persistent workers retain one session each across generations, with bounded result and compiled-plan caches.

Phase 2 starts with up to 768 unique session experts and at least 256 fresh candidates, allocated round-robin across session archives. It uses the same 1,024 population and global-only breeding; default 50 generations. There is no Phase 2 population expansion.

Both phases preserve V20's vectorized FP64 financial replay, fixed $1000 entry/$200 addition, fee model, causal top-10 membership, 20% stop-distance ceiling and $300 planned stock-position-risk ceiling. Maximum additions now evolves over 0..20. Original feature producers and certificates are retained. Average-movement projection changes to movement/close under `v21-magnitude-ratio-v1`; other relative distances and log returns remain unchanged.

## Scores and selection

For each session, P is full fee-adjusted net profit, W is positive-only fractional top-10%-traded-ticker profit removal, and D is session equity maximum drawdown:

`local score = P - 0.25 W - 0.05 D`

Globally, W_session is positive-only fractional top-10%-session removal over all configured training sessions, including inactive ones:

`global score = sum(P) - 0.25 max(sum(W), W_session) - 0.05 sum(D)`

Cost is negative score. Losses remain fully counted. There is no within-ticker trade trimming or complexity, activity, capital-time or risk-duration penalty. Financially valid negative-score and zero-trade candidates remain eligible parents; hard accounting/risk checks still apply. Summed session drawdowns are not continuous portfolio drawdown.

At population 1,024: 102 unchanged score elites, 205 fresh candidates, 359 score-tournament crossover slots, 215 uniform-random-parent slots and 143 elite-pair slots. All mating excludes shared ancestry within five parent links. Elite mating tries the wider valid pool before fresh fallback. Mutation selects 20..85% of all non-survivors, including fresh/fallback candidates, with 1..5 distinct effective edits. Elites copied unchanged are excluded. Exact effective duplicates are rejected.

Session archives are separate from breeding: retain 64 distinct positive-net-profit experts, sampled 32/16/10/6 from profit percentile bands 90..100/70..90/40..70/0..40. Best profit is guaranteed; underfilled bands donate slots to highest-profit remaining candidates. Percentiles use a bounded historical hash-priority reservoir (default capacity 1,024 including guaranteed profit leader), not unlimited history. No pairwise parameter-distance calculation remains.

Validation observes a frozen union of up to 256 top training-score and 256 top training-profit candidates, deduplicated and filled by score. No positivity/activity filter or validation selection feedback. The panel records original training score/profit ranks. Six sessions' prior V17 training exposure remains a dataset provenance concern.

## Sampling and evidence

Initialization balances requested rule-size bins (3..9, 10..19, 20..32), compatible feature families and expression choices. Numeric priors mix uniform and log-magnitude draws 50/50. Same-unit and temporal ratio templates use x/LAG(x,h), without subtracting one; raw positive-unit ratio literal priors cover .01..4 around baseline 1. Temporal windows count observed candles, not elapsed seconds. Accepted features/operators/windows/sizes, additions, duplicate rejection and rule-size rejection counts are recorded. Constraints can change accepted frequencies; this is not uniformity over all possible programs.

Generations retain evaluation, scoring, archive, reproduction and publication timings, source/data contracts, receipts, seals, RNG, population and ancestry. Source changes cannot silently resume checkpoints. Detailed read-only observers retain session navigation, progress bars, candidate rules, ranks, profit, win rate, positions and risk; objective labels reflect V21.

Generated data belongs below `D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v21`. Commit/push precedes immutable workstation synchronization. CPU tests cover costs, risk checks, selection, archives, expanded initialization, sealed restart, replay/cache reuse, display rendering and validation independence. GPU compilation and end-to-end speed remain unverified until authorized profiling. Larger initial pools and larger sampled expressions intentionally increase work; vectorization alone does not prove equal wall time.

`--capture-gib` records the retained CUDA graph-cache budget per worker (default 4, supported .25..16 GiB) in both phase contracts. Physical-headroom checks remain active; changing capacity requires a new run contract, never mutation of an active checkpoint.

## Approved 128-node continuation (2026-10-16)

The user authorized stopping the active global run at a sealed boundary and continuing with a 128-node maximum per rule. The random-generation bands remain 3..9 / 10..19 / 20..32; valid crossover/structural edits may grow rules up to 128. The 120-observed-candle context limit, objective, selection, action limits, population and generation target remain unchanged. The global contract explicitly binds maximum_rule_nodes=128. Pre-change seals and local archives stay immutable; a separately audited source/contract migration preserves the entire checkpoint state, seed pool and historical results. This continuation is not a fresh all-generations 128-node experiment. The legacy descriptor helper uses the declared node envelope to avoid overflow; V21's profit-band archive does not use parameter-distance selection.
