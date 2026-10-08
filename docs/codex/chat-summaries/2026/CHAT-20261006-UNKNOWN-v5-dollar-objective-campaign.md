# TASK-0223 V5 optimization, dollar objective and running-campaign handoff

- Chat started: 2026-10-06; exact start time and timezone unavailable
- Chat ended or last activity: 2026-10-08, closing handoff in progress
- Summary written: 2026-10-08 07:25 America/Vancouver (14:25 UTC)
- Chat/task identifier: 01a1119b-bf66-7682-8dd3-613676bc05b9; Continue TASK-0223 GPU optimization
- Repository or scope: D:/TradingCodes/quant-research-workbench; research/vectorized_backtest/v5/torch_backtest
- Related task-history entries: TASK-0223
- Source completeness: Partial; recent conversation and runtime checks accessible, early conversation represented by compacted context and prior summary

### Narrative

The user continued TASK-0223 from the earlier [atomic GPU V3 summary](CHAT-20260930-UNKNOWN-atomic-gpu-strategy-v3.md). That task sought reproducible causal strategy search rather than forecasting models or hindsight labels. Earlier V3 and V4 runs were deliberately stopped. Their immutable source, checkpoints, RNG state, stop receipts and sealed validation must remain untouched. V2 was outside scope. This chat developed and monitored V5, preserving original 30 training sessions and six sealed final validation sessions, 149 causal split-adjusted features, 120 observed-context candles, completed-interval decisions, next-interval fills, UTC elapsed durations and shared approximate 25% liquidity participation.

The early implementation focused on bounded throughput. V5 retained dynamic actual-lot specialization, original candidate identity restoration, and exact financial behavior. Explicit rule-prefetch prepares the next candidate gate batch on a separate CUDA stream; CPU feature preparation and receipt writing use bounded one-step lookahead. These switches remained explicit rather than unconditional defaults. Profiling measured modest warm end-to-end improvements, while increased gate memory and occasionally slower replay demonstrated that pipeline speedups were not universal. All-stage financial and actual-fill qualification was required before the full campaign. These early details are compacted context, not a newly repeated audit.

The user approved a staged 32-generation search with seed 20261005 and physical GPU batch 128: generations 1–10 use population 4096 on three sessions; 11–20 use 2048 on six; 21–28 use 1024 on ten; 29–32 use 512 on all 30. Each generation archives eight leaders plus four other randomly sampled eligible candidates, deduplicated by genome fingerprint. Each stage then evaluates its archive on all 30 training sessions. Later stages begin with eight retained checkpoint leaders plus their new generation archives. Upper bounds on checkpoint candidate counts are 120, 128, 104 and 56; duplicates reduce them. The sum of per-generation population evaluations is 71,680, not that many unique strategies. Full final populations or unevaluated fresh offspring do not automatically enter the checkpoint.

Migration preserves the approved 10/10/10/50/20 budget: elite unchanged, other random unchanged, fresh random, elite-parent offspring, random-parent offspring. Session panels are seeded and balanced. Different small panels cannot be compared as if their scores measured the same market sample; the full-training checkpoint provides the common comparison. Training gains, particularly those concentrated in one day, were never accepted as robustness evidence.

Immutable source 74e025fe3 passed 60 qualification tests and four full serial/pipelined cold/warm passes. Actual fills, terminal eligibility and financial arithmetic were audited. Its code hash was 99dd06317078aa04bfa23bc745dcbcd82ed5f5b13f5e10d508a9b04147314200. A PowerShell launcher lost its process ExitCode after polling and wrote a null qualification exit, preventing automatic training handoff despite successful qualification. Recovery preserved that failure receipt, checked the existing report and source/input identity, and launched training exactly once through the same scheduled worker task. It did not repeat qualification. The run was campaigns/20261007-74e025fe3-staged32.

The terminal evolved from three leaders to 100 ranked candidates, fixed regions, bounded messages, paginated financial/objective/position/performance details, and explicit qualification-versus-training progress. Renderer deployments were separate from immutable worker source. Commits 74e025fe3, c33b2209e, 5f063ec6e and 77037d5b6 cover this progression; 77037d5b6 exposed current population size. Normal and compact render checks and the real observer entry point were exercised. There was no native screenshot inspection of the visible workstation window.

