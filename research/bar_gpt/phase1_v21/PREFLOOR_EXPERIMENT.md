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

## Longer qualification: rejected for full training

Runtime `phase1-v21-prefloor3000-20261122` uses the same pinned source383d66139
and parent, with new3,000-step identities rather than claiming an exact resume.
The original1,000-step checkpoints remain intact. Control W&B `cclg9coe`,
candidate `tfbwvd5x`. Both reproduced their earlier500/1,000-step reported
metrics. Control/candidate durations329.2/324.3seconds, cached throughput
79,492/80,202windows/s. The existing Rich panel was run in workstation session1.

At3,000, candidate improves only85/216 raw groups over control, versus174 at
1,000. Worst final regression is2.45% raw and0.41pp direction/bin accuracy;
intermediate points are less stable (32raw groups exceed5% at1,500).
Candidate/control all-visible1s close MAE is$0.21605/$0.22059, masked close
$0.28347/$0.27685, daily-volume MAE811,312/822,544. Activity-volume moving
direction is79.253%/79.271%, essentially unchanged from parent's79.246%.

Against the retained parent, candidate improves135/216raw groups, but40 exceed
5% regression and6accuracy groups exceed1pp regression. Worst masked1s bid-high
MAE rises51.57%; worst masked daily ask-range bin accuracy falls2.39pp. Thus the
pre-floor auxiliary change alone is not a robust repair. Do not launch20epochs
from it or treat the1,000-step advantage as persistent. This does not prove the
underlying mechanism irrelevant; it disproves sufficient benefit of this isolated
change in this bounded matched test. It leaves the broader objective/readout
interference and representation-quality questions unresolved.

Control checkpoint SHA:
`69d5d9ee9cf5553bd97cb166cc2f420c8c539681d0111ba5803b3b8aa13d2eca`.
Candidate checkpoint SHA:
`9fec0fbf3aced27378325f3f51511c1addde60437093e3315a0f74cb38884ba8`.
