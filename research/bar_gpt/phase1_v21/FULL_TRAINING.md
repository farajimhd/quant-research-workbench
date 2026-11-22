# V21 full-population continuation

## Two-epoch intervention

The low-LR run was gracefully stopped after two complete validations during the
2026-11-22 monitor review. Initial/epoch1/epoch2 balanced scores were
0.59115/0.59061/0.59818 (lower is better). Daily trade-volume raw MAE reached
2.70x initial, daily quote-count MAE about2.44x, and moving-direction metrics
declined despite much better range classification. Daily volume error changed
from underprediction to a large positive bias; this is not just an aggregate
dollar-MAE comparison. All source data and predecessor checkpoints remain intact.

The next bounded matched trial uses `--activity-weight 10`: multiply the existing
log-activity level/change loss and its direction surrogate together by10. This
tests activity-family underweighting against strong price/body/range supervision.
It adds no output, decoder, reference or embedding field and does not clip data.
Default weight1 preserves the previous objective. This is a NEW scientific
identity, initialized from the same retained V21 parent, not an engineering resume.
The trial must compare all raw/direction/range groups with matched early full-data
traversal before promotion. Current operational identity is always ACTIVE_RUN.json.

A second bounded ablation uses `--activity-frame-counts` with the frozen runtime
coverage receipt. Exact training-manifest counts show daily windows are only
3,792,045/186,575,722 (2.03%). Only TRAIN counts derive fixed weights
`N/(6*N_timeframe)` for activity level/change/direction terms; price/shape losses,
sampling, valid masks and raw reported metrics stay unchanged. Source split hash
and full count reconciliation are required. No market shards were reread to fit
these weights; metadata fully resolved every selected window. This tests a
population-imbalance hypothesis rather than assuming the cause is established.

At matched 2,000 updates, blanket activity weight 10 improved only 95/216 raw-MAE
groups and raised all-visible 1s close MAE from $0.23991 to $0.31661. It was
rejected. Training-frequency activity weights improved 140/216 groups, lowered
daily-volume MAE from 890,455 to 761,452 and daily bid-count MAE from 24,646 to 22,973.
The latter also lowered visible/masked 1s close MAE to $0.22991/$0.27535 versus
$0.23991/$0.28904. However, masked 10s ask errors regressed by up to 48%, so this
is not evidence of a uniformly repaired model. The same balanced trial was
extended with exact optimizer/RNG/traversal resume to a bounded 5,000 updates.
These are fixed development-cache results, not full-validation results.
Runtime receipts: `activity-repair-comparison-2000.json` and
`activity-extended-control.json` in `phase1-v21-campaign-20261121`.

The 5,000-update extension also failed promotion. Daily volume improved 24.6%
and daily bid count improved 10.2% versus the matched control, but only 92/216
raw groups improved. Worst masked 1s ask MAE rose 37.0%, 1s bid moving-candle
direction fell 2.13 percentage points, and the balanced score worsened from
0.57668 to 0.58653. The activity benefit persisted at steps 3,000 and 4,000, but
price regressions fluctuated and did not resolve. Neither loss-weight trial is
approved as a full-training replacement. No training process remains active.
Preserved balanced-trial last checkpoint SHA256:
`61ea23b31d93bdefe59dfd75543bd3c3fad9e9bccfdf85a8e0718674abf6c689`.
Its run is `phase1-v21-activity-balanced-trial-20261122`, W&B `je3rp3xq`, source
`f2d163f3213609ea3b0529595e2f1f53a387e7ef`. Full per-group comparisons are in
`activity-balanced-comparison-extended.json`. The old full run remains stopped
with its checkpoint and STOP preserved. Population imbalance is confirmed;
gradient interference is a hypothesis requiring direct measurement.

## Gradient diagnosis and price-level ablation

`gradient_audit.py` measures separate objective gradients without updating the
audited checkpoints. It records exact training-cache indices, hashes, masks,
per-module norms and pairwise cosines. A second audit used 128 windows per
timeframe (768 total), rather than interpreting a population sample containing
only two daily windows. It ran at both 16-visible and 64-visible settings.
On the retained parent, the unweighted temporal price-level gradient norm was
about 1,387 masked / 716 visible, versus activity 23 / 37. At the rejected
5,000-step checkpoint it was 3,263 / 2,335 versus activity 25 / 33. The bar
anchor gradient was small. Activity/candle conflicts were sample-dependent;
the temporal cosine was mildly negative in the stratified audit. These are
pre-clipping, pre-Adam gradients, not causal proof or effective update norms.

