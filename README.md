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

Simulation trading replays a historical session as a configurable trading day.
The existing replay workspace can pace the market clock normally, step it, or
fast-forward to the next scanner signal. It combines scanner rows, chart data,
and historical Benzinga news at the simulated timestamp.

The replay implementation currently exists in `frontend/src/pages/LiveTradingPage.tsx`
and `/api/live-trading/*` backend routes, but the page is not in the active
navigation. It is retained because it directly implements the target product.

### Backtesting and debugging

`src/backtest` provides timestamp-ordered strategy simulation, order/fill and
portfolio modeling, metrics, artifacts, and a step debugger. `src/data_provider`
owns preparation and validation of bars, features, indicators, and supervision
data; backtests consume those provider-built artifacts rather than rebuilding
market data during a run.

Backtest and strategy pages remain in the frontend source but are not currently
routed by `frontend/src/App.tsx`. The backend APIs and engine are still present.
This is an integration gap, not sufficient evidence that the capability is
stale.

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
| Strategy definitions | `src/strategies` | Versioned scanner and trading logic registered through `src/strategies/registry.py`. |
| Backtesting | `src/backtest` | Event ordering, fills, fees, portfolio state, results, observability, and step debugging. |
| Prepared-data provider | `src/data_provider` | Builds and validates reusable historical bar/feature artifacts used by the current backtest and replay paths. |
| Live market engine | `src/market_engine` | Shared event, bar, scanner, broker, source, and storage contracts. |
| Live and recent market data | `services/qmd-gateway` | Rust gateway for Massive quotes/trades, compact events, live bars, indicators, scanner primitives, recent gap repair, and local streams/APIs. |
| Historical market data | `pipelines/market_sip` | Massive flat-file download, compact event ingestion, validation, repairs, and derived event/bar builders. |
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

Market-event consumers must select sources by trading date and combine them
when a requested range crosses the live/historical boundary:

- QMD emits live normalized compact events and maintains the low-latency
  in-memory stream used by live scanners, bars, and model consumers.
- `q_live.events_YYYY` is the QMD-owned recent event store. The default logical
  name is `events`; physical tables are selected by UTC event year. QMD repairs
  recent gaps with up to a three-day lookback.
- `market_sip_compact.events_YYYY` is the read-only historical event store built
  from Massive flat files by
  `pipelines/market_sip/flatfiles/download_update_events.py` and its ingestion
  path. Current QMD documentation also refers to a logical distributed
  `market_sip_compact.events` source across the yearly tables.
- A request spanning older and recent sessions must query both authorities,
  normalize them to the same event contract, concatenate them, order by event
  time/ordinal, and apply the appropriate deduplication boundary.
- Closed live bars are persisted in `q_live` using schemas aligned with the
  historical `market_sip_compact` bar layouts, allowing chart consumers to join
  recent and older ranges.

The repository code confirms the yearly physical table convention. Any
deployment-specific retention claim such as “today plus the previous three
trading days” still needs a live ClickHouse policy/row audit; it should not be
treated as guaranteed solely from this README.

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

IBKR Supervisor --> accounts/session --> real-live trading --> broker orders
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

Individual gateway setup, environment variables, data contracts, and run
commands are documented in each service or pipeline README. Secrets belong in
environment variables or discovered local env files and must not be copied into
runtime artifacts or committed.

## Stale and Archive Candidates for Review

These are candidates, not approved deletions. “No runtime import found” means a
repository-wide static reference scan did not find an import from code outside
the candidate family; it does not prove that notebooks, workstation copies,
external commands, checkpoints, or untracked runtime jobs no longer depend on
it.

