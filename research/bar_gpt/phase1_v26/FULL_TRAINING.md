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
An injected native Windows launcher exception also retained its traceback and
printed an atexit sentinel successfully after `main()` returned.

Verified deployment `f8f18c191103ea9e209257d477bf34e2178e3043` (1642 files).
Supervisor6652 launched trainer11120, creation1795407293.1812732, ownerMehdi.
Original58000 checkpoint and proof were copied to
`checkpoint_before_wandb_repair_58000.pt` and hash-verified before launch.
Loader restoration took318.17s. Replayed step58725 reproduced the original
loss11.455550139718019 exactly. Training then passed the old failure point;
step59125 measured8801windows/s,98% GPU utilization. Development validation59000
completed in4.24s (1s close MAE0.183109,hidden48 MAE0.181722 dollars).
Checkpoint59000 SHA256:
`7860aaa73618a113e3248ab299811fe808587a2214dc78ef3505a4ef172699a6`.
Remote W&B readback confirmed the same run advancing beyond the old failure
point with the new engineering revision. Rich panel6204 was restarted.
Runtime `wandb-repair-20261122.json` retains recovery and live verification.

## User-directed uniform epoch cosine, 2026-11-22

The user requested stopping the conservative-LR run and applying cosine1e-4
to1e-6 to all groups, with20 updates over20 epochs. Interpretation:20 distinct
epoch-held LR values, not20 optimizer updates. Epoch index0 uses1e-4; index19
uses1e-6, with `floor + .5*(peak-floor)*(1+cos(pi*epoch/19))` between them.
No warmup or per-group multipliers remain in this mode.

The prior run stopped cleanly at66079; checkpoint SHA256
`b5ed5440f4e4ec6406d382d46512b70e78053d68bce051cef66fb9aecb149d4a`.
Supervisor confirmed trainer/workers exited and GPU idle; its STOP remains.
This is a new scientific identity, linked through `--fork-from`. Weights,
Adam moments, RNG, global step and exact data cursor continue from the checkpoint.
The original20-epoch data horizon remains: the rest of epoch1 uses1e-4, with
subsequent changes at epoch boundaries. No dataset prefix is retrained merely
to reset epoch numbering. Best/reference metrics reset to the fork checkpoint.
Subsequent engineering resumes must retain `--fork-from` as well as `--resume`
so the scientific contract and ancestor hash remain identical.

CLI additions: `--schedule epoch_cosine --lr-peak 1e-4 --lr-floor 1e-6
--fork-from D:\TradingML\runtimes\bar_gpt\phase1-v26-full20-20261122\checkpoint_lr_fork_66079.pt`.
Use a NEW output directory for the first fork; model/data/masks, lightweight
decoder, throughput settings and W&B checkpoint-upload prohibition are unchanged.
Nine focused tests passed, including both cosine endpoints,20 distinct values,
epoch-based restoration, existing model tests and failure recovery.

Native continuation: `phase1-v26-full20-epochcos-20261122-r1`, source
`5724804ee68511128ee92f1fa12f1c8510e26934`; W&B
https://wandb.ai/mehdifaraji/bargpt-phase01-v26-full20/runs/nkuoy2g8 .
Supervisor15696, trainer8752 creation1795409013.9884884. Initial native
checkpoint verification confirmed identical weights, Adam state, RNG and cursor
to the preserved ancestor, and LR/peak1e-4 in price_bar,price_core,price_readout
and activity groups. Evidence: `fork-verification.json` in the new runtime.
The initial fork development reference was1s close MAE0.165258 and hidden48
MAE0.172384; these precede any higher-LR updates and are not improvement claims.

The first launch attempt (without-r1) failed before training because the
supervisor created the fresh output directory before trainer admission. Its
records remain preserved. The supervisor now publishes initial ownership in
the runtime parent until the trainer creates its directory; ten focused tests
passed, including this fresh-directory regression.

The user's overnight monitoring request remains active for this continuation.
An in-session observer checks health every30s and journals snapshots every5min
under `overnight-monitor-20261122.jsonl`, through2026-11-23T16:34:33Z. It exits
with an alert for failures, stale heartbeat, or stalled counters. Agent review
of learning and recovery remains in the active task; no scheduled automation
was created. The old-run observer exited when its trainer stopped.

### Overnight quality stop and bounded diagnosis

