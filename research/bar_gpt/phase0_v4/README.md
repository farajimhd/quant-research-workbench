# Phase 0 V4: one-bar feature transformer

This version reconstructs the same bar that it encodes. It implements the
proposed symmetric activity reference, with no true-reference decoder shortcut.
It does not modify or restart V26 or earlier Phase0 runs. It is an implementation
candidate, not an experimentally selected model or a forecasting claim.

## Inputs and targets

The source order is trade OHLC/volume/count, bid OHLC/size/count, then ask
OHLC/size/count. The existing certified adapter supplies 18 log-price/log1p-
activity fields plus nine time fields and 27 boolean source-valid flags.
Every training example is one bar. A loader can deliver 64-bar windows, but
the encoder flattens their leading dimensions into independent examples.
There is no temporal attention, random bar masking, or cross-bar reference.

Prices retain the prior four coordinates in each family:
`s=log(close)`, `d=log(open)-s`, `u=log(high)-max(log(open),s)`,
`l=min(log(open),s)-log(low)`. Missing OHLC completion follows the existing
source-valid contract. Valid observations are never clipped or reordered.

For quantities, let `b=log1p(bid_size)`, `a=log1p(ask_size)`, and
`t=log1p(trade_volume)`. Define `r=(b+a+t)/3`, `db=b-r`, `da=a-r`.
Store `r` at slot4, `db` at slot10, and `da` at slot16. The quantity inverse is:

```text
bid   = expm1(r + db)
ask   = expm1(r + da)
trade_volume = expm1(r - db - da)
```

Trade-count slot5 now stores the zero-safe average-size-like coordinate
`z=log1p(trade_volume)-log1p(trade_count)`. It represents the log of
`(1+volume)/(1+count)`, not exact average trade size. Recover the count using
`expm1(predicted_log_volume-z)`, where `predicted_log_volume=r-db-da`.
This preserves zero volume with nonzero count, zero count, and both zero.
Slots11 and17 remain independent `log1p(bid_count)` and `log1p(ask_count)`;
the former shared-count reference is removed. Targets remain all18 raw fields,
including trade count: it is transformed, not discarded.

For partial triples, compute r from valid members only and fill absent members
with r before forming deviations. Their completed deviations still sum to zero.
Supervise all three coordinates whenever at least one member exists. An absent
triple has zero coordinates and no coordinate loss. Raw/log reconstruction is
always supervised only where the ORIGINAL source mask is valid. The decoder
needs neither that mask nor the true reference to recover valid values.

When trade volume is absent but count is valid, construct z using the completed
volume from the quantity coordinates (the valid-quantity mean, or zero when
all quantities are absent). The decoder recovers this same completed volume,
so count remains recoverable without a mask-dependent inverse. Count-coordinate
supervision follows count validity alone. Missing quote counts are filled with
zero and excluded from supervision. A valid zero is never treated as missing.

This is invertible algebra, not a guarantee of lossless neural compression.
Reference errors affect all three quantities; asymmetric errors in db/da can
also amplify trade reconstruction error. Independent log1p remains a necessary
experimental control. No claim is made that volume and quote sizes have the
same distribution merely because they share this coordinate system.

## Architecture and precision

- 27 feature tokens: each has a standardized scalar, coordinate-valid flag,
  original source-valid flag, and learned feature identity.
- One learned summary token; two pre-norm transformer blocks, width32,
  four heads, feed-forward width64, zero dropout.
- Summary readout: LayerNorm and linear projection to a configurable16D embedding.
- Decoder: one16-to18 affine layer (306 parameters), followed by fixed inverse
  normalization and the documented algebra. Its sole variable input is16D.
- Total default model:19,010 trainable parameters, including18,704 in the encoder.
- Attention runs in chunks of4096 bars to bound workspace, without discarding
  samples. The decoder and inverse log-coordinate arithmetic explicitly disable
  autocast and use FP32; native-unit reporting uses FP64 exp/expm1.

Time and validity are encoder context; the reconstruction target is18 market
features, not the nine time fields or validity bits. No raw output is clipped
or projected: negative activity predictions, invalid OHLC order, or crossed
quotes must remain visible to future evaluation metrics. Use autocast around
the encoder if profiling justifies it; do not convert decoder weights to BF16.

## Calibration, loss and usage

`prepare.py` fits 27 fixed means/scales from the existing frozen TRAINING
subset, verifies its manifest/split, and audits inverse reconstruction in
original units. It never fits validation. Sampling is uniform bar exposures,
so overlapping/repeated windows retain their original multiplicity. Source
and calibration hashes are retained. V1-V3 calibration is incompatible.
The earlier V4 shared-count calibration is also incompatible. Refit calibration
using `prepare.py`; its parameters must carry
`transform_version=phase0-v4-symmetric-quantity-trade-ratio-v2`. The constructor
rejects unversioned/older calibration, and checkpoint extra state seals the same
coordinate semantics so matching tensor shapes cannot silently load old weights.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.bar_gpt.phase0_v4.prepare --output D:/TradingML/runtimes/bar_gpt/phase0-v4/calibration.json
```

```python
from research.bar_gpt.phase0_v4.model import Autoencoder, Config
from research.bar_gpt.phase0_v4.objectives import objective