On October 8 the user noticed only the first two ranking rows had nonzero net P&L. The saved generation-5 ranking confirmed +$424.01 and +$142.92 for those rows, while the remaining 98 rows had no fills or positions. These were actual zeros, not a rendering defect. Completely inactive simple strategies incurred only a small complexity cost and could rank above traded strategies with worse objective scores. The user requested an objective revision, rejecting unconditional exclusion of all inactive strategies and asking to retain some randomly.

The objective discussion evolved deliberately. An initial proposal combined median return and average return excluding the best session with a soft inactivity penalty. The user rejected excluding the best session, questioned median and mean, and chose summed net profit. The final approved objective is in dollars:

`score = total_net_profit - .25*T*L - .25*sum(DD) - .10*sum(RH) - .002*sum(CH) - 10*T*N/32 - 10*T*I`.

T is evaluated session count. L is the average loss in the worst ceil(20%*T) sessions, with gains contributing zero loss. DD is each session's maximum dollar drawdown. RH and CH are accumulated stop-risk and capital exposure in dollar-hours. N is total active rule nodes. Fees are already included in net profit and are not deducted twice. There is no median or excluding-best-session reward. Costs scale with session count; inactivity itself uses elapsed time.

I is the eligible-duration-weighted integral of `min(1, floor(consecutive_inactive_hours)/5)`. The first hour is free, subsequent completed hours increase the rate, and the rate caps after five hours. Each certified session starts a new inactivity clock; overnight and unobserved calendar gaps do not accumulate. Actual buy fills, including entry/add fills, reset the clock; sells do not. The implementation derives this from saved actual ledger timestamps and certified UTC start/end boundaries without modifying GPU execution. A reset could still incentivize an unnecessary trade; this is a known behavioral uncertainty, not a claimed guarantee against overtrading.

Separately, seeded selection removes floor(50% of completely inactive valid candidates) from archive/parent eligibility at selection boundaries, retaining the rest. Financial feasibility is not weakened. Ranking still displays all evaluated candidates, with invalid candidates last. The display is not the randomly filtered parent pool. The final full-training winner remains selected by its valid objective ranking, rather than a random final winner draw.

The user explicitly authorized integrating this objective, stopping the old campaign, restarting from the beginning without profiling again, and navigating the whole population with N/B wrapping first/last pages. The old campaign stopped at a durable boundary after generation 6, exit 130, on October 8 at 14:11:50 UTC. Original artifacts remain intact. Commit 3804a264d implements DollarObjective, ledger-derived inactivity, deterministic inactive selection, matching staged audit/validation calculations, qualification inheritance, and full-population pagination. Financial summaries are computed once per panel rather than repeatedly for all 4096 displayed candidates.

The new source was committed and pushed before immutable workstation deployment. Deployment verified 1242 files and code hash 0e75d72a6c1eb9ab576e175ce775469ad0776d67b0a488a7b9706a9f26f0f84c. Qualification inheritance explicitly checks the original source hash and passed report hash, original input/batch identity, and byte-identical execution files. Exactly 85 execution modules matched. Changes are confined to permitted controller/scoring/audit/display code and tests; inherited profiling is not represented as fresh-source profiling. No new GPU profiling job was run.

Validation included 33 focused laptop checks covering dollar arithmetic, best-session retention, hourly integral boundaries, fill-time identity restoration, inactive selection reproducibility, full-population navigation, and qualification rejection when execution bytes change. Thirty-two workstation checks passed; one legacy V4-import comparison was deselected because V4 is not in the self-contained deployment, and it passed on the laptop. A CPU smoke check rescored all 4096 candidates from the original immutable generation-000 three-session receipts, reconciled score components exactly and compared sampled new summary rows with existing metric_summary. Broader legacy contract tests were not all green: an older one-winner rejection assertion conflicts with previously relaxed feasibility, and initial temporary-directory access failed. The focused suite used the approved runtime test root and passed; do not claim a full repository suite passed.

