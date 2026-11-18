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


## Workstation qualification and full launch, 2026-11-17

Pinned runnable source: `31e35f7f7b69f2897cfdd063e33106575f224d08`.
Encoder11,643,840 parameters; affine decoder2,657,664. Nine local tests passed.
Actual default BF16/CUDA-graph tiny overfit:4.49610 to0.007408 after800 updates;
optimizer/RNG next-update continuation and standalone encoder parity passed.
The real certified loader trainer resumed its verified step40 checkpoint and
advanced to60 with unchanged model/data/schedule contract. These qualification
weights are not used to initialize full training.

Measured complete optimizer throughput (windows/second, short GPU-resident
profiles): graph batch256=35.5K,512=45.3K,1024=50.9K,2048=48.5K,
4096=44.5K,8192=43.8K,16384=44.1K. Full certified stream at batch1024,
eight workers/prefetch4 sustained50.15K over1.536million windows; loader
wait0.157s/30.63s and startup10.59s. Sixteen workers/prefetch2 sustained50.10K,
with startup21.00s. Larger batches/workers did not improve training throughput.
This larger encoder is slower than the historical150K/s narrow raw model.

Validation profiles include both visibility modes and complete native metrics.
Batch4096 measured25.1K steady windows/s; batch16384 measured27.5K. A compact
per-example grouped reduction retained all native results within1e-8 relative
tolerance on identical163840 windows. Batch32768 brought little additional
speed. Selected train/validation batches1024/16384, eight workers, prefetch4,
shard prefetch16/64MiB, one preparation thread/worker. CUDA graphs/fused AdamW,
BF16 attention and IEEE FP32 readout are retained. Graph-pool memory is reported
as reserved alongside active tensor memory; small active allocation alone does
not describe total GPU usage.

Full run: `phase1-v10-d384-l6-e768-mae16-100ep-20261117`, under workstation
`D:/TradingML/runtimes/bar_gpt/phase1-v10-full-setup-20261117/`.
W&B run: https://wandb.ai/mehdifaraji/bargpt-phase1-v10-full-mae/runs/5rklsuk2 .
100 full epochs;182207 optimizer updates/epoch including worker tails,
18,220,700 total updates. Rolling verified checkpoints and fixed-development
learning points every5000 updates; complete47,750,007-window validation after
every186,575,722-window training epoch. Full validation is not replaced by the
small development diagnostics. The user additionally authorized an active
eight-hour overnight watch with checkpoint-preserving engineering repairs or
explicitly identified scientific restarts; this is not scheduled automation.

Exact workstation launch (refuses an existing output; use `--resume` on the
launcher only for the same verified contract and preserved identity):

```powershell
Set-Location D:\TradingML\codes\bargpt-phase1-v10-31e35f7f7
$env:PYTHONDONTWRITEBYTECODE='1'
& C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe -B -m research.bar_gpt.phase1_v10.launch --output D:\TradingML\runtimes\bar_gpt\phase1-v10-full-setup-20261117\phase1-v10-d384-l6-e768-mae16-100ep-20261117 -- --source-revision 31e35f7f7b69f2897cfdd063e33106575f224d08 --calibration D:\TradingML\runtimes\bar_gpt\phase1-v10-full-setup-20261117\calibration.json --batch-size 1024 --validation-batch-size 16384 --workers 8 --prefetch 4 --epochs 100 --learning-rate 0.001 --min-learning-rate 0.00001 --checkpoint-every 5000 --diagnostic-every 5000 --wandb-mode online
```

The actual durable controller was launched once through Win32_Process.Create,
not a scheduled trainer. The existing triggerless manual panel task was reused;
its prior XML and new wrapper are preserved in the runtime setup root.
`qualification-ledger.json`, `overnight-monitor.json`, deployment seals,
per-profile results, launch ownership receipts and W&B readback are runtime
records. Full training quality is still being measured; qualification is not
proof of forecasting usefulness or completion of100 epochs.


## Overnight intervention: numerical instability

The first full run failed after completed step61160; its step60000 checkpoint,
logs and W&B identity5rklsuk2 remain preserved. This run did not complete one
full epoch. Frozen development error had already become catastrophic at60000.
The pinned diagnostic copy SHA256 is
`f73322b926fb3e615bb1b25e73cb67151823a042aa73a4bae463a5cf9d08aa14`.
All trainer/loader owners exited before diagnosis used the GPU.

Confirmed: first-layer QKV weight norm grew to1185, feed-forward activations
reached roughly73000, and gradients could be extremely large despite modest
batch loss. FP32 evaluation reproduces essentially the same bad native outputs
as BF16; changing output precision alone does not repair the trained model.
The specific causal contribution of batch noise versus unbounded attention
logits is not yet isolated. No invalid source data or hidden-reference shortcut
was established by this evidence.

The next candidate explicitly changes attention to normalize each learned query
and key in FP32 and use scale8 dot products (bounded attention logits, without
clipping source values, targets or value vectors). The standard attention path
remains available for reproducing the first run. Gradient norm accumulation is
now FP64 so finite FP32 gradients cannot overflow the clipping norm. Before a
replacement full launch, require actual GPU qualification and a long-horizon
training-only cache stress test. This architecture change gets a new identity;
it is not a silent optimizer resume. Larger batches will be reprofiled.

