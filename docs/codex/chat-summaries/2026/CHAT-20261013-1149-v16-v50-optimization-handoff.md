# V16 through V50 optimization, robustness audits and active workstation handoff

- Chat started: 2026-10-13 11:49:36.559 America/Vancouver; source session metadata 2026-10-13T18:49:36.559Z.
- Chat ended or last activity: 2026-10-25; user requested this closing handoff and intends to archive the chat. Detailed operational snapshot: 2026-10-25 10:41 America/Vancouver / 17:41 UTC.
- Summary written: 2026-10-25 10:44:54 America/Vancouver / 17:44:54 UTC.
- Chat/task identifier: 01a13699-43ae-7370-b553-a70e53f2c5a6, **Investigate and resume V16 run**.
- Repository or scope: D:/TradingCodes/quant-research-workbench; research/vectorized_backtest/v16 through v50; laptop/workstation runtimes.
- Related task-history entries: TASK-0223.
- Source completeness: Partial. Latest user instructions, implementations, tests and runtime evidence are accessible. Early V16-V41 details and some V43-V47 assistant/tool history are compressed or unavailable in the current context. App thread inventory was inspected; other chats were not reviewed as part of this summary.

## Narrative

### From V16 recovery to distributed V42

This chat continued [the V7-V16 handoff](CHAT-20261011-1305-v7-v16-optimization-handoff.md), whose active V16 description is now historical. The original purpose was diagnosing and resuming causal GPU optimization, improving useful trading behavior and throughput while preserving financial semantics, checkpoint population/RNG/pedigree, certified inputs and validation isolation. Intermediate V17-V41 implementation details are not reconstructed here. TASK-0223's earlier 30-training/six-validation description was superseded by the later explicit 24-training/12-validation campaign.

V42 used population 1,024, seed 2020, sixfold local initialization, all 24 local 100 archives and global 200 evolution. Its optimizer remained pinned to commit 9d0da4e80 and original source contracts. Windows sharing failures in a mutable laptop checkpoint display mirror stopped the coordinator and workers. An audited I/O-only repair, commit 25a08fb0a, preserved search state. Later controller policy d5e6cfee3 widened global initialization to a historical/fresh 3,072-candidate screen selecting 1,024; activation alone was not proof screening had completed. The later retained state confirms the screen completed. Preserve its selection/cohort receipts and generation 1 reuse evidence if reviewing that historical campaign.

The user stopped monitoring. After an app update terminated a run, launch ownership moved to Task Scheduler so optimization could survive app closure/updates. All V42 work ultimately moved to workstation to free laptop resources. A later user-authorized switch stopped V42 safely at sealed global 59; partial generation 60 evidence remained. V42 is not an active recovery target, and neither archiving this chat nor a historical heartbeat authorizes restarting it. The old V18-named monitoring automation remains paused.

### V43-V47: concentration, execution and crossover

The user explored profit concentration penalties, ticker/session specialists and selection distributions, repeatedly requiring comparison from the same 6,144 Aug 13 initial programs. Ticker concentration penalties were rejected because a legitimate day's opportunity may exist in one security. V43 removed ticker trimming; V44 explored specialist scoring; V45 selected ticker/session positives while retaining raw-net-profit elite and crossover routes. These were evolving experiments, not accepted robust strategies.

A V45 leader's large XHG winner prompted review of size, additions, target bypass and fill feasibility. V46 introduced bid/ask execution, quote-age guards and profiling, retaining fees and existing financial/risk constraints. V47 added whole-network logical composition while preserving ordinary mask/parameter crossover, sibling schedules, mutation and ancestry/species controls. Whole-network OR/AND/XOR is already implemented; gradient-based fitting and the later binary-head-label proposal are not.

Saved Aug 13 run endpoints differ: V43 generation 34, V44 generation 42, V45 generation 50, V46 generation 13 and V47 generation 8. Their native objectives and execution differ, so their scores or peak profits are not a controlled version ranking. The bid/ask change did not eliminate the large Aug 13 outlier. Its market/execution feasibility is not established merely by profitability or noise stability.

