# Phase2 V3: V11 embedding-only forecasting pretraining

Approved November19,2026. Freeze the completed Phase1 V11 epoch25 LAST checkpoint
(256-dimensional export, four width128 attention layers) and its exact calibration.
No continuously changing teacher or phase1 reconstruction decoder enters training.

Use the sealed all-published Phase2 certificate, original2019–2025 train/2026
validation split and every eligible origin. Context is8×1s,4×10s,2×1m,1×1h,1×daily
windows of64 actual bars. Targets are the next64 OBSERVED1s bars inside the same
source block, not64 wall seconds. Original masks, split basis, receipts and causal
indices are preserved. This historically reused validation set is not a pristine
external test of all previous representation selection.

The frozen tokenizer uses full64-bar visibility and exactly V11 log/log1p,
source validity, UTC fields and visible-median transformation. Fixed endpoint
tiles preserve cache-miss numerical geometry. No future target embedding is made.
The shared loader starts directly at the saved publication/block/origin cursor;
it does not replay the preceding epoch on resume. CPU I/O and CUDA publication
preparation remain bounded, ordered and prefetched. Legacy cache admission is
conservative (reserves512 dimensions); actual populated V11 banks are256-wide.

Phase2 input is16×256 with explicit timeframe, position and causal start/end ages.
Two pre-LN attention layers: width256,4heads,FF1024. Latest1s content has a residual
path into the learned256-dimensional export. No phase2 feature/token masking.
The decoder receives ONLY that export: a learned18-coordinate anchor plus a
256→256→1152 horizon residual head. Its initial scale rows come from the frozen
phase1 learned reference head; they remain trainable, not copied raw references.
All future absolute levels are predicted. FP32 structured readout converts
s/d/u/l/activity codes to log-market values, enforces OHLC/paired quotes and
nonnegative activities, then FP64 inversion produces all18 raw-unit forecasts.
Actual future references, clocks, source masks and bars are labels/evaluation
evidence only, never prediction inputs. Forecasting can use the optional decoder;
downstream representation users may discard it and retain the context encoder.

Objective: source-valid SmoothL1 with1000×log-price errors (delta1=10basis points)
and log1p activity errors, equal price/activity family means. No clipping of valid
targets. Validation reports the window-weighted mean of per-batch objective,
native errors/counts/variance/structure, price bands/quantiles and matched-validity
last-bar, historical mean/median and training-mean controls. These do not establish
trading profitability. The compatibility train loop uses actual `loss`, never
mislabels the robust objective as MSE.

One full dataset pass initially. Defaults: AdamW1e-4→1e-5,1000-update warmup,
full-validation cadence10000updates, rolling checkpoint1000updates. The inherited
full-pass cosine advances at validation boundaries by certified block progress;
validation does not advance the pass. Patience24 is a fail-safe. Qualify batch,
workers and caching on the workstation before launch; no throughput extrapolation
from cached compute alone. W&B is mandatory online in `mehdifaraji/bargpt-phase2`.
Run manifests pin all source/data/teacher/configuration identities. Same-run
resume requires exact contract and restores optimizer/scheduler/RNG/source cursor.

Entry: `python -B -m research.bar_gpt.phase2_v3.run_train --mode profile|train`
with explicit `--output`, `--source-revision`, `--dataset-release`,
`--teacher-checkpoint`, `--teacher-sha256`, `--phase1-calibration`, CUDA/BF16 and
resource settings. Train additionally requires `--execute`. Exact generated
commands are retained under the runtime root. Use `panel --output RUN` to monitor.
`context-encoder.pt` and optional `forecast-decoder.pt` export the final durable
state, bind the phase1 teacher SHA and include configuration and provenance.
