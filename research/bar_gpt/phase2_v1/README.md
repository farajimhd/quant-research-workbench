# Phase-two v1: frozen-tokenizer latent forecasting

## All-published dataset release (subsequent user decision)

The user authorized using **all saved candidate blocks at their natural
distribution**, dropping the75/25 selection rule. The original selected-block
manifests remain immutable. `release.py` creates a separate
`bargpt-phase2-all-published-v1` certificate and per-root Parquet block/unit indices.
Use `--dataset-release <certificate.json>` instead of legacy `--dataset-root`.
Every included block still uses the same session/quote/split/index/target rules.
The release binds every publication through complete sealed ledger-part SHA,
journal sequence and publication count/byte/classification reconciliation.
Fresh payload SHA/content/quote/causal checks are stratified samples. The certificate
explicitly records `all_payload_hashes_recomputed=false` and
`all_physical_files_restat=false`; it is not an exhaustive4TB physical rehash or
an assertion that every stored origin satisfies the model's longer warmup.
The training reader still verifies touched receipts/payloads and eligibility.

Certification never opens SQLite, changes source roots, regenerates bars,
rebalances tickers or starts training. Its CLI runs at low CPU/I/O priority,
requires64GiB available RAM and preserves150GiB disk headroom. Per-root metadata
time and concurrency are bounded. A partial audit never publishes a certificate.
`--resume` can reuse completed root reports only after report/index SHA and exact
source HEAD/plan/result revalidation. Incomplete root passes are repeated; no
unchecked prefix is promoted. The CLI permits up to ten minutes per metadata
root pass, with progress counters; it never rereads the entire4TB payload corpus.

Design agreed November 14, 2026. Model version: `bargpt-phase2-latent-v1`.
This is distinct from the existing `bargpt-phase2-ar-sampled-v2` **shard format**.
No extractor, source bars, active training job or shard is changed by this model.

## Frozen teacher and target

Pin an explicit immutable phase-one V4 checkpoint by SHA256. Its encoder is
frozen, in evaluation mode, and receives all source-valid market columns through
the exact V4 local-reference preprocessing and nine UTC features. One real
64-bar window produces one 512-dimensional embedding. Source missingness stays
distinct from artificial masking. Never refit normalization or update the teacher.

**Selected training teacher: completed epoch50**, as explicitly chosen by the
user. Phase-two training resolves only
`D:/TradingML/runtimes/bar_gpt/encoder-v4-mlp-balanced-20261113/checkpoint_epoch_050.pt`.
The launcher requires V4 terminal `complete` with all50 epochs, full training and
validation evidence for epoch50, no STOP, and matching completed epoch/phase and
history inside the checkpoint itself. It computes and records the immutable
file SHA in the phase-two teacher/run/checkpoint contract. Encoder and decoder
are loaded strictly from `encoder.*` and `decoder.*` in **that same file**.
`best`, `last`, earlier epochs and other V4 runs are rejected for training; there
is no fallback while epoch50 is unfinished. Optional explicit teacher path/SHA
must match this selection. Omitting both lets training resolve and SHA-pin the
selected final file once complete. This does not wait, launch training, or change
V4 automatically. Explicit older checkpoints remain allowed for bounded
audit/profile compatibility tests only, not phase-two training.

At origin i, use eight disjoint historical 1s windows ending at i, i-64, ...,
i-448. The ninth 1s window contains origins i+1 through i+64 and is **only the
target**. The target and its actual future clocks/references never enter inputs.
The target represents 64 future observed 1s bars, not necessarily 64 wall seconds.
Its local normalization does not identify the future absolute price reference;
latent forecasting alone is not independently invertible absolute-price forecasting.

## Inputs and objective

| Frame | Historical tokens | Required observed history |
| --- | ---: | ---: |
| 1s | 8 | 512 |
| 10s | 4 | 256 |
| 1m | 2 | 128 |
| 1h | 1 | 64 |
| daily | 1 | 64 |

Input `[batch,16,512]`; target `[batch,512]`. Token ordering is timeframe-major,
then oldest-to-newest. Metadata consists of timeframe, window position, causal
window ages and price/activity references relative to the current 1s window.
Only past/current metadata is exposed; the future target has no metadata input.