### V48: isolated phase 0, then session and global search

The user proposed learning ticker/session specialists first, with a dynamically divided total 1,024 population, then mixing specialists across tickers. V48 implemented strict immutable ticker/session ownership in phase 0 and promotion from a bounded archive spanning all completed generations, not just final-generation winners. Within-session phase 1 removed ownership and re-evaluated promoted strategies. Selection retained elite, fresh and crossover allocations rather than filling every slot with specialists.

The initial Aug 13 pilot used phase 0 = 30, phase 1 = 20 and loss weight 0.5. It stopped at phase 0 generation 14 before the authorized full campaign. The full V48 specification became phase 0 = 50, phase 1 = 50, global = 100, loss weight 0.75, 24 training and12 display-only validation sessions. Each session's first two phases stayed on one host. Frozen validation never fed reproduction or training RNG.

Commits f19186557, e40f48bc9, 0f7d8c2c3 and c5d024604 delivered distributed execution, an owned-lane neural optimization, receipt-based scheduling and scheduler import-path repair. The first scheduled bootstrap failed before training because package profile.py shadowed the standard library; the launcher path was corrected and failed evidence preserved. Short owned-lane profiling improved GPU replay 1.93x on laptop and 1.34x on workstation with identical financial/episode hashes; 36 focused checks passed on each host. These are bounded profile measurements, not an end-to-end campaign speed claim.

Diversity was uneven: hundreds of ticker cases had one exploratory candidate while profitable cases received many slots and historical specialists. The user rejected this allocation as insufficiently productive. V48 stopped with two completed session pairs; active Aug 6 phase 0 stopped at 7 and July 31 phase 1 at 12. V49's later 15 completed sessions must not be attributed to V48.

### V49: session-wide cost and the robustness diagnosis

V49 removed phase 0 and ticker quotas. Phase1 optimized each whole session for 50 generations; phase 2 was intended to evolve globally for 100. Its score was net profit minus 0.5 times gross losing-position dollars minus 0.2 times equity drawdown, including open-position P&L. Global fitness sums identical session costs, including the sum of session maximum drawdowns; it is not a continuous cross-session maximum drawdown. Commit 82a7869df was pushed and deployed immutably; 29 focused tests passed locally/remotely, and short replay outputs matched across GPUs.

This corrected a material V48 cost limitation: V48 used realized closed-position drawdown, which could miss a large adverse excursion inside an eventual winning position. However, increasing drawdown coverage did not establish generalization. At the user's stop request,15 V49 sessions had completed phase 1. Both workers exited and locks released, preserving Aug 24 generation 12 and Aug 25 generation 41 partial-session checkpoints. No V49 global optimization started.

The subsequent audit selected the best retained archived expert from each completed session. Median own-session results were eight positions,57% win rate and about 1,400 dollars equity drawdown. Thirteen of 15 obtained over half their net profit from one ticker; 11 relied on one winner for over half their net profit. Removing the largest winner arithmetically made four nonpositive. This diagnostic is not a counterfactual trading replay or a rule that concentration is always bad.

Fifteen frozen experts were replayed on Aug 18, Aug 27 and Sep 1, dates none was evolved on, both at original costs and an additional 5 bps per fill: 90 candidate/session replays. Only 3/39 valid baseline replays and 2/39 stressed replays were profitable; six additional replays per condition failed terminal-liquidation validity. No candidate achieved a positive combined V49 score across those three dates; one had positive combined net profit. Those are other training dates, not untouched chronological validation. The 12 validation days remained unopened in this audit. It diagnoses weak local-candidate transfer, not a completed global campaign or every candidate ever generated. Entry-timing perturbation was not tested.

### V50: fresh direct global search

The user requested V50 derived from V49's phase 2 path, with no local optimization or archived-expert seeding. The score is:

`net profit - 0.1 * gross losing-position dollars - 1.0 * maximum equity drawdown`.