The next matched bounded trial changes only `--price-loss-weight 0.05`.
This scales the price-level part of the inherited log-value objective, leaving
its activity/coordinate terms and body/range/direction/anchor penalties intact.
It adds no model input, reference, embedding field or decoder. A disposable
16-window real-data overfit test reduced loss 32.52 to 14.01 in 100 updates.
Compare with the same 2,000-update full-data control; do not promote based on
the changed training-loss scale. Runtime hypothesis and acceptance record:
`phase1-v21-campaign-20261121/price005-hypothesis.json`.

The price-weight trial was rejected and gracefully stopped at update 1,334.
At matched update 1,000, only 40/216 raw groups improved: 1s close MAE rose from
$0.23989 to $0.40567, daily volume MAE from 824,605 to 2,599,517 and daily bid
count MAE from 24,365 to 81,313. The balanced score worsened from 0.59795 to
0.74190; the 500-update comparison was already worse. Thus large raw price
gradients do not establish that reducing their weight improves this system.
The pipeline measured 68,430 windows/s (8192 batch, 16 workers), 39.7-second
startup, and passed 16/64-visible graph-gradient parity. Source is
`5d476639c41190bc6243b43a3b4f1f2079184886`; W&B run `slfbgf84`; retained last
checkpoint SHA256 is
`03e601bd7bea1950855b321ac6fa6cbe783af6526a985b196453b1c67ea6b7f2`.
STOP, optimizer state, logs and checkpoint are preserved; owner/workers exited.
No full-training replacement has passed. Next diagnosis should distinguish
optimizer-preconditioned updates and training-batch distribution from raw
gradient magnitude before another weight sweep. The run's W&B failed status
reflects the existing reporter's deliberate-stop handling, not a training crash.

## Loader and optimizer diagnostics; slower decoder trial

The exact ordered CPU loader prefix covered 1,048,576 windows in 128 batches
(32.24 seconds). Of these batches, 79 contained six timeframes, 44 contained
five, and five contained four. Daily windows were 2.114% versus 2.032% of the
full training population. The median dominant-timeframe share per batch was
43.1%; the maximum was 93.2%. This rules out pure-timeframe ordering or missing
daily windows in that prefix, but not batch distribution variance. Source
`de46d088f192fac2433adaeb3c9eae05fb308756`; receipt
`phase1-v21-loader-audit-20261122/result.json`. Last-bar scale summaries in that
receipt must not be described as whole-window statistics.

The saved-Adam audit used 128 training windows per timeframe, two checkpoints,
and both visibility modes. Under fixed bias-corrected second moments and saved
group LRs, the stopped full run's masked decoder activity norm was 3.217 versus
price-level 0.791; temporal price remained larger (9.268 versus activity 0.638).
This reverses the decoder's raw-gradient ranking. These quantities exclude
momentum, next-step variance and clipping: they are not actual Adam updates.
Source `3505ef761f1707b33f2cae8b885ea84ec8c667b3`; receipt
`phase1-v21-adam-audit-20261122/result.json`.

A matched trial then changed only `--decoder-lr-multiplier 0.1`, retaining the
original objective and encoder LRs. Source `241426407c170a37fc1fc10a3e57e2a4f87870a1`;
runtime `phase1-v21-decoder01-trial-20261122`; W&B `ikhewtws`. It completed 2,000
updates at roughly 65-70K windows/s. Compared with control, masked 1s close MAE
improved $0.28904 to $0.23122, all-visible close worsened $0.23991 to $0.24326,
and daily volume improved 1.35%. The balanced score improved 0.60109 to 0.59567,
but daily ask moving-candle accuracy fell 1.84 percentage points. Scores were
worse at 500 and 1,000 updates. Only 90/216 raw groups improved at 2,000, though
the worst raw regression was 3.60%. This is a retained tradeoff, not a supported
full-training replacement under the declared direction-regression criterion.
Checkpoint SHA256:
`ee66197a5b475f7f4d7bcb1b1e24c1c58fc8cb067b6820087dcc3f05ba78e9a0`.
Owner/workers exited; checkpoint hash and remote W&B metrics were verified.
Per-group evidence is `phase1-v21-campaign-20261121/decoder01-comparison.json`.

