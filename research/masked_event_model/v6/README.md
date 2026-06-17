# Masked Event Model v6

v6 is the event-token masked autoencoder variant for compact market-event
samples. It is version-local: model, masking, loss, progress, training, and
profiling code live under `research/masked_event_model/v6`.

The main change is masking at the event-token level:

```text
header_uint8: [B, 14]       always visible
events_uint8: [B, 128, 16]  mixed-ratio event tokens removed from the encoder
```

At training time the encoder sees:

```text
[CLS] + header_token + visible_event_tokens
```

With the default mixed mask schedule, 70% of batches sample a mask ratio between
50% and 80%, 10% use the zero-mask policy clamped by `min_masked_events`, and
20% sample between 1% and 50%. Header bytes are not removed; they can receive
low-rate bit corruption. Visible event bytes can also receive low-rate bit
corruption for robustness.

All encoded tokens are projected through the exported chunk embedding bottleneck
before the decoder sees the representation. The chunk embedding is the mean of
the projected CLS, header, and visible event tokens rather than only the CLS
token:

```text
encoded tokens [B, token_count, d_model]
  -> to_embedding [B, token_count, embedding_dim]
  -> mean over token_count [B, embedding_dim]
  -> embedding_to_decoder [B, d_model]
  -> decoder memory [B, 1, d_model]
```

This keeps the production chunk embedding on the reconstruction loss path and
prevents the decoder from bypassing the exported `[B, embedding_dim]`
bottleneck. The decoder uses learned queries for the removed event positions.
Each masked query is the learned mask token plus the masked event position
embedding, and it cross-attends only to the single embedding-projected chunk
memory token. It does not build or process a full 128-event decoder sequence.
The decoder predicts only the removed event bytes:

```text
event_bit_logits: [B, masked_events, 16, 8]
```

Loss is binary cross entropy with logits over masked event bits only. v6 weights
that BCE with a fixed semantic `[16, 8]` bit-weight matrix: numeric bytes use
little-endian bit significance `[1, 2, ..., 128]`, while packed/categorical
bytes such as event flags, exchanges, and conditions use the maximum weight for
every bit. The unweighted BCE is still logged as
`pretrain/loss_event_unweighted` for comparison with older runs. Production
embedding uses the explicit `encode(...)` path, which sees the full unmasked
header and all 128 events:

```text
chunk_embedding: [B, embedding_dim]
event_embeddings: [B, 128, embedding_dim]
```

The weighted BCE is normalized by the actual semantic weight mass of the masked
targets. This matters for mixed-ratio masking: normalizing only by batch size
makes the loss scale change with the number of masked events, which destabilizes
long mixed-precision runs. Weight-mass normalization keeps the objective scale
comparable across low-mask and high-mask steps while preserving the semantic
priority of important bits.

AMP defaults to `--amp-dtype auto`. On CUDA devices with BF16 support this uses
BF16 autocast and disables GradScaler, keeping mixed-precision speed without
loss-scale growth. If BF16 is not available, the FP16 fallback uses a bounded
GradScaler (`--amp-max-scale`) so long runs cannot silently grow into repeated
overflow.

## Defaults

```text
data source: sample_cache
sample cache root: D:\market-data\prepared\event_sample_cache
events per chunk: 128
event mask ratio: 0.70
event mask schedule: mixed, high 50-80% for 70% of batches, zero policy for 10%, low 1-50% for 20%
header bit corruption: 20% of samples, 5% of header bits
visible event bit corruption: 30% of samples, 20% of visible event bits
W&B project: June2026-event-token-mae-v6
```

## Profiling

One-shard profiling:

```powershell
python research\masked_event_model\v6\run_profile_one_shard.py --steps 50 --batch-size 4096 --fresh-start
```

Model-size sweep:

```powershell
python research\masked_event_model\v6\run_model_size_sweep.py --steps 200 --fresh-start
```

The sweep includes practical combinations across embedding sizes 32 and 64,
batch sizes 1024/2048/4096/8192, and the tiny/small/medium/high model presets.

## Limited Real Training

```powershell
python research\masked_event_model\v6\train_medium_bit_limited_shards.py --fresh-start
```

This trains over 10 sample-cache shards and uses a shuffled 5% slice of the next
shard for validation. Each epoch is one pass over the selected train shards.

The final long-run launcher for the masked-query decoder path is:

```powershell
python research\masked_event_model\v6\train_10shard_long.py --fresh-start
```

Defaults are medium `d_model=256`, `embedding_dim=32`, `batch_size=4096`, 10
training shards, 4 epochs, one cosine restart per shard, validation at each
shard boundary, async latest checkpoints every 25 steps, and no shard
interleaving. The launcher prints the equivalent low-level trainer command
before starting and accepts direct overrides for model size, batch size, shard
range, validation shard, W&B run name, and warm-start checkpoint.

## Artifacts

Each run writes a single run directory with:

```text
config.json
run_manifest.json
metrics.jsonl
logs/fatal_error.txt
checkpoints/
artifacts/model/model_details.json
artifacts/model/model_parameters.jsonl
artifacts/model/model_summary.txt
artifacts/model/model_summary_torchinfo.txt              # encoder-only production embedding path
artifacts/model/model_architecture_torchview.png         # encoder-only production embedding path
artifacts/model/model_architecture_torchview.svg         # encoder-only production embedding path
artifacts/model/model_summary_training_torchinfo.txt     # masked reconstruction path, logits-only output
```

If optional graph packages are unavailable, matching `*_error.txt` files are
written instead of silently skipping model artifacts.
