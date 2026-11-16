# Phase-two Bars V1

## November 16 architecture investigation and prepared stopping policy

The stopped `phase2-bars-v1-20261116` run remains immutable and is NOT restarted
by these source changes. Its existing patience8 STOP remains in force.

V4 full encoding supplies all18 normalized market columns, validity and nine
time features (including timeframe). Input participation does not imply retention
of raw normalization references: multiplying every raw price by2 leaves the
normalized prices and embedding identical while doubling the price reference.
No claim is made that a512-dimensional bottleneck preserves every input detail.

Phase two has no row-position encoding. Self-attention is permutation equivariant;
selecting row7 makes the output invariant to permutations of the other15 rows.
This holds for an untrained model as well as the trained checkpoints and is not
caused by concatenating attention heads. V4 can carry within-window temporal and
timeframe content, but phase-two row placement adds no temporal-order information.
Permutation invariance alone does NOT prove timing information is absent from
token content. A bounded linear probe on frozen cached embeddings (192 training
contexts,48 separate2025 diagnostic contexts) recovered timeframe with92.6-94.8%
accuracy, but the eight1s window slots with only11.5-13.5% accuracy (12.5% chance),
including a probe supplied the latest1s embedding as an anchor. This is evidence
of weak accessible relative-order information, not proof of nonlinear impossibility
or full2026 validation quality. Do not duplicate timeframe encodings by assumption.
No architecture or metadata-conditioning change has been implemented here.

Keep consecutive rolling origins and the shared GPU pools; no redundant raw
window materialization or publication mixing is introduced. Loss rebalancing is
only a proposal: fixed TRAINING-only per-channel scales with a floor, averaged
within price/activity/reference families, would balance units without changing
targets. It needs explicit calibration and comparison before activation; the
current normalized-bar plus reference-change objective remains unchanged.

New direct-bars launches default to `--patience 24`,
`--stop-min-session-fraction 0.10` and `--stop-stale-session-fraction 0.02`.
ALL stopping gates must hold:
24 consecutive non-improving full validations, at least10% of the planned
training session-publication slots traversed cumulatively, and at least2% of
a pass's slots completed since the best validation. Validation-only publications
are excluded. Repeated ticker-days in separate certified roots count as separate
publication exposures, not unique market sessions. The current publication is
counted conservatively only after advancing to the next one; pass exhaustion
counts its final publication. A full validation at the same position does not
increase coverage. History/checkpoints record each review and the policy is
included in the model contract. Resume derives counters from that bound history;
old histories require an explicit migration, never an implicit reset or STOP
override. Old experiment launchers retain their original patience behavior.
Validation frequency, scheduler, targets, architecture and nonfinite guards are
unchanged. These defaults are engineering choices, not evidence of better learning.

Implemented2026-11-15 as a separate experiment. The running
`phase2-unmasked-v1-20261115` remains pinned to its original source and is not
modified, restarted or migrated. No new training task is started by implementation.

## Architecture and unchanged inputs

The input path is identical to the current unmasked version: frozen completed
V4 epoch50,16 context embeddings of512 features,frame-major oldest-to-newest,
8x64 1s,4x64 10s,2x64 1m,1x64 1h,1x64 daily. No feature masking, extra input
tokens, positional features, statistical heads or latent centering are added.
The phase-two encoder remains one pre-LN512 Transformer block,8heads of64,
FF2048,GELU,dropout0; readout is existing latest1s row7 and remains512 dimensions.
The prediction head retains hidden256/GELU and changes only its output width:

`512 ->256 ->1159 = (64 x18 normalized future bar values) +7 references`.

The output order follows the canonical18 trade/bid/ask OHLC,volume or size,and
count channels. The seven reference names are price,trade_volume,trade_count,
bid_size,bid_count,ask_size,ask_count. A reference is one value for the complete
64-bar window,not a separate reference for every bar. Public `predict()` returns
raw bars[B,64,18],references[B,7],and the encoder representation[B,512]. Internal
bar tensors use[B,18,64] for compatibility with shared transformations/metrics.

## Targets: directly from the future raw-bar transformation

The original certified4TB release,source-valid masks,single-session blocks and
causal eligibility are unchanged. The target is strictly the next64 OBSERVED1s
bars inside the same block,not necessarily the next64 wall-clock seconds.
`targets.from_raw()` invokes the SAME V4 `normalize()` authority. It retains:

- `raw`: actual future values[B,18,64],and their source-valid mask.
- `bars`: their V4-normalized values[B,18,64].
- `actual_reference`: actual Float64 future price/activity references[B,7]
  computed by that transformation.
- `references`: the training parameterization of those same future references,
  relative to references from the known latest64-bar1s history window.
- `origin_log_reference`: known historical ln(price reference) and
  ln1p(activity references),used for numerical anchoring/inverse only.
- `reference_valid`: observed-reference flags; an activity reference with no
  source-valid future observations is not supervised or counted as observed zero.