### Bounded extension and numerical check

The same decoder-rate identity was extended to 5,000 updates with exact state
resume, retaining the complete step-2,000 checkpoint separately. The unchanged
promotion gate still failed: only 45/216 raw groups improved, worst masked quote
MAE increased 51.5%, and worst moving-candle accuracy fell 1.94 percentage
points. Balanced score was 0.59026 versus control 0.57668. The intermediate
3,000/4,000 scores were 0.65219/0.58429 versus 0.59148/0.58698: improvement was
not stable. The final checkpoint SHA256 is
`3e2657083d599de388b2e524784c39546a6f2131ffddc1aa893b507997de75cc`.
Evidence: `decoder01-comparison-extended.json` in the campaign runtime. Owner and
workers exited, hashes and final remote W&B metrics were verified. Do not
promote or blindly resume this trial.

A paired readout-precision audit evaluated the final checkpoint on 11,146 fixed
development windows. It reused identical FP32 decoder codes and masks, changing
only subsequent coordinate assembly from FP32 to FP64. A synthetic test proves
FP32 can erase a sub-ULP candle body, but the measured all-visible moving-candle
accuracy differences were below 0.1 percentage point. Maximum relative raw-MAE
change was below 4e-7. This numerical effect does not explain the major drift.
No training transform, checkpoint or metric authority was changed by the audit.
Source `83ea7805c449c2f0a2a07013b783158739dc2130`; receipts under
`phase1-v21-precision-audit-20261122`.

Reporter commit `8aeb85029` corrects future planned `qualification_complete`
runs to W&B exit code zero while retaining that distinct local state. Four
status tests pass; failure and deliberate-stop classifications stay unchanged.
It was not injected into an immutable running snapshot, so earlier W&B runs
retain their historical failed label despite completing their bounded work.

The user approved 20 epochs despite the earlier reconstruction/direction
tradeoffs, and subsequently explicitly allowed any learning rate. Three matched
initial full-data trials selected a substantially smaller fine-tuning LR.
No model, input, decoder, objective, data split or validity rule changed between
these LR trials. Both encoders and the single head train under `joint_repaired`.

## Historical selected run (stopped; do not resume blindly)

- Runtime: `D:/TradingML/runtimes/bar_gpt/phase1-v21-full20-low-lr-20261121`.
- Source: `0b404847bbbb8a9c1ef143e595e8f27d314c8a8c`, hash-verified immutable snapshot.
- W&B: https://wandb.ai/mehdifaraji/bargpt-phase01-v21-full20/runs/1hx6mtjb
- Parent: retained V21 body-calibrated checkpoint in RESULTS.md, unchanged.
- All 186,575,722 training windows per epoch, 20 epochs; unchanged 47,750,007-window
  validation population after every epoch. Development checks every 1,000 updates.
- Batch 8,192, 16 workers, prefetch four, verified shard prefetch, pinned staging,
  captured raw preparation/teacher/student/backward and captured validation metrics.
- Decoder and temporal encoder LR: 250-update warmup from 1e-7 to 1e-6, then cosine
  to 1e-7 by consumed windows across the complete 20-epoch horizon.
- Bar encoder multiplier: 0.03, thus peak 3e-8 and floor 3e-9. It remains trainable.
- AdamW, zero weight decay. Last/best checkpoints retain optimizer, RNG, cursor
  and scheduler state. Engineering resumes must keep batch and worker counts.

The manifest's original authorization prose mentions the initial 1e-4 request;
the user's later explicit free-LR instruction supersedes it. Numeric contract
fields above are the actual executed schedule, not that historical prose.

## Complete-pipeline profiling

The immutable profile receipt is `phase1-v21-full-profile-20261121/profiles.json`,
source `9101dbd7ad3d085cea792edb9e3dc327af06ef78`.

