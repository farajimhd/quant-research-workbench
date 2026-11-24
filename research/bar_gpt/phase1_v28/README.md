# Phase1 V28: masked reconstruction, encoder fine-tuning and structural supervision

Warm-start from the gracefully stopped V27 checkpoint. Keep the original 64x32
representation and four residual mixer blocks. Unfreeze the Phase0 stem, residual
blocks and 32D readout; keep its 32→18 decoder and calibration frozen. Gradients
pass through the decoder. No targets, references or original embeddings bypass
the exported representation.

## Masking and losses

Each training window independently hides a uniformly chosen 4–8 of its 64 bar
positions. Replace those 32D embeddings with one learned 32D mask token BEFORE
temporal mixing. Bars stay in their original chronological positions. Phase0
encodes bars independently, so hidden values cannot enter visible embeddings.
No target-derived mask, normalization or reference enters the decoder.

The objective is 0.5 hidden reconstruction + 0.5 visible reconstruction, plus
0.05 each of direction, range and OHLC penalties. Reconstruction retains V7
SmoothL1 and average-volume supervision; trade count remains diagnostic only.
This is a masked autoencoder, not pure L1/mean-absolute-error optimization.

Direction loss averages candle open/close, adjacent closes and adjacent activity
changes (including smoothed average volume, excluding derived trade count).
Down/flat/up classes are balanced. A differentiable target-sized hinge is zero
at exact reconstruction; label-dependent margins are loss weights only. Price
log-delta scale is 0.001, activity log1p-delta scale 0.1, with a 1%-of-scale
denominator floor for tiny moves. Flat labels penalize nonzero predicted changes.
Range loss compares log(high/low), scaled by 0.001. OHLC penalties use smooth
positive violations of H>=O/C and L<=O/C and H>=L. Only the structural penalty
exempts source-inconsistent candles, with counts reported; their reconstruction
targets remain intact. No source clipping or projection is introduced.

## Continuation and reporting

Use a new experiment identity because trainable parameters, masking and objective
changed. Preserve parent weights and the exact data cursor, batch4096, workers16
and seed17. Continue the REMAINING part of the original 20 epochs. Start fresh
AdamW state. Decay mixer/mask-token LR 1e-4→1e-6 over remaining windows; encoder
LR is one tenth of that. Exact V28 resumes preserve optimizer and random-mask RNG.
Graph warmup/capture and evaluation do not consume the training mask RNG stream.

All-visible development/full-validation and hidden-only masked development are
reported separately. Masked development uses fixed seed1701 and includes every
source-valid hidden target; adjacent metrics require both endpoints hidden.
Main table/selection loss remains comparable reconstruction loss. `objective/*`
logs added losses; training total loss includes them and cannot be compared
directly with the table loss. The panel shows parent baseline, both learning
rates and masked development loss, plus the three existing population tables.

W&B project: `mehdifaraji/bargpt-phase1-v28-masked-finetune32`. No checkpoints
are uploaded. Use `python -B -m research.bar_gpt.phase1_v28.launch --console-log
<existing-runtime>/console.log --output <new-runtime-run> --source-revision
<hash> --parent-checkpoint <pinned-v27.pt> --calibration <v7-calibration.json>`.
Defaults preserve the parent traversal. Runtime source/data admission, STOP
handling and last/best/initial verified checkpoints remain in force.

`profile` accepts the same flags and a fresh runtime directory. It checks source
identity, tiny overfit, decoder immutability, encoder updates, eager/graph
gradients, full development metric parity and masked evaluation RNG preservation,
then measures FP32 throughput for prefetch/chunk choices without changing batch
or worker count. Full source data and split membership remain immutable.
