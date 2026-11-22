# Actual Adam-step diagnostics (2026-11-22)

Eight disposable continuation steps expose noisy price updates and a mismatch
between activity log-loss and raw-unit metrics. These observations motivate
targeted optimizer/objective tests; they do not establish an optimizer bug or
the cause of all full-run regressions. Stochastic momentum need not decrease
every individual batch loss.

First audit source`784403e2ee2412a5944f76c81bae70df6fca277b`, runtime
`phase1-v21-adam-steps-20261122`, W&B`055zik8a`. It restores the pre-floor control
and combined basis/pre-floor3,000-step checkpoints, including Adam moments,
sampling RNG, CPU/CUDA RNG, LR and clipping. Identical next batches/masks are
verified by hashes. Steps3,001–3,008 include six16-visible and two64-visible
updates. Training data/supplement/teacher/checkpoint hashes are checked; original
checkpoint hashes are rechecked after execution. No updated model is saved.
Same-batch before/after measurements cover component losses and visible/hidden
raw MAE for all18features. Runtime7.97seconds after preflight/provenance.

Total loss rises on5/8combined-candidate steps and3/8control steps. The price-loss
term accounts for nearly all the total-loss movement. Norms before clipping are
roughly1,083–7,106 across the two copies; clipping and saved moments are included
in these real updates, unlike earlier fixed-preconditioner diagnostics.

On combined step3,007, total loss falls0.2474; base activity log-loss falls4.10e-6
and auxiliary activity loss falls3.50e-5. Nonetheless59/72activity raw-MAE groups
worsen. Median raw-group change is+0.0294%; worst is visible1s bid-size+0.434%
(+13.46native units of MAE). This demonstrates a local objective/metric tradeoff,
not proof of its cumulative effect over an epoch. Other steps improve activity
groups; report the full receipt rather than cherry-picking this step.

## Update alignment and precision counterfactual

Follow-up source`a094d3e540e9b4ad2e6215f192fff778eeaf4ba5`, runtime
`phase1-v21-adam-alignment-20261122`, W&B`whqr8ryn`. Same combined checkpoint and
eight sampled updates reproduce the original BF16 before/after losses. The
gradient/update inner product is computed by module, undoing the common clipping
factor for a first-order loss-change estimate. Additional evaluations disable
encoder autocast on identical before/after weights and inputs; they do not retrain
in FP32 or change the actual BF16-derived update. Configurations restore even on
failure (tested). Runtime7.34seconds. Two focused tests passed.

| Step | Actual BF16 loss change | First-order estimate | FP32 counterfactual change |
|---|---:|---:|---:|
| 3001 | +0.01053 | +0.00837 | +0.00892 |
| 3002 | -0.20355 | -0.30170 | -0.23998 |
| 3003 | +0.14178 | -0.23064 | +0.10924 |
| 3004 | +0.08650 | +0.06410 | +0.07196 |
| 3005 | +0.01378 | +0.01127 | +0.00996 |
| 3006 | -0.38636 | -0.44713 | -0.36738 |
| 3007 | -0.24740 | -0.60314 | -0.20933 |
| 3008 | +0.04737 | +0.03906 | +0.06492 |

Four uphill estimates are dominated by temporal encoder updates. For example,
step3,004 contributions are temporal+0.06403, decoder+0.000066, bar+0.0000048.
Step3,003 has a downhill local estimate but uphill finite-step result, consistent
with curvature/overshoot or forward-gradient approximation effects. BF16 is not
the sole explanation: all five increases persist in the FP32 counterfactual.
This does not rule out benefits from independently training in another precision.

Next: a justified temporal-update-size/momentum or paired-visibility-gradient
test can distinguish these mechanisms. Prior decoder-LR0.1 and global loss-weight
trials do not test temporal update control. Inspect prior LR trials before
choosing an ablation, preserve an unchanged control, and evaluate raw activity
errors as well as the log-space objective. No20-epoch run is qualified here.
