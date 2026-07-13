# Quant Research Workbench

Quant Research Workbench is a local-first trading platform intended to support
automatic trading, manual or semi-automatic trading, and combinations of both.
The target product joins strategy execution, market replay, backtesting, live
brokerage operations, flexible charting and scanning, market/news/SEC data, and
causal market forecasting in one operator workflow.

This README describes the current product direction and the repository code
that still contributes to it. The final section lists review-only stale or
archive candidates. Nothing in that list has been removed merely because it is
not currently wired into the UI.

## Product Modes

### Automatic trading

Algorithmic strategies define scanning, entry, position management, exit, and
account-routing behavior. The active forecasting research is
`research/packed_market_model` with shared infrastructure in `research/mlops`.
The intended production path is for `services/market-ai` to serve the selected
causal model and make live forecasts available to automatic strategies.

`services/market-ai` is currently a reserved, disabled boundary rather than a
production inference service. Live model-driven trading therefore remains a
target capability, not a completed one.

### Manual and semi-automatic trading

The platform is intended to let an operator trade manually while reusing
algorithmic helpers for scanners, customized orders, position management,
profit-taking, immediate re-entry, and similar workflows. A strategy may mix
manual decisions and automatic actions, and may target one account, multiple
accounts, or paper accounts.

The current routed UI contains a real-live trading workspace and brokerage order
submission. The broader reusable manual-helper library described above is not
yet a distinct, complete subsystem in the repository.

### Simulation trading

Replay and Backtest are routed trading workspaces. Their setup pages use the
Rust historical gateway, resolve exchange-session windows through the shared
runtime calendar, configure an IBKR-shaped simulated account, and open the
shared source-aware container canvas. Replay includes its anchor date;
Backtest selects exchange sessions before its exclusive anchor date.

The earlier prepared-data replay remains in
`frontend/src/pages/LiveTradingPage.tsx` and `/api/live-trading/*` only as a
legacy implementation while its scanner/strategy features are migrated. The
routed historical pages do not silently fall back to it.

### Backtesting and debugging

`src/backtest` provides timestamp-ordered strategy simulation, order/fill and
portfolio modeling, metrics, artifacts, and a step debugger. `src/data_provider`
owns preparation and validation of bars, features, indicators, and supervision
data; backtests consume those provider-built artifacts rather than rebuilding
market data during a run.

The routed Backtest setup and container workspace use the new historical
contracts. Starting a new-runtime backtest remains blocked until strategies are
migrated into the persisted event-runtime contract and the shared historical
run-controller API is implemented. The old `/api/backtests/*` jobs and the
disconnected `StrategyPage.tsx` continue to represent the legacy prepared-bar
research engine; they are not presented as runtime parity.

### Live trading

The active UI route is the real-live trading workspace. It obtains live market
state from QMD, applies scanner and tradability context, reads IBKR account and
portfolio state, and can preview or submit orders. The intended end state is a
single workspace in which automatic and manual behavior can run together across
paper or live accounts.

## Active Architecture

| Area | Current authority | Role and current status |
|---|---|---|
| Operator UI | `frontend/src`, `src/backend` | React/Vite UI served by FastAPI. The routed surface currently exposes real-live trading and service operations; simulation, strategy, data, and research pages exist but are disconnected. |
| Strategy definitions | `src/strategies`, `src/trading_runtime/journal.py` | Versioned implementation code plus persisted, immutable strategy revisions and configuration. Only automatic revisions are eligible for backtest. |
| Trading runtime | `src/trading_runtime` | IBKR Client Portal-shaped order, execution, account, position, portfolio, risk, journal, live-adapter, simulated-broker, and historical orchestration authorities shared by live, paper, replay, backtest, and debug. |
| Legacy backtesting | `src/backtest` | Existing prepared-bar strategy host and artifacts. Its `BarFillModel` and `Portfolio` remain only on the legacy job routes while those strategies are migrated; they are not the new brokerage authority. |
| Prepared-data provider | `src/data_provider` | Existing historical feature artifacts. New historical execution and bar calculation use canonical events; feature migration remains separate from brokerage semantics. |
| Shared market engine | `src/market_engine` | Canonical event and event-derived bar contracts used by historical and live consumers. |
| Live and recent market data | `services/qmd-gateway` | Rust gateway for Massive quotes/trades, compact events, always-on canonical intraday bars, indicators, scanner primitives, recent gap repair, and local streams/APIs. |
| Historical market-data API | `services/qmd_history_gateway` | Read-only Rust compact-event, canonical-event, and event-derived bar API for Replay, Backtest, and Backtest Debug. It reads `market_sip_compact.events_YYYY` and depends on live QMD's shared `qmd_core` decoder/bar implementation; it never connects to Massive. |
| Historical market data | `pipelines/market_sip` | Massive flat-file download, compact event ingestion, validation, repairs, and derived event/bar builders. |
| Trading audit persistence | `services/trading_journal_gateway` | Mirrors the crash-safe local trading outbox into typed ClickHouse `q_live.tr_*` tables without making ClickHouse the order-command queue. |
| News | `services/news_gateway`, `pipelines/news/benzinga` | Live Benzinga acquisition plus historical ingestion, normalization, persistence, coverage, and repair. |
| SEC filings | `services/sec_gateway`, `pipelines/sec/edgar` | Live SEC feed handling plus historical filing/document/XBRL extraction and rebuilding. |
| Text embeddings | `services/text_embed_gateway` | Tokenizes and embeds news and SEC text, persists model-ready outputs, and reconciles coverage. |
| Reference and tradability data | `services/reference_gateway`, `pipelines/reference_data` | Point-in-time issuer/security/listing identity, symbol mapping, tradable universe, borrow, and market-publication context. This service was omitted from the initial app description but is required by live scanner and order safety paths. |
| Broker access | `services/ibkr_gateway_supervisor` | Supervises IBKR Client Portal Gateway login/session state and supplies account checks used by real-live trading. |
| News intelligence | `services/news-intelligence` | Experimental/TBD semantic classification service for normalized news. It does not own acquisition or canonical storage. Its eventual name and scope may expand to SEC or other text. |
| Active model research | `research/packed_market_model` | Current causal packed-event model family and trainer. |
| Shared data/ML infrastructure | `research/mlops` | Environment, ClickHouse, manifests, checkpoints, metrics, packed loaders, and other shared utilities. It is also imported by operational services and pipelines, so it cannot be archived with old models. |
| Forecast serving | `services/market-ai` | Reserved production boundary for the final causal model. It is intentionally disabled today; code below `src/market_ai` is exploratory. |