Net includes bid/ask execution and fees; losses are penalized additionally despite already reducing net. There is no explicit trade-count or win-rate penalty. The inherited global aggregator sums this session cost across training dates. Risk, quote-age, terminal-validity, crossover, mutation, ancestry/species and bounded memory/cache guards remain.

The approved Aug 13 pilot ran 50 generations as its budget, with 3,072 fresh calibrated random programs plus 3,072 templates, followed by 1,024 steady candidates. The initial 6,144 were evaluated, not silently preselected. Old pool-file environment routes and historical fitness were excluded. Commit 9b9fd31ae introduced this direct-global pilot; 21 focused checks passed in the repository and immutable snapshot, covering initialization, population reduction, resume, bid/ask CPU/GPU execution and validation isolation. The pilot did not finish 50: the user authorized its stop at sealed generation 6 to launch the full run.

Commit 674f21b6f enabled the full workstation-only campaign: 24 training sessions, 12 later display-only validation sessions, 100 generations and a new fresh 6,144 cohort calibrated on eight representative training windows. Twenty-four focused checks passed on laptop and workstation. The standalone scheduled process owns CPU coordination and the GPU; no laptop optimizer or separate distributed worker/controller is needed. Training completes before successor/RNG freezing, then validation is evaluated only for display and sealed independence evidence. The workstation terminal's T key switches training/validation, V reverses sorting, and Q closes the observer only.

At the17:41 UTC audit, generation 1 had evaluated all 6,144 founders across 24 training sessions and frozen its 1,024 successor. Validation was in progress, three of 12 sessions complete, with zero globally completed/sealed generations. Training had 1,666 financially eligible candidates and just one positive score: serial 4985, net 64.20, score 22.29, three closed positions. This is a partial generation snapshot, not final performance. Exact owner PID 30116, creation epoch 1792942979.3410664, was alive under svchost.exe with a fresh status, lock held and no STOP/failure files. Historical PIDs alone are never authority.

### Noise audit and the final design discussion

The user approved testing frozen many-generation candidates under feature noise without changing live V50. Aug 30 is not a certified training date; July 30 and Aug 13 V49 generation 50 finalists were used. The laptop diagnostic perturbed only continuous normalized neural observations, leaving Boolean/validity channels, financial prices, bid/ask fills, and position accounting intact. Gaussian knot scales 0.01/0.03, smoothly interpolated over 60 seconds, were tested with seeds 101/202/303. This is a neural-input sensitivity test, not a coherent alternate market path or training with noise.

July 30's initial top three shared entry/exit timing and mainly differed in sizing. Two additional timing variants were therefore included. Primary summaries use three distinct timing patterns per date; sizing variants remain controls. All 64 replays were financially eligible; zero-noise and repeat controls reproduced saved financial ledgers. Three projector tests covered masks, input immutability, determinism, time correlation and CPU/GPU agreement. Additional timing variants emitted nonfatal Windows compiler-cache path-write warnings; execution and parity controls completed.

Median retained profit under mild/stronger noise was 87.3%/82.4% for July 30, worst stronger 57.3%; Aug 13 retained 99.8%/99.1%, worst stronger 98.9%. Aug 13's roughly 45,000-dollar winner survived, while positions rose from 27-28 to 39-60. Thus noise reveals some sensitivity but can leave session/outlier dependence intact. No noise was installed in V50 and no generalization benefit from noisy training was demonstrated.

Finally, the user explored evolving one binary label per action head, combining parent heads/graphs, freezing inherited branches, and fitting new connections or all weights. Structural crossover could use multiple operators, not only parallel branches. Discussion established that a constant head target across observations favors constant output when fully fitted; many inputs do not supply varying supervision. No backpropagation, label evolution or new architecture was implemented.

For fixed parent outputs, a finite target probability p gives `theta_new = theta + (logit(p)-z^T theta) z/(z^T z)`, with z=[h1,h2,1]. This is the minimum-change fit to one observation, not a unique solution or a profitable input-dependent rule. A sigmoid needs finite p rather than exact 0/1. Fixed Boolean OR/AND heads can avoid weight search; XOR needs another layer or explicit operator. The user has not chosen a concrete next implementation.

## Durable decisions

