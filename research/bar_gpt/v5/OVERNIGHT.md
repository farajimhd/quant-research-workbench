# Unattended v6 handoff

Performance maintenance keeps the existing model, sample order, masks,
optimizer and scheduler contracts. The derived loader-index-v1 files bind the
sealed split hash and are mmap metadata, not a new dataset. All market payload
hashes and complete-bar checks still run. Training and bounded learning probes
remain ordered. Full validation may deliver ready batches out of order: every
worker emits the same original batches/masks, all tails are retained, and the
final equal-feature sufficient-statistic reduction is unchanged. Bounded native
probes must confirm identical full target counts and reconstruction metrics.

Deploy performance changes at a validation checkpoint boundary. Native task
administration must run in an interactive scheduled task with RunLevel Highest;
the workstation's ordinary SSH token cannot disable the existing trainer task.
Do not leave a failed handoff marked as draining. Preserve the completed training
phase checkpoint, request an owned controller STOP, wait for both processes to
exit, and only then update the pinned source and resume through the normal
controller retry path. Never remove an unrelated human STOP.

Run `overnight_v1.py --output <workstation runtime directory> --source-revision <commit>`
from a committed, verified workstation snapshot. A native interactive scheduled
task owns this single job and survives laptop disconnection. It is not a recurring
calendar automation. `progress.json`, per-child ledgers/logs and `incident.json`
are the operational evidence. An output-root `STOP` requests graceful shutdown.

The controller waits for BOTH completed cohort members, matching final counters
and released controller locks. It never freezes a growing dataset. The nominal
1.5 TB allocation produces somewhat less payload after reserved overhead; each
member must reach at least 95% of its byte allocation. Early source exhaustion
is reported, not silently accepted as the intended corpus.

The completion audit checks every receipt seal, declared feature count and final
run counter. Payload size/header, first/middle/last records and full SHA256 checks
are targeted across member, timeframe, year and liquidity strata. It avoids
opening hundreds of thousands of payloads before training. The split index uses
the fixed v6 record size and sealed file byte counts; NPY headers are smaller
than one record, so integer division determines the exact row count, reconciled
against each receipt. This bounded audit does not certify every payload byte
in advance. Every file used by training is header/size/SHA256 checked and
every consumed record is validated by the loader. Missing quote evidence retains
its validity mask; missing trades fail the run. All six timeframes and all 18
market channels need real evidence in both members.

A single chronological approximately 80/20 split covers the entire cohort.
Full contexts crossing the boundary are purged, including hourly/daily windows.
Calibration is training-only. A short actual-loader large/BF16 profile compares
batch 4096 and 2048 with 32 workers and prefetch two. Keep batch 4096 if valid;
2048 is only an OOM fallback. A 128-update training/16-batch validation smoke run
must save valid resumable state before the full 50-epoch campaign begins.

Full training keeps the agreed large encoder, BF16, SDPA, batch 4096, 32 workers,
cosine 1e-3 to 5e-5, 1000-update warmup, 10-step losses and 1000-step metric uploads.
Full validation and epoch/best/last checkpoints run each epoch. W&B project:
`bargpt-v6-latest-bar-overnight`. The workstation training panel opens with each
child; the shard panel remains during extraction.

Learning review uses a fixed 32-batch validation probe before training and after
each epoch, with identical masks and membership. It stops for nonfinite evidence,
a probe regression over 50%, three epochs without 0.5% improvement, or three
validations over 2% worse than an earlier best while training loss improves over
1%. One noisy validation result or a train/validation gap alone never stops it.

For divergence/no learning, at most one bounded optimizer investigation runs:
two 128-update trials at 3e-4 and 1e-4 with identical initialization/data/masks.
A fresh full campaign starts at the better lower rate only if a trial measures
at least 0.5% probe improvement. This tests an LR-instability hypothesis; it is
not a claim that every learning failure is caused by LR. Persistent overfit keeps
the best checkpoint and stops. Unknown data/architecture failures preserve the
incident and stop rather than weakening data checks or inventing a model repair.
Any future model change must use a new encoder module.

Infrastructure failures allow two checkpoint resumes, each in a fresh output
directory. Partial training epochs replay from their last durable epoch boundary;
validation-phase checkpoints avoid repeating completed training. A 20-minute
lack of progress requests STOP, followed by termination of only the owned child
tree if it cannot drain in two minutes. Numerical failures use the bounded LR
investigation instead of repeatedly resuming poisoned state.

On Windows, repeated opens of a growing metrics journal can stall the training
thread at upload boundaries. Reporter keeps one line-buffered handle, preserving
each loss row's immediate visibility and upload cadence. A telemetry-only source
upgrade can use `run_epoch_upgrade.py` to wait for an immutable complete-epoch
checkpoint and successful learning review, drain the owned controller, then use
its ordinary checkpoint-retry path under the new committed snapshot. Existing
STOP files cancel the handoff. This changes no model/data/optimizer contract and
discards no completed epoch. A checkpoint resume keeps the original W&B ID and
display name using `wandb-identity.json`, including legacy checkpoint ancestry.
Changing scientific model/data/optimization settings starts a separate W&B run;
changing worker counts, source snapshots or output folders does not. The saved
training contract still independently controls whether a checkpoint is compatible.

`wandb_relay_v1` can consolidate accidentally separated attempt journals into the
original remote run while following the active attempt without restarting it.
Original journals and attempt runs remain intact; exact metric payload hashes
deduplicate overlap and remote migration acknowledgments make retries idempotent.
A writer lease prevents the relay and a future resumed reporter from uploading
concurrently. A resumed reporter requests relay handoff and waits for its uploads
to drain; this marker never stops or controls a trainer.

The relay retries transient Windows status-file sharing/replacement failures.
Unread journals retain their byte offsets and are retried on the next pass;
unavailable status is reported explicitly and never interpreted as trainer exit.
Its progress includes the last submitted global step and source-read errors.
The native relay task should also allow bounded restart after a genuine process
failure, independently of the trainer task.