`services/gateway_core` is shared infrastructure used by multiple gateways and
is active even though it is not a standalone product service.

## Market Event Data Contract

Market-event consumers select the source by runtime mode:

- QMD emits live normalized compact events and maintains the low-latency
  in-memory stream used by live scanners, bars, and model consumers.
- `q_live.events` is the QMD-owned recent event store. It retains the current
  market session plus three prior market sessions in daily partitions. QMD may
  temporarily retain more when historical continuity has not confirmed that an
  older session is archived.
- `market_sip_compact.events_YYYY` is the read-only historical event store built
  from Massive flat files by
  `pipelines/market_sip/flatfiles/download_update_events.py` and its ingestion
  path.
- Live and paper trading read only QMD's live stream. QMD is not a replay or
  backtest source.
- Replay, Backtest, and Backtest Debug read only the historical gateway. It
  orders yearly-table rows by `(sip_timestamp_us, ticker, ordinal)` and mirrors
  the live compact-event schema using the historical ordinal as the stable
  historical source/arrival sequence.
- Historical bars are calculated from those events in the gateway/runtime. No
  historical bar table is an execution source of truth.
- `q_live.intraday_bars_v1` is the single rolling live bar table. Sparse
  `trade`, `quote_bid`, and `quote_ask` bars are built at `100ms`, then rolled
  up from closed base bars to `1s`, `5s`, `10s`, `30s`, `1m`, `5m`, and `1h`.
  It retains the same current-plus-three-prior-session window as live events.

After 08:00 Eastern, QMD discovers Massive quote and trade flatfiles and records
per-object readiness in `q_live.qmd_flatfile_coverage_v2`. A workstation QMD
can launch the unchanged historical updater after collection closes; a laptop
QMD reports the exact workstation command. Neither QMD nor the app writes
historical event tables directly. QMD also rechecks indexed object identity on
a bounded cadence, so a changed remote flatfile reopens that session for an
auditable updater rerun.

The intraday bar product is always enabled and uses the same compact-event
encoding and numeric sanitization as packed training preparation. Its exact
training subset is `100ms`, `1s`, `5s`, `30s`, and `1m`; additional operational
resolutions share the same table. The richer scanner/indicator bars remain
memory-only and are not duplicated into separate ClickHouse layouts.

QMD's terminal follows the same boundary. Its primary surface shows the live
Massive-to-`q_live.events` pipeline, confirmed recent event/bar coverage,
historical flatfile handoff into read-only `market_sip_compact`, downstream
products, and only actionable failures or workstation commands. It retains
timestamped last-good state during monitor/API interruptions and exposes writer
backlog, commit, failure, and recovery state from the Rust service rather than
inferring health from configured endpoints.

## News, SEC, Embeddings, and Forecast Flow

```text
QMD live events --------------------------> scanners/charts/strategies
      |                                               |
      +--> q_live recent events/bars                  |
                                                      v
Massive flat files --> market_sip_compact history --> replay/backtest/model loader

News Gateway --> q_live canonical news --------+
SEC Gateway  --> q_live filing/text/XBRL -------+--> Text Embed Gateway
                                                     |
                                                     v
                                       tokens and embeddings
                                                     |
                                                     v
Packed Market Model research --> Market AI (future) --> strategy forecasts

IBKR Supervisor --> accounts/session --> IBKR adapter ----+
Historical events --> simulated IBKR-shaped broker -------+--> shared trading runtime
                                                              |--> q_live.tr_* audit
Reference Gateway --> identity/tradability -----------^
```

