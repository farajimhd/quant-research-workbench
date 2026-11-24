# Phase1 V27: residual temporal mixer on frozen Phase0 V7

This experiment reconstructs the same 64 bars using sequence context. It is not
a forecast, and the 64x32 output is not a single compressed window summary.

## Information path

The pinned Phase0 V7 encoder independently transforms each bar to 32 values.
Four trainable residual mixer blocks alternate a temporal 64→128→64 MLP and
a feature 32→128→32 MLP, with pre-layer normalization and GELU. Final branch
projections start at zero, so initialization reproduces the pinned parent exactly.
The frozen, shared Phase0 affine 32→18 decoder consumes only the exported 64x32
contextual embeddings. No original embedding, raw value, local reference, or
target statistic is passed around this representation to the decoder.

Only mixer parameters receive optimizer updates. Encoder inference uses no-grad;
decoder inference remains differentiable with respect to its input. The Phase0
parameters and training-only calibration buffers are frozen and tested unchanged.
Temporal dense weights distinguish chronological positions and connect every bar.

## Scientific contract

- All 64 bars visible; no masking in this first temporal-context experiment.
- The same certified full Phase1 split and frozen development sample as Phase0.
- The same 0.05–100000 adjusted-price eligibility, original source validity, and
  trade/bid/ask price and activity transforms. No clipping or shard modification.
- The same equal-family SmoothL1 objective with price factor 1000 and 0.1
  coordinate auxiliary loss. Smoothed average-volume ratio is supervised;
  reconstructed trade count remains diagnostic only.
- FP32 throughout. Profiling may vary loader settings and batch size; model
  capacity and objective do not change to achieve the 150K windows/s target.
- Twenty epochs, cosine LR 1e-4→1e-6 by consumed windows; initial optimizer is
  fresh. Parent training cursor and LR are not resumed into a new experiment.

## Operation

Use workstation `python -B -m research.bar_gpt.phase1_v27.launch` with
`--console-log <existing-runtime-dir>/console.log --output <new-runtime-run>
--source-revision <deployed-commit> --phase0-checkpoint <pinned-parent.pt>
--calibration <parent-calibration.json> --epochs 20 --learning-rate 1e-4
--min-learning-rate 1e-6 --batch-size 4096 --workers 8`.

The launcher sets PYTHONDONTWRITEBYTECODE and checks immutable deployment hashes,
parent proof, split/calibration identity, STOP markers and workstation GPU owners.
The dataset defaults to the same Phase1 authority used by Phase0. The standalone
`profile` module accepts the same arguments and requires a fresh runtime output.
It verifies initial identity, frozen weights, tiny overfit, eager/graph gradients,
all development metrics including tail batches, and measured loader throughput.

W&B project: `mehdifaraji/bargpt-phase1-v27-mixer32`. Checkpoints remain local;
only bounded initial/last/best checkpoints are retained. Resume with `--resume
<same-run>/checkpoint_last.pt` and the unchanged scientific contract. Preserve
human STOP files; only explicit operator authorization permits their archival.

The `panel --run <runtime-run> --keep-open --maximize` viewer retains independent
training-sample, development and complete full-validation tables, price bands,
directions, range and activity quality, speed/ETA, and frozen baseline loss.
Missing full-validation metrics remain pending until that complete pass finishes.
For comparison, the zero-mixer initial development report is the pinned parent
baseline on identical rows. Improvement alone does not prove forecasting utility.