| Candidate | Evidence | Dependency/risk before removal | Recommended review action |
|---|---|---|---|
| `research/masked_event_model` | Superseded research family with many version directories; no production/service/pipeline imports were found. References outside the family are confined to old `temporal_event_model` experiments. | Historical notebooks, checkpoints, workstation launchers, and temporal-model comparison scripts may still use it. | Archive the whole family together with any dependent comparison experiments after checking workstation jobs and artifact manifests. |
| `research/temporal_event_model` | Superseded by `packed_market_model`. Its remaining shared-module references are from the old chronological cache/loader chain, not from packed training. | Temporal v3 is imported by temporal-specific `research/mlops/rolling_loader` build/profile tools and old masked-model experiments. Those consumers should be archived with the model rather than treated as a reason to retain it. | Migrate the two packed-used helper seams described below, validate the packed run chain, check external workstation jobs/artifacts, and then archive temporal v3 with its chronological loader consumers. |
| Chronological loader legacy inside `research/mlops/rolling_loader` | Packed training, ticker-stream profiling, and model profiling use `research/mlops/packed_market`, not the chronological loader. Most daily-index datasets, offline-cache builders, Rust chronological-loader code, and associated profilers serve temporal v3 only. | `run_profile_full_modality_loader.py` still imports `month_window` from `daily_index_cache.py` and context query/bar-building functions from `daily_index_context.py`. This is architectural leakage from a superseded loader package, not evidence that packed training requires the old loader. | Move the packed-used month-window and context-query/bar helpers into `research/mlops/packed_market` or a genuinely generic MLOps module, update the profiler imports, and archive the remaining chronological loader package after validation. |
| Legacy candidates inside `research/mlops/data` | The packed full-modality profiler currently uses `data.config.RollingMarketDataConfig`; the imported rolling-cache helper also reaches constants in `data.contracts`. No repository consumers outside `research/mlops/data` were found for the other data-provider modules. | Standalone commands, workstation runtime copies, or external jobs are not visible to the static repository scan. Removing the whole package now would also break the two packed-used types. | After migrating the packed helper seam, keep or relocate only the configuration/contracts still required by packed code and review the remaining modules as a separate archive set. |
| Generic and operational `research/mlops` utilities | ClickHouse, environment, manifest, path, checkpoint, metric, model-artifact, W&B, compact-event, and related helpers are used by packed training and/or operational services and pipelines. | Broadly archiving `mlops` would break QMD-adjacent pipelines, news/SEC pipelines, reference gateway, news gateway, Market AI encoding, and active packed-model training. | Keep these shared utilities; perform consolidation at module/package granularity rather than archiving `research/mlops` as a whole. |
| `services/market-ai/src/market_ai` prototype implementation | The service README and launchers explicitly state that production serving is disabled and the batching code is exploratory. | The service boundary itself is required by the target architecture, and its historical encoder imports `research.mlops.clickhouse_events`. | Keep the service shell/README; review prototype modules once the packed-model inference contract is defined. |
| Disconnected frontend pages | `StrategyPage`, `ResearchRunsPage`, `MarketDataBuildPage`, `MarketDataReviewPage`, and the historical `LiveTradingPage` are not routed by the current `App.tsx`. | Strategy configuration, backtesting, data review, and simulation are explicit product requirements; deleting these pages would discard potentially reusable implementation. | Treat as an integration audit, not stale deletion. Compare each page/API with the target unified workspace and either reconnect, refactor, or replace it deliberately. |
| `src/frontend` | The directory is empty while the active UI lives in top-level `frontend`. | None found in the tracked source inventory, but verify packaging scripts before deletion. | Safe-looking cleanup candidate after a final path/reference check. |
| Generated and runtime artifacts in the working tree | Local directories such as `frontend/node_modules`, `frontend/dist`, `__pycache__`, `.pytest_cache`, `wandb`, `tmp`, logs, and backtest outputs are not application source. | Some may be ignored local state needed for development, but none should be documented or committed as product components. | Keep ignored locally as needed; remove only through a separate approved cleanup. |

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

## Review Decisions Still Needed

1. Migrate `month_window` plus the packed-used context query/bar-building
   functions out of `research/mlops/rolling_loader` into
   `research/mlops/packed_market` or a genuinely generic MLOps module. Update
   `run_profile_full_modality_loader.py` to use the new authority.
2. Validate packed training, ticker-stream profiling, full-modality profiling,
   and model profiling after the migration.
3. Check workstation runtime launchers, scheduled/manual commands, checkpoints,
   and artifact manifests for external dependencies that static repository
   imports cannot reveal.
4. Archive `temporal_event_model` together with its temporal-specific daily-index,
   offline-cache, and Rust chronological-loader consumers. Archive
   `masked_event_model` and dependent comparison experiments in the approved
   boundary.
5. Separately review `research/mlops/data`, retaining or relocating only the
   configuration/contracts required by the packed path after migration.
6. Decide which disconnected UI pages should be reconnected versus replaced by
   the unified trading workspace.
7. Define the packed-model prediction schema and live cache contract before
   implementing the production `market-ai` service.
8. Confirm the deployed ClickHouse retention and views for `q_live.events_YYYY`
   and `market_sip_compact.events_YYYY` with live schema/row checks.
9. Define the cross-text scope and final service name for News Intelligence.
