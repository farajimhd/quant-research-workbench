# RL trading V2: PPO campaign, executable-action fixes, and stopped continuation

- Chat started: 2026-09-26 07:48:22 PDT (America/Vancouver), verified from session metadata.
- Chat ended or last activity: 2026-09-30, final documentation request; exact request time unavailable in the reviewed context.
- Summary written: 2026-09-30 18:07 PDT (America/Vancouver).
- Chat/task identifier: 01a0de30-4ca4-7c60-bc73-28a34be5a96a; Research Unsupervised RL Trading V2.
- Repository or scope: D:\TradingCodes\quant-research-workbench; research/rl_trading/v2; laptop-only experiments.
- Related task-history entries: TASK-0219.
- Source completeness: Partial. Later implementation and failure investigation are recorded directly; early campaign details include retained handoffs and heartbeat evidence rather than every original design exchange.

## Narrative

The user wanted a second RL trading approach that could learn from its own simulated experience instead of V1 imitation learning. The resulting V2 used PPO actor-critic training for one shared portfolio account. A temporal market encoder, cross-listing context, and account/position state supported a discretionary pass or buy/reduce/close decision for one listing each second, with continuous sizing parameters. The simulator rewarded account equity changes relative to initial equity. Learning from subsequent simulator outcomes did not authorize future information in the observation or decision path.

The campaign remained laptop-only. Source authority was D:\TradingCodes\quant-research-workbench, and a frozen source checkout under C:\Users\g835l\.codex\worktrees\rl-v2-open-v1-pinned\quant-research-workbench launched experiments. Generated banks, logs, checkpoints, and metrics stayed under D:\TradingML\runtimes. V1, the workstation, previous runs, and sealed data were protected. The training contract used Aug19/20/21 training sessions, Aug24 development validation, one $10,000 account, and 512 one-second steps per PPO rollout. Aug25 was reserved for one final evaluation after checkpoint selection, never training or selection.

The execution model used next-second IOC fills against a price-only proxy with fees and modeled slippage. It did not establish production execution quality or profitability. Session handling included a no-entry/mandatory-exit window before 20:00 ET. Missing executable market data could prevent flattening; invalid terminals remained invalid rather than being credited with invented liquidation. Whole-share sizing and causal market freshness were material contracts.

An early severe-churn run, ppo-v2-early-exit-aug2026-seed 17-v1, stopped at checkpoint 645. Its iteration 565 validation mean was approximately -85.8%, with thousands of fills and an invalid terminal. The run and failed W&B record were preserved. Conservative PPO settings subsequently used learning rate 3e-5, clip 0.1, entropy weight 0.0001, and target KL 0.01. Policy-only transfer retained verified lineage while resetting optimizer, account, and RNG. Startup failures were preserved rather than overwritten, including environment discovery and pre-initialization W&B resume problems.

The stable run ppo-v2-stable-aug2026-seed 17-v2 produced a V4 best checkpoint at iteration 113, SHA-256 660ec43dfd1b2ab5f351053cb9a3566afbd6bd4b3f83f7d6e72d9e0738eb4c8c. Its approximately +7.31% mean on three validation rollouts was weaker evidence than the later nine-rollout gate. The campaign tightened selection to require every one of nine fixed stochastic validation rollouts to have a valid terminal and execute trades, with positive 25th-percentile net return. A favorable mean alone was insufficient.

V5 balanced action-type probabilities by eligible counts. It completed 338 iterations and three training sessions but selected no checkpoint. Aug24 mean/q25 returns at iterations 113,226,338 were -4.24%/-5.76%, -6.67%/-11.74%, and -3.54%/-5.79%. All terminals were valid and traded, but the distribution remained negative. Final validation averaged about 149 fills and $149 fees, with very few discretionary closes. Frozen commit c62a89745 had68 focused tests passing. Its first startup attempt failed on serialized tuple/list comparison before training and was retained separately.

V6 introduced an account-level buy/reduce/close choice before selecting a listing. It was stopped gracefully at checkpoint 193 after iteration 113 validation collapsed to mean -29.91%, q25 -44.81%, one invalid terminal, and about 2,001 fills per rollout. The identified migration defect was a uniform new action-type prior that changed the inherited V4 joint probabilities. The architecture change therefore did not preserve the transferred policy's behavior.