The replacement campaign started generation 1 at 14:15:58.775 UTC, using the same scheduled worker and observer tasks. The actual worker PID was 4708 and renderer PID 21340. The last closing snapshot at about 14:25 UTC showed training/Backtest, zero completed generations, first session, eight completed batches of 32, and no reported error. PIDs and progress are snapshots, not permanent identities. The existing ten-minute monitor was updated for the replacement, then paused at the user's request to conclude this chat and start a new monitoring chat. The worker and observer continue running. No new chat was automatically created.

### Durable decisions

- Confirmed requirements: approved dollar equation, elapsed-hour inactivity, seeded half-removal of inactive candidates, full-population wrap navigation, same 32-generation schedule, no repeated profiling, six validation sessions sealed until audited final freeze.
- Architecture: immutable new source and new run directory for an objective restart; never change the old experiment mid-run. Reuse existing scheduled tasks, one worker and one observer. Reproduce random selection in staged_audit.
- Rejected approaches: hard exclusion of every inactive strategy; median/excluding-best-session rewards; calendar-gap inactivity; changing financial/causal validity to encourage trades; duplicate GPU campaigns.
- Assumptions: certified session interval is eligible time; buy/add fills reset inactivity. These were documented explicitly in implementation and monitor instructions.
- Uncertainty: objective weights and trading behavior are unvalidated research choices. No full-budget optimization, final audit or final validation has completed for the replacement.

### Delivered outcomes

- Source commit 3804a264d pushed on codex/rl-v2-single-account-sessions; task-owned changes only.
- New runtime: `D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v5/campaigns/20261008-3804a264d-staged32-dollar` on DESKTOP-SAAI85T.
- Immutable code: `D:/TradingML/codes/quant-research-workbench-vector-v5-3804a264d`.
- Evidence: deployment/deployment.json, campaign/qualification_reuse.json, campaign/scoring-smoke.json, original passed qualification report and receipt hashes.
- Ownership: V5 CURRENT-PROFILING-20261007.json and V4 transition-20261006/current-ownership.json point to the replacement. Original V3/V4 and the superseded V5 campaign remain stopped.
- This summary and TASK-0223 ledger refresh replace the stale V3-active task-history state. Overall task remains In progress.

### Unfinished or hanging work

1. TASK-0223 training: replacement just started; continue the existing worker through 32 generations and each 30-session checkpoint. New chat owns monitoring and exact immutable recovery if needed. Do not launch another campaign.
2. TASK-0223 final audit: after training releases owner.lock and finalist is ready, use the deployed immutable `staged_audit --output RUN --freeze`. Verify exact RNG/population, hashes, elapsed inactivity, objective arithmetic, financial ledgers and fresh input attribution. No freeze before a passed audit.
3. TASK-0223 final evaluation: only then use `staged_validation --output RUN` once for default/winner on six sealed sessions. Verify final input and financial audits; never tune or repeat validation. Supplemental input production, if needed, must follow the existing frozen-access gate.
4. TASK-0223 delivery: report concentration, P&L, position/fill/holding/risk metrics and timing with research limitations. Finish durable documentation, commit/push task-owned changes, and clean up owned worker/observer/tasks/monitor when no longer needed.
5. Monitoring handoff: old automation monitor-task-0223-v3-optimization is PAUSED and attached to this closing chat. New chat must establish a single heartbeat monitor attached to itself after reading ownership; do not resume the old-chat monitor or leave two monitors active.
6. Preserve unrelated edits in three V1/V6 notebooks and tests/test_canonical_trading.py. This chat did not own or commit them.

### Handoff to the next chat

Read AGENTS.md, TASK-0223, this summary and the V5 torch_backtest README. First refresh the workstation owner JSON, actual PID/creation-time/command lines, campaign status, latest receipts, worker log and exit. Access is through the Workstation-D share or existing workstation SSH configuration; do not expose credentials. Input authority remains V4 campaign_inputs/20261006-source-aligned/sessions.json, SHA256 9764bdfd2751e408bef3c4366ec437ec3ae9b053706f2cf21a4ce3c18abfbfb6. Never re-profile or restart stopped versions. Continue the existing replacement; no new run is authorized. Adopt monitoring in the new chat and notify only meaningful progress, failure, completion or required user action. All-budget audit/freeze and once-only validation remain required before declaring TASK-0223 complete.
