# V19 masked activity regression investigation

V18 completed one full pass and stopped at update22786. Its complete validation
showed masked100ms bid-size MAE increasing from41195 to11936638 (289.76x), despite
an improved aggregate quality score. Price candle direction also declined.
All parent checkpoints, datasets, receipts and STOP markers remain preserved.

First reproduce the exact failed metric over all47750007 validation windows,
same8192 batch,16 ordered workers, mask seed917 and source contract. Retain only
the32 worst windows with raw/validity/time tensors, exact masks and traversal
positions. These cases are diagnostic only: never training examples or fitted
calibration. Compare parent/stopped predictions and an explicitly labelled
untrained count-scaling ablation. Source hashes and traversal allow reproduction.

Hypothesis: the polynomial log-coordinate activity loss underweights rare raw
overshoots after expm1, while64/N slot scaling may amplify masked extrapolation.
The audit must distinguish these hypotheses from data or reduction bugs.

Four matched6000-update experiments start from the preserved stopped checkpoint,
fresh AdamW1e-5, seed559, original training/development subsets and fixed teacher.
`control` retains V18 exactly. `relative01` and `relative1` add weights0.1/1 of
Huber(beta0.1) on (prediction-target)/(1+target), averaged equally over valid
activity channels and source-valid bars. This is computed as expm1 of the log
ratio in FP64; no valid target or prediction ceiling is introduced. It includes
hidden bars with full weight. Existing log-level gradients still correct negative
pre-clamp coordinates. Other objectives, architecture, masking and256D embedding
remain unchanged. A changed objective has a new W&B identity.

No raw inputs, references, directions or statistics are appended to embeddings.
Targets enter loss/evaluation only. Calibration uses the original training subset;
no validation outlier is trained on. Each arm is bounded to60minutes including
its tiny-fit gate and evaluation. Selection must retain price/body quality and
reduce native tail errors, then pass full-population validation before full training.

The full forensic scan reproduced MAE11936637.613190947, matching the failed
validation reduction. Its32 worst windows contribute99.9251% of total error.
Worst target51200 was predicted as4.848e15, versus9.031e12 by the pre-full parent.
On the same cached visible inputs, removing count scaling without retraining
reduced the stopped model prediction to951898; all-visible prediction355820.
This confirms amplification, not accuracy of an untrained scaling change.
`unscaled_relative01` is the fourth matched arm: same relative01 objective,
but no64/N multiplication of positional slots. Both architecture state and
loss identity are recorded; checkpoint readers restore the count-scaling flag.
Known failure cases are evaluation-only stress tests and are explicitly labelled
as such. They are neither pristine held-out evidence nor fitted training targets.
