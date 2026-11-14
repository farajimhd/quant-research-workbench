# Phase-two v1: frozen-tokenizer latent forecasting

Design agreed November 14, 2026. Model version: `bargpt-phase2-latent-v1`.
This is distinct from the existing `bargpt-phase2-ar-sampled-v2` **shard format**.
No extractor, source bars, active training job or shard is changed by this model.

## Frozen teacher and target

Pin an explicit immutable phase-one V4 checkpoint by SHA256. Its encoder is
frozen, in evaluation mode, and receives all source-valid market columns through
the exact V4 local-reference preprocessing and nine UTC features. One real
64-bar window produces one 512-dimensional embedding. Source missingness stays
distinct from artificial masking. Never refit normalization or update the teacher.

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

During training mask whole historical token contents at 20% on average (three or
four of 16, randomized without bias). Retain metadata; masked content is replaced
by a learned mask vector. Evaluation uses all 16. The only optimized loss is mean
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
training or validation. Initial schedule defaults are **10 epochs**, batch256,
AdamW3e-4, weight decay.01, epoch-cosine floor5e-5,1000-update warmup, norm clip1,
and patience8 validation non-improvements. These are phase-two defaults awaiting
the launch decision, not inherited optimizer state or changes to phase one.
Defaults: FP32, one CPU thread,256-window tokenizer chunks,512MiB session cache;
CUDA/BF16 is explicit and needs profiling before a heavy launch. There is no
background GPU process or scheduled task created by importing/implementing this.

W&B is disabled by default. `--wandb-mode online` creates a **new** run under
`bargpt-phase2-latent-v1` named from the new output. Exact same-run resume uses
`--resume <same-output/checkpoint_last.pt>`, restoring its run ID/global step.
Native snapshots without Git must pass `--source-revision <committed SHA>`.
`progress.json`, `metrics.jsonl`, `results.json` and `eligibility.jsonl` retain
progress, full-validation metrics and per-block rejection/eligible counts.
Validation additionally reports the current epoch's TRAINING-only mean-target
baseline. No target references or validation statistics are model inputs.

Write a `STOP` in the run output to drain at an update boundary and checkpoint.
No launcher clears STOP. An intentional patience stop also writes STOP. A hard
interrupt retains the last committed checkpoint; it does not publish an in-flight
optimizer state. Checkpoints retain exact root/session/block/eligible-origin
cursors, Python/NumPy/Torch/CUDA RNG, optimizer, scheduler, history and metrics.
Every save is fsynced, CPU-reloaded and independently SHA-read back; epoch files
are immutable. No optimizer/scheduler/RNG state from V4 is trained or changed.
