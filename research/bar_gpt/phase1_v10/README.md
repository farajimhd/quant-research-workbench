# Phase 1 V10: large learned masked autoencoder, full certified data

User-authorized fresh full training: 100 epochs, cosine learning rate from
1e-3 on the first update to 1e-5 on the last, no silent early stopping or sample
cap. Every window independently draws 16 random visible bars on every training
visit. MAE means masked autoencoder here; the reconstruction objective uses
source-valid **48 hidden bars**, with log-relative Huber errors, time feature
reconstruction and validity prediction. Full validation separately measures
hidden-bar reconstruction and all-visible reconstruction across the entire
frozen validation partition. Do not confuse it with bounded diagnostics.

Architecture: 64 possible positions, 54-wide bar inputs (27 transformed values
and 27 flags), physically gather 16 bar tokens before a width384, six-layer,
12-head Transformer (feed-forward width1536). Four learned pooling tokens give
a fixed readout; they are parameters, not extra observed bars. Flatten their
outputs and project to576 learned shape dimensions. A separate FP32 learned
projection of138 visible-only summary inputs contributes192 dimensions, giving
a768-dimensional embedding. A single FP32 affine768-to3456 decoder emits64×
(18 structured market codes +9 time values +27 validity logits). Fixed structured
inversion preserves nonnegative activity and OHLC/paired quote semantics; raw
inverse uses FP64. No reference is copied into the embedding or passed around it.

This changes E26's patch tokenization and mixed visibility deliberately: individual
bar tokens avoid processing hidden bars and support exactly16 random observations.
All-visible inference retains the same four pooling outputs; its quality is
measured independently, not assumed equivalent to masked training.

Only training windows from the existing frozen Phase1 subset fit calibration.
The seal binds its payload, identities and original full split. Fixed calibration
does not limit full training: all186,575,722 training windows and all47,750,007
validation windows are traversed per epoch, including worker tails. Data authority
is `phase1-v6-cohort-balanced-v4/split.json`; source reads retain payload hashes,
source-valid masks, split adjustment and canonical time semantics. No historical
flatfile/event reads are introduced. The V7 copied-reference model and its stopped
checkpoint remain untouched; only its certified stream/GPU preparation utilities
are reused. This Phase1 run does not assert a leakage-safe future Phase2 split.

`run engineering` checks actual-data preparation, transform roundtrip, tiny masked
overfit, finite gradients, full optimizer/RNG checkpoint continuation and encoder
export parity. `run compute` compares eager/captured complete optimizer steps.
`run stream` measures real certified I/O, validation, preparation, transfer,
backward and optimizer work; `run validation-profile` measures both validation
modes with native metrics. Generated evidence belongs under the runtime setup
root, not source. Benchmark model changes are not scientific training resumes.

`run train` has safe complete defaults for the requested scientific schedule;
the calibration path, output and immutable source revision are required. The
workstation preflight checks snapshot hashes, GPU owners, RAM/disk reserves,
dataset/calibration seals and STOP markers. Only one GPU task is admitted.
`launch` provides a hidden keep-awake controller with exact child ownership;
no recurring scheduled automation is created. Checkpoints are rolling last/best
with optimizer, scheduler position, RNG and ordered traversal cursor. Exact
resume requires unchanged source/model/data/optimizer/engineering contract and
retains the original W&B identity. A STOP preserves a checkpoint and closes
owned loaders. A failed profile is preserved and not called a completed sweep.

W&B: `mehdifaraji/bargpt-phase1-v10-full-mae`. Training, validation, source/data
identity and diagnostics are categorized; validation includes all18 raw MAEs,
activity log errors, price bands including below$0.10, approximate histogram
median/P90, denominator counts, structural/nonfinite counts and variance.

The `panel` module preserves the V5-style operational layout with training and
validation speed/ETA, loader wait, batch/concurrency, GPU memory, LR and full
validation results. It labels the16-visible/48-hidden training contract explicitly.
No claim of fastest full-stream configuration is made before profiling.
