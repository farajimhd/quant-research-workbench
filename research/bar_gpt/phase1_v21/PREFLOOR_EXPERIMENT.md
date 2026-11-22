# Auxiliary activity changes before the floor

Hypothesis: applying the nonnegative floor before auxiliary activity differences
couples the difference objective to absolute level at negative codes. Computing
that auxiliary objective on unconstrained log1p codes may reduce interference.
This follows BASIS_AUDIT.md; it is not a proven explanation of full-run failure.

Compare `joint_repaired` and `joint_prefloor` under the same experiment invocation:
same retained body-direction parent, seed559, train/development windows, batch,
mask sequence, optimizer and step count. Only the activity delta MSE and change
direction BCE use pre-floor codes in the candidate. The existing unconstrained
activity level penalty, all price losses, raw reconstruction floor, input
transforms and encoder/decoder architecture remain unchanged. The single decoder
still receives only the 512D embedding. No basis parameterization is used.

Initial bounded plan: 1,000 steps per arm, LR1e-6, bar LR3e-8, CUDA graphs,
existing batch profile256 through8192 and tiny-overfit gate. Training uses the
sealed114,599-window pool; development uses the same11,146 windows. This cached
experiment measures compute throughput, not full-population disk throughput.
Retain each final checkpoint and baseline/periodic metrics. Each arm retains
the existing50-minute loop deadline with evaluation reserve before60minutes.

Acceptance requires a useful activity-scale or direction improvement without
material price or worst-group degradation. Report all comparable raw groups,
masked/all-visible separately; flag >5% raw-MAE regression or >1percentage-point
direction regression for review. No full-training promotion from aggregate loss
alone. Outcomes belong in the runtime comparison and this document.

Seven local repair tests passed, including unchanged predictions/other loss
families, finite gradients, and source-invalid exclusion for the new arm.
