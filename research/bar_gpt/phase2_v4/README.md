# Phase2 V4: identical Phase1 decoder heads for future bars

Training now defaults to a loader-integrated next-candle census. The training
session reads its already-verified `reader.views[0]` CPU array, computes tiny
int8 class/year/liquidity tags, and carries those as CPU-only target metadata.
The model and loss never receive the metadata. Packed batches preserve row
alignment, including batches crossing publication boundaries. Counts commit
only after successful optimizer updates, and are saved in the same verified
checkpoint as the optimizer/traversal/RNG. Unconsumed prefetch cannot alter them.
`loader-census.json` mirrors durable counts; `training_census/` W&B metrics are
logged with validation. Validation loading does not compute these extra tags.
The scope is consumed exposure since enablement, with the start step/cursor
explicitly saved. Previous independent partial census is retained separately,
never blindly merged; the earlier training prefix remains uncovered here.
`profile_loader_census.py` compares identical real loader units with/without
counting, checks first-batch tensor parity, and performs no optimizer updates.

`class_census.py --release CERTIFICATE --output NEW_RUNTIME_DIR --source-revision
SHA` counts the actual next-candle classes across every eligible training origin
using CPU only. Defaults: horizon1, one idle-priority CPU, very-low I/O priority,
50% work duty and 64MiB/s sealed-payload throughput cap. No Torch/model imports,
no GPU context, no training stop/resume and no W&B writer. `--split validation`
checks parity; `--horizons 1 2 3 ... 64` is explicit optional wider scope.
The same sealed readers, `plan_block` eligibility and common validity mask are
used. Overlapping labels retain the same exposure as training; this is not a
deduplicated-bar count. Results include per-year/liquidity strata and rejection
counts. Traversal is independently shuffled for early coverage, but partial
counts must not be represented as a complete distribution.
Atomic `state.json` is resumable at publication boundaries with `--resume`.
`progress.json` exposes completion, ETA, throughput and resource limits. STOP
is checked between publications and during throttling and is never removed by
the script. `--profile-units N` gives a bounded pilot, never a full-census claim.

User requested a separate copy of V3 with the actual Phase1 V11 decoder heads,
and authorized fixes to problematic training and scheduling. V3 checkpoints,
logs, identities and source snapshots remain independent.

The context encoder remains two attention layers,width256,4heads,FF1024 and a
256-dimensional export. Freeze the same completed Phase1 epoch25 encoder.
The decoder exactly reuses `phase1_v11.model.Autoencoder.decode`: linear256->64x54
readout, separate32->9 reference head, price-coordinate factor100, structured
OHLC/quote inverse and FP64 raw-unit inversion. Its weights initialize from that
same Phase1 checkpoint. Both heads receive only the exported256-vector; the
reference head reads its last32 dimensions. Future references are never inputs.

The54 outputs per bar are18 market coordinates,9 time fields and27 validity
logits, exactly as Phase1. This forecast experiment supervises all18 future
market features using original source masks. It retains the V3 raw-log forecast
objective (price1000x log error,activitylog1p,equal family SmoothL1). Auxiliary
time/validity outputs are retained for head identity but do not enter the loss.
There is no separate future-median auxiliary loss: references learn through the
inverse-transformed forecast objective, not a claim of calibrated future medians.
Targets remain the next64 OBSERVED1s bars. This changes prediction targets, not
the decoder's equations. All64 horizon metrics and causal controls are retained.

Two additional changes address the diagnosed training instability:

- Fixed AdamW learning rate3e-5 after1000updates of warmup from3e-6. It does not
  wait for the multi-billion-origin traversal to decay. Validate every5000updates.
- Deterministically shuffle complete training sessions within each certified
  release root (seed17041+rootindex). Keep every session/block/eligible origin,
  all within-window ordering and the original validation population/order.
  Pin Polars version and actual traversal hashes. Resume uses the shuffled
  publication/block/origin cursor. Batches still contain overlapping windows;
  session shuffling reduces long ticker runs but is not independent-window sampling.

This is a combined head/initialization/order/LR experiment, not a transformation-only
ablation. It is not yet evidence of better forecasting. One full pass initially,
rolling last/best checkpoints,patience24 full validations. W&B remains
`mehdifaraji/bargpt-phase2` with a NEW run identity. Qualified engineering settings
start from batch4096,4CPU I/O/4 preparation workers,tokenbatch1024,8GiB prefetch,
4GiB session cache,BF16 attention/FP32 heads,fused optimizer,CUDA graph steps.

Entry points: `python -B -m research.bar_gpt.phase2_v4.run_train --mode profile`
and `--mode train --execute`, with explicit release,teacher SHA,calibration,
immutable source revision and runtime output. Detached controller:`phase2_v4.launch`.
Rich monitor:`phase2_v4.panel --output RUN`. Commands and measured profiles belong
under the workstation runtime root, not source. First qualify exact head parity,
future shapes,masks,gradient/resume behavior,tiny fitting and full pipeline speed.

