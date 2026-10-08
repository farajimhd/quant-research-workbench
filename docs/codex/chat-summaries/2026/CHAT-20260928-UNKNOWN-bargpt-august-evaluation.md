# BarGPT August checkpoint evaluation and serving-capacity diagnosis

- Chat started: 2026-09-28; exact start time unavailable (America/Vancouver date).
- Chat ended or last activity: 2026-09-29; user requested closure and history consolidation.
- Summary written: 2026-09-29 07:36 PDT (America/Vancouver).
- Chat/task identifier: 01a0e8b0-eadf-7360-a17b-99e3a97f0ad3, Evaluate BarGPT on Aug 2026 data.
- Repository or scope: quant-research-workbench, services/bar-gpt, August 2026 evaluation.
- Related task-history entries: TASK-0217, TASK-0197, TASK-0170.
- Source completeness: Partial. Current conversation, condensed earlier context, committed source and detailed runtime HANDOFF.md were accessible; no complete replay of every early tool output was performed.

### Narrative

The user had trained BarGPT over billions of origins but could not interpret the training evaluations or determine whether a saved checkpoint was usable. The request became two separate questions: whether forecasts improve on simple baselines, and whether the actual serving implementation can meet a real-time deadline. The user asked to use the BarGPT service, briefly considered read-only ARTE bars, then explicitly chose BarGPT's existing input path. Preparation therefore used HistoricalBootstrap and certified canonical SIP events, not ARTE bars or retained raw flatfiles.

The user authorized implementation while the workstation GPU was occupied and later GPU execution only when demonstrably idle. Training must never be stopped, paused or contended with. Initial planning assumed 500 symbols; the user explicitly replaced that with **100 symbols, each updated once per second**. Later the user narrowed quality evaluation from all August data to selected high-volume tickers and selected premarket, regular and after-hours windows. Those clarifications supersede older plans in the append-only runtime handoff.

Commit 934b8e362 delivered a CPU preparation and actual-service replay/scoring runner. Immutable SQLite packets retain source revisions, native v3 physical targets and checkpoint/source hashes. Replay uses service loading, cache admission, preparation, forward, decoding and prediction journaling, with backend publication disabled. It reconciles exact ticker/model/origin/checkpoint identities, supports restartable evidence and fails closed on missing input, source drift, occupied GPU or excessive paced backlog. Batch-versus-single and training-collation feature/mask/clock checks are separate from forecast scoring. Synthetic implementation tests are not market acceptance evidence.

The first real AAPL packet required 25.66 minutes of historical preparation. This is warm-up cost, not inference latency. It contained 119 eligible origins and 87,855,104 bytes, SHA-256 1a34d78bd1d7b16463e3044baf60916d602e7bcd2f6c32c3df7d1155abc62142. The runner eventually evaluated all four immutable checkpoints on this small pilot. Repeated GPU ownership checks and task-owned child monitors prevented evaluation from competing with training; work waited when another Python process acquired the device.

An early three-symbol paced replay accumulated more than ten seconds of backlog. Profiling isolated repeated prediction-journal open/append/close operations. A bounded real-payload diagnostic wrote 120 identical lines, about 9.96 MB: repeated close cost approximately 12.7-13.2 seconds, versus about 0.004 seconds of persistent-handle writes plus 0.204 seconds final close. Exact output hashes matched. Commit 35d80c2a6 introduced a locked persistent PredictionJournal, per-record flush, daily rotation, shutdown close and propagated I/O failures. Neither implementation promises fsync-level power-loss durability. Forty service tests passed; revised real predictions matched the old overlapping outputs exactly. No production restart occurred.

Commit 47a6f247d refined the GPU guard to allow only the exact Windows System32 LogonUI.exe path, whose Microsoft signature was verified. An unknown executable with the same basename remains disallowed. Nine evaluation tests passed on both hosts. The frozen workstation bundle is composed from the original evaluation commit plus these scoped patches; all 1,635 files were hash-verified rather than copying unrelated current-branch work.

