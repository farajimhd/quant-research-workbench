# temporal_event_model v1

First downstream test for pretrained compact-event encoders.

The active v1 training path is the cache-v2 single-chunk price-direction probe:

```text
x.bin current chunk [B,14] + [B,128,16]
        -> frozen masked_event_model encoder
        -> chunk embedding [B,32]
        -> nonlinear MLP decoder
        -> low/high tick regression [B,2,2]
        -> up/down/path class logits
```

The two output chunks correspond to the two stored future chunks in cache v2.
For each future chunk, labels are derived from the future chunk header plus the
future event price deltas:

```text
low_ticks  = future_ask_anchor_ticks + min(primary_price_delta_ticks)
high_ticks = future_ask_anchor_ticks + max(primary_price_delta_ticks)
```

The loss is normalized low/high absolute-tick MSE plus cross-entropy for
upside, downside, and path classes. The future header is used only to build the
labels; the model does not predict future header bits. This avoids the earlier
failure mode where small predicted-anchor errors dominated semantic direction
metrics.
This is intentionally simple and fast; it does not use the older multi-context
temporal transformer decoder.

The older ClickHouse-window temporal trainer is still present for future
experiments that build true multi-chunk temporal context, but cache v2 does not
store that context today. Do not use the old `CONTEXT_CHUNKS` notebook path for
cache-v2 linear probing.

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
probe then predicts low/high absolute ticks plus classification labels for the
two stored future chunks from `y.bin`:

```text
future chunk 1: events t+1   ... t+128
future chunk 2: events t+129 ... t+256
```

Targets are built only from compact bytes:

1. decode the current `x.bin` chunk and take the last valid primary price as
   the reference for metrics,
2. decode each stored future `y.bin` chunk's ask-anchor ticks and tick scale,
3. take the low and high primary-price deltas inside each future chunk,
4. convert those extrema to absolute ticks with the target future header,
5. train MSE on normalized absolute low/high ticks and cross-entropy on
   up/down/path classes.

Per future chunk:

```text
regression target: low_ticks / 2^20, high_ticks / 2^20
up class:          no_up, up, strong_up
down class:        no_down, down, strong_down
path class:        flat, up_only, down_only, two_sided
```

With two stored future chunks, the probe predicts four normalized tick values
and six class-logit groups per sample.

The default class thresholds are:

```text
flat:   abs(return_bps) < 2
strong: abs(return_bps) >= 20
```

Rows whose current or future chunk cannot decode a positive price are masked out
for that future chunk.

Validation metrics convert predicted ticks back to dollar prices using the
target chunk tick scale, then report:

- regression MSE,
- low/high tick MAE,
- decoded low/high dollar MAE,
- predicted-low <= predicted-high validity rate,
- upside, downside, and path confusion matrices.

## Streaming-Window Embedding Probe

`window_embedding_probe.py` is the elevated linear-probe style test for the
production embedding path. It does not use saved cache-v2 samples. Instead, it
queries one ticker/time window from ClickHouse, creates a rolling embedding
stream, and trains a small temporal head from selected embeddings.

For each sampled ticker window:

1. query ordered events from `market_sip_compact.events`,
2. build every valid rolling 128-event compact chunk,
3. run the frozen event encoder once per valid rolling chunk,
4. keep those embeddings in RAM for the block,
5. select two context groups from the embedding stream:
   - dense recent embeddings,
   - sparse older embeddings by geometric lag,
6. train the temporal head to predict the same tick-extrema targets used by the
   cache-v2 probe.

The default context is production-aligned:

```text
recent_count = 16
recent_stride = 1
older_count = 16
older_lags = geometric lags from 32 to 1024
context shape = [B, 32, 32]
```

The context is ordered oldest to newest before entering the temporal head. The
target chunks are:

```text
future chunk 1: events t+1   ... t+128
future chunk 2: events t+129 ... t+256
```

Run a dry command preview:

```powershell
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_window_embedding_probe_laptop.py --print-only
```

Run a small smoke against one ticker:

```powershell
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_window_embedding_probe_laptop.py --checkpoint epoch1 --tickers AAPL --blocks-per-epoch 2 --validation-blocks 1 --validation-batches-per-block 1 --batch-size 128 --block-max-events 50000 --run-name v1-window-probe-smoke-aapl
```

Run the default elevated probe:

```powershell
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_window_embedding_probe_laptop.py
```

Use named checkpoints to compare pretraining stages:

```powershell
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_window_embedding_probe_laptop.py --checkpoint epoch1 --run-name v1-window-probe-v20-epoch1

python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_window_embedding_probe_laptop.py --checkpoint latest --run-name v1-window-probe-v20-latest
```

Run one probe:

```powershell
python D:\TradingML\codes\temporal_event_model\v1\research\temporal_event_model\v1\run_cache_probe.py --print-only
python D:\TradingML\codes\temporal_event_model\v1\research\temporal_event_model\v1\run_cache_probe.py
```