The shared source-valid reference rule is unchanged: price is the interpolated
Float64 median of all valid trade/bid/ask closes in that window; each activity
reference is the interpolated median of its valid values. Absent activity uses
the canonical normalization fallback0 but remains explicitly invalid as a label.
The historical price anchor is the historical WINDOW reference,not the last
trade price. Historical anchors never depend on future bars or future references.

Reference targets are represented as:

`price_change =100 * (ln(future_price_ref) -ln(origin_price_ref))`

`activity_change =ln1p(future_activity_ref) -ln1p(origin_activity_ref)`.

This is an internal output parameterization,not a substitute source for labels.
Actual future references are retained and absolute reference errors are reported.
The normalized bars use the actual future-window references during LABEL
construction,exactly like phase one. During inference/inverse the model uses its
PREDICTED reference changes and known historical anchors; true future references
and true future timestamps never condition the prediction.

## Loss and validation

Loss is source-valid normalized-bar MSE PLUS source-valid reference-change MSE,
with component weights1:1. Separate means keep seven references from being
overwhelmed by1152 bar values. Prices retain the canonical100*ln(P/reference)
scale; activity retains ln1p differences. There is no embedding-target loss,
no frozen V4 decoder forward,and no actual-future-reference-conditioned decoding.

Validation reports the composite objective and its two components,training-mean
baseline,raw-unit MAE/RMSE by channel/horizon,absolute reference MAE/counts,
and price-only MAPE,median/P90 APE and price-band breakdowns. Raw price/quantity
errors use predicted future references,so they include reference forecast error.
Target-conditioned legacy decoder metrics/cosine are not mislabeled as direct
forecast metrics. The normalized composite controls the unchanged patience guard.
Predictions are not silently clipped: negative activity/reference predictions,
OHLC-order violations,crossed quote opens/closes and nonfinite errors are exposed.
The decoder does not guarantee structurally valid market forecasts.

## Engineering, recovery and safety

The shared fixed-tile context cache,coalesced preparation,ordered workers,causal
packing,full-pass scheduler,online W&B project and checkpoint proofs are reused.
Exact Float64 source references are cached alongside the1s context tiles. The
cache is private to the immutable receipt/teacher/precision/split-basis identity,
uses explicit readiness bits,and reserves its bytes inside the existing total
device-cache budget. Only missing future reference endpoints are calculated
separately; they do not require a future V4 embedding forward. Labels reuse these
exact medians with preallocated device indices instead of sorting the same
historical/future windows on every batch. Source-value,reference and mask checks
still complete before any optimizer update. No Float32 cached log reference is
substituted for the canonical Float64 median.
No future embedding target is explicitly requested; fixed pool-relative context
tiles may still incidentally encode extra endpoints,as required for unchanged
tokenizer numerics. This is not a claim of zero tile overfetch.

Target batches are dictionaries with separate value/mask/raw/anchor fields,
including Float64 reference labels. They are packed with their exact source
cursor and passed through the existing one-next-batch prefetch. The new CUDA
step captures only the needed bar/reference value+mask loss buffers. All
loss/gradient nonfinite checks still precede AdamW. Full-state recovery seals
model,AdamW,scheduler,RNG,cursor,history and target contract. An embedding-target
checkpoint cannot resume this output head under the exact-contract check.

Use a NEW output and W&B name in the existing `bargpt-phase2` project when
training is authorized. Retain the current explicit native settings:BF16,
batch1024,token tile2048,Torch1,prep4/IO4,host prefetch8192MiB,cache1024MiB,
GPU prefetch,fusedAdamW,cuDNN,captured updates;10 FULL passes;validation/LR update
every150000updates and pass end;peak3e-4/floor5e-5/one-time2000warmup;pass
restart multiplier0.9;patience8;clip1;weight decay0.01;loss10;rolling checkpoint
1000,rolling validation/best,and immutable complete-pass checkpoints. No scheduler
or precision change is implied by switching targets. GPU profiling/launch refuses
another active BarGPT trainer; default audit is CPU/read-only.

The runtime implementation setup contains `prepared-command.json` with complete
explicit native training arguments; it is preparation,not an active campaign.
For a safe bounded label/inverse audit from the repository:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B research/bar_gpt/phase2_bars_v1/run_train.py --mode audit --dataset-release D:/TradingML/runtimes/bar_gpt/phase2-all-published-certification-20261114/release-v1/certificate.json --output D:/TradingML/runtimes/bar_gpt/phase2-bars-v1-audit --max-blocks 4 --profile-origins 16
```

The launcher refuses existing output directories. Audit is bounded/sample
evidence,not full-corpus qualification or a GPU throughput result. GPU graph
parity/performance must be qualified before any full training activation; no
concurrent GPU experiment is run against the existing unmasked training job.
`python -B -m research.bar_gpt.phase2_bars_v1.qualify --dataset-release <certificate.json>
--output <new-runtime-result.json>` runs that explicit same-input model/AdamW/RNG
capture check only when no other BarGPT trainer is active. Its throughput is
student-only and must not be reported as sustained full-pipeline speed.
