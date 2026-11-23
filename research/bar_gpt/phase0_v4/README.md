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
Store `r` at slot4, `db` at slot10, and `da` at slot16. The independent count
triple uses slots5,11,17 with the same rule. The inverse is:

```text
bid   = expm1(r + db)
ask   = expm1(r + da)
trade = expm1(r - db - da)
```

For partial triples, compute r from valid members only and fill absent members
with r before forming deviations. Their completed deviations still sum to zero.
Supervise all three coordinates whenever at least one member exists. An absent
triple has zero coordinates and no coordinate loss. Raw/log reconstruction is
always supervised only where the ORIGINAL source mask is valid. The decoder
needs neither that mask nor the true reference to recover valid values.

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

Implementation verification on2026-11-23: five tests passed locally. A separate
CPU smoke through the certified raw adapter used four training windows
(256 independent bars), produced finite gradients and completed an optimizer
update. Maximum native roundtrip error divided by max(1,abs(target)) was
3.263e-6. The smoke used small-sample training calibration only and is not a
full calibration or a held-out evaluation. Receipt:
`D:/TradingML/runtimes/bar_gpt/phase0-v4-implementation-20261123/native-smoke.json`.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m pytest research/bar_gpt/phase0_v4/test_model.py --import-mode=importlib -o consider_namespace_packages=true -p no:cacheprovider --basetemp D:/TradingML/runtimes/bar_gpt/phase0-v4-tests -q
```
