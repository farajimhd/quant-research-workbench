# Masked Event Model v8

v8 is the event-token masked autoencoder variant for compact market-event
samples. It is version-local: model, masking, loss, progress, training, and
profiling code live under `research/masked_event_model/v8`.

The main change is masking at the event-token level:

```text
header_uint8: [B, 14]       always visible
events_uint8: [B, 128, 16]  fixed 70% event tokens removed from the encoder
```

At training time the encoder sees:

```text
[CLS] + header_token + visible_event_tokens
```

v8 is the fixed-mask ablation of v6. Every batch masks exactly 70% of the 128
event tokens, subject only to the existing `min_masked_events` clamp. Header
bytes are not removed; they can receive low-rate bit corruption. Visible event
bytes can also receive low-rate bit corruption for robustness. This keeps the
architecture, objective, optimizer, scheduler, and data path aligned with v6
while removing random mask-ratio variation from the training regime.

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

Loss is binary cross entropy with logits over masked event bits only. v8 weights
that BCE with a fixed semantic `[16, 8]` bit-weight matrix: numeric bytes use
little-endian bit significance `[1, 2, ..., 128]`, while packed/categorical
bytes such as event flags, exchanges, and conditions use the maximum weight for
every bit. The weighted objective is normalized by the semantic weight mass
actually present in the batch, not by raw batch size, so the loss scale stays
close to ordinary BCE and does not grow with the number of masked events. The
unweighted BCE is still logged as `pretrain/loss_event_unweighted` for
comparison with older runs. Production
embedding uses the explicit `encode(...)` path, which sees the full unmasked
header and all 128 events:

```text
chunk_embedding: [B, embedding_dim]
event_embeddings: [B, 128, embedding_dim]
```

## Defaults

```text
data source: sample_cache
sample cache root: D:\market-data\prepared\event_sample_cache
events per chunk: 128
event mask ratio: 0.70
event mask schedule: fixed 70%
header bit corruption: 20% of samples, 5% of header bits
visible event bit corruption: 30% of samples, 20% of visible event bits
AMP dtype: auto, preferring BF16 on supported CUDA devices
FP16 GradScaler cap: 2048 with growth interval 10000
W&B project: June2026-event-token-mae-v8-fixed-mask
```

## Profiling

One-shard profiling:

```powershell
python research\masked_event_model\v8\run_profile_one_shard.py --steps 50 --batch-size 4096 --fresh-start
```

Model-size sweep:

```powershell
python research\masked_event_model\v8\run_model_size_sweep.py --steps 200 --fresh-start
```

The sweep includes practical combinations across embedding sizes 32 and 64,
batch sizes 1024/2048/4096/8192, and the tiny/small/medium/high model presets.

## Limited Real Training

```powershell
python research\masked_event_model\v8\train_medium_bit_limited_shards.py --fresh-start
```

This trains over 10 sample-cache shards and uses a shuffled 5% slice of the next
shard for validation. Each epoch is one pass over the selected train shards.

The final long-run launcher for the masked-query decoder path is:

```powershell
python research\masked_event_model\v8\train_10shard_long.py --fresh-start
```

Defaults are medium `d_model=256`, `embedding_dim=32`, `batch_size=4096`, 10
training shards, 10 epochs, one cosine restart per shard, validation at each
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
