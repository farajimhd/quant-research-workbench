# Temporal Event Model v2

v2 is the first production-aligned single-ticker temporal predictor. It keeps the
market-structure encoder as a standalone module, loads the latest pretrained
`masked_event_model/v20` checkpoint, encodes historical compact event chunks,
and trains a temporal model to predict future mid-price return horizons.

## Data Flow

1. Load one random `(ticker, 15-day window)` from `market_sip_compact.events`.
2. Build many origins from that window.
3. For each origin, encode `context_chunks` historical event chunks with the
   market-structure encoder. The default is 64 chunks with dense recent lags and
   geometric older lags.
4. Compute target returns from the as-of quote mid price at future event horizons:
   `8, 16, 32, 64, 128, 256, 512, 1024`.
5. Train the temporal model on normalized return bps. Metrics are reported back
   in bps.

The loader filters origins that do not have a valid quote mid at the origin and
at every requested future horizon. This prevents invalid trade-only states from
entering the loss.

## Model

`MarketTemporalReturnPredictor` receives context embeddings shaped `[B, K, E]`.
The pretrained encoder is called outside the temporal model so production can
reuse the exact same streaming embedding cache.

Default dimensions:

- `K = 64` context chunks
- `E = 32` market-structure embedding dimension
- temporal width `256`
- temporal transformer layers `4`
- return head output `8` future horizons

The market encoder is frozen by default. Use `--fine-tune-encoder` only for an
explicit fine-tuning experiment.

## Workstation Run

From the workstation:

```powershell
python D:\TradingML\codes\temporal_event_model\v2\research\temporal_event_model\v2\run_train_workstation.py
```

Useful overrides:

```powershell
python D:\TradingML\codes\temporal_event_model\v2\research\temporal_event_model\v2\run_train_workstation.py --run-name v2-return-horizon-test --batch-size 1024 --blocks-per-epoch 32
```

The launcher prints the equivalent `train.py` command before starting. It uses:

- W&B project: `June2026-market-ai-temporal-v2`
- encoder checkpoint: newest `checkpoint_latest.pt` found under
  `D:\TradingML\runtimes\masked_event_model\v20\pretrain`
- ClickHouse URL: discovered from `REAL_LIVE_CLICKHOUSE_WRITE_URL` first, then
  other shared ClickHouse env vars, then `http://localhost:18123`

## Metrics

The Rich terminal and JSONL/W&B logs include:

- loss
- mean MAE in bps
- mean RMSE in bps
- mean sign accuracy
- per-horizon MAE/RMSE/sign accuracy/Pearson correlation
- step, data, encoder, train, and throughput timings
- GPU allocated/reserved memory

