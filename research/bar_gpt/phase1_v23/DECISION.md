# V23 larger encoder experiments — 2026-11-22

Completed six bounded experiments and the final structural audit. The small
128x2 encoder remains the best overall choice within this matched campaign.
Larger models improved single-window fitting and, in one arm, range-bin accuracy,
but did not establish a robust balanced-quality advantage. No full training was
launched and no candidate is promoted past the low-price quality gate.

## Controlled comparison

The feature-attention encoder stays width 16, four heads, one block, with a 16D
bar representation. Temporal attention expands from width 128 / two blocks /
four heads to 256 / four / eight and 384 / six / twelve. Head dimension remains
32. Position-aligned encoder outputs are **64 x 32**. Every model uses the same
shared Linear(32,18) decoder: **594 parameters**, receiving only exported slots.
No target references or additional inputs enter the decoder.

All arms start from the trained V22 e07 checkpoint. Existing heads are replicated;
added blocks start as identity residuals. FP32 function parity, finite gradients,
hidden-input isolation and checkpoint restoration were tested. The new blocks
received nonzero updates; this was verified in saved checkpoints.

This tests temporal capacity with a warm start, not larger feature encoders or
from-scratch training. Function preservation does not imply identical Adam
updates or exact mixed-precision equivalence. Native initial aggregate raw MAE
for width 384 was 4.94% above the parent; width 256 was essentially unchanged.

Unchanged transforms: fixed training-only normalization of log prices, log1p
activities and metadata at the feature encoder; the decoder reconstructs absolute
log close, log body/wick coordinates and log1p activity. No local median or true
reference is supplied. The V21 joint_repaired objective, Phase 0 anchoring,
source-valid masks and prefloor activity-change supervision remain unchanged.

Each arm receives 8,388,608 sampled windows, batch 4,096, 2,048 updates, with full
development evaluation at steps 0/512/1024/1536/2048. Sampling is uniform with
replacement from the same 114,599 training windows. There are 11,146 development
windows. These are repeated exposures, not millions of independent examples.
16 randomly selected bars are visible for three of every four updates; all 64
are visible on the fourth. Masked validation scores hidden positions separately.

## Results

All-visible development results; close MAE is **1s trade close only**. Direction
and range are equal-group means over available timeframes and streams. Candle
direction excludes unchanged candles; activity direction is balanced
down/flat/up recall. Full per-channel and masked results are in runtime receipts.

| Run | Parameters | Close MAE | Candle direction | Activity direction | Range-bin accuracy | Training windows/s |
|---|---:|---:|---:|---:|---:|---:|
| e01 control 128x2 | 368,066 | $0.7029 | 86.50% | 48.63% | 21.68% | 43,927 |
| e02 256x4 | 2,437,058 | $2.9969 | 87.15% | 48.84% | 23.55% | 26,314 |
| e03 384x6 | 7,788,482 | $0.9438 | 84.45% | 48.79% | 32.46% | 15,052 |
| e04 control, seed 997 | 368,066 | $0.4424 | 86.55% | 48.71% | 22.79% | 44,330 |
| e05 256x4, width-adjusted LR | 2,437,058 | $0.9130 | 86.24% | 48.69% | 22.50% | 26,368 |
| e06 384x6, width-adjusted LR | 7,788,482 | $1.0579 | 82.98% | 48.51% | 16.20% | 15,092 |

The first three arms use the same sampling seed 559. The fourth repeats the
best original configuration with seed 997. Width-adjusted follow-ups use seed
559 and the original V22 parent, not the preceding larger-model checkpoints.

## Confirmed optimization confound and follow-up

A CPU FP32 diagnostic on 64 fixed training windows measured the mean absolute
log-close change after one Adam update. For 16 visible bars it was 0.2768 at
width 128, 0.5525 at 256 and 0.8438 at 384, despite matching starting predictions.
Dividing temporal LRs by width/128 reduced these to 0.2807 and 0.2888. The
all-visible diagnostic produced the same pattern. This is a one-batch diagnostic,
not an LR optimum or validation result.

The campaign was explicitly expanded from four to six runs to test this issue.
Original LRs: feature 3e-7; temporal stem/blocks 1e-5; query/readout/decoder 3e-4.
Follow-ups divide both temporal rates by 2 or 3; feature and decoder rates stay
unchanged. Fresh optimizer state is used in every arm. No scientific identity
was resumed with changed settings.

## Qualification and limitations

All original arms passed the finite-gradient/CUDA-graph parity checks. Tiny-fit
loss after 1,000 updates fell from about 138 to 40.7 / 23.2 / 16.0 with increasing
capacity. This confirms learning and better single-window fitting, **not near-
perfect overfitting**. The larger model's range gain does not establish better
balanced reconstruction or forecasting usefulness.

Early and final price errors vary substantially; best-checkpoint curves are
reported alongside equal-budget final results. The medium original run's best
aggregate checkpoint was step 1,536 ($0.8091 close MAE), not its $2.9969 final
checkpoint. Neither beats the matched control's final balanced score.

