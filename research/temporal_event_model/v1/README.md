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

For origin event `t`, with `events_per_chunk=128`, `context_chunks=N`
(`64` by default), and stride `s`, the model receives `N` already-rolled event
chunks. If stride is `1`, this matches the direct rolling execution:

```text
events 1,2,3,4       -> e1 ending at 4
events 2,3,4,5       -> e2 ending at 5
events 3,4,5,6       -> e3 ending at 6
...
```

At origin `t`, v1 chooses which cached `e` embeddings to pass to the temporal
model using lag steps. The default schedule is `dense_geometric`:

```text
first half: dense recent lags 0,1,2,...
second half: geometric older lags out to context_max_lag_steps
chunk_end = t - lag_step * stride
```

For `N=64`, the default uses 32 dense recent lags and 32 geometric older lags
out to `context_max_lag_steps=512`. Chunks are ordered oldest-to-newest before
being sent to the temporal transformer. Use `--context-lag-schedule consecutive`
to reproduce the old contiguous behavior.

```text
context[0]  = oldest selected chunk
...
context[N-1] = current chunk ending at t
target[0]   = next chunk, events t+1 ... t+128
```

Batch shapes:

```text
context_header_uint8: [B, 64, 14]
context_events_uint8: [B, 64, 128, 16]
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
same semantic bit weighting convention as masked-event v6/v7. The future header
is weighted more heavily by default (`header_weight=2.0`, `event_weight=1.0`)
because event prices are delta-coded against the target chunk header; a bad
future header can make otherwise plausible event deltas decode to the wrong
market price level.

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

## v2 Cache Price Probe

The original ClickHouse-window trainer above is kept intact for future temporal
chunk-prediction work. The cache price probe is a separate downstream test that
uses the v2 event sample cache:

```text
D:\market-data\prepared\event_sample_cache\cache_v2_cycle_20260619_134422
```

For each sample, it reads:

```text
x:      current compact chunk from shard_*.x.bin
y:      first two future compact chunks from shard_*.y.bin
```

The frozen masked-event encoder turns `x` into one chunk embedding. A small MLP
probe then predicts the class of the max future price in the two stored future
chunks from `y.bin`:

```text
future chunk 1: events t+1   ... t+128
future chunk 2: events t+129 ... t+256
```

Targets are built only from compact bytes:

1. decode current `x.bin` chunk and take max quote/trade price,
2. decode each stored future `y.bin` chunk and take max quote/trade price,
3. convert each future max-price return into one of:
   `strong_down`, `down`, `flat`, `up`, `strong_up`,
4. train `BCEWithLogitsLoss` on the two one-hot 5-class targets.

The default class thresholds are:

```text
flat:   abs(return_bps) < 2
strong: abs(return_bps) >= 20
```

Rows whose current or future chunk cannot decode a positive price are masked out
for that future chunk.

Run one probe:

```powershell
python D:\TradingML\codes\temporal_event_model\v1\research\temporal_event_model\v1\run_cache_probe.py --print-only
python D:\TradingML\codes\temporal_event_model\v1\research\temporal_event_model\v1\run_cache_probe.py
```

From the laptop, use the UNC-configured launcher so the cache and checkpoint
are read from the workstation shared drive:

```powershell
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_cache_probe_laptop.py --checkpoint epoch1 --print-only
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_cache_probe_laptop.py --checkpoint epoch1
```

```powershell
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_cache_probe_laptop.py --checkpoint epoch2 --print-only
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_cache_probe_laptop.py --checkpoint epoch2
```

Run the first v20 checkpoint comparison, using the same 10 training shards and
one validation shard:

```powershell
python D:\TradingML\codes\temporal_event_model\v1\research\temporal_event_model\v1\train_compare_v20_epoch_probes.py --print-only
python D:\TradingML\codes\temporal_event_model\v1\research\temporal_event_model\v1\train_compare_v20_epoch_probes.py
```

The comparison defaults are:

```text
epoch 1 checkpoint: checkpoint_step_000130176.pt
epoch 2 checkpoint: checkpoint_step_000260352.pt
batch size:         512
W&B project:        June2026-event-encoder-linear-probes
```
