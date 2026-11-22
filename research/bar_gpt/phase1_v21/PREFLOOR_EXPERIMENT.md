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

## Initial paired result

Source383d66139c097b9134518dda8f133d23d7333bb3, runtime
`phase1-v21-prefloor-20261122`. Control W&B `pachj5rh`, candidate `x0z7xvy8`,
project `mehdifaraji/bargpt-phase01-v21-repair`. Both completed1,000 updates;
candidate/control durations119.5/121.5seconds after profiling and cache setup.
The measured cached throughputs were78,593/78,524windows/s, respectively.
Batch8192 was fastest among256..8192, peak profile allocation12,668MiB.
Both tiny runs reduced loss121.68 to40.68. Graph/eager gradients qualified at
16 and64 visibility. Checkpoint/data hashes remain in runtime receipts.

At1,000, candidate improves174/216 raw-error groups against control, with worst
raw regression1.57% and worst direction/bin-accuracy regression0.219pp. None
cross the declared5%/1pp review thresholds. Candidate/control 1s close MAE is
$0.23251/$0.23357 all-visible and$0.25679/$0.27432 masked. However daily-volume
MAE is746,895/744,652 and 1s candle direction90.769%/90.826%. The retained
parent's all-visible close is$0.22770 and candle direction91.142%.

At500updates, candidate candle direction was88.863% versus control91.016%,
despite better close MAE. Thus this is a retained hypothesis, not a qualified
full-training repair. A longer matched run is needed to determine whether the
final advantage persists. Do not promote based on the final point alone.

`compare_prefloor --run RUN` verifies both checkpoint hashes, final step count,
and starting metrics (1e-8relative/1e-10absolute tolerance for GPU reduction
roundoff), then records all216 group ratios and accuracy differences in
`parent-aware-quality.json` (the original `paired-quality.json` is retained).
It excludes evaluation duration from initial parity and compares every learning
point against the retained parent's initial metrics as well as the trained
control. At1,000, candidate beats the parent in130/216raw groups, but41groups
regress more than5%; worst masked1s bid-high MAE increases39.1%. Two accuracy
groups regress more than1pp (masked daily quote range bins). This is additional
evidence against promoting the short trial.
No source data, output constraints or exported embedding fields changed.
