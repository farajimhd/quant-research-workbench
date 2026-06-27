# Masked Event Model

This folder contains event-token masked autoencoder experiments for compact
market microstructure chunks. Each version is self-contained under `vN/` and
keeps its own model, masking, loss, progress, training, profiling, and notebook
code. Stable shared engineering utilities live in `research/mlops`.

The current production-facing pretraining line uses compact event sample-cache
records:

```text
header_uint8: [B, 14]
events_uint8: [B, 128, 16]
```

The header is visible to the encoder. The event tensor contains 128 compact
event tokens, each represented by 16 packed bytes. Recent versions unpack bytes
to bits inside the network and reconstruct only masked event tokens:

```text
event_bit_logits: [B, masked_events, 16, 8]
```

The intended reusable artifact is the encoder. Decoder layers are disposable
pretraining scaffolding and should not be used by downstream temporal or
supervised models unless a version explicitly says otherwise.

## Current Architecture Pattern

The current event-token MAE family uses this high-level path:

```text
header_uint8 + visible event_uint8
  -> bit/token projections
  -> CLS + header + visible event tokens
  -> transformer encoder
  -> chunk embedding bottleneck
  -> lightweight masked-event decoder
  -> event bit logits for masked events only
```

The production path uses `encode(...)`:

```text
header_uint8 + all 128 events_uint8
  -> encoder
  -> chunk_embedding
```

For fixed-vector versions, `chunk_embedding` has shape `[B, embedding_dim]`.
Tokenwise ablations return token/event feature grids and are noted separately
below.

## Version Differences

| Version | Core Data/Input | Bottleneck | Decoder | Loss | Main Purpose |
| --- | --- | --- | --- | --- | --- |
| v2 | v22 quote/trade event chunks | Two-stream quote/trade encoder | Sparse masked reconstruction heads | Type-specific masked losses | First standalone masked event model over v22 chunk tensors. |
| v3 | v22 quote/trade event chunks | Wider internal encoder with 256-dim exported embedding | Sparse masked reconstruction heads | Type-specific masked losses | Tests larger internal width while keeping compact embeddings. |
| v4 | Compact sample-cache bytes/bits | Byte/bit chunk embedding | Chunked masked-byte decoder | BCE on masked byte bits | First compact byte-event training path and sample-cache loader. |
| v5 | Compact event tokens, bit input | CLS-based fixed embedding | Masked-query cross-attention | BCE-with-logits mean | First event-token MAE with whole-event masking. |
| v6 | Compact event tokens, bit input | Mean pooling, random mask ratios | Masked-query cross-attention | Unweighted BCE mean after later update | Random mask-ratio robustness test; slower and noisier. |
| v7 | Compact event tokens, bit input | Learned attention pooling, fixed 70% mask | Masked-query cross-attention | Unweighted BCE mean after later update | Tests learned pooling versus mean pooling. |
| v8 | Compact event tokens, bit input | Mean pooling, fixed 70% mask | Masked-query cross-attention | Semantic-weighted BCE originally | Fixed-mask weighted-loss baseline; exposed weighted-loss/AMP issues. |
| v9 | Compact event tokens, bit input | Mean pooling, fixed 70% mask | Masked-query cross-attention | Ordinary BCE mean | Loss ablation of v8; regular mean BCE optimized better. |
| v10 | Compact event tokens, bit input | Tokenwise retained-token feature grid | Cross-attention from flattened memory | Ordinary BCE mean | Tests tokenwise embedding geometry with one feature per token. |
| v11 | Compact event tokens, bit input | Tokenwise retained-token feature grid | Multi-token memory from token features | Ordinary BCE mean | Tests tokenwise memory with two compact features. |
| v12 | Compact event tokens, bit input | Mean pooling fixed embedding | Per-masked-event MLP | Ordinary BCE mean | Major speed/quality baseline; fast decoder and stable learning. |
| v13 | Compact event tokens, bit input | v9 plus extra activations | Cross-attention decoder with extra nonlinearities | Ordinary BCE mean | Tests added nonlinearities after bridge/projection layers. |
| v14 | Compact event tokens, bit input | v13 bridge activations | Cheaper v9-like decoder FFN | Ordinary BCE mean | Ablates decoder FFN nonlinearities from v13. |
| v15 | Compact event tokens, byte input | Mean pooling fixed embedding | Per-masked-event MLP | Ordinary BCE mean | Tests byte-zscore input instead of bit input. |
| v16 | Compact event tokens, bit input | Tokenwise v11-style event features | Per-masked-event MLP | Ordinary BCE mean | Tests tokenwise bottleneck on the fast v12 decoder path. |
| v17 | Compact event tokens, bit input | Max pooling fixed embedding | Per-masked-event MLP | Ordinary BCE mean | v12 with max pooling instead of mean pooling. |
| v18 | Compact event tokens, bit input | Perceiver-style latent pooling | Per-masked-event MLP | Ordinary BCE mean | Tests learned latent cross-attention pooling. |
| v19 | Compact event tokens, bit input | Mean + max + last/header/CLS summaries | Per-masked-event MLP | Ordinary BCE mean | Tests cheap richer summaries before fixed embedding. |
| v20 | Compact event tokens, bit input | Fixed-grid chunk bottleneck | Per-masked-event MLP | Ordinary BCE mean | Current full-pretrain candidate: v12 speed with fixed semantic slots. |
| v21 | Compact event tokens, bit input | Grouped semantic bottleneck merged to one vector | Per-masked-event MLP | Ordinary BCE mean | Tests CLS/header/eight event-group branches before final merge. |
| v22 | Compact event tokens, bit input | Ten branch outputs concatenated to wider embedding | Per-masked-event MLP | Ordinary BCE mean | Tests branch-local nonlinear capacity before concatenation. |
| v24 | Compact event tokens, bit input | v20 fixed-grid bottleneck | Residual MLP decoder | Ordinary BCE mean | Tests whether a stronger residual decoder improves downstream signal. |
| v25 | Compact event tokens, bit input | Grouped semantic bottleneck merged to one vector | Per-masked-event MLP + header decoder | Ordinary BCE mean + header BCE | Adds header reconstruction to force anchor/count/timing information into the exported embedding. |
| v26 | Compact event tokens, bit input | Ten exported branch tokens `[B,10,Z]` | Branch-token cross-attention event decoder + header decoder | Ordinary BCE mean + header BCE | Tests preserving semantic branch tokens for both reconstruction and downstream probing. |