The trainer now retains a rolling best-development checkpoint, preserves a
rejected nonfinite batch plus a diagnostic checkpoint, stops on catastrophic
balanced development price errors, and marks exceptional W&B exits as failures.
A diagnostic failure checkpoint is evidence, not an approved resume candidate.


Normalized-attention qualification completed65000 optimizer updates at constant
1e-3 on the training-only67606-window cache, random batches256 and fresh16-bar
masks, in616.33 seconds. No finite-gradient guard fired. The selected65000-step
checkpoint has masked/all-visible development price error1.427%/1.366%, and
activity log MAE0.7298/0.6874. The all-visible input-median control on the exact
same development windows has1.888% price error and0.7376 activity log MAE.
Worst price-band errors remain large (a singleton valid1m bid-low group accounts
for the largest error); no example was dropped. These are development findings,
not full-population validation or forecasting evidence. The control and detailed
metrics are preserved under the runtime setup root.

`--initialize-checkpoint` permits the verified training-only stability parent,
requires identical architecture and frozen transform buffers, and pins its hash,
source and training subset identity in the new full-run contract. It transfers
learned weights with a fresh optimizer by default; `--initialize-optimizer`
is the explicitly evaluated optimizer-retention ablation. The full run
receives fresh RNG/traversal and its own100 complete epochs/cosine schedule.
Initialization policy is part of its contract. This is a new scientific identity, not a
resume of the failed standard-attention run. Exact engineering resumes still
require an unchanged full-run contract and W&B identity.


The exact full-data prefix handoff ablation selected a fresh optimizer. Starting
from the identical65000-step parent, source9a6b8c0d1, batch2048, same seed and
204800 full-stream windows: retained AdamW state produced12.394% all-visible
price error at100 updates; fresh AdamW produced1.854% (initial1.366%). Activity
log MAE was0.8878 versus0.6807. Retained optimizer initialization is rejected for
this handoff. This does not prove a batch-composition defect; the optimizer-state
comparison changed only that state, and the fresh path is being checked longer.
The full replacement uses the parent's learned weights, a fresh optimizer/RNG,
and a new100-epoch full-data schedule and identity.


The longer fresh-optimizer batch ablation selected batch8192. With the same
qualified parent, seed17, eight deterministic shard lanes and exactly2,097,152
full-stream windows (1024 updates at2048 versus256 at8192), all-visible price
error was4.867% versus2.043%; activity log MAE0.6792 versus0.6624. These are
bounded-prefix development results, not a completed full epoch. Larger batches
still show some price variation; continuing full validation is required.
GPU-resident optimizer throughput at8192 is37.25K windows/s versus41.51K at2048.
The approximately10% throughput tradeoff is deliberate for the observed quality
benefit. Validation batch16384 and eight prefetched workers are retained.

Additional roundtrip qualification covered all67606 training-subset windows:
maximum positive-target relative transform/inverse error2.861e-6; every valid
zero activity value reconstructed exactly. Raw absolute floors differ by scale
and are preserved for every channel in`all-training-transform-roundtrip.json`.
No source example was clipped or removed.

## Qualified replacement full run

The replacement is `phase1-v10q-d384-e768-b8192-mae16-100ep-20261117`,
W&B https://wandb.ai/mehdifaraji/bargpt-phase1-v10-full-mae/runs/93pkdlqt.
Its immutable source is `65d57183f914f8196bff909cfd95ebf69baef86b`.
Live ownership, eight loader workers, advancing W&B counters and the step1024
checkpoint SHA256 were independently verified. Early throughput is about37K
windows/s, GPU compute utilization100%, loader waiting about0.04%; this does
not establish sustained quality improvement. Initial development price errors
are above the qualified parent. The overnight watch continues.

Run from `D:\TradingML\codes\bargpt-phase1-v10-65d57183f` on the workstation:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$root='D:\TradingML\runtimes\bar_gpt\phase1-v10-full-setup-20261117'
& C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe -B -u -m research.bar_gpt.phase1_v10.run train `
  --output "$root\phase1-v10q-d384-e768-b8192-mae16-100ep-20261117" `
  --source-revision 65d57183f914f8196bff909cfd95ebf69baef86b `
  --calibration "$root\calibration.json" --attention qknorm `
  --initialize-checkpoint "$root\qknorm-stress-65000\checkpoint_best.pt" `
  --no-initialize-optimizer --batch-size 8192 --validation-batch-size 16384 `
  --workers 8 --prefetch 4 --epochs 100 `
  --learning-rate .001 --min-learning-rate .00001 `
  --checkpoint-every 512 --diagnostic-every 512 --wandb-mode online
```

This is the original launch command, not a command to duplicate a live owner.
The parent checkpoint SHA256 is
`d99c2994effc3b689c967af1d61740e11cfb37821c5d039859759ce230683620`.
The run uses all186,575,722 training windows and47,750,007 validation windows,
including worker tails. Exact ownership and checkpoint proofs are in
`verified-full-launch-qknorm.json`; initial failed-launch receipts remain intact.
