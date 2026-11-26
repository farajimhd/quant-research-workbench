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
