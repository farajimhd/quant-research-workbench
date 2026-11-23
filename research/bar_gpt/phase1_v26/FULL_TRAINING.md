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

## Operator optimization qualification, 2026-11-22

User requested a substantial speedup relative to historical100K/s runs. The
current trainer was gracefully checkpointed at17160 (SHA256
`bf85596f14f9ccd4d68ea6edbbc17fc3d3cc49213bae6b9b67dd4ebbd55be042`), and its
exact process and workers exited before isolated GPU measurements. The pause
marker was retained under an operator-specific name for the subsequent resume.

Native operator traces under `phase1-v26-kernel-profile-20261122` showed about
88ms per16-visible update and194ms per64-visible update (batch1024). Small-head
feature attention, FP32 stable temporal attention, and activity attention were
major costs; loader waiting was negligible. The272-wide/eight-head activity
branch has34-value heads and uses a math fallback. Installed Torch2.12.0+cu132
reports `is_flash_attention_available()==False` despite the enabled flag;
Triton is not installed. GPU utilization is not proof of optimal kernel use.

Matched graph-only measurements, including preparation/teacher/backward but
excluding optimizer/loader, used the live checkpoint and unchanged masks:

| Candidate | Weighted windows/s | Gain versus paired baseline | Decision |
| --- | ---: | ---: | --- |
| Original | 8,781 | — | Retained |
| Explicit tiny feature attention | 9,886 | 12.6% | Failed strict gradient parity |
| Zero-pad unaligned heads, preserve original scale | 9,385 | 6.9% | Failed gradient and directional qualification |
| Combined tiny + padding | 10,741 | 22.3% | Rejected after matched trajectory |
| FP32-only tiny feature attention | 9,383 | 6.7% | Rejected after matched trajectory |
| Dense FP32 feature/stable temporal attention | 9,716 | 10.5% | Failed strict gradient parity |

The first three variants are recorded in `phase1-v26-attention-profile-20261122`;
FP32 variants in `phase1-v26-attention-fp32-profile-20261122` (paired baseline8794/s).
No architecture, parameter, precision setting, mask, or objective was changed.
Zero padding uses the ORIGINAL head-dimension scale, not the padded scale.

Because rounding differences failed elementwise gradient tolerance, a separate
behavioral qualification was run; this did not erase those failures. Identical
checkpoint/Adam state,128 matched1024-window updates and fixed development
validation were compared before/after. Acceptance required all raw-MAE and
relative-quantile ratios within2%, every directional/accuracy difference within
0.1 percentage points, and no nonfinite regression. Teacher anchors were shared
baseline outputs in this trajectory test; raw-pipeline gradient/speed tests
separately included candidate teacher execution. These are bounded development
checks, not a claim of long-horizon equivalence.

`phase1-v26-kernel-trajectory-20261122`: combined candidate's pre-update daily
trade-volume hidden48 balanced direction fell44.615%→26.392% (18.223pp);
padding alone changed a directional group by17.194pp. Both failed. The FP32-only
trajectory (`phase1-v26-fp32-trajectory-20261122`) preserved raw errors closely
before updates, but changed a directional group by1.064pp before and4.188pp
after; worst relative-quantile ratio after updates was1.02060. It also failed.
Small hidden-group counts and near-zero prediction changes can amplify these
discrete metrics; the recorded differences must not be treated as macro scores.

No faster kernel was promoted. Experimental kernels are opt-in probe utilities,
not installed by the full-training launcher. Resume retains original source
`0f0130792`, scientific contract, optimizer/scheduler/RNG/traversal, and W&B run.
The completed measurements establish modest unqualified kernel gains, not a
safe substantial improvement or a route to100K/s. Future work could evaluate
a FlashAttention-capable environment or hardware-aligned architecture, with
fresh numerical/quality qualification; neither was silently substituted here.

Reproduce from a pushed, verified snapshot with `kernel_probe`,
`attention_probe`, and `trajectory_probe`, each taking `--source-revision SHA`,
`--output D:\TradingML\runtimes\bar_gpt\UNIQUE_RUN` and `--checkpoint PATH`.
Run only with the training owner gracefully stopped. Detailed JSON metrics and
Chrome traces remain in the runtime directories; no checkpoints were uploaded.
Comparison W&B run (separate from the active trainer so concurrent summary
updates cannot overwrite its evidence):
https://wandb.ai/mehdifaraji/bargpt-phase01-v26-full20/runs/k26p17160 .
All five disposable GPU profiling/trajectory processes were verified exited.
The original trainer resumed under PID26728, creation1795392386.496, owned by
DESKTOP-SAAI85T\Mehdi. CPU traversal restoration took82.3s; after graph warmup,
step17925 reported8797windows/s and98% GPU utilization. No optimization gain
is claimed for that observation. The workstation SSH Rich panel was restarted.

## W&B transport recovery, 2026-11-22

Trainer26728 exited near step58725 after Windows error64 in W&B's local async
service-client socket. The W&B internal log recorded HTTP200 uploads immediately
before the disconnect. It does not establish why the local service connection
was reset; no numerical/model failure is evidenced. Failure cleanup then raised
MailboxClosedError, and closing the launcher stream hid the original exception.
Checkpoint58000 remained valid (SHA256
`5e7d356e91159dc5afae46c0de191b456bbe63b32e8341c87bd0d744332b2d3c`).

V26 now wraps ONLY recognized logging-transport failures, after appending local
metrics. The trainer checkpoints current model/optimizer/scheduler/RNG/cursor
on that exception, saves the chained traceback, closes local reporting and
exits75 without invoking the broken W&B shutdown hooks. Other exceptions retain
their original traceback; cleanup errors are recorded separately. Standard
log streams remain open through interpreter shutdown.

`full_launch --supervise` runs a finite parent process, not a scheduled task.
It permits at most three retries,30s apart, only for exit75 plus a child-owned
receipt matching the checkpoint proof. It verifies child/observed descendant
exit before retrying; remaining workers or changed receipts fail closed.
Human STOP markers prevent launch/retry. Each retry uses the same run directory,
W&B identity and exact checkpoint state. Model, objective, masks, LR, batching,
precision and kernels are unchanged. Initial recovery from the historical
failure replays work after58000; those previously logged steps remain in history.

Validation: eight focused tests passed, including injected transport failures,
local-journal preservation, nontransport rejection, bounded supervisor retries,
STOP handling and exact serialized optimizer/RNG next-update recovery.
