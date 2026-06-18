# Masked Event Model v14

v14 is based on v13 and keeps the same fixed-mask, unweighted-loss,
masked-query cross-attention decoder setup. Its only intentional architecture
change relative to v13 is removing the extra activation and normalization from
the decoder FFN output projection. The bottleneck and decoder-memory bridge
activations remain in place. It is version-local: model, masking, loss,
progress, training, and profiling code live under
`research/masked_event_model/v14`.

The main change is masking at the event-token level:

```text
header_uint8: [B, 14]       always visible
events_uint8: [B, 128, 16]  fixed 70% event tokens removed from the encoder
```

At training time the encoder sees:

```text
[CLS] + header_token + visible_event_tokens
```

v14 is the bridge-activation ablation of v13. Every batch masks exactly 70% of
the 128 event tokens, subject only to the existing `min_masked_events` clamp.
Header bytes are not removed; they can receive low-rate bit corruption. Visible
event bytes can also receive low-rate bit corruption for robustness.

All encoded tokens are projected through the exported chunk embedding bottleneck
before the decoder sees the representation. The chunk embedding is the mean of
the projected CLS, header, and visible event tokens rather than only the CLS
token:

```text
encoded tokens [B, token_count, d_model]
  -> Linear + GELU + LayerNorm [B, token_count, embedding_dim]
  -> mean over token_count [B, embedding_dim]
  -> Linear + GELU + LayerNorm [B, d_model]
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

Unlike v13, the decoder FFN does not apply an extra GELU or LayerNorm after its
contraction back to `d_model`; that path matches v9's cheaper decoder FFN. The
final `16 x 8` bit projection intentionally remains linear because
BCE-with-logits expects unconstrained raw logits.

Loss is ordinary binary cross entropy with logits over masked event bits only.
v14 does not multiply semantic bit weights into the objective. The training loss
uses the PyTorch default mean reduction over all masked event bits. Semantic
metrics can still be emitted for diagnostics, but they are not used for
backpropagation. Production
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
W&B project: June2026-event-token-mae-v14-bridge-activations
```

## Profiling

One-shard profiling:

```powershell
python research\masked_event_model\v14\run_profile_one_shard.py --steps 50 --batch-size 4096 --fresh-start
```

Model-size sweep:

```powershell
python research\masked_event_model\v14\run_model_size_sweep.py --steps 200 --fresh-start
```

The sweep includes practical combinations across embedding sizes 32 and 64,
batch sizes 1024/2048/4096/8192, and the tiny/small/medium/high model presets.

## Limited Real Training

```powershell
python research\masked_event_model\v14\train_medium_bit_limited_shards.py --fresh-start
```

This trains over 10 sample-cache shards and uses a shuffled 5% slice of the next
shard for validation. Each epoch is one pass over the selected train shards.

The final long-run launcher for the masked-query decoder path is:

```powershell
python research\masked_event_model\v14\train_10shard_long.py --fresh-start
```

Defaults are medium `d_model=256`, `embedding_dim=32`, `batch_size=4096`, 10
training shards, 4 epochs, one cosine cycle per selected-shard epoch,
validation at each shard boundary, async latest checkpoints every 25 steps, and no shard
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
