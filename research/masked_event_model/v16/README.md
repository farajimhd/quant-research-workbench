# Masked Event Model v16

v16 is the `v12` fast per-masked-event MLP decoder experiment with one focused
architecture change: the exported chunk embedding is token-wise, following the
`v11` idea, instead of mean-pooled into one vector.

The baseline pieces intentionally stay the same as `v12`:

```text
input representation: bit
masking: fixed 70% event-token removal
loss: ordinary BCE-with-logits mean over masked event bits
decoder: per-masked-event MLP, no decoder self-attention/cross-attention
training defaults: BF16-capable AMP, torch.compile enabled, no shard interleave
```

## Bottleneck

At training time the encoder sees:

```text
[CLS] + header_token + visible_event_tokens
```

With the default 128 events and 70% mask ratio:

```text
masked events: 90
visible events: 38
encoder token count: 1 CLS + 1 header + 38 visible events = 40
```

The reusable bottleneck is:

```text
encoded tokens [B, token_count, d_model]
  -> token-wise projection
chunk_embedding [B, token_count, event_embedding_features]
```

Defaults:

```text
event_embedding_features: 1
decoder_bottleneck_tokens: 40
```

Production `encode(...)` does not mask and returns the richer token-wise tensor:

```text
chunk_embedding: [B, 130, 1]  # CLS + header + 128 events
encode_events:  [B, 128, 1]  # event-only view
```

The decoder is still disposable. During pretraining it flattens the fixed
training bottleneck:

```text
chunk_embedding [B, 40, 1]
  -> scalar-safe normalization  # Identity when event_embedding_features = 1
  -> flatten [B, 40]
  -> Linear/GELU/LayerNorm [B, d_model]
  + masked event position embedding [B, 90, d_model]
  -> MLP
  -> event_bit_logits [B, 90, 16, 8]
```

This keeps the fast `v12` decoder family while testing whether downstream tasks
benefit from event-preserving encoder output.

The scalar-safe normalization matters for the default one-feature bottleneck:
`LayerNorm(1)` would collapse every token feature to zero and prevent the
decoder from using encoder information. Multi-feature bottlenecks still use
feature-wise LayerNorm.

## Default 10-Shard Training

```powershell
python D:\TradingML\codes\masked_event_model\v16\research\masked_event_model\v16\train_10shard_long.py --fresh-start
```

Equivalent explicit command:

```powershell
python D:\TradingML\codes\masked_event_model\v16\research\masked_event_model\v16\train_10shard_long.py --fresh-start --run-name v16-v12mlp-v11tokenemb-f1-bs8192-10shards --batch-size 8192 --event-embedding-features 1 --decoder-bottleneck-tokens 40 --amp-dtype bf16
```

The default W&B project is:

```text
June2026-event-token-mae-v16-token-bottleneck
```

## Wait For v12, Then Train v16

When another terminal is already training `v12`, use this foreground handoff
launcher to wait and then start v16 in the same terminal:

```powershell
python D:\TradingML\codes\masked_event_model\v16\research\masked_event_model\v16\run_after_v12.py
```

The launcher watches for a process whose command line contains
`masked_event_model`, `v12`, and `train_10shard_long`. When that process exits,
the launcher uses `os.execv(...)` to replace itself with v16
`train_10shard_long.py`; this avoids log-capturing subprocess behavior, so Rich
renders in the terminal as if the training command were launched directly.

Default handoff target:

```text
W&B project: June2026-event-token-mae
run name: v16-v12mlp-v11tokenemb-f1-bs8192-10shards-after-v12
AMP dtype: bf16
fresh start: true
```

Extra arguments after the launcher options are forwarded to
`train_10shard_long.py`.

## Important Assumptions

`decoder_bottleneck_tokens` must match the training token count produced by the
masking setup. For 128 events and fixed 70% masking this is 40. If the event
mask ratio or events per chunk changes, update `decoder_bottleneck_tokens`
accordingly.

`embedding_dim` remains accepted by launchers for compatibility with older
scripts, but it is not the exported representation width in v16. Use
`event_embedding_features` for the token-wise bottleneck width.