model = Autoencoder(calibration['parameters'], Config(embedding=16))
embedding = model.encode(values, valid)    # (...,27) -> (...,16)
prediction = model.decode(embedding)      # no target/reference argument
raw_prediction = model.reconstruct_raw(embedding)  # (...,18), original units
loss, components = objective(model, prediction, values, valid)
loss.backward()
```

The initial objective retains Phase0's equal price/activity SmoothL1 means,
with log-price residual factor1000 and activity factor1, plus0.1 standardized
coordinate loss. It supervises both absolute scale and relative composition.
It is not a directional-accuracy objective. Directional metrics below are
evaluation diagnostics, not new loss terms. `data.forward_batch` provides the
certified loader adapter; the V4 trainer imports the V4 objective explicitly.

## Reading the code

Read `transforms.py` first: it defines what the18 coordinates mean and why the
inverse needs no target reference. Then read `model.py`: the documented attention
block explains query/key/value shapes, residual connections and normalization.
`objectives.py` explains what backpropagation minimizes. `metrics.py` measures
quality in original units independently of that training objective. Finally,
`schedule.py`, `train.py`, `run_train.py` and `panel.py` cover the schedule,
update/checkpoint boundaries, launch contract and read-only terminal display.

## Training and recovery

`run_train.py` supports the complete certified Phase1 train/validation split.
All64 bars per window are independently encoded; repeated source exposures are
retained. Default20 epochs, batch256 windows,8 workers and prefetch4 are safe
initial settings, not a claim of maximum workstation throughput. The current
path is eager FP32, with bounded pinned staging on CUDA. Profile before using
a larger batch or adding mixed precision/CUDA graphs.

The stateless cosine scheduler defaults to1e-4→1e-6 across consumed training
windows. Every optimizer update samples the curve at its ending window count;
the last update uses exactly1e-6. Validation does not advance the scheduler.
Both endpoints and the schedule can be understood directly in `schedule.py`.
These are configurable defaults, not evidence that1e-4 is optimal for this model.

Periodic evaluation uses the fixed development subset. Full validation runs at
each epoch boundary and must match the full manifest count. Best checkpoints
use development loss only as a storage heuristic; selecting a useful model
still requires reviewing raw errors, worst groups and direction metrics.
Loss is a window-weighted mean of batch objectives: compare it with the same
validation batch configuration. Initial development metrics precede training.

`checkpoint_last.pt` and `checkpoint_best.pt` retain model/optimizer state,
transform identity, RNG, exact ordered batch cursor, consumed windows and
scheduler parameters. Checkpoints have serialization readback and SHA proofs.
`--resume` requires the SAME output directory and scientific/source contract.
The loader replays/skips already-consumed batches, then restores model RNG;
this costs startup time but does not retrain those windows. Interrupted
validation restarts from its beginning. No partial evaluation is called complete.

Create a `STOP` file in the run directory for graceful checkpointing at the next
boundary. The trainer never deletes it. Ctrl+C inside an optimizer operation
instead preserves the last verified checkpoint, because an interrupted update
cannot be assumed atomic. A recognized W&B transport failure checkpoints the
current committed update and exits with local evidence. Other failures preserve
the last verified checkpoint and report failure; there is no blind restart loop.

W&B defaults online in `mehdifaraji/bargpt-phase0-v4`, with one unique directory
per run and preserved remote identity on resume. Metrics/configuration are
uploaded; model checkpoints are never uploaded. `--wandb disabled` is available
for local qualification. Runtime ownership and source hashes are checked by the
existing workstation preflight; do not run from a mutable workstation checkout.

From a pushed, hash-verified workstation snapshot, using workstation Python:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$revision = 'FULL_HASH_OF_THE_VERIFIED_SNAPSHOT'
$run = 'D:/TradingML/runtimes/bar_gpt/phase0-v4-training-01'
$calibration = 'D:/TradingML/runtimes/bar_gpt/phase0-v4/calibration.json'
python -B -u -m research.bar_gpt.phase0_v4.run_train --output $run --source-revision $revision --calibration $calibration --epochs 20 --learning-rate 1e-4 --min-learning-rate 1e-6
```

Add `--max-updates 10` for bounded qualification; it still uses real initialization
and development evaluation. Resume with the SAME arguments plus
`--resume "$run/checkpoint_last.pt"`, removing that bounded limit when ready.
There is no implicit scientific fork when a contract changes.

