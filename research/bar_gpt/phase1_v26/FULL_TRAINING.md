# V26 full-population20-epoch continuation

The user's2026-11-22 instruction authorizes full training despite the documented
cheap-price and hidden-direction weaknesses. This is a new full-data identity,
initialized from the selected e03/update2048 checkpoint. It is not a resume of
the bounded campaign's optimizer or scheduler. Model, transforms, family loss,
teacher anchor, independent clipping and masking are unchanged.

Authority: corrected `phase1-v6-cohort-balanced-v4/split.json`, hash
`d60f707c91c54b72ec7a11a9119334cc973351be699c8b6d5f3d724957640d63`.
Every epoch traverses186575722 training windows once, retaining worker tails;
47750007 validation windows are evaluated after each epoch. No random subset
substitutes for full training. Frequent fixed development evaluations are
additional and report exact price bands, including sub-$0.10 median error.
Full streaming evaluation reports raw-unit errors and candle/range/activity
direction for all-visible,8-hidden and48-hidden conditions, without retaining
full-population predictions. Exact quantiles/structure diagnostics remain on
the fixed development set; no full-population quantile claim is made.

Peak price readout LR3e-7, core6e-8, bar9e-9; activity1e-6. Warm up from1% of
each peak for500 updates, then cosine decay to1% over20 full training epochs,
using consumed windows rather than epochs rounded to batches. Fresh AdamW,
zero weight decay, fused CUDA optimizer, BF16 temporal/activity attention; FP32 price-feature attention, teacher and heads. Calibration
is frozen from training only. No local true reference enters the decoder.

`run_full --mode profile` compares full raw-loader/preparation/teacher/model/
backward/optimizer throughput, worker counts and batch sizes; graph losses and
all gradients are checked against eager execution, including visible-only
teacher parity. Validation is profiled with the actual loader and metric path.
Training uses ordered CPU worker/shard prefetch and reusable double-buffered
pinned staging with asynchronous H2D. Increasing GPU memory usage alone is not
the optimization target. Profile-selected batch and worker count are frozen
in the resume contract. Existing strict market validation remains enabled.

Resumes preserve model, Adam moments, consumed-window scheduler, epoch/cursor,
global RNG and mask RNG through the global Torch state. Current ordered loader
replays/skips its consumed CPU prefix to preserve traversal; resuming deep in
an epoch can consequently take time. This is exposed as restoring traversal,
not falsely reported as active training. Prefix skipping is not yet optimized.

Bounded rolling last/best checkpoints plus final checkpoint; no per-step archives.
Best promotion uses the V26 development retention rules; routine deviations
are logged, not silently discarded. Nonfinite updates abort without replacing
the last valid checkpoint. Human STOP markers are preserved. A greater-than10x
worst-group raw-MAE regression checkpoints and stops for attention. Lesser
regressions remain visible during the explicitly authorized20-epoch trial.

Example from a pushed, hash-verified workstation snapshot:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe -B -u -m research.bar_gpt.phase1_v26.run_full --mode profile --output D:\TradingML\runtimes\bar_gpt\phase1-v26-full-profile-20261122 --source-revision <sha>
# Use the measured batch/workers in the training command:
& C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe -B -u -m research.bar_gpt.phase1_v26.run_full --mode train --epochs 20 --output D:\TradingML\runtimes\bar_gpt\phase1-v26-full20-20261122 --source-revision <sha> --batch-size 1024 --workers 8 --validation-batch-size 512 --validation-workers 8
```

W&B project:
`mehdifaraji/bargpt-phase01-v26-full20`. Metrics and configuration are uploaded,
never weights. `full_panel --output <run>` is a read-only Rich progress view.

Memory admission is capped at85% of physical VRAM to fail before Windows GPU
oversubscription. The4096-batch first profile filled96494MiB and was stopped
through its preserved STOP marker;1024/2048 gave equal speed. TF32 high math is
only eligible after raw errors, directional metrics and gradient parity pass
`precision_probe`; its selection is recorded in the immutable contract.

Initial measured pipeline:512/1024/2048 batches gave approximately8.64-8.67K
windows/s;16 workers did not improve throughput and doubled startup. High
TF32 math was rejected: clipped-gradient relative differences34.4%/3.82%,
despite small directional-metric changes. Highest precision is retained.
`full_launch` directs stdout/stderr to runtime logs and can be launched via
Win32_Process.Create without a scheduled task, independent of the SSH session.

## Verified full-run launch, 2026-11-22

Run `phase1-v26-full20-20261122` is active on the workstation from immutable
snapshot `0f0130792d542a8a4c490f4613ae02423d435a1b`. W&B:
https://wandb.ai/mehdifaraji/bargpt-phase01-v26-full20/runs/spou69fc .
The native eight-update canary saved a complete checkpoint and resumed under
the same identity. A detached Windows launch initially failed on invalid
standard handles before training; `full_launch` now supplies valid inherited
stdin/stdout/stderr handles. The checkpoint was preserved across that repair.

Selected training batch1024/workers8 and validation batch512/workers8, each
with prefetch4. Workers16/32 did not improve speed and increased startup.
Live training measured approximately8.55-8.87K windows/s,98% GPU utilization,
30GiB physical GPU memory and0.0065s loader wait per25 updates. These are
observations, not a guarantee of sustained whole-epoch throughput. Estimated
20-epoch duration including full validation is roughly6-7 days.

The first periodic development evaluation at step1000 completed in3.20s:
all-visible1s trade-close MAE0.192761 versus initial0.194202 dollars;
hidden48 MAE0.180611 versus0.180979;1s candle direction86.193%;1s moving-volume
direction99.845%;daily bid range accuracy78.344%. Sub-$0.10 median relative
error remains213.512%, so this does not establish robust scale recovery.
These are fixed-development results, not completed full-population validation.
Checkpoint1000 proof has SHA256
`df49e5b950355c5bc47ae17eeae5dd811212a236c4bdf5bb0c1da53a4d26c85f`.
Remote W&B readback confirmed running state, training counters at1975,
development metrics and no uploaded weight files. The Rich panel is running
through a workstation SSH terminal; a physical desktop window was not verified.