| Batch | Workers | Measured windows/s | Loader wait, 64 batches | Startup |
|---:|---:|---:|---:|---:|
| 4,096 | 16 | 66,219 | 0.0247 s | 39.7 s |
| 8,192 | 16 | 68,333 | 0.0266 s | 39.9 s |
| 16,384 | 16 | 66,096 | 0.0297 s | 40.3 s |
| 16,384 | 32 | 64,693 | 0.0320 s | 82.3 s |

Selected 8,192/16. Its peak allocated memory was 10,560 MiB during profiling.
Live training sustained roughly 65-69K windows/s, about 19 GiB reported device
memory and 97-100% sampled GPU utilization. A larger batch was not faster.
The old 150K/s raw-baseline workload is not a matched model/loss comparison.

Visible-only frozen-teacher work saves computation without changing the anchor
loss. Actual 16/64-bar batches passed loss/norm and every-gradient parity against
eager execution, plus comparison to the original full-teacher anchor. Prior
full validation measured 76.8K windows/s with the same 8,192/16 validation setup.

## Learning-rate evidence

Identical initial full traversal and parent; figures below are fixed development
metrics, not full-validation or forecasting results. All predecessor checkpoints
and logs remain preserved.

| Trial | Updates | 1s close MAE | Masked close MAE | Candle direction | Daily bid range bins |
|---|---:|---:|---:|---:|---:|
| Parent | 0 | $0.22770 | $0.21152 | 91.14% | 79.31% |
| Equal LR toward 1e-4, warmup500 | 250 | $1.25633 | $2.63203 | 69.40% | 87.61% |
| Protected encoders, warmup5000 toward 1e-4 | 1,000 | $0.33266 | $0.35179 | 87.71% | 85.49% |
| Selected peak1e-6, bar multiplier0.03 | 1,000 | $0.23989 | $0.23774 | 90.68% | 81.94% |

The equal-LR run hit the 10x worst-group emergency guard (17.46x observed) and
stopped at250. Its fixed legacy bar readout close MAE rose from $0.01305 to
$0.20311, evidence of substantial embedding drift; a fixed readout is diagnostic,
not a refitted information-retention probe. Lower encoder rates reduced that
drift. The selected run is the strongest LR trial, but has not yet improved all
parent metrics. The user explicitly approved observing full-data learning.

The initial checkpoint writer rejected a NumPy float quality score before any
update. Converting the score to a native float fixed it; a regression test passed.
The zero-update restart retained its original W&B identity. This engineering
failure is distinct from the subsequent equal-LR scientific failure.

## Recovery, panel and monitoring

The selected run completed a deliberate 1,000-update canary and continues from
that checkpoint without resetting optimizer, schedule, RNG or traversal. Resume:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.bar_gpt.phase1_v21.run_full --mode train --output D:/TradingML/runtimes/bar_gpt/phase1-v21-full20-low-lr-20261121 --source-revision 0b404847bbbb8a9c1ef143e595e8f27d314c8a8c --epochs 20 --batch-size 8192 --workers 16 --learning-rate 1e-6 --min-learning-rate 1e-7 --warmup-steps 250 --bar-lr-multiplier 0.03 --temporal-lr-multiplier 1 --validation-every 1000 --checkpoint-every 500 --resume D:/TradingML/runtimes/bar_gpt/phase1-v21-full20-low-lr-20261121/checkpoint_last.pt
```

Check exact PID/creation-time ownership and STOP markers before running it. The
runtime `resume-full.ps1` is an equivalent detached-launch entry. No epoch limit
is bypassed. Prefix replay preserves exact data order; it can take time on resume.

Use `full_panel --output RUN` for Rich progress, and `monitor --output RUN` for a
CPU-only process/counter/validation receipt. The thread heartbeat is
`monitor-bargpt-v21-full-training`, every30 minutes. Its authority is runtime
`phase1-v21-campaign-20261121/ACTIVE_RUN.json` and `FULL20_MONITOR.md`.
It checks real counters, W&B readback, speed, failures and multiple validation
points, preserves STOPs and unrelated owners, repairs engineering failures, and
uses new identities for changed scientific experiments. It disables itself after
20 epochs and final validation; no automatic multi-day extension is authorized.