Initial trainable model: 512-wide, six pre-norm Transformer layers, eight heads,
2048-wide feedforwards, a learned summary token, final LayerNorm, and a small
512->256->512 MLP prediction head. Joint attention covers within- and cross-frame
tokens. Q/K/V and feedforwards mix latent dimensions; dimensions are not candles.
The default `--attention-layout batch-first-sdpa` reuses the shared direct SDPA
block, retaining the same parameters/equations while avoiding MultiheadAttention
sequence-first transpose/copy overhead. `--attention-layout torch` retains the
original implementation for qualification. Backend changes are contract-bound;
floating-point training trajectories are not claimed bit-identical across kernels.

During training drop **20% of the512 latent feature coordinates**, not whole
historical tokens. Each batch draws a fresh uniform random subset of102 or103
coordinates; all samples and all16 windows share that batch's coordinate IDs.
Physically gather only the409/410 retained coordinates before the encoder. A
learned coordinate projection uses only the matching columns of its512x512
weight matrix to produce the512-wide internal hidden state. No masked values,
zero placeholders or learned mask vectors enter the encoder. Metadata stays
unchanged. This saves work in the input projection only; attention still has16
historical tokens plus the summary token, so a20% overall speedup is not assumed.
The revised trainable model has19,453,184 parameters (including the projection).

`--feature-drop-rate` controls this training-only augmentation;20% is provisional,
and0 enables a no-drop ablation. No claim of a representation benefit has been
made without that comparison. Validation and production always use all512
coordinates in all16 windows. `Forecaster.encode_context` requires eval mode and
provides the complete512-dimensional representation for phase three. The
future-target embedding always retains all512 coordinates. The optimized loss is mean
squared error against the detached future V4 embedding. Historical masked tokens
are not reconstruction targets. Later supervised/RL stages can use the summary.
This is rolling causal forecasting, not recursive feeding of predicted tokens.

## Unchanged shards and exact eligibility

Use the existing sealed session pools and selected-block manifests. A block is
4096 consecutive observed 1s origins from one NY04:00-20:00 session. Load the same
64-bar-prefix block slices as the shared shard reader; do not borrow extra history
from another block/session, regenerate data, pad missing history or cross a split.

Compute each coarse latest index with `searchsorted(close_us, origin_us,
side='right')-1`, checking equality against stored indices. Every history window
must contain 64 real bars; all of its closes must be <= the origin. Eligibility
requires available history `[512,256,128,64,64]`. Advance origins by one through
the eligible range, determined from **all five actual indices**, not a guessed
512-origin burn-in. The first 1s eligibility can be origin448 when 63 earlier
bars are available, but coarse history may move the boundary much further right.

Require the entire target inside this block: last eligible index4031; indices
4032..4095 cannot be scored. A block with no eligible origins is reported as such,
never padded. Preserve counts/reasons and distinguish selected stored blocks,
eligible origins and repeated overlapping origin presentations. Original 75/25
block selection does not guarantee 75/25 eligible-origin exposure after burn-in.
Any future rebalance requires an explicit policy, not hidden resampling.

## Execution, identity and recovery

Transfer shared values/masks/UTC clocks/indices to the requested device once per
session under an explicit memory budget. Batch unique 64-bar endpoints through
the teacher; cache its embeddings and local references for that immutable session.
Cache identity binds receipt/window, frame, share basis, tokenizer SHA and
preprocessing contract. Encoding uses fixed pool-relative endpoint tiles, anchored
at the first complete64-bar window. Tile size and precision are checkpoint-bound:
Float32 GEMM can otherwise vary with batch shape after a cache miss or resume.
Tiles may precompute unused/future endpoints, but those vectors never enter inputs
unless the causal plan explicitly selects their completed windows. Reuse unchanged
coarse tokens until their closes change;
all eight 1s windows advance at the next observed origin. Actual timestamps govern
gaps and rollovers. No per-origin ClickHouse or disk queries, and no teacher
gradient graph. Training batches consecutive eligible origins in parallel.

Training is opt-in, uses a new output/run identity, and must not interrupt V4.
Provide bounded audit/profile modes and strict dataset/tokenizer/config identities.
Resume must restore phase-two weights, AdamW, scheduler, all RNGs and exact data
cursor. Save a pending checkpoint on graceful STOP, and preserve epoch/best/last
states. Validation has no token dropout and never changes model/optimizer/RNG
training state. Report MSE, cosine similarity and persistence/unconditional
baselines separately; a low latent error is not price accuracy or trading success.