The uniform epoch-cosine continuation stopped at step67000 after921 new
updates: the existing emergency worst-group MAE guard fired. This was a
learning regression, not a process failure. Supervisor recorded exit0 and no
remaining owned children. The observer exited on `quality_attention`;
it is not evidence of a still-running full training job.

All-visible 1s trade-close MAE rose from0.165258 to5.495402 dollars; hidden48
MAE rose from0.172384 to5.507173. Across timeframe/channel groups the price
MAE geometric ratio was11.5682 and activity6.10387 relative to the fork.
The parent66079 checkpoint remains pinned; the stopped67000 checkpoint and
`quality-alert.json` remain intact. The failed schedule is not automatically
restarted by the engineering-failure supervisor.

`optimizer_probe.py` compares retained versus fresh Adam state using the same
parent weights, seed73391, sampled windows/masks, unchanged architecture/loss,
batch1024 and uniform1e-4 for every group. These are cached training-subset
diagnostics, not full-stream replications or a replacement training schedule.
Each arm has a900-second deadline and writes metrics/configuration to W&B;
no checkpoints are uploaded or promoted.

In `phase1-v26-epochcos-optimizer-diagnosis-20261122`, both1024-step arms failed
retention gates. Retained Adam: close MAE0.868854, price ratio3.14292,
activity ratio1.27868. Fresh Adam: close MAE1.046085, price ratio3.79194,
activity ratio2.54456. W&B identities are6le0rqas andiugl86fo respectively.
Resetting Adam alone therefore did not establish a safe recovery.

The longer matched test is `phase1-v26-epochcos-optimizer-long-20261122`,
4096 updates per arm, source713a1b2b81afe51998d7aa8a1ee7309ca7960134.
Retained Adam ended at close MAE2.555194, price ratio5.41737 and activity
ratio1.73733, again failing retention. Its W&B identity isqjm73zx1.
Fresh Adam ended at close MAE1.423800, price ratio3.60515 and activity
ratio1.40750, also failing retention (W&Bcbim99dd). Both arms completed in
under490seconds; remote W&B readback confirmed finished state at4096.
Lower uniform rates are explicitly diagnostic controls; the user-requested
full-run schedule remains paused and unchanged pending a supported decision.

Three2048-update retained-Adam controls completed on the same cached data and
seed, source50691d904a4fcc2f4d861aed0632c25bd3aa653b:

| Uniform LR | 1s close MAE ($) | Price MAE ratio | Activity MAE ratio | Candle / range / activity direction (%) | W&B |
| --- | ---: | ---: | ---: | --- | --- |
| 1e-6 | 0.185837 | 1.02457 | 0.96680 | 90.55 / 38.94 / 68.37 | r00ivgb6 |
| 3e-6 | 0.351715 | 1.42639 | 0.97633 | 90.55 / 38.92 / 68.38 | b19q7usp |
| 1e-5 | 0.869753 | 2.49413 | 1.00615 | 90.66 / 38.88 / 69.17 | kq471rit |

Ratios are geometric means of all-visible timeframe/channel raw MAE ratios
to the preserved66079 checkpoint, not ratios of aggregate dollar MAE. All
three final evaluations failed the existing gates. At1e-6 the aggregate
ratios were close to baseline, but worst price group was1.25046 and three
price-band medians exceeded their limits. Some intermediate evaluations
passed; those did not establish sustained improvement. No gate was relaxed.

Control runtimes are `phase1-v26-epochcos-lr{1e6,3e6,1e5}-diagnosis-20261122`.
Each contains reference, manifest, learning points, gates and completion
receipt. The initial reference close MAE was0.165258. These short controls
support rejecting the abrupt uniform1e-4 continuation; they do not establish
an optimal LR or prove long-run convergence impossible. Adam reset alone did
not fix it. The source objective includes basis-point-scaled price shape
terms and an inherited latent anchor; their interaction is a possible cause,
not a confirmed defect from this experiment.

Operational decision: preserve both full-run checkpoints and leave the full
run stopped. Reverting to the earlier group-specific rates would change the
user's explicit uniform schedule and has not been done automatically. The
finite diagnostic processes exited; no scheduled monitoring automation was
created. Recovery tests were rerun locally:5 passed. W&B stores metrics and
configuration only; the diagnostic runs uploaded zero artifacts.
