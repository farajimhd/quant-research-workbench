# In-House Microstructure Event-Language Transformer v22

v22 replaces the v21 fixed 1s/10s snapshot experiment with a hierarchical
event-chunk model.

The model input is fixed-time chunks:

```text
chunk_ms = 500
context_seconds = 60
context_chunks = 120
```

Each chunk keeps quote and trade events separately before the model:

```text
quote_values:  [batch, context_chunks, max_quote_events, quote_feature_count]
trade_values:  [batch, context_chunks, max_trade_events, trade_feature_count]
event_kinds:   [batch, context_chunks, max_total_events]
event_indices: [batch, context_chunks, max_total_events]
chunk_summary: [batch, context_chunks, summary_feature_count]
```

The quote encoder and trade encoder project their own feature sets to the same
`d_model`. The model then reassembles selected quote/trade embeddings into
timestamp order inside each chunk, applies local attention inside chunks, pools
one embedding per chunk, and applies global attention across the context.

Target:

```text
targets: [batch, 6, 1, 13]
```

The default six horizons are `t+10s`, `t+20s`, `t+30s`, `t+60s`, `t+120s`,
and `t+300s`. Each target is future mid-price log-return bps from current mid,
encoded as v14-style binary magnitude bits.

Before final training, profile chunk/cap choices:

```powershell
python research\inhouse_transformer\v22\profile_event_chunks.py --flatfiles-root D:\market-data\flatfiles\us_stocks_sip --start-date 2025-11-01 --end-date 2025-12-05 --tickers ALL --chunk-ms 100,250,500,1000 --caps 64,128,256,512 --processes 8 --polars-threads-per-process 2
```

Preprocess raw quote/trade CSV.GZ into shared sparse event-chunk Parquet.
The training data provider expands each ticker/session into a vectorized
500ms wall-clock grid, so idle chunks for illiquid symbols are included during
training without exploding the on-disk cache:

```powershell
python research\inhouse_transformer\v22\preprocess_event_chunks.py --flatfiles-root D:\market-data\flatfiles\us_stocks_sip --cache-root D:\market-data\flatfiles\us_stocks_sip\derived\event_chunks_v1 --start-date 2025-11-01 --end-date 2025-12-12 --tickers ALL --chunk-ms 500 --max-quote-events 128 --max-trade-events 192 --max-total-events 256 --processes 4 --polars-threads-per-process 8 --build-chunks
```

Train from preprocessed chunks:

```powershell
python research\inhouse_transformer\v22\train.py --flatfiles-root D:\market-data\flatfiles\us_stocks_sip --cache-root D:\market-data\flatfiles\us_stocks_sip\derived\event_chunks_v1 --output-root D:\TradingData\quant-research-workbench\market_data\models\inhouse_transformer\v22 --device cuda --batch-size 512 --num-workers 8 --prefetch-factor 4 --tickers ALL --wandb-entity mehdifaraji --wandb-project May2026-microstructure-event-language-v22
```

Numeric inputs are normalized causally per sample/context:

- price-like fields are converted to log-return bps versus origin current mid;
- size/count/volume fields use `log1p`;
- numeric event and chunk-summary features are z-scored over the current context;
- masks and categorical event kind/index tensors are not z-scored.