V7 corrected that mismatch: learned type logits became residuals over logsumexp of inherited listing logits, preserving the original joint action probabilities at zero residual. Main commit a1a88dde8 and frozen commit 9f4930e30 were pushed;70 focused tests passed. The user asked to continue after early churn improved, so the bounded run finished. It still regressed: validation mean/q25 moved from -4.56%/-7.85% at 113 to -4.43%/-7.68% at 226 and -16.33%/-17.53% at 338. Invalid terminals increased from zero to two to three, and final rollouts averaged about 1,323 fills, $1,096 fees, and 20,080 reduce decisions. This was not convergence, so the run was preserved without a blind extension.

Investigation identified two concrete executable-action defects: reducing one share always rounded to zero, and discretionary reduce/close actions remained selectable with stale current market data. V8 masked stale discretionary exits and one-share reductions. For larger holdings, reduce requested at least one whole share while leaving at least one. Mandatory IOC exits stayed independent, and unavailable next-second execution still failed closed. This was an explicit action-contract change, not evidence that optimization alone had been repaired. Main3796a3209 and frozen caa121736 were pushed, with 71 focused tests passing in frozen source.

V8, ppo-v2-executable-actions-aug2026-seed 17-v1, completed 338 iterations and three sessions. Its nine-rollout validation improved substantially within the run: mean/q25 were -15.52%/-16.87% at 113, -8.00%/-12.17% at 226, and -7.08%/-11.63% at 338. All three validations had zero invalid terminals. Average fills fell from1,152 to 241 to 74, and fees from about$1,064 to$297 to$86. Nevertheless, q25 remained negative and status was no_valid_checkpoint. W&B d7a0109a3a76 synced338. The user instructed stopping after this version; the monitoring heartbeat was deleted and no automatic follow-up was launched.

The user asked what PPO epochs, rollouts, causality, and realized net PnL meant. A512-step rollout represents512 simulated seconds, approximately 8.53 minutes, followed by PPO updates on that collected experience. Configured PPO epochs are repeated passes over a rollout, not additional market sessions. Four epochs with 16 minibatches allow64 updates per iteration, but KL early stopping can reduce that count. Realized net PnL arises on sells from fill proceeds less allocated acquisition basis and selling fees, with buying fees in basis. Simulator accounting updates with executions; dashboard aggregates and completed-session metrics are different reporting frequencies. Improving training or individual replay charts do not replace same-contract validation distributions.

The user later explicitly authorized a new path loading V8's last checkpoint with 12 PPO epochs. Existing exact continuation correctly rejected changed epochs, so an explicit --continue-with-more-epochs-from-run path was added in research/rl_trading/v2/train.py. It permitted only increasing epochs and the cumulative completed-session target while preserving the remaining model, data, execution, and training contract. Unlike policy-only migration, it restored policy, optimizer, account, cursor, and RNG. Strict ordinary resume was not weakened. README and tests documented and checked this narrow exception. Main commit 3f863d427 and frozen d53d465c7ee3427d29aaf1ddf1c939e6e34588da were pushed;71 main and 72 frozen focused tests passed. Frozen cherry-pick conflicts were resolved while preserving its existing migration helpers.

Preflight verified V8 checkpoint 338 SHA-256 eeffd9f86f99e80fc81fffa1d84d02562a28e2958da9eeabe542877724564581. The new CUDA run ppo-v2-executable-actions-aug2026-seed 17-12epochs-v1 targeted six cumulative sessions and 676 global iterations, adding three sessions. It used a512-step rollout, batch 32, seed 17, unchanged conservative optimization and KL early stopping, and the same nine-rollout validation authority. Its lineage explicitly recorded epochs 4 to 12. W&B33e8c7898bd5 was online; no recurring monitor was recreated.

That continuation terminated abruptly. Read-only investigation found both launcher and child absent, while stale status still said running at 564 with five completed sessions and zero recorded failures. W&B had synced564, and checkpoint 564 was readable, contract-compatible, and had finite policy tensors. The last stdout was2026-09-30 19:59:53 UTC during iteration 565 validation, replicate 5 at second 26,239 of57,600. There was no Python traceback or useful W&B exception, no matching reboot evidence, and no confirmed Windows event attribution. Older kernel reports were not treated as proof of this failure. The cause remains unknown; there is no demonstrated training-code exception or diagnosed GPU/OS cause.

Continuation validation 339 had mean -5.54%, q25 -8.07%; validation 452 then deteriorated to mean -40.54%, q25 -43.71%, three invalid terminals, about 2,893 fills and $2,643 fees. Neither passed selection. Thus the early improvement did not establish that more epochs would continue helping. The user explicitly declined restart. No resume, code repair, driver change, status rewriting, holdout evaluation, or new experiment followed. The final documentation request records this stopped state for archiving.

