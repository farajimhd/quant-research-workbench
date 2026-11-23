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
It is not a directional-accuracy objective. `data.forward_batch` provides the
certified loader adapter. Full training, W&B integration, scheduler selection,
and workstation throughput qualification are deliberately not launched by this
model implementation; do not route it through the older hard-coded V1 engine.

## Validation

`test_model.py` checks every activity-mask combination, missing/zero/large
activities, tiny prices, partial OHLC, unusual valid geometry, NaN placeholders,
bar independence, embedding-only decode, state restoration, FP32 readout under
autocast, finite gradients, empty supervision, and an actual240-update easy fit.
These tests establish runnable algebra/model behavior, not validation quality.

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
python -B -m pytest research/bar_gpt/phase0_v4/test_model.py --import-mode=importlib -o consider_namespace_packages=true -p no:cacheprovider --basetemp D:/TradingML/runtimes/bar_gpt/phase0-v4-tests -q
```
