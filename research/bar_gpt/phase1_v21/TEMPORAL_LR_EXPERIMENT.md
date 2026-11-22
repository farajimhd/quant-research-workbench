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