- **Confirmed:** preserve V50's exact cost, fresh 3,072/3,072 initialization, 1,024 population, 100-generation global-only workstation run and 24/12 split. The pilot and all predecessors remain stopped; do not resume them automatically.
- **Architecture:** validation never influences reproduction, training RNG, objective weights or optimizer stopping. Preserve frozen-successor hashes and source/input/receipt bindings. No quote, terminal-liquidation or risk-guard weakening to manufacture eligibility.
- **Rejected/superseded:** ticker quotas/phase 0; penalizing ticker concentration merely for specializing; assuming realized-only drawdown captures open-position risk; archived-expert initialization for V50. Old V16 random-only and earlier budgets are historical, not current requirements.
- **Operational:** Task Scheduler survives app updates while the logged-in user remains available; this is not verified reboot recovery. Keep one normal nonmaximized progress window per intended host. Old monitoring stays paused. Archiving the chat is not a training stop request.
- **Uncertainty:** robust trading ability, outlier feasibility, final full-run behavior, and whether noise training or binary-head designs improve transfer remain unproven. No profitability or completion-time promise.

## Delivered outcomes

- Scoped commits above on `codex/rl-v2-single-account-sessions`; unrelated concurrent files/history were preserved. Current implementation: `research/vectorized_backtest/v50/{full_run,full_controller,full_common,full_seed,initial_population,progress}.py` and README.
- Workstation code: `D:/TradingML/codes/quant-research-workbench-v50-full-674f21b6f`.
- Active workstation campaign: `D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v50/full-674f21b6f-p1024-s2020-g100`. Authority: V50 `active-full.json`, campaign `full-job.json`, `owner.json`, `full-status.json`, generation seals and `launch-proof.json`. Laptop accesses workstation through `//DESKTOP-SAAI85T/Workstation-D`.
- Scheduled tasks: `Codex-V50-full-workstation` and `Codex-V50-full-observer-workstation`; no automatic old-chat monitor was restarted.
- V49 stop/transfer audit: `torch_backtest_v49/evaluation-stop-1792939731863279400/{stop-proof,evaluation-summary}.json`.
- Noise audit: `torch_backtest_v50/noise-audit-1792943715844835400/{summary,all-results,live-run-proof}.json`; diagnostic code and controls remain there. No diagnostic process remains live.

## Unfinished or hanging work

1. **TASK-0223, active V50:** full optimization and final validation audit are unfinished. Next owner must inspect active pointers, exact process creation time, freshness/current clocks and last sealed checkpoint; verify every final generation/split receipt, successor/RNG independence, final exit and ownership release before completion claims. The user authorized this run; archive must not stop it.
2. **TASK-0223, robustness:** only limited frozen-candidate noise/transfer diagnostics exist. After usable full-run checkpoints, compare multi-session behavior, drawdown and execution sensitivity without tuning to displayed validation. A new experiment needs explicit scope; do not silently inject noise or revise costs into the active immutable deployment.
3. **TASK-0223, head-label proposal:** conceptual only. First clarify the observation-dependent behavior or exact Boolean operator being evolved and whether weights remain fixed, are solved for one sample, or are fitted across samples. Do not implement constant-label fitting as if it supplied profitable supervision.
4. **Incomplete source chats:** early portions of this chat are compressed; predecessor narratives preserve historical V7-V16 context. Other inventoried chats, including the separate TASK-0223 pipeline chat, were not reread here. Consult their own summaries if those earlier decisions become relevant.

## Handoff to the next chat

Read TASK-0223, this summary, V50 README and the active full-job first. The main next action is a read-only health/seal check of the workstation V50 run, not another launch. Current repository HEAD may include unrelated concurrent commits: never copy it blindly over the immutable deployment. Keep source/population/RNG/pedigree/input seals, financial guards and validation isolation intact. Preserve stopped pilot/V49/V48/V42 evidence. Resume/restart only after failure diagnosis and exact-owner release; architecture, cost or noise changes require a separately authorized version/experiment. This chat may be archived while the scheduled optimizer continues independently.