## Quality metrics

- All18 channels by timeframe: raw MAE, signed bias, MAPE, signed percentage
  error, log error, target/prediction variance and valid observation counts.
- Exact-zero targets are excluded only from percentage errors and explicitly
  counted; tiny nonzero targets below1e-8 remain included and separately counted.
- Percentage-error median/P90 are fixed-histogram upper bounds, not exact
  quantiles. Values above1e6% are counted; an unbounded tail quantile is omitted.
- Price-band MAE/MAPE/counts across below$0.10, $0.10–1, $1–5, $5–20, $20–100
  and $100+. These pool price-field observations within each timeframe.
- Candle and adjacent-close direction, neutral-band direction, signed move bins,
  range-bin accuracy and complete confusion matrices use existing price bins.
- All six activity channels: adjacent-bar down/flat/up confusion matrices,
  per-class counts/recall, ordinary and balanced directional accuracy.
- Negative/nonfinite outputs, OHLC violations, crossed quote fields, and
  nonpositive predicted denominators excluded from price-ratio diagnostics.
- Coordinate MAE includes slot4 (quantity reference) and slot5 (trade ratio).
  Embedding variance and near-constant dimension counts flag possible collapse.

Adjacent comparisons stay INSIDE each64-bar window and require both source
endpoints valid. They compare independently reconstructed bars, not future
forecasts. No labels are fed into the encoder. Metrics run during evaluation,
not every optimizer update, and accumulate fixed-size vectorized statistics.
Nonfinite validation cannot be certified complete.

## Progress terminal

Open a workstation terminal in the same immutable snapshot:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.bar_gpt.phase0_v4.panel --run D:/TradingML/runtimes/bar_gpt/phase0-v4-training-01
```

The Rich panel shows epoch/validation bars, windows/s, ETA, LR, loss, cumulative
loader wait, GPU utilization/memory, saved update and recent1s quality metrics.
Rates are windows/s; each window contains64 bar exposures. Missing observations
display `n/a`, not zero. Partial validation is labelled. A stale heartbeat is
marked after30 seconds, and unchanged counters after5 minutes are distinguished
from heartbeat freshness. Inconsistent completion/coverage is flagged.
Closing the viewer never stops the trainer. `--once`
or redirected output prints a plain snapshot without terminal control codes.
No scheduled task or background training is created by opening this viewer.

## Validation

`test_model.py` checks every activity-mask combination, missing/zero/large
activities, tiny prices, partial OHLC, unusual valid geometry, NaN placeholders,
bar independence, embedding-only decode, state restoration, FP32 readout under
autocast, finite gradients, empty supervision, and an actual240-update easy fit.
These tests establish runnable algebra/model behavior, not validation quality.
The training extension additionally tests exact resumed-versus-uninterrupted
model weights, optimizer and RNG states, scheduler endpoints, full-population
coverage on a small CPU fixture, graceful STOP, interrupted validation and
checkpointing after an injected reporting failure. Panel tests cover60/100-column
layouts and active/stale/failed/stopped/completed/inconsistent-coverage states.
The actual panel entry point was inspected in redirected and interactive PTY
output; no workstation window or long training process was left running.

The extended local suite has25 passing tests. A separate certified-adapter
metrics smoke covered256 bars and emitted999 populated metrics with no nonfinite
outputs, using only four training windows. This is implementation evidence,
not held-out performance. Receipt: `native-quality-smoke.json` under the same
implementation runtime below. Online W&B and workstation throughput have not
been qualified for this new trainer yet.

Initial implementation verification on2026-11-23: five tests passed locally. A separate
CPU smoke through the certified raw adapter used four training windows
(256 independent bars), produced finite gradients and completed an optimizer
update. Maximum native roundtrip error divided by max(1,abs(target)) was
3.263e-6. The smoke used small-sample training calibration only and is not a
full calibration or a held-out evaluation. Receipt:
`D:/TradingML/runtimes/bar_gpt/phase0-v4-implementation-20261123/native-smoke.json`.
That receipt predates the trade-ratio change. The revised transform passes11
tests, including zero/count edge cases, all64 activity validity combinations,
predicted-volume dependency, finite gradients, and rejection of incompatible
calibration/checkpoint semantics. No full training or full calibration has run.
The revised certified-loader CPU smoke also passed256 bars with finite gradients
and an optimizer update; maximum native roundtrip error/max(1,abs(target)) was
3.219e-6. Its separate receipt is `native-ratio-smoke.json` in the same runtime.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m pytest research/bar_gpt/phase0_v4 --import-mode=importlib -o consider_namespace_packages=true -p no:cacheprovider --basetemp D:/TradingML/runtimes/bar_gpt/phase0-v4-tests -q
```
