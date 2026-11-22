# V22 bounded capacity campaign

Compare global512/global256 against position-preserving64x8/16/32 exports;
then feature attention width16 and the combined candidate. Eighth arm follows
recorded evidence. At most8 scientific configurations, each<=60minutes including
profile/overfit/evaluation; total<=8hours. No automatic full training.

All capacity arms reset readout/decoder; compatible feature and temporal blocks
start at the same retained V21 body-direction parent. The trained parent is an
additional evaluation reference, not a fresh-readout control. Inherited temporal
LR1e-5, featureLR3e-7, new modulesLR3e-4; same rule for every arm. Width4->16
uses tested function-preserving head replication (4heads instead of1). Fixed
training normalization and V21 joint_repaired/prefloor objective are unchanged.
No local reference, target-derived metadata, or input-to-decoder bypass.

Position encoder: existing temporal blocks encode visible states;64 learned
position queries cross-attend only to these states. Visible position-aligned
encoded states / visible-mean hidden states form an encoder residual. Shared
128->N readout exports64xN. The ONLY decoder is sharedLinearN->18, FP32.
Global control retains aligned flattening and mask adapter, resetting readout.
64x8 matches512 scalar representation size, not parameter count.

Frozen V8 training/development identities plus verified V19 training supplement.
No new historical reads. Uniform sampling over frozen pool; no importance
weights. Preserve original split, calibration and teacher hashes. Development
is repeatedly used; final selection is development evidence, not held-out proof.
Masked train16visible for3/4updates;all64 every4th. Source-valid masks preserved.

Profile complete train steps including objective/backward/optimizer, CUDA graph
parity and memory; choose batch from measured512/1024/2048/4096 candidates.
Compare equal sampled-window exposure, also reporting wall time. Local rolling
last/best checkpoints with optimizer/RNG/traversal state; no W&B artifacts,
wandb.save, watch or checkpoint upload. W&B only configs/scalars/results.

Acceptance: finite predictions/gradients; improving tiny fit; no target shortcut;
balanced rawMAE across sixframes/all18channels, price/activity direction and range
accuracy, masked/all-visible separately. Report worst groups and compare to
trained parent. Do not promote solely from aggregate loss. STOP files respected.