From the laptop, use the local-cache launcher after copying the first two v2
cache shards to:

```text
D:\market-data\prepared\event_sample_cache\cache_v2_cycle_20260619_134422
```

The laptop launcher defaults to one shuffled training shard, ten validation
batches from the second shard, five epochs, RAM preloading for the training
shard, and step-frequency validation.

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

### Full-Grid Versus Masked-Visible v20 Probe

Use this diagnostic to test whether a v20 checkpoint only works under the sparse
encoder input distribution used during masked-event pretraining.

It runs two cache-v2 linear probes with the same checkpoint, data, batch size,
learning rate, validation split, and W&B project:

1. `fullgrid`: normal production path where v20 sees all 128 event records.
2. `masked70`: diagnostic path where the frozen v20 encoder sees a fresh random
   visible subset and the fixed-grid bottleneck keeps about 70% of event slots
   zero, matching the fixed-mask pretraining regime.

Workstation command:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\temporal_event_model\v1\run_compare_v20_full_vs_masked_probe.py
```

Use `--print-only` first to inspect both generated commands. Use `--only full`
or `--only masked70` to run just one side.

## Fixed Checkpoint Evaluation

Training-time validation in `run_cache_probe_laptop.py` samples a validation
order per run. For fair checkpoint comparison, use the standalone fixed
evaluator after training. It loads the three newest temporal probe
`checkpoint_latest.pt` files by default and evaluates all of them on the same
fixed validation set:

```text
cache split:       train
validation shard:  second shard, shard index 1
validation rows:   10 batches x 1024 rows
shuffle seed:      20260621
```

Run from the laptop:

```powershell
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_evaluate_cache_probe_laptop.py --print-only
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_evaluate_cache_probe_laptop.py
```

To force exact checkpoints instead of the three newest runs:

```powershell
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_evaluate_cache_probe_laptop.py `
  --checkpoint "D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe_laptop\<run_a>\checkpoints\checkpoint_latest.pt" `
  --checkpoint "D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe_laptop\<run_b>\checkpoints\checkpoint_latest.pt" `
  --checkpoint "D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe_laptop\<run_c>\checkpoints\checkpoint_latest.pt"
```

The evaluator writes:

```text
D:\TradingML\runtimes\temporal_event_model\v1\cache_price_probe_laptop_eval\fixed-shard1-10x1024\fixed_eval_results.json
```

Open `evaluate_cache_probe_checkpoints.ipynb` to run the same evaluation from a
notebook and plot:

- tick-extrema loss, normalized tick regression MSE, and dollar/tick MAE,
- upside, downside, and path accuracies,
- row-normalized confusion matrices with raw counts,
- target-vs-predicted class distributions for the three classification heads.

## Cache-Probe Fine-Tuning

After comparing frozen-encoder probes, use
`finetune_cache_probe_checkpoints.py` to fine-tune the trained temporal probe
checkpoints on the same cache-v2 tick-extrema objective. The objective is the
same one used by `cache_probe.py`: normalized low/high absolute tick regression
plus upside, downside, and path classification targets derived from decoded
future chunk extrema. It loads only the newest
`checkpoint_latest.pt` file by default, or explicit `--checkpoint` paths.

Fine-tuning modes:

- `bottleneck`: train the probe MLP head plus only the v20
  `chunk_embedding_bottleneck.fixed_grid_to_chunk_embedding` sequential module.
  The upstream event transformer remains frozen and deterministic.
- `full`: load the trained temporal probe checkpoint, then train the probe MLP
  head plus all learnable parameters in the loaded event encoder.
- `scratch_full`: use the checkpoint only as a config source, initialize the
  event encoder and probe MLP head randomly, and train the full model. This is
  the direct no-pretraining comparison.

The default schedule is five epochs with manual cosine annealing inside each
epoch. The epoch starts at `4e-4`, restarts each epoch, and the next epoch's
base LR is multiplied by `0.9`.

Run bottleneck fine-tuning from the laptop:

```powershell
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_finetune_cache_probe_laptop.py --mode bottleneck --print-only
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_finetune_cache_probe_laptop.py --mode bottleneck
```

Run full encoder fine-tuning:

```powershell
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_finetune_cache_probe_laptop.py --mode full --print-only
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_finetune_cache_probe_laptop.py --mode full
```

Run the randomly initialized full-model comparison:

```powershell
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_finetune_cache_probe_laptop.py --mode scratch_full --print-only
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_finetune_cache_probe_laptop.py --mode scratch_full
```

Run all three modes sequentially in the same terminal:

```powershell
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_finetune_three_modes_laptop.py --print-only
python D:\TradingCodes\quant-research-workbench\research\temporal_event_model\v1\run_finetune_three_modes_laptop.py
```

The full encoder mode carries much more activation memory than the bottleneck
mode. The launcher defaults to `batch_size=1024`; reduce it if the laptop GPU
runs out of memory.