## Running the Operator UI

Install Python and frontend dependencies:

```powershell
pip install -r requirements.txt
npm.cmd --prefix frontend install
```

Run the backend API and React development server in separate terminals:

```powershell
.\scripts\run_backend.ps1
npm.cmd --prefix frontend run dev
```

For a production-style local build:

```powershell
npm.cmd --prefix frontend run build
.\scripts\run_backend.ps1 -NoReload
```

For deterministic browser captures, start the runnable UI, then run a targeted
review matrix (affected route, representative light/dark themes,
minimum/default/maximum application scales, and normal/compact viewports):

```powershell
npm.cmd --prefix frontend run ui:review -- --page service-qmd
```

Run bounded full-product coverage with:

```powershell
npm.cmd --prefix frontend run ui:review:full
```

Use `-- --matrix exhaustive` only for shared theme, scale, layout, or component
infrastructure changes. Captures and a JSON manifest are written under the
system temporary directory by default, not into the repository. The launcher
uses Python Playwright when available and otherwise re-runs itself through the
configurable `UI_REVIEW_CONDA_ENV` environment, which defaults to the existing
`ml4t` Conda environment. It does not install or download browsers.

Individual gateway setup, environment variables, data contracts, and run
commands are documented in each service or pipeline README. Secrets belong in
environment variables or discovered local env files and must not be copied into
runtime artifacts or committed.

## Repository Cleanup Status

The approved model/loader consolidation is complete in source:

- `research/masked_event_model` and `research/temporal_event_model` were removed.
- The temporal daily-index/offline-cache/Rust chronological loader under
  `research/mlops/rolling_loader` was removed.
- The unused experimental provider stack under `research/mlops/data` was
  removed.
- Packed-used month-window, multimodal context-query, and intraday bar helpers
  now live in `research/mlops/packed_market/context.py`; the full-modality
  profiler no longer imports a superseded loader abstraction.
- The empty `src/frontend` placeholder was removed. The active UI remains in
  top-level `frontend`.
- Obsolete workstation commands pointing through a `masked_event_model\v4`
  code root were updated to `quant_research_workbench_pipelines`.

Historical checkpoints, experiment outputs, and workstation runtime directories
were retained outside the repository as provenance. They are not current source
authorities and should be removed only under an explicit artifact-retention
decision.

### Components that should not be classified as stale

- `research/market_references` is operational reference data/code used by QMD
  and market-SIP tools; it is not another forecasting model family.
- `services/reference_gateway` contributes point-in-time identity, tradability,
  and borrow context required for safe scanning and order routing.
- `pipelines/*` remain necessary for historical completeness, repair, and the
  live/historical data contract even though operators interact mainly through
  services and the UI.
- `services/news-intelligence` is not production-complete, but its proposed text
  analysis role aligns with the app description. It needs scope/name review,
  not automatic deletion.

## Remaining Tasks to Reach the Final Working Version

The main foundations exist, but the following product-level outcomes are still
required before the workbench is a complete end-to-end application:

1. **Complete and certify the authoritative data foundation.** Finish the SEC
   v3 cutover, align its point-in-time market identity links, regenerate derived
   text products, and audit coverage and integrity before dependent services use
   the new data.
2. **Finish consumer migration to the shared market-data contract.** The
   separate live-QMD and historical-gateway authorities now expose compatible
   compact events and event-derived bars. Migrate the remaining legacy
   prepared-bar backtest/replay consumers and certify parity.
3. **Finish causal model selection and production forecast serving.** Complete
   the intended packed-model inputs and validation, approve a checkpoint and
   prediction contract, and implement the currently disabled `market-ai`
   service for replay and live forecasts.
4. **Migrate and select strategies on the shared runtime.** The IBKR-shaped
   runtime, simulated broker, central risk checks, strategy revision store, and
   historical runner exist. Remove the legacy strategy host's fill/portfolio
   authority as each feature-dependent automatic strategy is moved to
   event-derived bars, then promote strategies only after evidence-based tests.
5. **Certify the brokerage execution lifecycle.** Preview, submission,
   warning replies, modification, cancellation, account-specific routing, and
   normalized portfolio resources now have shared contracts. Add authenticated
   paper-session websocket/reconciliation acceptance tests before live use.
6. **Unify the operator workflow.** Reconnect, merge, or deliberately replace
   the disconnected simulation, strategy, backtest, market-data, and research
   pages so operators can move from research to deployment in one workspace.
7. **Finalize text intelligence.** Decide the scope and final service boundary
   for news and SEC intelligence, then integrate versioned outputs with the
   canonical stores, live streams, model inputs, and operator UI.
8. **Pass full-system release validation.** Prove replay/live parity, paper
   execution, service restart and recovery, stale-data behavior, risk controls,
   performance, and operational recovery before enabling unattended or live
   automatic trading.
