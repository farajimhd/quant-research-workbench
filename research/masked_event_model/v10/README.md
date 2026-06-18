# Masked Event Model v10

v10 is the tokenwise-embedding ablation of v9 for compact market-event samples.
It is version-local: model, masking, loss, progress, training, and profiling
code live under `research/masked_event_model/v10`.

The masking and objective stay aligned with v9:

```text
header_uint8: [B, 14]       always visible
events_uint8: [B, 128, 16]  fixed 70% event tokens removed from the encoder
loss: ordinary BCE-with-logits mean over masked event bits only
```

At training time the encoder sees:

```text
[CLS] + header_token + visible_event_tokens
```

The v10 change is the exported bottleneck geometry. v9 projected every encoded
token to `embedding_dim` and mean-pooled the token axis into `[B, embedding_dim]`.
v10 keeps the token axis and adds a second projection to compact per-token
features:

```text
encoded tokens [B, token_count, d_model]
  -> to_embedding [B, token_count, embedding_dim]
  -> to_event_features [B, token_count, event_embedding_features]
  -> embedding_to_decoder [B, token_count, d_model]
  -> decoder memory [B, token_count, d_model]
```

The decoder uses learned queries for the removed event positions. Each masked
query is the learned mask token plus the masked event position embedding, and it
cross-attends only to the compact tokenwise decoder memory. It does not see raw
masked event bytes. The decoder predicts only the removed event bytes:

```text
event_bit_logits: [B, masked_events, 16, 8]
```

Production embeddings use the explicit `encode(...)` path, which sees the full
unmasked header and all 128 events:

```text
training chunk_embedding: [B, 2 + visible_events, event_embedding_features]
production chunk_embedding / event_embeddings: [B, 128, event_embedding_features]
```

The default starts with `event_embedding_features=1` so each retained token
exports one compact scalar feature while the intermediate projection remains
`embedding_dim=32`.

## Defaults

```text
data source: sample_cache
sample cache root: D:\market-data\prepared\event_sample_cache
events per chunk: 128
event mask ratio: 0.70
event mask schedule: fixed 70%
embedding_dim: 32
event_embedding_features: 1
header bit corruption: 20% of samples, 5% of header bits
visible event bit corruption: 30% of samples, 20% of visible event bits
AMP dtype: auto, preferring BF16 on supported CUDA devices
FP16 GradScaler cap: 2048 with growth interval 10000
W&B project: June2026-event-token-mae-v10-tokenwise-embedding
```

## Profiling

One-shard profiling:

```powershell
python research\masked_event_model\v10\run_profile_one_shard.py --steps 50 --batch-size 4096 --fresh-start
```

Model-size sweep:

```powershell
python research\masked_event_model\v10\run_model_size_sweep.py --steps 200 --fresh-start
```

The sweep includes practical combinations across embedding sizes, final
event-feature counts, batch sizes, and the tiny/small/medium/high model presets.

## Limited Real Training

```powershell
python research\masked_event_model\v10\train_medium_bit_limited_shards.py --fresh-start
```

This trains over 10 sample-cache shards and uses a shuffled 5% slice of the next
shard for validation. Each epoch is one pass over the selected train shards.

The final long-run launcher for the tokenwise bottleneck path is:

```powershell
python research\masked_event_model\v10\train_10shard_long.py --fresh-start
```

Defaults are medium `d_model=256`, `embedding_dim=32`,
`event_embedding_features=1`, `batch_size=4096`, 10 training shards, 4 epochs,
one cosine cycle per selected-shard epoch, validation at each shard boundary,
async latest checkpoints every 25 steps, and no shard interleaving. The launcher
prints the equivalent low-level trainer command before starting and accepts
direct overrides for model size, embedding feature count, batch size, shard
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
