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
2. **Implement one live and historical market-data path.** Put QMD recent data
   and historical SIP data behind the same ordered, deduplicated contract used
   by charts, scanners, replay, backtests, and model consumers.
3. **Finish causal model selection and production forecast serving.** Complete
   the intended packed-model inputs and validation, approve a checkpoint and
   prediction contract, and implement the currently disabled `market-ai`
   service for replay and live forecasts.
4. **Select strategies and build a shared trading runtime.** Promote strategies
   only after evidence-based evaluation, and reuse the same scanning, risk,
   entry, position-management, exit, re-entry, and account-routing logic across
   backtest, simulation, assisted, and automatic trading.
5. **Complete the brokerage execution lifecycle.** Connect the live UI to
   broker preview and submission, handle confirmations, changes, cancellation,
   fills, and reconciliation, and validate safe paper and live multi-account
   routing through the IBKR supervisor.
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
