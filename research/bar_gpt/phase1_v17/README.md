# Phase1 V17: activity magnitude and change retention

Bounded successor to rejected V16. Keep the frozen Phase0 teacher, original
chronological split, fixed training-only latent normalization, temporal positional
encoder, 256D learned bottleneck and single linear decoder unchanged. Nothing is
appended to the embedding. Retain 16 random visible bars in three of four batches,
with all-visible retention training in every fourth batch, as in the parent.

Hypotheses: the latent loss underconstrains decoded activity; explicit log1p
activity MSE reduces exponential inverse tails; adjacent log-change MSE improves
retention of activity increases and decreases. An optional fourth-power log-error
penalty tests stronger tail control. These are objectives on predicted outputs;
raw targets never enter the encoder or decoder. No target or prediction clipping.

Matched control/activity/activity_delta arms start from the same V16 diagnostic
checkpoint with fresh AdamW at1e-5, seed559, batch1024, 3000 updates, evaluation
every1000. Optional activity_tail uses an additional0.1 fourth-power penalty.
Same frozen67606 training/11146 development windows. Training-only single-window
overfit gates, GPU cache timing and training throughput are recorded separately.
Each arm has a50-minute training stop (remaining10 minutes for saving/evaluation);
the initial campaign is bounded to at most8 experiments and8 hours. No full run.

Metrics compare reconstructed activity[t] to reconstructed activity[t-1], against
the corresponding target change. Both source masks must be valid; never connect
nonadjacent bars or cross windows. Report per-timeframe/channel3x3 confusion,
up/down/flat recalls and counts, balanced accuracy, moving-only accuracy, raw
MAE/bias, log MAE, zero-target count, maximum raw/log errors, nonfinite count and
overshoot count where predicted(1+activity)>10*target(1+activity). Exact equality
defines flat; no denominator-based direction metric. Unchanged baseline is the
target-flat fraction. Masked metrics require both bars hidden; all-visible
metrics and frozen teacher controls are separate. These are retention metrics,
not future forecast accuracy. Native18-channel metrics remain in every audit.

Acceptance requires improved activity scale/tails and direction without material
price/body regressions on development, then broader held-out qualification. A
subset gain alone cannot establish repair of V16's full-population failure.

Run from a committed, hash-verified workstation snapshot:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe -B -m research.bar_gpt.phase1_v17.experiment --checkpoint D:\TradingML\runtimes\bar_gpt\phase1-v16-scale-recovery-20261121\scaled_slots_price\checkpoint.pt --output D:\TradingML\runtimes\bar_gpt\phase1-v17-activity-20261121 --revision SOURCE_SHA
```

W&B project: `mehdifaraji/bargpt-phase1-v17-activity`. Each arm gets a unique run.
Checkpoints retain optimizer and RNG states; a changed loss is a new experiment.