The quality population was frozen without looking at forecast performance: August 3 and 12, 08:30-08:35, 10:00-10:05 and 16:30-16:35 New York time, with five tickers per window ranked by preceding 30-minute volume-reporting-eligible share volume. Thirty ticker-window selections covered 24 distinct tickers. Nineteen extended-hours selections had all observed trades excluded by the existing condition-12 origin contract; USHY in the remaining window had no observed trades. These were accounted for rather than replaced. Quotes alone do not create eligible evaluation origins. Thus extended-hours forecasting was not evaluated, and the findings do not imply every extended-hours ticker is unsupported.

The ten regular-session ticker windows produced 2,924 eligible predictions per checkpoint, 11,696 total across four checkpoints, with no missing predictions and exact SQLite/scorecard reconciliation. The checkpoints were v3_first (0.5B), v3_close (4.5B), v3_5b and v3_final (final epoch 2, approximately 25.674B exposures). The common contract hash is 61c8b8fca977403971ada4dd94202586e46a4567a0b3ee10f08ec3ba0e5196b0. Final checkpoint SHA-256 is 8f61a7e463d358c81805f1a5eba6b8a29113dea9700de312dcdb8e2020565d08.

Accuracy did not justify promotion. For the final checkpoint, pooled trade-close MAE skill against no-change at 5/30/60/300/900/3600 seconds was -2.355/-2.964/-3.171/-3.616/-3.125/-3.679 percent on August 3, but +3.139/+2.762/+3.362/+9.785/+8.622/-1.034 percent on August 12. Positive means lower error. Earlier checkpoints also had mixed advantages. Nominal 80 percent interval coverage at 300/900/3600 seconds was 48.14/49.49/4.94 percent on August 3 versus 91.00/69.48/62.08 percent on August 12. There were no quantile crossings. Only two dates and overlapping targets do not support a robust day-level confidence interval. Momentum was often beaten, but its long-horizon extrapolation is a weak comparator. Physical OHLC heads were scored; autoregressive, condition, availability, volume/count and volatility heads and trading profitability remain outside completed scoring.

BF16 batch parity failed on real multi-symbol inputs. Tolerances were not relaxed. Quality comparisons continued one ticker at a time with unchanged BF16 precision, while FP32 was investigated as a separately identified serving alternative. Five-symbol FP32 replay completed 120 paced sweeps and 567 eligible predictions without deadline misses, p95 scheduled completion about 410 ms. This was neither 600 predictions nor full live certification. Production precision was not changed, and BF16 quality results must not be relabelled FP32 results.

The capacity universe was then frozen to the top 100 preceding-volume tickers for August 3, 10:00-10:05, superseding a July event-count candidate. The first 16 included five already prepared samples, nine new sequential CPU preparations, and two explicitly derived MSFT/NVDA packets. Reuse verified unchanged source revisions, normalization anchors without intervening splits, sufficient causal context, target support, source compatibility and parent packet hashes. The combined dataset has 4,330 eligible origins; actual per-second population varies. The nine new preparations took about 2.62 hours. No remaining-84 campaign was launched.

All 16 symbols passed strict FP32 batch parity at a common eligible origin, maximum absolute difference 5.722046e-6. However the two-minute paced run produced 1,750 predictions over 120 sweeps with **24 deadline misses (20 percent)**. Median scheduled completion was 778 ms, p95 1.341 seconds and p99 1.368 seconds; no sweeps failed. This configuration failed the every-second deadline even at the 16-symbol population. It establishes neither a precise maximum capacity nor 100-symbol suitability. Prepared replay excludes live aggregation, HTTP transport and backend delivery.

The final investigation explained the recurring spikes more precisely. An initial nested-profiler attempt failed and was retained; subsequent diagnostics used worker profiling and then unchanged-GC callbacks. Three slow sweeps coincided with generation-2 collections of approximately 486-497 ms outside model inference. Stack capture located every steady-state collection trigger in replay cache admission through TickerCache._order_and_bound, at sorted(target.items()). The shared service cache sorts and rebuilds its entire retained OrderedDict on each touched-view update, allocating temporary tuples. This identifies the trigger, not proof that all scanned heap cost originated there. CPU tensor conversion and cache scans remain additional scaling costs.

