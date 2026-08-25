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

Production startup should use an approved external release manifest rather
than selecting a checkpoint by filename or recency:

```powershell
.\scripts\run_bar_gpt.ps1 `
  -ReleaseManifest D:\TradingML\runtimes\bar_gpt_service\configuration\releases.json
```

The manifest is a JSON array whose rows contain `model_id`, `version`,
`checkpoint`, `checkpoint_sha256`, `contract_hash`, `role`, and optional
`enabled`. The service verifies the expected checkpoint and model-contract
hashes before admitting a release. The full application stack can be managed
with `python scripts\manage_application_services.py start|stop|restart|status`.
If the catalog does not exist yet, pass
`--checkpoint-root "\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\bar_gpt"`;
the manager selects immutable versioned artifacts, calculates both hashes, and
writes the external catalog before starting any workspace process. It does not
replace an existing catalog from checkpoint filename recency.

The default bind is `127.0.0.1:8805`. Runtime prediction journals are written
under `D:\TradingML\runtimes\bar_gpt_service`, never in the repository.
`BAR_GPT_MAX_TICKERS` defaults to 500; size it together with
`BAR_GPT_MAX_BATCH_SIZE` from measured host-memory and GPU-memory headroom.

The app's **Services → BarGPT** page edits service-owned operational intent:
promoted release selection and champion/shadow roles, device and precision,
batch and queue bounds, warm-up concurrency, retained prediction history, and
the QMD Live connection. It selects immutable server-registered release IDs;
checkpoint paths are never accepted from or exposed to the browser. Updates
are revisioned and persisted atomically outside the repository, then become
effective after a BarGPT restart. Override the default configuration journal
with `BAR_GPT_OPERATIONAL_CONFIG` when a host needs a different runtime root.

Watchlists, Auto/Manual trigger mode, application ticker limits, enabled model
use, Data Fields, Rule Sets, and Signal Streams remain revisioned application
intent in **Market Discovery**. Operational status follows the common Services
contract through `/health`, `/snapshot/status`, and `/metrics`.

Serving scopes are replaced atomically with `PUT /scopes/{scope_id}` and carry
an explicit Live, Paper, Replay, Backtest, or Backtest Debug mode plus an Auto
or Manual inference trigger. Cache updates continue in Manual mode. Automatic
serving performs one forward pass after each completed eligible one-second bar;
it never recursively generates future bars.

Warm-up uses the same point-in-time identity, split normalization, sparse-event
aggregation, masks, and feature contract as the checkpoint loader. Intraday
history expands from a short causal window only when a thin ticker still lacks
required view counts. Calendar views are rebuilt with the v13 vectorized
compact-event daily authority; the removed legacy BarGPT daily table is not a
serving dependency. Live calendar context is persisted only in same-session,
model-contract-hashed snapshots. Every reuse is additionally matched against
an explicit QMD History source plan: active ClickHouse row, block, and mutation
revision evidence for the
compact-event, condition-reference, point-in-time identity, and stock-split
tables. The revision is checked both before and after the incremental intraday
refresh; a concurrent repair fails the warm rather than mixing revisions.
Legacy snapshots without this revision evidence are rejected. The first warm
remains authoritative only through the existing QMD direct-event loader and
its point-in-time identity and split reads; the snapshot is an optimization,
not a second source authority. Removed live scope members are retained for a
bounded grace period before pending work and cache rows are reclaimed.
Warm materialization retains the complete configured per-view causal context
and any not-yet-admitted historical rows, but does not construct discarded
older `RawBar` objects. Admission uses bounded bulk merges off the event loop;
health and readiness therefore remain responsive while a large ticker warms.
Automatic origins observed before readiness are coalesced to the newest origin
per ticker and admitted once warm, without being reported as inference errors.
Bounded warm concurrency prioritizes interactive Replay, Backtest, and
Backtest Debug scopes ahead of queued broad Live watchlist warming. Broad Live
discovery may run only one raw compact-event warm at a time, leaving the
remaining configured capacity for interactive scopes and preventing
opportunistic scans from saturating ClickHouse. Contract- and source-revision-
validated same-session calendar snapshots are shared across Live, Replay,
Backtest, and Debug caches and refreshed after every successful warm. Running
Live warm jobs are not cancelled; the interactive scope receives the next
available worker slot.
Managed shutdown also signals synchronous history loaders between their
bounded ClickHouse requests, so an active multi-query warm plan cannot extend
shutdown indefinitely.

Canvas can render translucent forecast OHLC candles and independent open,
high, low, and close lines. Operators can select v2 or v3, q10/q50/q90, one
physical horizon or all horizons, and Auto or Manual trigger mode. Manual mode
keeps context current and exposes **Infer now** only after the ticker is warm.

Historical Rule Sets that reference BarGPT fields use a synchronous,
fail-closed clock barrier so a fast backtest cannot outrun GPU inference. Runs
without BarGPT field dependencies retain the asynchronous scope path.

The initial authority is full-prefix inference. KV reuse is disabled until a
separate parity certification covers rollover, masks, session boundaries,
splits, late corrections, and multiview as-of fusion.
