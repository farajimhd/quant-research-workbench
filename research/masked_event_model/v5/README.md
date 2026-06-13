# Masked Event Model v5

v5 is the event-token masked autoencoder variant for compact market-event
samples. It is version-local: model, masking, loss, progress, training, and
profiling code live under `research/masked_event_model/v5`.

The main change is masking at the event-token level:

```text
header_uint8: [B, 14]       always visible
events_uint8: [B, 128, 16]  70% of event tokens removed from the encoder
```

At training time the encoder sees:

```text
[CLS] + header_token + visible_event_tokens
```

With the default 70% event mask and 128 events, the encoder sees 38 event
tokens instead of all 128. Header bytes are not removed; they can receive
low-rate bit corruption. Visible event bytes can also receive low-rate bit
corruption for robustness.

The decoder uses learned queries for the removed event positions. Each masked
query is the learned mask token plus the masked event position embedding, and it
cross-attends to the encoded `[CLS] + header + visible_event_tokens` memory.
It does not build or process a full 128-event decoder sequence. The decoder
predicts only the removed event bytes:

```text
event_bit_logits: [B, masked_events, 16, 8]
```

Loss is binary cross entropy with logits over masked event bits only. Production
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
header bit corruption: 20% of samples, 5% of header bits
visible event bit corruption: 30% of samples, 20% of visible event bits
W&B project: June2026-event-token-mae-v5
```

## Profiling

One-shard profiling:

```powershell
python research\masked_event_model\v5\run_profile_one_shard.py --steps 50 --batch-size 4096 --fresh-start
```

Model-size sweep:

```powershell
python research\masked_event_model\v5\run_model_size_sweep.py --steps 200 --fresh-start
```

The sweep includes practical combinations across embedding sizes 32 and 64,
batch sizes 1024/2048/4096/8192, and the tiny/small/medium/high model presets.

## Limited Real Training

```powershell
python research\masked_event_model\v5\train_medium_bit_limited_shards.py --fresh-start
```

This trains over 10 sample-cache shards and uses a shuffled 5% slice of the next
shard for validation. Each epoch is one pass over the selected train shards.

The final long-run launcher for the masked-query decoder path is:

```powershell
python research\masked_event_model\v5\train_10shard_long.py --fresh-start
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
artifacts/model/model_summary_torchinfo.txt
artifacts/model/model_architecture_torchview.png
artifacts/model/model_architecture_torchview.svg
```

If optional graph packages are unavailable, matching `*_error.txt` files are
written instead of silently skipping model artifacts.
