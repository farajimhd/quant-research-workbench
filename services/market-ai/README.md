# Market AI Service

`market-ai` is the Python service boundary for market-data ML inference. It is
responsible for turning compact market events into model-ready event chunks,
batching encoder inference, maintaining per-ticker embedding state, batching
temporal inference, and exposing the same batching primitives for offline
training/replay.

The service consumes the same compact unified event row shape documented by
`services/qmd-gateway/docs/DATA_CONTRACTS.md` and uses the shared historical
window encoder from `research.mlops.clickhouse_events` when available. That keeps
live serving and offline pretraining aligned on the exact
`header_uint8 [14] + events_uint8 [128,16]` representation.

## Runtime Flow

```text
startup
-> load model/runtime config
-> warm each watched ticker from the prior trading day or a replay source
-> stream compact events from qmd-gateway
-> append each event to a per-ticker ordered ring
-> emit one event chunk whenever a ticker update has enough context
-> batch chunks for event-encoder inference
-> append encoder embeddings to each ticker's embedding ring
-> build temporal contexts from recent and older embeddings
-> batch temporal model inference
-> publish predictions
```

The hot path emits work only for tickers that update. It does not rebuild a
whole-market scanner batch at every timestamp.

## Training Reuse

Offline training/replay should use the same `StreamBatchingEngine` with a
historical event iterator. The training helpers in `market_ai.training` replay
events through the production state machine, optionally run an encoder model,
and emit temporal samples or future-labeled samples.

This avoids a separate training-only batching implementation and makes it much
harder for production serving and offline experiments to drift.

## Smoke Test

From the repo root:

```powershell
python services\market-ai\run_smoke.py
python -m unittest discover -s services\market-ai\tests
```

The smoke test uses synthetic compact rows and a deterministic dummy encoder. It
does not start a server or connect to ClickHouse.

## Current Scope

This initial service adds the event serving and batching core. Model loading,
HTTP/websocket APIs, and qmd-gateway stream subscription should be added on top
of these primitives once the model checkpoints and live deployment contract are
finalized.