No full training launch, model promotion, future-reference head or phase-three/four
training is authorized by implementation alone. Native GPU profiling/training
requires a separate resource-safe launch; CPU real-shard checks are allowed.

## Entry point and defaults

Use module `research.bar_gpt.phase2_v1.run_train` or execute `run_train.py` directly.
Audit is the default; it writes only to its new runtime output, never to shards.
Dataset roots must have clean terminal results (`complete`, `inventory_exhausted`
or `capacity_reached`) certifying the exact sealed selected-block export. A failed,
live, partially stopped or empty selected root is rejected. Stored candidate bytes
alone do not certify balanced training exposure; no empty root is silently ignored.
Repeat `--dataset-root` for every completed parent/continuation to be included.
The loader verifies the original 75/25 selected-block counts, selection SHA/seal,
receipt bindings, session year, full payload SHA and causal indices. It does not
open SQLite or query ClickHouse. Paths and exact selection identities are frozen
in the training checkpoint contract; no root is discovered or silently added.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.bar_gpt.phase2_v1.run_train `
  --mode audit --dataset-root D:/TradingML/runtimes/bar_gpt/phase2-ar-sampled-v2 `
  --output D:/TradingML/runtimes/bar_gpt/phase2-v1-audit-NEW --max-blocks 4
```

To additionally audit actual tokenizer outputs, supply `--teacher-checkpoint`
and `--teacher-sha256`. A checkpoint path without its exact SHA is rejected.
`--mode profile` runs only a bounded new-model repeated-batch experiment:
default four blocks,16 origins per block,three steps. It is explicitly not a
full-corpus throughput measurement. CPU is the safe default; CUDA is explicit.

Full training additionally requires `--mode train --execute`, a pinned V4
checkpoint/SHA, and a new runtime output. Audit/profile bounds never truncate
training or validation. Initial schedule defaults are **10 logical epochs**, batch256,
AdamW3e-4, weight decay.01, initial cosine floor5e-5,2000-update one-time warmup, norm clip1,
and patience8 validation non-improvements. These are phase-two defaults awaiting
the launch decision, not inherited optimizer state or changes to phase one.
Defaults: FP32, one CPU thread,256-window tokenizer chunks,512MiB session cache;
CUDA/BF16 is explicit and needs profiling before a heavy launch. There is no
background GPU process or scheduled task created by importing/implementing this.

W&B is **mandatory online for training**, in the shared new **`bargpt-phase2`**
project for phase-two versions. The launcher records `phase2_version` in the run
configuration and names a new run from its new output directory. Online is the
training default; disabled/offline training is rejected. Missing credentials or
initialization failures stop training, with no silent local-only fallback.
Secrets are discovered through the shared environment loader, never copied into
code or manifests. Audit/profile remain W&B-disabled and create no training run.
Exact same-run resume uses
`--resume <same-output/checkpoint_last.pt>`, restoring its run ID/global step.
Native snapshots without Git must pass `--source-revision <committed SHA>`.
`progress.json`, `metrics.jsonl`, `results.json` and `eligibility.jsonl` retain
progress, full-validation metrics and per-block rejection/eligible counts.
Validation additionally reports the current epoch's TRAINING-only mean-target
baseline. No target references or validation statistics are model inputs.

### Decoded validation diagnostics (target-reference-conditioned)

The SHA-pinned V4 checkpoint supplies **both** the frozen encoder and frozen
small MLP decoder. At validation only, decode all 18 feature IDs from (a) the
predicted 512-vector and (b) the actual future-target 512-vector. Both use the
actual next-64-observed-1s window's UTC features and exact Float64 local price and
per-quantity references. These target-only values never enter phase-two input
metadata, its encoder, or its latent-only optimization loss. The second branch
is the V4 reconstruction baseline; neither branch is an independent absolute
price forecast. Phase three consumes learned embeddings, not this decoder.

Errors compare inverse-transformed outputs directly with original raw target
bars on the shard session's common split basis. Use the shared V4 inverse:
prices = reference * exp(output/100), quantities = expm1(output+log1p(reference)).
No clipping, rounding, synthetic bars, sampling or source-invalid targets enter
the metric population. Counts retain overlap between rolling target windows.
Only **1s** is a reconstruction target; input 10s/1m/1h/daily views do not acquire
invented target metrics.

W&B/local metric keys (existing latent metrics remain unchanged):

- `validation_decoded_raw_mae/1s/<channel>` and `_raw_rmse/1s/<channel>`:
  price USD/share, quantities as stored, counts observed events/bar.
- The same metrics under `validation_decoder_baseline_*` use actual target latents.
- Both branches expose source-valid counts, nonfinite/overflow counts and each
  channel's `horizon_01` through `horizon_64` MAE/RMSE/counts/overflows. Horizons
  are observed bars, not wall-clock seconds.
- Price channels only: `validation_decoded_price_mape_pct`,
  `_price_median_ape_pct`, `_price_p90_ape_pct` and `_by_price_band`, plus the
  corresponding decoder-baseline metrics. Compute relative errors **after** the
  inverse in real units against positive raw truth. Shared V4 price bands and
  bounded histogram quantiles apply (approximate median/P90; bin resolution is
  recorded). Empty/overflow-affected aggregates are `null`, not zero.

`--validation-every N` is the single authority for **both** logical epoch length
and full-validation/cosine restart frequency; default **150,000 optimizer updates**.
Each logical epoch ends with exactly one full validation. `--epochs 10` therefore
means at most1,500,000 updates, not ten passes over the4TB dataset. Data continues
from its exact consumed cursor across logical epochs. It wraps to data start only
after a full data pass is exhausted; a data wrap does not restart LR or trigger an
extra validation. Progress/history expose `data_pass` separately from logical epoch.

During a cycle the LR follows an update-based cosine. With initial peak3e-4,
floor5e-5, and `--lr-restart-decay .9`, zero-based cycle c has peak
`3e-4 * .9**c` and floor `5e-5 * .9**c`. Thus the nominal start/peak values are
3e-4,2.7e-4,2.43e-4,... . Scale the floor too, so the cosine shape remains valid
when restart peaks eventually fall below the original floor. The initial2000
updates linearly warm from the initial floor to the initial peak; this startup
warmup does not change the nominal peak used for the .9 restart rule, and is never
repeated at later epochs or on resume. Warmup must be shorter than the cycle.
Validation never advances the LR update clock. The next cycle starts only after
its boundary validation completes. Eight logical-epoch non-improvements in latent
MSE still control the existing patience guard; model/optimizer/moments/RNG are
preserved across LR restarts.

All metrics use the current optimizer step and existing run identity; there is
no backfill. Validation restores Python/NumPy/Torch/CUDA RNG and prior train/eval
mode. Checkpoints preserve consumed cursor, pending validation, last completed
validation step, data-pass count, scheduler counter and history. A crash after
the final optimizer update but before the validation-phase checkpoint resumes
validation before taking another update. `results.json` records every logical
epoch's validation, nominal peak/floor and last update LR. No duplicate periodic
validation or separate interval-results file is generated. Explicit
`--validation-every 0` retains the old full-pass/epoch-cosine mode for comparison;
it is not the new validation-aligned configuration.

The validation policy and shared inverse/decoder/price-metric source hashes are
bound into the run contract. This implementation does not silently upgrade an
older checkpoint's contract or modify any running phase-one job. The bounded
CPU/CUDA profile path includes decoded diagnostics after its compute timing;
it is not a full validation throughput claim.

### Bounded engineering profile

`python -B -m research.bar_gpt.phase2_v1.benchmark --dataset-release <certificate.json>
--output D:/TradingML/runtimes/bar_gpt/<new-profile> --device cpu` compares1/2/4
I/O threads on up to4 certified sample publications, checking hashes and causal
eligibility. The training loader's separate bounded pool is described below.
Three interleaved worker trials after explicit warmup measure warm OS cache;
concurrent load can still affect them. These are not a corpus speed
qualification. CPU-only profiles use no model GPU work. `--device cuda` additionally
profiles128/256/512 tokenizer chunks and FP32/BF16 batches256..4096 using repeated
real embeddings, bounded to120seconds of compute by default. GPU profiling refuses
actual live Python encoder/phase-two training processes (PID+creation), checks GPU
headroom, and requires the completed selected epoch50 teacher. No earlier teacher
is substituted. Disposable model/optimizer updates do not create a training run.
These cache-only compute timings cannot establish epoch duration or full validation
cost. Native profile evidence and proposed runtime settings are retained under
`D:/TradingML/runtimes/bar_gpt/phase2-extra-opt-final-20261114`. The interval now
defaults to150,000updates; hardware timing remains an estimate, not a wall-clock
scheduler. Implementing this schedule does not launch a training run.

### Compact index, prefetch and packed optimizer batches

The all-published reader verifies every sealed index/inventory part SHA and source
HEAD/plan/result binding, then retains compact Polars groups. It does not eagerly
construct/resolve1.58million payload paths. Unit paths/tuples materialize only on
use. Unsafe lexical paths are rejected at index load; resolved filesystem
containment is checked before opening each used receipt, including symlink escapes.
Split-aware iteration skips train metadata during validation without changing
global unit indices or checkpoint cursors. Raw files retain per-use hashes and
causal/geometry checks.

`--io-workers` (default4, range0..8) prefetches verified session pools on CPU only,
in source order. `--prefetch-mib` (default2048) bounds aggregate sealed payload
sizes conservatively, including current/in-flight readers and contiguous copies;
an oversized unit fails explicitly instead of skipping/truncating. Zero workers
provides the serial comparison. No CUDA operations execute in reader threads.

Consecutive eligible examples are packed across source blocks/publications to
fill the configured optimizer batch. Context/targets stay inside each example's
original session/block; raw bars are never concatenated across session boundaries.
No example is dropped, duplicated or reordered. Only the epoch's final optimizer
batch may be short. The checkpoint cursor points to the exact consumed position
even if a batch stops partway into another publication. Pending read-ahead is
recomputed on resume, never treated as trained. Target-only validation evidence
is packed with exactly the same examples. `--no-pack-batches` provides the old
per-block minibatch boundary for runtime comparisons. Packing/prefetch settings
are recorded in the run contract and cannot silently change on resume.

`--gpu-prefetch` enables one bounded preparation worker on a separate CUDA stream.
It prepares only frozen teacher embeddings and metadata; random coordinate masks,
gradients, nonfinite checks, clipping and updates stay on the training thread.
CUDA events and allocator stream records protect every returned tensor, including
target-only validation evidence. The next-batch queue is bounded to one future.
Worker exceptions propagate, including failures discovered during early close;
STOP/interrupt joins the worker and discards only untrained read-ahead. The exact
consumed cursor remains authoritative for resume. CPU operation rejects the flag.

`--optimizer-backend fused` selects native CUDA fused AdamW with unchanged
learning rate/betas/epsilon/weight decay; `auto` retains PyTorch's default. It is
recorded in the run contract. The nonfinite-gradient check still occurs before
every update. No CUDA graphs or clock approximations are enabled.

The tokenizer caches the shared transform's UTC fields once per immutable
publication/frame pool. It still resets each window's relative positions and
first-gap validity, and restores gaps across cache-tile boundaries from the
actual UTC clocks. The original fixed teacher tiles and normalization stay
unchanged. Packed token/reference banks replace per-frame minibatch transfers
with one bounded pinned index transfer; token order, targets and causal checks
are unchanged. The extra banks/cache are included in the device-cache budget.
Any engineering revision still changes the implementation fingerprint: an old
checkpoint must be explicitly prepared and full-state/hash/parity verified
before resume, never accepted through a compatibility bypass. Preserve its
immutable parent and original W&B ID/name/step when preparing that entry.

Write a `STOP` in the run output to drain at an update boundary and checkpoint.
No launcher clears STOP. An intentional patience stop also writes STOP. A hard
interrupt retains the last committed checkpoint; it does not publish an in-flight
optimizer state. Checkpoints retain exact root/session/block/eligible-origin
cursors, Python/NumPy/Torch/CUDA RNG, optimizer, scheduler, history and metrics.
Every save is fsynced, CPU-reloaded and independently SHA-read back; epoch files
are immutable. No optimizer/scheduler/RNG state from V4 is trained or changed.