Low-price errors remain a promotion veto. Across 133,330 valid price fields below
$0.10, native-GPU median relative errors were 264.63%, 446.60%, 376.10%, 151.00%
and 259.72% for e01–e05; e06 was 155.99%. No valid nonpositive or positive-below-1e-8 price
denominators were excluded in these evaluations. Outputs were finite and
nonnegative. The post-training native-GPU audit checked all 11,146 development
windows in both modes: zero nonfinite/negative outputs, OHLC violations or quote
crossings in every arm. All-visible checks covered 2,139,590 valid stream-bars
and 2,852,816 valid bid/ask field comparisons per model. No exported coordinate
was constant across examples at the 1e-8 standard-deviation threshold. Raw
prediction/target means and variances for all 18 channels are retained in
`phase1-v23-final-audit-20261122/results.json`. This rules out total embedding
collapse, not loss of particular information or accurate shape amplitudes.

Against the seed-559 small control, final geometric raw-MAE ratios across 216
groups were 1.636 (e02), 1.082 (e03), 1.100 (e05), and 0.992 (e06). Thus e06 has
a small aggregate raw-error gain, but only 86/216 groups improve and its candle
and range accuracy worsen. Worst raw regressions versus that control were
4.375x for e02 (1s bid high), 1.751x for e03 (daily bid count), 1.327x for e05
(masked 1s trade high), and 1.538x for e06 (1s bid high). Full worst-group lists
are in `worst-groups.json`. Control seed variation is material; tiny differences
do not establish architecture superiority.

The older trained V21 reference, reevaluated in V22, had $0.2277 close MAE,
88.01% candle direction and 32.86% range accuracy, versus 46.19% activity balanced
direction. It is not a matched continuation in this campaign, but remains an
important reminder that the new slot models have not beaten all prior evidence.

This is development-set evidence only. No held-out audit or forecasting probe
was consulted because these candidates still fail reconstruction quality gates.
Frozen first-2,048-window training evaluations are retained but do not have the
same group composition as development, so their gap alone is not an overfitting
diagnosis. These experiments do not disprove larger-model generalization.

## Throughput and reproducibility

The complete bounded dataset was cached on the workstation GPU; initial dataset
and teacher-cache preparation took about nine seconds. Training uses CUDA graphs qualified against eager
gradients, with no disk loader on the update path. Batches 1,024/2,048/4,096 were
profiled; a common 4,096 preserved matched optimizer steps and samples. This
prioritizes a controlled comparison over individually changing batch size.
Reported throughput includes gather, masking, loss, backward, optimizer and
synchronization, excluding startup/evaluation. Allocated profile peaks were
about 10.7 / 20.9 / 37.1 GiB. A live large-model check showed 100% GPU utilization.
Individual experiments finished in 4.25–10.86 minutes including their tiny-fit,
profile and end evaluations. Both trainers exited successfully without resumes
or engineering failures. The final six-model structural audit took 10.4 seconds.

Source revisions:
- Original four runs: `4aa10499bec4cfd993bb8bab79336ad9b52961b9`.
- LR follow-ups: `dd602865d21ff41701fbafd71381823a384d2fc3`.
- Post-training audit/report: `cf325586ba2b421516ad44669e8dc3b4cf5ba250`.

All were committed/pushed on the laptop before immutable workstation deployment
and file-by-file hash verification. Five focused tests pass on both machines.

Workstation runtime folders under `D:\TradingML\runtimes\bar_gpt`:
- `phase1-v23-large-20261122`
- `phase1-v23-large-lr-scaled-20261122`
- `phase1-v23-final-audit-20261122`

Dataset hash: `434661f361dbbc228f1bfd287455cb1ab71029c9c9e7d669788cfdbc959c2951`.
Training supplement: `d9e8bd9a84efd4172caf747b55648b70692167ff4bc3b80a59a1b88ba167fbf3`.
Original split: `d60f707c91c54b72ec7a11a9119334cc973351be699c8b6d5f3d724957640d63`.
V22 parent checkpoint: `a9c018fc4a37c6cf01a50021d68c459abe26dad90adb43534ab9d2854c63f6ee`.
Inherited training calibration and Phase 0 checkpoint hashes are in manifest.json.
No calibration used development samples; no sampling weights were introduced.

Use README commands to reproduce training. Run compare/checkpoint_audit with
`--output <original-runtime> --additional-root <LR-followup-runtime>` to rebuild
the combined report without changing original result receipts.
[W&B project](https://wandb.ai/mehdifaraji/bargpt-phase01-v23-large) contains
metrics/configuration only; checkpoint-upload verification is recorded separately.

## Retained candidate and next decision

Retain e04 as the best balanced checkpoint **within this campaign**, while
keeping e01 as its matched-seed control. Do not automatically promote it over
the older reference or launch full training: the sub-$0.10 median error is still
151%, range accuracy is 22.79%, and masked candle direction is only 51.72%.
These results support addressing scale/readout optimization and shape amplitudes
before another capacity increase. They do not establish forecast improvements.

Pinned workstation checkpoint:
`D:\TradingML\runtimes\bar_gpt\phase1-v23-large-20261122\e04-w128-d2-s997\checkpoint_last.pt`

SHA256: `2fb20c49d4f9ac15aca52ce1528b1aecfc83d097a2de19b2405828582f30977b`.
`checkpoint_best.pt` represents the same final training step. The corresponding
source is `4aa10499bec4cfd993bb8bab79336ad9b52961b9`; the README gives the full
campaign command and the trusted-checkpoint restore call. All other last/best
checkpoints and their receipts remain under the experiment directories.
