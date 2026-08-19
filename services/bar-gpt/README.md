# BarGPT Service

BarGPT Service is the production inference boundary for versioned BarGPT v2
and v3 checkpoints. It owns causal context caches, mode-scoped serving leases,
full-prefix dynamic batching, raw prediction preservation, semantic decoding,
and prediction publication. It has no Rule Set, Strategy, risk, sizing, or
order authority.

Configure one or both immutable releases:

```powershell
.\scripts\run_bar_gpt.ps1 `
  -V2Checkpoint D:\TradingML\runtimes\bar_gpt\v2\checkpoint.pt `
  -V3Checkpoint D:\TradingML\runtimes\bar_gpt\v3\checkpoint.pt
```

The default bind is `127.0.0.1:8805`. Runtime prediction journals are written
under `D:\TradingML\runtimes\bar_gpt_service`, never in the repository.
`BAR_GPT_MAX_TICKERS` defaults to 500; size it together with
`BAR_GPT_MAX_BATCH_SIZE` from measured host-memory and GPU-memory headroom.

Serving scopes are replaced atomically with `PUT /scopes/{scope_id}` and carry
an explicit Live, Paper, Replay, Backtest, or Backtest Debug mode plus an Auto
or Manual inference trigger. Cache updates continue in Manual mode. Automatic
serving performs one forward pass after each completed eligible one-second bar;
it never recursively generates future bars.

Historical Rule Sets that reference BarGPT fields use a synchronous,
fail-closed clock barrier so a fast backtest cannot outrun GPU inference. Runs
without BarGPT field dependencies retain the asynchronous scope path.

The initial authority is full-prefix inference. KV reuse is disabled until a
separate parity certification covers rollover, masks, session boundaries,
splits, late corrections, and multiview as-of fusion.
