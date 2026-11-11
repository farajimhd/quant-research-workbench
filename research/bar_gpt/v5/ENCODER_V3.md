# Encoder v3: local references before unrestricted masking

This is a copy of the current `encoder_v1.py` experiment and its runnable path,
inside the v5 research directory. It does not modify the historical BarGPT v3
autoregressive project or any active v1/v2 training process.

## Changes from encoder v1

1. Calculate references from the full, source-valid 64-bar raw window **before**
   random augmentation. One shared price reference is the Float64 median across
   all available trade/bid/ask closes. Every one of the six activity features
   has its own Float64 median across the 64 bars. Even-sized medians use linear
   interpolation. Valid zeros participate; source-invalid observations do not.
2. Prices retain `100*ln(price/price_reference)`. Quantities use
   `ln(1+quantity)-ln(1+quantity_reference)`, with multiplier1. This replaces
   v1's globally fitted log-space activity median/IQR. No global fitting scan is
   needed; the persisted `calibration.json` describes the reference policy and
   split binding rather than containing fitted activity statistics.
3. Keep approximately60% whole-column masking (10 or11 hidden columns per
   batch), with independent random feature permutations per sample. Remove the
   visible-close requirement and rejection sampling. All18 feature identities
   are eligible; all three close columns may be hidden together. This does not
   mean all18 columns are hidden simultaneously.
4. Full validation remains unmasked, scores every source-valid value, and uses
   the same preprocessing as training. The single forward produces both
   transformed metrics and raw MAE. Inverses are
   `reference*exp(price_prediction/100)` and
   `(1+quantity_reference)*exp(quantity_prediction)-1`. The implementation uses
   Float64 `expm1(prediction+ln(1+reference))` for the latter. No clipping or
   invented values are introduced. Overflow/count reporting remains explicit.
5. V3 architecture/preprocessing/reference-policy contracts reject v1/v2 resume
   states. `run_encoder_v3.py` is the separate entry point, with default W&B
   project `bargpt-column-mae-v3`. Future training requires a fresh output root;
   no current task pin, W&B identity, checkpoint or data authority is changed.

For an entirely unobserved quantity column, its reference is a zero placeholder
and it has no scoring observations. A window without any observed closing price
still fails source eligibility; this is independent of augmentation masks.

## Deliberate interpretation

Normalization sees values that may subsequently be masked, as requested. The
reference arrays are not extra encoder/decoder tokens, but the normalized
values depend on these full-window references. Raw MAE measures reconstruction
**conditioned on the known window scale**, not independent recovery of a hidden
absolute scale and not future forecasting. A future forecast cannot use an
unknown future window's true reference at inference.

## Preserved behavior

The feature-column encoder, latest-bar token, decoder,512-dimensional large
configuration, attention, dropout, AdamW, cosine scheduling, equal-feature
SmoothL1 loss, learning guards and checkpoint contents are copied from v1.
The existing raw v6 shards, exact split membership, source masks, chronology,
split adjustment and nine context features (including timeframe) are unchanged.
The decoder remains included in every model checkpoint. Market preprocessing
is vectorized over batches, bars and features; there is no per-observation loop.

## Validation scope

`test_encoder_v3.py` covers independent inverse arithmetic, all18 features and
six timeframes, legitimate zeros/missing evidence, unrestricted masks, reference
timing, unchanged UTC features, no validation RNG draws, copied architecture
weights, real CPU forward/backward, the actual file-loader path and checkpoint
orchestration with fake CPU kernels. This is engineering validation, not evidence
that v3 has better reconstruction or downstream trading performance.