## Experiment Log

### v2-v3: quote/trade chunk MAE

These versions used the earlier v22 quote/trade chunk representation. They were
useful for validating the two-encoder idea and z-scored numeric training, but
the representation was not compact enough for the later large-scale plan. v3
kept the v2 training loop while testing larger internal width and a projected
embedding bottleneck.

### v4: compact byte-event sample cache

v4 moved training onto compact sample-cache records and introduced the core
engineering that later versions reused: shard loading, Rich progress, W&B,
profiling, async checkpointing, validation batches, and encode-only profiling.
It also established that large batches are feasible only when the decoder and
metrics are carefully controlled.

### v5-v9: event-token MAE and loss/masking ablations

v5 introduced whole-event masking and a reusable encoder bottleneck. v6/v7/v8
tested random mask ratios, learned pooling, fixed-mask ablations, and semantic bit weighting. The
weighted objective looked appealing semantically but created optimization and
stability problems; regular unweighted BCE mean in v9 was cleaner and learned
better in the observed runs. Fixed 70% masking was also easier to compare than
mixed mask ratios and was faster for long runs.

### v10-v11 and v16: tokenwise bottleneck attempts

These versions kept event/token feature grids instead of collapsing to a single
fixed vector. The idea is attractive for downstream temporal tasks, but the
observed early behavior was weaker than the v12/v20 fixed-vector family. v16 in
particular did not learn strongly enough in the 10-shard runs to become the
main pretraining path.

### v12: fast MLP decoder baseline

v12 was the first version that combined stable learning with high throughput.
Replacing the masked-query attention decoder with a per-masked-event MLP made
training roughly twice as fast in practical runs while keeping the encoder on
the reconstruction path. This became the base for later pooling and bottleneck
experiments.

### v13-v14: activation placement

Adding activations after several linear bridge layers improved early learning
in some profiling, but decoder-side nonlinearities slowed the run and could
hurt the shard-to-shard objective. v14 removed the decoder FFN activation while
retaining bridge activations, giving a speed profile closer to v9, but v12
remained the stronger speed/learning baseline.

### v15: byte-zscore input

v15 tested whether feeding normalized bytes instead of expanded bits could save
model work. It is a useful ablation for input representation, but the current
main line stayed with bit input because the bit objective is directly aligned
with the packed event representation.

### v17-v19: pooling alternatives

v17 tested max pooling, v18 tested Perceiver-style latent pooling, and v19
tested cheap summary concatenation. v18 was more expressive but slower and did
not reduce loss as strongly as v12 early in training. These experiments led to
the v20 fixed-grid bottleneck: preserve positional slot semantics without
adding a heavy learned pooling mechanism.

### v20: fixed-grid bottleneck

v20 keeps the fast v12 decoder and fixed 70% event masking, but replaces
mean-only pooling with a fixed semantic grid before the final embedding. The
encoder output is scattered into 130 slots:

```text
slot 0: CLS
slot 1: header
slot 2..129: event positions 0..127
```

