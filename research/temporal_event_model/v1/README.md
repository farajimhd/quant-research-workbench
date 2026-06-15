# temporal_event_model v1

First single-ticker temporal predictor over compact unified SIP event chunks.

## Data Contract

Source data comes from ClickHouse:

```text
market_sip_compact.events
market_sip_compact.train_2019_to_2025
market_sip_compact.validation_2026
```

For training, the loader repeatedly:

1. samples one ticker from the train index,
2. samples one 15-day timestamp window inside that ticker's train range,
3. queries all clean unified events for that ticker/window ordered by ordinal,
4. samples one stride from `[16, 32, 64, 128]`,
5. rolls every valid origin into context/target chunks.

For origin event `t`, with `events_per_chunk=128`, `context_chunks=16`, and
stride `s`:

```text
context[0]  = oldest chunk ending at t - 15*s
...
context[15] = current chunk ending at t
target[0]   = next chunk, events t+1 ... t+128
```

Batch shapes:

```text
context_header_uint8: [B, 16, 14]
context_events_uint8: [B, 16, 128, 16]
target_header_uint8:  [B, 1, 14]
target_events_uint8:  [B, 1, 128, 16]
```

Validation uses a fixed manifest of ticker/window/stride blocks created at run
start from `validation_2026`. It does not resample on each validation call.

## Model

The model uses a pretrained v6/v7/v8 event encoder to turn each compact event
chunk into one embedding. The encoder is frozen by default.

```text
[B, K, chunk] -> flatten -> event_encoder -> [B, K, embedding_dim]
[B, K, embedding_dim] -> temporal transformer -> future query decoder
```

The decoder predicts the next compact chunk as bit logits:

```text
header_bit_logits: [B, H, 14, 8]
event_bit_logits:  [B, H, 128, 16, 8]
```

Loss is `BCEWithLogitsLoss` over future header/event bits. Event bits use the
same semantic bit weighting convention as masked-event v6/v7.

## Run

From the laptop repo:

```powershell
python research\temporal_event_model\v1\run_train.py --dry-run --encoder-version v7 --encoder-checkpoint "D:\path\to\checkpoint_best_val.pt"
```

For real training:

```powershell
python research\temporal_event_model\v1\run_train.py --encoder-version v7 --encoder-checkpoint "D:\path\to\checkpoint_best_val.pt" --batch-size 256 --max-steps 10000
```

Use `--print-only` to show the equivalent command without running it.

## Paired v6/v8 Encoder Comparison

To compare downstream temporal learning with the same temporal v1 setup and only
swap the frozen event encoder checkpoint:

```powershell
python research\temporal_event_model\v1\train_compare_v6_v8_encoders.py
```

The paired launcher defaults to:

```text
v6 checkpoint: D:\TradingML\runtimes\masked_event_model\v6\pretrain\v6-semantic-sumdivbatch-emb32-bs4096-10shards\checkpoints\checkpoint_step_000020340.pt
v8 checkpoint: D:\TradingML\runtimes\masked_event_model\v8\pretrain\v8-semantic-sumdivbatch-emb32-bs4096-10shards-fixedmaskedratio\checkpoints\checkpoint_step_000020340.pt
W&B project: June2026-temporal-v1-v6-v8-encoder-compare
```

It forces both encoders to the matching masked-event checkpoint architecture:
`d_byte=40`, `d_model=256`, `embedding_dim=32`, `heads=8`,
`encoder_layers=10`, `decoder_layers=4`, `ffn_mult=4`, and `dropout=0.08`.
Use `--print-only` to inspect both exact training commands before running.
