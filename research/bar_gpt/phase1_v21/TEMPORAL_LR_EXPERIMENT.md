# Temporal update-size ablation

ADAM_STEPS_AUDIT.md found that the temporal encoder dominates the observed
uphill updates, while one downhill local estimate overshot at the finite step.
This motivates reducing only temporal update size; it does not establish that
lower LR will repair the model. Prior decoder-LR0.1 and global-weight trials
changed different variables and are not repeated here.

Compare `joint_basis_prefloor` against `joint_basis_prefloor_slow_temporal`:
same retained body-direction initialization, activity DCT decoder coordinates,
pre-floor auxiliary activity losses, 512D embedding, single lightweight decoder,
data/sampling/masks, seed559, Adam defaults and weight decay0. Control temporal
LR1e-6; candidate1e-7. Both bar LR3e-8 and decoder LR1e-6. All parameters remain
trainable. No raw fields, targets or reference values are appended to embeddings.

Each arm starts a fresh optimizer under a new scientific identity, not a resume.
The three-group candidate optimizer records its temporal multiplier in the
checkpoint contract; the unchanged control retains its original two groups.
Restore preserves the multiplier and explicit basis schema. Local checks verify
identical initial model tensors, complete/disjoint parameter groups and unchanged
bar/decoder rates. Existing basis parity, checkpoint and tiny-fit gates apply.

Plan3,000updates per arm with500-step development validation, measured batch
selection and CUDA graphs. Fixed train114599/dev11146window identities; same
samples within the pair. Each arm has the existing50-minute loop deadline plus
evaluation reserve within60minutes. Preserve original checkpoints and STOPs.
Compare all216raw groups and directions/range bins against both control and
retained parent. Flag>5%raw and>1pp accuracy regressions; do not promote solely
from aggregate loss or one final validation. Close-price band checks remain
necessary for any promising finalist before full training.

## Completed paired result (2026-11-22)

Source `66a00c204b679102a7163ffbe8bd866278100bf7`, runtime
`phase1-v21-temporal01-20261122`. Both arms completed 3,000 updates at
batch8,192; W&B `t479m5ko`/`w0zhp704` finished with remote metric readback.
Control330.3s/79,175 cached windows/s; candidate324.6s/79,871/s. These are
cached experiment measurements, not full-shard throughput. Foreground exit0;
training and panel processes exited; panel task Ready.

Candidate versus control improves117/216 raw groups, with20 regressions>5%
(worst1s bid-high+15.25%) and one accuracy regression>1pp. Against retained
parent it improves174/216 groups, with one raw regression>5% (masked daily
volume+7.66%) and ten accuracy regressions>1pp (worst masked daily ask range
-3.123pp). Parent/candidate all-visible1s close MAE is0.22770/0.20925 dollars;
masked0.21152/0.19182; candle direction91.142/91.046%. Daily volume MAE
729291/739603. Close learning is steadier, but range regressions grow across
the seven validation points: accuracy flags0,2,7,7,9,10,10. Standalone bar-probe
close MAE worsens0.01305 to0.01581. Lower temporal LR is not a complete repair.

Frozen follow-up `phase1-v21-temporal01-audit-20261122`, W&B `sf6jxfx3`,
completed18.8s over114599 train/11146 dev windows. It restores the actual
basis checkpoint and rechecks its hash. Raw groups improved183 train/174 dev;
27 worsen on both,15 improve train but worsen dev. Masked daily volume also
worsens7.96% on train, so that defect is not validation-only overfit.
No embedding dimension has variance below1e-10. Dev1s trade close below$0.10
mean relative error worsens2.809% to3.072%, median2.465% to2.810%, P90
5.241% to5.571% (4096 cells, not independent sessions). $0.10-1 mean improves
2.520% to2.436%. No zero/tiny close denominators occur in this audit.

Retain candidate SHA
`efd773910e06d53fbfd6f1b6280cad0be83886c75b9d6f652b0392718cec0433`
for research, not full20 promotion. The evidence supports smaller temporal
updates for price stability, while range and standalone bar retention remain
unresolved. A next experiment should isolate those losses or bar-encoder drift,
with the same parent/control and price-band checks; do not silently restart
full training or assume a longer run will repair the regressions.