Masked event slots are zero. Visible event slots receive the encoded token at
the correct original event index. The grid is then flattened and projected to
`[B, embedding_dim]`. This makes the bottleneck see consistent slot semantics
in training and production while still keeping the encoder compute sparse
during MAE training.

## Lessons Learned

- The compact event representation makes large-batch pretraining practical, but
  the data path has to be shard-oriented. Querying ClickHouse online per batch
  was too slow for training throughput.
- Whole-event masking is cleaner than byte-level masking for the encoder. It
  avoids mixing partial feature fragments and lets the encoder process fewer
  tokens during pretraining.
- The decoder must not receive masked event bytes. It should receive only the
  exported bottleneck plus positional information needed to reconstruct the
  masked event locations.
- Weighted semantic bit losses were less robust than expected. Regular
  BCE-with-logits mean became the default because it optimized better and
  avoided scale/AMP instability.
- BF16 is the preferred training precision on the workstation GPU. FP16 needed
  scaler guardrails; BF16 avoided the worst scaler behavior while staying fast.
- Decoder complexity matters. Since the decoder is discarded after pretraining,
  a cheap decoder is preferred unless a heavier decoder demonstrably improves
  downstream embeddings.
- Shard boundaries can shift loss distribution. Long-run comparisons should
  evaluate at shard boundaries and use validation batches sampled from
  validation shards, not only step-level training loss.
- `torch.compile`, async checkpointing, sparse metrics, and no shard interleave
  were important engineering choices for stable throughput.
- The embedding API must be explicit. Downstream models should be able to load
  the encoder and call `encode(...)` without depending on decoder modules.

## Main Full-Pretraining Candidate

The v20 full-pretraining experiment started at `2026-06-19T16:13:21`
according to the first logged training metric.

```text
run name: v20-fullpretrain-sharddecay-fixedmask070-emb32-bs8192-3epochs
experiment start: 2026-06-19T16:13:21
W&B project: June2026-event-token-mae-full
train cache: D:\market-data\prepared\event_sample_cache\cache_20260611_195259
validation cache: D:\market-data\prepared\event_sample_cache\cache_20260617_112833
batch size: 8192
epochs: 3
planned steps: 390,528
shards per epoch: 129
events per chunk: 128
masked events: 90 per sample, 70.3125% actual mask ratio
visible events: 38 per sample
input representation: bit
d_model: 256
encoder layers: 10
heads: 8
embedding_dim: 32
decoder: per-masked-event MLP
decoder precision: active AMP dtype, no forced FP32 by default
AMP dtype: bf16
optimizer: AdamW
base LR: 1e-3
scheduler: shard_decay_cosine
eta_min: 1e-6
epoch_decay_ratio: 0.80
shard_decay_fraction: 0.60
grad clip norm: 1.0
torch.compile: enabled
validation: one shuffled full batch from each configured validation shard
checkpointing: async latest, best validation, and archive checkpoints
```

### Current Results Snapshot

This snapshot was read from the workstation runtime metrics before the run had
finished and should be replaced with the final summary after completion.

```text
runtime: \\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\masked_event_model\v20\pretrain\v20-fullpretrain-sharddecay-fixedmask070-emb32-bs8192-3epochs
first logged metric: 2026-06-19T16:13:21, step 10
latest parsed metric: 2026-06-21T11:03:57, step 327,030
epoch progress: epoch 3, 51.22%
samples seen total: 2,679,029,760
latest train loss_total: 0.226014
latest train grad_norm: 0.012803
latest train LR: 1.78888e-4
recent train loss mean, last 200 logged rows: 0.227436
recent train samples/sec mean, last 200 logged rows: 46,415
latest shard-end validation step: 326,457
latest validation loss_total: 0.229897
latest validation bit accuracy: 88.08%
latest validation byte exact accuracy: 62.93%
latest validation balanced bit accuracy: 80.96%
```

The run appears numerically stable at the snapshot point: no AMP skips were
reported in the latest row, recent gradient norms were small, and the latest
training and validation losses were close. Final interpretation should wait for
the complete epoch-3 metrics and the final checkpoint.

## Common Commands

Run the current full-pretraining launcher:

```powershell
python research\masked_event_model\v20\train_full_pretrain.py --fresh-start
```

Run v20 10-shard training:

```powershell
python research\masked_event_model\v20\train_10shard_long.py --fresh-start
```

Run a one-shard profile:

```powershell
python research\masked_event_model\v20\run_profile_one_shard.py --steps 50 --batch-size 8192 --fresh-start
```

Run a smoke test:

```powershell
python research\masked_event_model\v20\test_smoke.py
```

Older versions keep the same launcher pattern under their own `vN/` folders.