The proposed fix is incremental ordering: preserve normal append and existing-key revision order, mark only new out-of-order keys for sorting, and always retain the existing capacity/eviction rules. It was **not implemented**. GC was never disabled or retuned. At this point the user explicitly stopped work, requested a summary, and then authorized this history update while concluding the task. No further experiment or optimization is authorized by this closure.

### Durable decisions

- Confirmed: 100 symbols once per second is the target; selected high-volume session samples replace exhaustive August evaluation.
- Confirmed: canonical SIP through BarGPT remains input authority; no ARTE substitution, raw-flatfile fallback, relaxed eligibility or parity tolerance.
- Architecture: preserve immutable checkpoint/source identities, complete origin reconciliation, failed trials and explicit derivation lineage. Laptop source precedes committed, pushed, hash-verified workstation execution.
- Rejected: treating synthetic tests, a one-symbol result, BF16 single-ticker accuracy, or prepared paced replay as full real-time certification.
- Acceptance: later-August data stays sealed; no candidate selected or promoted. Declared training ends 2026-01-01 and validation ends 2026-08-01 were audited, but complete checkpoint ancestry/exposure was not.
- Closure: evaluation follow-up resume-bargpt-gpu-evaluation is PAUSED. Restart requires a new user request, not the old heartbeat instructions.

### Delivered outcomes

- Evaluation runner, journal optimization and GPU guard: commits 934b8e362, 35d80c2a6 and 47a6f247d, pushed on codex/rl-v2-single-account-sessions.
- Frozen workstation source: D:\TradingML\codes\bargpt-eval-47a6f247d. No frozen-source edits or production release/configuration changes.
- Durable runtime evidence on laptop and workstation: D:\TradingML\runtimes\bar_gpt_evaluation\aug2026, especially HANDOFF.md, SAMPLED_DEVELOPMENT_REPORT.md, sampled_development_report.json, capacity16_combined, capacity16_fp32_paced120 and capacity16_fp32_gcstack30.
- Shutdown audit found no evaluation-owned Python/SSH workers on the laptop and no evaluation-owned worker on the workstation. No training was started by this task. Unrelated RL launchers PIDs 45756/55208 and queued-training supervisor 27636 were observed and left untouched; this is not a claim that the whole workstation has no running scripts.
- No subagents were spawned. Generated evidence stays outside the repository; unrelated working-tree changes were preserved.

### Unfinished or hanging work

All items below belong to TASK-0217 with serving integration under TASK-0197 and are stopped at user request, not completed:

1. Cache optimization: diagnosis and proposed design only. On renewed authorization, implement on laptop with differential tests covering chronological append, corrections, duplicates, out-of-order batches, eviction and derived rollups; validate exact retained rows and predictions. Commit/push before workstation synchronization.
2. Serving capacity: 16-symbol FP32 fails the deadline; 100 remains unmeasured. After a validated fix, explicitly derive compatible manifests, repeat parity and sustained paced trials, then size remaining preparation. A separate actual live path with independent eligible-origin reconciliation is required for certification.
3. Checkpoint selection: two-day accuracy and calibration are inconsistent. Extend a justified development design, complete ancestry/exposure audit, align precision-specific accuracy, freeze selection before opening any acceptance data. No deployment approval exists.
4. Extended-hours and other heads: sample exclusions remain; investigate coverage within the existing contract rather than silently admitting incompatible origins. Unscored heads and profitability need separate scope.

### Unavailable or incomplete source chats

Earlier content in this task is partly condensed; the runtime handoff preserves experiment details. App inventory exposed other repository tasks, including Design BarGPT production serving, but their full conversations were not reviewed for this closure. The existing CHAT-20260819-0845-bargpt-production-serving summary and TASK-0197 provide related context, not a claim of newly reviewing those chats.

### Handoff to the next chat

Read TASK-0217, TASK-0197, this summary, services/bar-gpt/EVALUATION.md and the runtime HANDOFF.md. The final user instruction is STOP; do not resume automatically. If asked to resume, inspect live processes and manifests first, preserve failed evidence and sealed acceptance, and implement the incremental cache-ordering fix before spending on the remaining universe. The old handoff's earlier active-automation language is superseded by closure. Never stop unrelated training or interpret appending history as promotion or real-time acceptance.