## Durable decisions

- **Requirements:** Laptop only; preserve V1, old runs/banks, causal observations, source/data hashes, and fail-closed execution. No new training or restart without fresh user authorization.
- **Architecture:** V2 learns from PPO simulator trajectories, not imitation targets. Distinguish policy-only migration with fresh state from exact stateful continuation. The12-epoch exception is narrow and explicit.
- **Acceptance:** Nine fixed Aug24 rollouts must all terminate validly and trade, and q25 net return must be positive. None of V5-V8 or the12-epoch continuation met that gate. Repeatedly inspected Aug24 is development validation, not an untouched release test.
- **Rejected approaches:** Blindly extending regressing runs, weakening terminal validity, using Aug25 for selection, assuming a favorable mean means convergence, and claiming production profit from modeled fills.
- **Assumptions:** Fees/slippage and price-only fills are simulator modeling assumptions. Strong simulator results would still require independent execution and generalization evidence.
- **Uncertainty:** V6 migration mismatch and V7 executable-action defects were confirmed. Remaining PPO drift/churn and the abrupt12-epoch process exit are unresolved; more training is not a proven remedy.

## Delivered outcomes

- Versioned V2 PPO training and preserved experiment lineage, checkpoint contracts, validation gates, online W&B metrics, and bounded session targets.
- Confirmed probability-preserving hierarchical migration and executable reduce/freshness corrections, with pushed commits and the focused test counts recorded above.
- Explicit 12-epoch continuation in research/rl_trading/v2/train.py, accompanying README/test updates, and real frozen-source preflight/startup validation.
- Runtime roots: D:\TradingML\runtimes\rl-trading\v2\train\<run-name>; launcher logs: D:\TradingML\runtimes\rl-trading-v2-laptop-campaign\train-<run-name>.stdout.log and .stderr.log.
- Final W&B records: [V8](https://wandb.ai/mehdifaraji/rl-trading-v2/runs/d7a0109a3a76), [12-epoch continuation](https://wandb.ai/mehdifaraji/rl-trading-v2/runs/33e8c7898bd5). Failed and superseded experiments remain preserved.
- TASK-0219 records the stopped campaign; TASK_HISTORY.md is regenerated from CSV. Unrelated notebook/test modifications are excluded from this documentation commit.

## Unfinished or hanging work

| Item | Current state and reason | Exact next action / dependency | Task |
|---|---|---|---|
| Robust convergence | No selected checkpoint; later validation regressed despite some early improvements. | Only if user reauthorizes: inspect PPO/account/execution evidence and propose a bounded hypothesis before further compute. | TASK-0219 |
| Abrupt termination | Processes exited during validation 565; checkpoint 564 survives; status is stale. Cause unconfirmed. | Only if requested: read-only OS/process/log investigation; do not infer a cause or restart automatically. | TASK-0219 |
| Sealed evaluation | Aug25 unused because no final valid checkpoint was selected. | Keep sealed; evaluate once only after valid final selection and authorization. | TASK-0219 |
| Production acceptance | No calibrated execution or production profitability evidence. | Separate simulator, generalization, and execution acceptance if research resumes. | TASK-0219 |

## Unavailable or incomplete source chats

This summary reviews only the current chat. Related discoverable chats were not reviewed or merged: Research RL trading requirements (01a025a4-77f2-7021-bbac-8caf6421aef1), Review hindsight dataset phases (01a0d394-5478-73e3-bbde-7206b07fea85), and Design RL trading v3 architecture (01a0e859-d349-77e2-ae61-8d0b3957004d). Their exact dates were not needed or verified. Earlier exchanges in this chat are partially represented by retained campaign handoffs; missing details are not reconstructed. V3 decisions must be recovered from its own chat rather than assumed to belong to V2.

## Handoff to the next chat

Read TASK-0219, this summary, research/rl_trading/v2/README.md, and the continuation contract in train.py first. The final operational instruction is **do not restart training**. There are no live campaign processes or recurring monitor established by the final work. Preserve checkpoint 564 and all run artifacts, including stale status; inspect actual process/log evidence before interpreting it. No checkpoint passed the robust selection gate. Do not touch V1, workstation, sealed Aug25, or unrelated working-tree changes. The next operational action requires a new user request; archiving this chat does not authorize resume or cleanup.