Initial launch: source `95cad7fe0d4cdf1735d1b1c5de85467b632a5971`, run
`full-p1head-lr3e5-shuffled-pass01`, W&B `mehdifaraji/bargpt-phase2/gh6kl6tc`.
Runtime:`D:/TradingML/runtimes/bar_gpt/phase2-v4-phase1-head-20261119` holds
`training-command.txt`,`start-full.ps1`,deployment proofs and profile results.
The256-session profile covered782733 eligible origins at49920/s including startup;
cached compute peaked near350K/s at batch4096. Tiny32-example loss1.515->0.789
after400updates. Exact head parity,mask/gradient,CUDA resume,fixed schedule and
session-population/order tests passed. Real train/validation/checkpoint integration
completed13updates. This is qualification, not proof of forecast superiority.
V3 stopped gracefully at85656; its final checkpoint,logs and STOP are retained.
# Evaluation extension: shifted actuals and directional accuracy

Move-size extension: `move_metrics.py` uses batched GPU bucketization/scatter
reductions, with fixed-size float64 accumulators. It evaluates trade/bid/ask close,
both origin-to-horizon returns and consecutive predicted/actual path returns,
grouped separately by actual or predicted signed moves. Bins are exact zero and
up/down `(0,.05]`, `(.05,.1]`, `(.1,.25]`, `(.25,.5]`, `(.5,infinity)` percent.
Every group reports count/share, direction accuracy, raw/relative MAE, both
baseline MAEs and skills, and mean actual/predicted returns. A common valid mask
requires current, origin and previous actual close for fair baseline comparisons.
No future actual is substituted into the predicted path. No bins are fitted.

All 64 horizons and three close channels are retained in the bounded rolling
`move-buckets-latest.npz`, uploaded to W&B Files after validation. Its `sums` axes
are definition, grouping, horizon, channel, signed bin, statistic; labels and step
are embedded. `MoveMetrics.row` converts any slice to reported metrics. W&B curves
cover all-horizon aggregates for all three closes and horizons 1–3 for trade close.
This avoids tens of thousands of scalar curves and checkpoint-history growth.
`audit_forecast --compare-overhead` compares the existing metrics without move
buckets, then all metrics, then existing metrics again on the identical checkpoint.

`audit_forecast.py --run RUN --output NEW_RUNTIME_DIR --source-revision SHA`
runs the entire certified validation split on the stopped run's verified last
checkpoint, without an optimizer or W&B writer. The same `ForecastMetrics`
reducer runs in training validation after qualification. Audit output binds the
checkpoint, source, dataset, step and runtime. Resume with `--forecast-audit
AUDIT_JSON` uploads the qualified result at its original step to the same W&B run.

`shifted_actual_future_informed` repeats the last observed feature for horizon 1,
then uses the previous actual target feature for horizons 2–64. This is an
evaluation-only one-bar movement benchmark, not a deployable fixed-origin forecast.
`last_observed` remains the causal persistence control. Both expose raw MAE,
model raw MAE on identical masks, log MAE, counts and raw-MAE skill. All 18 channels
have aggregate metrics; trade/bid/ask close also have all 64 horizon metrics.

Direction from origin compares each predicted close and actual close with the
last observed close. Consecutive-path direction compares successive predictions
with successive actuals; actual future values never enter model predictions.
Exact unchanged raw prices are a third class; report both three-class accuracy
and accuracy restricted to actual nonzero moves, with unchanged/moving counts
and the fraction of actual moves that are up (class-imbalance context). Predicted
direction uses log differences to avoid inverse-rounding turning a constant log
prediction into a spurious move. No tick-size/deadband assumption is introduced.
Tolerance hit rates use absolute relative error <= 0.05%, 0.1%, or 0.2%.
All comparisons require source validity of both operands. The extra `last_raw`
metadata is used only by evaluation, never by the model or training loss.

The instrumentation resume pins ancestor 95cad7fe0, verifies checkpoint proof,
the unchanged scientific/numerical contract and an explicit source-file allowlist.
The existing one-full-pass policy, optimizer, LR, traversal and RNG are retained.

## Matched Phase1 inheritance diagnosis

`python -B -m research.bar_gpt.phase2_v4.diagnose_inheritance --run STOPPED_RUN --output NEW_RUNTIME --source-revision SHA` is a read-only GPU audit. It verifies both checkpoints and the certified release, evaluates all708600 validation origins, and compares the frozen Phase1 decoder's last historical close with the actual last observed close alongside Phase2's first-future-close error. It retains signed/absolute errors and correlation sufficient statistics for all three close channels, plus the normal forecast metrics. The teacher reconstructs its own past input; Phase2 predicts future bars. Correlation can suggest inheritance but is not a causal experiment. The original training STOP and checkpoint are untouched. Run only after the owned trainer has exited.
