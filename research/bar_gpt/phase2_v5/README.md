# Phase 2 V5

V29's frozen encoder embeds the last 64 completed bars for each of five
timeframes. The residual forecaster exports a 128-dimensional state. A packed
linear projection supplies 19 32-dimensional bar vectors to the exact frozen
V29 decoder, using the pinned checkpoint's coordinate calibration. Targets are
10 observed 1s, six observed 10s, and one observed 1m, 1h, and daily bar.
Trade-count output is recovered indirectly through the V29 average-volume
coordinate. The loss balances timeframes and preserves source validity.

Labels use intervals starting at or after the observed 1s decision close.
Intraday labels stay within the session. Daily labels may use the next observed
daily candle in a later certified publication in the same root and split when
the overlapping daily anchor and share basis agree. Missing labels are masked.
This is a conservative subset of possible cross-publication daily targets.
The source stores only bar close clocks. Its daily interval is 09:30–16:00 NY.

The reader uploads the pools once per session, encodes unique context endpoints
in tiles and gathers GPU-native batches. Seeded session traversal and
within-session order can be reproduced from the checkpoint cursor. A full run
covers one complete release pass and ends with full certified validation.
Frozen V29 fine-tuning is deferred until forecast evidence supports that change.

Use python -B -m research.bar_gpt.phase2_v5.launch with --console-log,
--output, --source-revision, --release, --teacher and --calibration. Add
--profile-sessions to benchmark a bounded run. All artifacts and checkpoints
live under the workstation runtime root.

With --session-lanes 2 the loader prepares upcoming frozen V29 encodings on
two CUDA streams. The optimizer consumes sessions in certified traversal order.
--graph-updates replays the existing FP32 forecast forward and backward at
4,096 and 8,192 origins. Other batch sizes use eager execution. Gradient
clipping, the finite check, the per-update cosine rate and AdamW stay outside
the graphs. To adopt this execution path from acefd3256, supply --resume-serial
on the first resume only. Later resumes use the deployed source revision and
ordinary --resume. The ten-pass schedule completes full validation each pass.

## Unique-origin release

Run python -B -m research.bar_gpt.phase2_v5.migrate_unique with --release,
--output, --source-revision and --workers. A separate --pilot output exercises
real sessions and estimates storage before the full copy. Only the full run
publishes a certificate. The journal commits bounded batches of sessions.
Restart with the same arguments and source revision to use the journal.
A STOP file in the migration output pauses between batches. Use
migrate_panel --run OUTPUT to see progress.

The unit is the complete ticker session, with a variable number of sorted
origins. Five shared losslessly compressed pools preserve values, validity
bits and timestamps. The converter rejects inconsistent price bases,
conflicting shared bars and any insertion into an original 64-bar context.
Each payload records checksummed arrays and a digest of its source receipts.
The migration accounts for every certified block occurrence before publishing.

A trimmed source pool can shift a label to a later candle. The new release
labels from the union of certified pools under the existing interval start rule
and preserves the original daily successor basis checks. The catalog records
source target shifts and newly filled slots by timeframe. Rows with no target
remain in the index and are counted separately by the loader.

Use --unique-release PATH/certificate.json in place of --release for a new
training identity. The prior optimizer cursor represents stored occurrences.
The new loader shuffles sessions and encodes each distinct V29 context once
within a session. The pass checks both visited and trained eligible counts.
# Deduplicated continuation

The new unique-release run starts from the original completed pass-one checkpoint,
keeping the model, AdamW moments, optimizer update count and RNG state. Its own
epoch and origin counters restart. Each epoch walks the certified training split
once, counts rows without any valid forecast target separately and keeps
validation isolated. The learning rate follows cosine decay by origin traversal
from `1e-4 * 0.9 ** epoch` to `5e-7` for epochs 1 through 20.

Run `python -B -u -m research.bar_gpt.phase2_v5.continue_unique --help` for the
workstation launcher. It profiles 512 already-verified training sessions and 48
validation sessions in a profiling-only catalog, compares loader and CUDA stream
settings on identical batches, and waits for the full migration certificate
before starting the new W&B run. It records the selected settings and final
certificate in the control directory. A `STOP` file in that directory cancels
the pending full launch. Once training starts, use the STOP file in the training
directory and resume its own `checkpoint_last.pt` using the recorded command
with `--resume` replacing `--from-completed-pass`.

Short chronological session clusters are shuffled each epoch. Origins within
each session remain shuffled for optimizer updates. Within-session V29
embeddings are keyed by completed endpoints, while the optional bounded GPU
cache reuses 1h and daily embeddings across sessions only when all 64 source
records match. Faster timeframes continue to use the session-local banks.
# Forecast objective comparison

`python -B -u -m research.bar_gpt.phase2_v5.compare_objectives --help` launches
matched trials from a stopped unique-run checkpoint, then starts a separate
W&B training run with the winning objective. It preserves the position in
the original epoch, optimizer moments, and scheduler. Each trial compares the
same training batches and full validation origins. The decision favors
equal-timeframe balanced trade-close direction relative to the last completed
candle, with persistence-normalized MAE and a worst-frame/stream MAE guard.
Individual trial speed, per-horizon metrics and the decision are retained in
the workstation runtime.

Weights are specified as `price,activity,coordinate,direction,range,ohlc,from_last`.
The direction term covers candle bodies and adjacent predicted close and
activity changes. The final term compares predicted and realized changes to
the same last completed candle of each timeframe, with separate close,
activity, and log-range directions. That candle's source validity controls the
loss mask. Anchor values are never passed to the forecast model or decoder.

`--anchored-direction` puts candle/path and last-completed-candle direction
under one coefficient, using equal weights between the two. For example,
`--loss-weights .65,.35,.1,.1,.1,.1,0 --anchored-direction` adds direction,
range and OHLC at 0.10 each. `--graph-tail-bucket 1024` or `2048` with
`--graph-updates` lets variable-size training batches use CUDA graph replay.
Padding rows are excluded by all validity masks and each original batch still
receives one optimizer update with the same real origins.
