# Service Gateway Standard

This document defines the operating convention for QMD, News, SEC, Reference,
Text Embed, IBKR Supervisor, Market AI, and future data services in this repo.

## Table Of Contents

- [Core Principles](#core-principles)
- [Shared Vocabulary](#shared-vocabulary)
- [Required Lifecycle](#required-lifecycle)
- [Shared Gateway Core](#shared-gateway-core)
- [Service Designs](#service-designs)
  - [Service Matrix](#service-matrix)
  - [QMD Gateway](#qmd-gateway)
  - [News Gateway](#news-gateway)
  - [SEC Gateway](#sec-gateway)
  - [Reference Gateway](#reference-gateway)
  - [Text Embed Gateway](#text-embed-gateway)
  - [IBKR Gateway Supervisor](#ibkr-gateway-supervisor)
  - [News Intelligence Service](#news-intelligence-service)
  - [Market AI Service](#market-ai-service)
  - [Maintenance Runner](#maintenance-runner)
  - [Cross-Service Dependency Rules](#cross-service-dependency-rules)
- [Storage Rule](#storage-rule)
- [Market Session Source Of Truth](#market-session-source-of-truth)
- [Active Collection Window](#active-collection-window)
- [Backfill Policy](#backfill-policy)
- [Queue Policy](#queue-policy)
- [Coverage Policy](#coverage-policy)
- [Preflight Policy](#preflight-policy)
- [Logging Policy](#logging-policy)
- [Terminal Policy](#terminal-policy)
  - [Status Vocabulary](#status-vocabulary)
  - [Required Panels](#required-panels)
  - [Rendering Policy](#rendering-policy)
  - [Shared Dashboard State](#shared-dashboard-state)
  - [Service-Specific Panels](#service-specific-panels)
- [API Policy](#api-policy)
- [Audit Policy](#audit-policy)
- [Shared Config Groups](#shared-config-groups)

## Core Principles

Services are independent reconcilers. A service should not depend on another
service delivering a special event in order to notice durable data that already
landed in ClickHouse. Instead, it should periodically compare:

```text
upstream source tables / streams / coverage
minus
its own output tables / coverage
=
work still needed
```

Event-like logs and maintenance task rows are useful for observability and
operator workflows, but they are not the source of truth for downstream work.
The source of truth is durable upstream data plus durable coverage and the
consumer's own durable output state.

Hot paths must stay narrow:

```text
live ingest/poll
-> validate and normalize current data
-> write canonical data
-> update coverage/status
-> return to live work
```

Heavy historical work, bridge rebuilds, embedding extraction, publication
maintenance, and model inference catch-up should run in background workers or
after-hours maintenance unless the service is specifically an offline worker.

Documentation in this standard must avoid unexplained engineering shorthand.
When a term such as `backpressure`, `drop`, `fanout`, `canonical`, `coverage`,
or `reconciliation` is used, the nearby text should explain the practical
behavior. The reader should not need to infer implementation behavior from
jargon.

## Shared Vocabulary

All services should use the same names for the same operational concepts.

| Term | Standard meaning |
| --- | --- |
| `provider` | External or upstream system such as Massive, SEC, IBKR, FINRA, ClickHouse source tables, or a local model server. |
| `source` | The raw or logical input watched by the service. A source may be an API endpoint, websocket, file tree, or upstream table. |
| `sink` | The durable output table, stream, or artifact that the service owns. |
| `artifact` | Disk output created by a service: raw JSON, downloaded files, manifests, extracted parts, reports, logs, model outputs. |
| `live polling` | Repeated current-window provider/source check. |
| `source sync` | Low-frequency reconciliation with external reference-like sources. |
| `initial fill` | First population of an empty or untrusted dataset. |
| `backfill` | Broad historical population over a large range. |
| `gap fill` | Repair of missing intervals inside already-known coverage. |
| `coverage` | Durable statement that a source interval was fetched, written, or verified empty. |
| `reconciliation` | Source-minus-output query that discovers missing work. |
| `preflight` | Required dependency checks before live work, provider fetches, database writes, or historical work. |
| `audit` | Post-write integrity validation over persisted data. |
| `maintenance` | Deferred heavier repair/sync/audit work. |
| `run log` | Structured JSONL operational log. It is not raw data storage. |
| `task ledger` | Stable list of lifecycle tasks and their status for terminal/API visibility. |
| `write policy` | Whether writes are allowed now: prod/temp/dry-run, market-hours allowed/deferred, workstation required. |
| `domain item` | Recent item meaningful to a service: news article, SEC filing, market event, reference issue, embedding batch, prediction batch. |

These terms must stay distinct:

```text
initial fill != backfill != gap fill != reconciliation != coverage != audit
```

Initial fill and backfill write data. Gap fill repairs missing intervals.
Reconciliation discovers missing work. Coverage records completed/empty
intervals. Audit checks persisted data correctness.

## Required Lifecycle

Every gateway should follow this order:

```text
load config
-> resolve storage
-> open structured run log
-> run dependency preflight
-> ensure schemas
-> prepare coverage manifest
-> run reconciliation
-> plan startup work
-> start live ingest or polling
-> start background workers
-> expose API and terminal status
-> audit writes
-> graceful shutdown
-> drain required queues
-> finalize coverage
```

No live polling, provider fetch, database write, or historical backfill should
start before preflight succeeds.

Not every service performs every step, but skipped or disabled steps should be
visible in the task ledger and dashboard. For example, a model-serving service
may have no coverage manifest, but it still has preflight, model load, runtime
state, API health, and graceful shutdown.

## Shared Gateway Core

Shared behavior should live in a small reusable service layer. The shared layer
must provide contracts, policies, formatters, and helpers. It must not hide
domain logic in a large base class.

Recommended package shape:

```text
services/gateway_core/
  types.py
  config.py
  lifecycle.py
  preflight.py
  storage.py
  coverage.py
  reconciliation.py
  backfill.py
  provider.py
  schedule.py
  audit.py
  logging.py
  dashboard.py
  rich_renderer.py
  health.py
  errors.py
```

Shared concepts:

| Module | Owns |
| --- | --- |
| `types.py` | Shared enums and dataclasses such as service status, task status, severity, coverage status, work mode, write mode, provider status. |
| `config.py` | Common grouped config objects and env naming conventions. |
| `preflight.py` | Ordered dependency checks and `PreflightReport`. |
| `storage.py` | Workstation-first storage resolution and artifact/log root checks. |
| `coverage.py` | Coverage table helpers, interval compaction, and gap detection primitives. |
| `reconciliation.py` | Source-minus-output planning contracts. |
| `backfill.py` | Inline/deferred/workstation script planning policy. |
| `provider.py` | Timeout, retry, rate-limit, and provider-status contracts. |
| `schedule.py` | Market-aware cadence and source-sync schedule helpers. |
| `audit.py` | Standard audit result shape. |
| `logging.py` | Structured JSONL logging, redaction, async queue behavior. |
| `dashboard.py` | JSON-serializable dashboard state contract. |
| `rich_renderer.py` | Shared Rich renderer for the standard dashboard panels. |
| `health.py` | Consistent `/health`, `/config`, `/metrics`, and `/snapshot/status` shapes. |

Domain logic should remain inside the owning service:

- SEC filing parsing and XBRL extraction.
- Benzinga normalization, URL policy, and enrichment.
- QMD quote/trade parsing, compact events, bars, indicators, and scanner
  primitives.
- Reference identity resolution, conid selection, issue resolution, and
  tradability decisions.
- IBKR login/session mechanics.
- Tokenization, embedding, and model inference.

## Service Designs

This section maps the current service code into the shared gateway model. It is
intended as the review surface for future refactors: current behavior should be
made to converge on these policies, and new services should be added here before
their implementation spreads into separate conventions.

### Service Matrix

| Service | Type | Primary sources | Primary sinks | Cadence | Canonical responsibility |
| --- | --- | --- | --- | --- | --- |
| QMD Gateway | high-rate Rust streaming gateway | Massive stock websocket `T.*`, `Q.*`; Massive REST repair; historical `market_sip_compact.events_<year>` coverage | `q_live.events`, live continuity rows, 1d live bars, sparse abnormal market-state rows, QMD coverage tables, local streams | continuous websocket plus startup/after-hours repair | Lossless live market-event capture, 1d bar persistence, compact streams, and Massive-only scanner primitives. |
| News Gateway | Python REST/text gateway | Massive-served Benzinga REST, approved external URL/PDF artifacts | `q_live.benzinga_news_normalized_v1`, `q_live.benzinga_news_ticker_v1`, coverage manifest, raw artifacts | market-aware polling | Canonical Benzinga news rows and ticker links with async enrichment. |
| SEC Gateway | Python SEC filing gateway | SEC current Atom feed, submissions JSON, companyfacts JSON, daily archives | `q_live.sec_filing_v2`, `sec_filing_document_v2`, `sec_filing_text_v2`, SEC XBRL tables, SEC coverage | market-aware polling plus historical gap fill | Canonical SEC filing/text/XBRL rows. |
| Reference Gateway | Python low-frequency reference reconciler | Massive reference endpoints, q_live identity tables, IBKR Client Portal, FINRA/SEC/Massive publications | identity graph, source mappings, issues, tradable/scanner publications, market reference publications, reference alerts | daemon cycles and after-hours maintenance | Keep market reference identity, conid/routing evidence, tradability publications, and slow reference publications coherent. |
| Text Embed Gateway | Python GPU/model reconciliation gateway | normalized news, SEC filing text, SEC market bridge, historical-compatible context tables | token tables, embedding tables, embedding coverage | market-aware polling and historical reconciliation | Keep news and SEC text tokenized and embedded with historical-compatible contracts. |
| IBKR Gateway Supervisor | Python broker-session supervisor | local IBKR Client Portal Gateway process and CPAPI auth endpoints | JSONL events, optional compact ClickHouse supervisor event table, Rich telemetry | fixed keepalive/status cadence | Keep one IBKR Client Portal session authenticated and observable. |
| News Intelligence Service | Python model-serving service | normalized article request payloads, local model artifacts, optional local LLM endpoint | synchronous classification response | request-driven | Serve news labels; it must not poll providers or write canonical news rows. |
| Market AI Service | Python ML serving/replay boundary | QMD compact event stream or replay iterator | prediction stream/API, in-memory model state, future prediction tables when defined | event-driven | Convert compact events into model chunks, run encoder/temporal inference, and publish predictions. |
| Maintenance Runner | Python offline coordinator | service coverage/source tables | maintenance run/task tables, reports, generated commands | after-hours or manual | Coordinate service-specific gap checks without owning domain data. |

### QMD Gateway

**Current code path:** `services/qmd-gateway`.

**Role:** QMD is the only high-frequency market data service. It stays narrow:
Massive quote/trade ingest, compact live events, bars, live abnormal
market-state overlay, Massive-only scanner primitives, and market-data repair.
It must not own broker orders, account state, portfolios, conids, logos,
fundamentals, issuer identity, or final trading signals.

**Sources:**

- Massive websocket stock trades and quotes, normally wildcard `T.*` and `Q.*`.
- Massive REST trades/quotes for recent q_live gap repair.
- Massive market status and holiday endpoints for session state, startup
  maintenance scheduling, and terminal/API market-state display.
- `market_sip_compact.events_<year>` tables for historical flatfile event data.
  The correct table is selected by event year, for example
  `market_sip_compact.events_2026` for 2026 events.
- Historical flatfile continuity/publication metadata created by the updated
  `download_update_events.py` pipeline. That script is the authoritative
  historical flatfile update path.
- Local Massive reference files for quote/trade condition packing.

**Sinks and durable contracts:**

- `q_live.events`: compact live market event stream/table for the current
  short live-retention window. This table is not a permanent historical store.
  The default retention is the current day plus 3 prior market days. Older rows
  are stale after maintenance verifies that the corresponding
  `market_sip_compact.events_<year>` table contains the same historical
  coverage. After verification, maintenance should delete q_live rows older
  than the retention window to protect SSD storage.
- `q_live.events` schema/encoding is version-sensitive. If the compact event
  encoding algorithm changes or a bug is found in the encoded structure, the
  table may need to be recreated from a clean slate. Historical data should be
  recovered from `market_sip_compact.events_<year>` plus the recent q_live
  retention window, not by keeping stale encoded q_live rows forever.
- `q_live.live_event_ordinal_continuity`: append-only ticker-local ordinal
  continuity snapshots.
- `q_live.live_market_bars`: 1d bars only. Intraday bars can exist in memory,
  streams, or downstream app caches, but the standard q_live persistent bar
  contract is daily bars unless a separate durable intraday bar table is
  explicitly designed.
- `q_live.live_symbol_market_event_v1`: sparse abnormal state open/close audit
  rows. Ordinary `normal` state is not persisted. The purpose of this table is
  to retain compact exceptional state transitions such as halt/resume evidence,
  estimated LULD near/breach transitions, or locked/crossed quote transitions
  without scanning the full event stream. This table is justified only if the
  trading app, audit process, or model review needs a durable record of these
  sparse exceptional states. If no consumer uses it, it should be disabled or
  removed rather than expanded into a large state table.
- `q_live.qmd_live_event_coverage_v1`: recent live compact-event and 1d-bar
  coverage.
- `q_live.qmd_flatfile_event_coverage_v1`: historical flatfile coverage.
- `q_live.qmd_gap_fill_symbol_universe_v1`: durable ticker queue for recent
  live repair.
- `q_live.qmd_gap_fill_runs`: repair audit log.
- Stale/non-standard tables `live_massive_trades`, `live_massive_quotes`, and
  `live_market_indicators` must not be part of the standard QMD persistence
  path. QMD should not persist raw quotes, raw trades, or materialized
  indicators in q_live. Indicators are computed from events/bars in memory or
  on demand from persisted bars/events.

**Hot path policy:**

- Required quote/trade paths must wait rather than discard data. Practically,
  if a required internal queue is full, the websocket processing path slows down
  until the required worker catches up. This is what this document means by
  `backpressure`. It protects canonical market events from being silently lost.
- Local websocket broadcasts are best effort. If a UI client is disconnected or
  too slow, the service may skip sending some transient UI updates to that
  client and report the skipped count. The canonical event is still processed
  and persisted; only the temporary client message can be skipped.
- Bar, indicator, scanner primitive, compact-event, and live-market-state work
  must receive the same normalized `MarketEvent` stream.
- Indicators are kept in memory or computed later from bars/events. Persisted
  indicator rows should remain removed from the standard service because they
  duplicate data that can be reconstructed.

**Coverage and gap fill:**

- Recent q_live gaps are discovered from `qmd_live_event_coverage_v1`, not from
  min/max event timestamps.
- A recent interval is clean only where compact-event and bar coverage overlap,
  or where an explicit completed repair row covers it.
- Repair covers the current market day plus
  `QMD_RECENT_LIVE_PRIOR_MARKET_DAYS` prior US market sessions, inside
  04:00-20:00 ET.
- Gap repair converts Massive REST rows into the same `MarketEvent` type as the
  websocket and sends them through the same state, bars, scanner, indicator, and
  persistence fanout.
- When no live symbols exist yet, QMD seeds the repair universe from the latest
  configured historical sessions; during streaming hours it also adds
  websocket-discovered symbols to the durable universe queue.
- Historical flatfile gaps are planned from the year-partitioned
  `market_sip_compact.events_<year>` tables and their continuity metadata. QMD
  must not merge q_live live rows into the historical flatfile database. The
  historical update path is `download_update_events.py`.
- Daily maintenance must coordinate q_live retention with the flatfile update
  path. Before q_live rows older than the 3-prior-market-day retention window
  are deleted, maintenance must verify that the corresponding
  `market_sip_compact.events_<year>` coverage exists. This allows the trading
  app to read old history from `market_sip_compact.events_<year>` and use
  `q_live.events` only for the current/recent window that flatfiles have not
  safely covered yet.
- QMD may support one explicit destructive mode named `init-reset`. This mode
  is QMD-only and must not be generalized to News, SEC, Reference, Text Embed,
  or IBKR tables. `init-reset` drops and recreates QMD-owned q_live live/recent
  tables, including event, ordinal, bar, coverage, symbol-universe, gap-fill,
  and optional sparse abnormal-state tables. It is intended for first clean
  startup, compact-encoding reset, or ordinal reset when rolling q_live event
  retention has made ordinal counters unnecessarily large.
- `init-reset` is not blocked by market hours. If the operator explicitly
  starts QMD in this mode, it should reset the QMD-owned tables whether the
  market is open or closed. The reset command still must be exclusive for QMD
  writers: another QMD process must not be writing the same tables while they
  are dropped and recreated.
- After `init-reset`, q_live live coverage is intentionally empty. The normal
  recent gap-fill path must then rebuild the current market day plus
  `QMD_RECENT_LIVE_PRIOR_MARKET_DAYS` prior market sessions from Massive REST.
  During market hours, the current-day recovery may run as a separate gap-fill
  process while live collection resumes. The reset mode itself should not add
  separate gap-fill knobs.

**Terminal/API:**

- QMD should expose the standard dashboard snapshot in addition to its current
  `/health`, `/config`, `/metrics`, `/snapshot/maintenance`,
  `/snapshot/coverage`, market snapshots, bar snapshots, indicator snapshots,
  scanner snapshots, compact-event streams, and live-market-state streams.
- The QMD terminal must show market status, websocket status, ingest rates,
  queue pressure, ClickHouse writer lag, coverage/repair progress, recent
  compact events, and abnormal market-state transitions using the shared panel
  vocabulary.

**Out of scope:**

- Reference tradability decisions.
- IBKR conid lookup or order routing.
- Final scanner/trade signals.
- Portfolio/order/fill state.

### News Gateway

**Current code path:** `services/news_gateway` and
`pipelines/news/benzinga/news_pipeline`.

**Role:** The News Gateway is the production Benzinga news ingestion service.
It polls the Massive-served Benzinga REST endpoint, saves raw payloads,
normalizes each item through the shared Benzinga item pipeline, enriches text in
background workers, writes canonical rows, and serves recent news to the app.

**Sources:**

- Massive-served Benzinga REST endpoint.
- Approved external article/PDF/plain-text URLs discovered from Benzinga items.
- Massive market status and holiday endpoints for active/closed cadence and
  startup/after-hours maintenance policy.

**Sinks and durable contracts:**

- Raw provider JSON artifacts:
  `<data-root>/news-benzinga/raw/YYYY/MM/DD/benzinga_<id>.json`.
- Live URL artifacts:
  `<data-root>/news-benzinga/live-url-artifacts`.
- `q_live.benzinga_news_normalized_v1`: final canonical normalized rows.
- `q_live.benzinga_news_ticker_v1`: article-to-ticker links.
- `q_live.benzinga_news_coverage_manifest_v1`: provider coverage.
- JSONL operational logs under
  `<data-root>/prepared/news_gateway/logs/<run_id>/`.

**Hot path policy:**

- The provider row is added to in-memory recent-news state immediately.
- Slow URL/PDF fetch, extraction, canonical normalization, and ClickHouse
  publish run in bounded background workers.
- ClickHouse receives final canonical rows only; partial in-memory pending rows
  are not inserted.
- Existing canonical news IDs are skipped and logged as duplicate/known rows.

**Schedule and coverage:**

- Active window: 04:00-20:00 ET. Premarket and after-hours count as active for
  news.
- Active cadence: poll every 5 seconds, fetch the last 5 minutes.
- Closed cadence: poll every 5 minutes, fetch the last 10 minutes.
- Polls align to wall-clock boundaries and intentionally overlap.
- Coverage bootstrap trusts the completed historical range, then verifies
  recent empty buckets with cheap provider probes when configured.
- Startup gaps up to the configured inline threshold are filled concurrently.
  Larger gaps generate workstation scripts or auto-run on the workstation
  outside the active collection window.

**Terminal/API:**

- The terminal should show dependency preflight, market status, live/background
  processing status, provider rows, unique/duplicate rows, raw saves, publish
  status, enrichment status, coverage/gap state, latest news, and active versus
  resolved failures.
- Failed transient enrichment/provider errors should not keep the full dashboard
  red after recovery. Active critical errors remain red.

**Out of scope:**

- Model labels and sentiment inference. Those belong to News Intelligence or a
  downstream inference service.
- SEC filing ingestion.
- Reference identity correction.

### SEC Gateway

**Current code path:** `services/sec_gateway` and
`pipelines/sec/edgar/sec_pipeline`.

**Role:** The SEC Gateway is the live service layer for SEC filings. It writes
canonical SEC filing parent, document, text, skip, and XBRL rows. It does not
own ticker/reference mappings or embeddings.

**Sources:**

- SEC current Atom feed for live filings.
- SEC submissions JSON for true acceptance metadata and filing lists.
- SEC companyfacts JSON for XBRL concepts and facts.
- SEC daily archives for historical filing text and documents.
- Massive market status/holiday endpoints for cadence and maintenance policy.

**Sinks and durable contracts:**

- `q_live.sec_filing_v2`.
- `q_live.sec_filing_document_v2`.
- `q_live.sec_filing_text_v2`.
- SEC XBRL concept, company fact, frame, and frame-observation tables.
- `q_live.sec_coverage_manifest_v1`.
- JSONL logs under the SEC gateway run-log root.

**Hot path policy:**

- Poll SEC current feed, enqueue new accessions, and process them in a bounded
  live worker pool.
- For each accession, canonicalize parent filing metadata from submissions,
  download the accession text, parse SGML documents, extract normalized text,
  fetch companyfacts when XBRL or inline-XBRL evidence exists, and write the
  configured SEC database.
- Cache submissions and companyfacts by CIK with bounded count and age.
- Missing SEC companyfacts for a CIK is a normal provider condition and should
  be cached as a missing-CIK state, not treated as a fatal gateway failure.

**Schedule and coverage:**

- Active cadence defaults to 30 seconds.
- Closed cadence defaults to 300 seconds.
- Coverage kinds include live feed, daily archives, bulk submissions,
  companyfacts, text extraction, integrity audit, and stage-level historical
  fill rows.
- The live run keeps one live coverage row open and updates it after successful
  feed fetches, including empty/all-duplicate polls.
- Historical gap fill is stage-aware. Completed stages can be skipped on resume;
  semantic coverage rows are written only after the full unified fill command
  succeeds.
- Large historical work should use the unified SEC historical gap-fill command,
  not a sequence of ad hoc old scripts.

**Terminal/API:**

- The terminal should show market status, feed items, live queue, active
  workers, written/skipped filings, XBRL row counts, cache sizes, coverage gaps,
  generated scripts, audit status, and recent filings.

**Out of scope:**

- `id_sec_market_bridge_v1`. Reference Gateway owns bridge maintenance.
- Text embeddings. Text Embed Gateway owns context/token/embedding output.
- Final news/SEC signal or LLM interpretation.

### Reference Gateway

**Current code path:** `services/reference_gateway` and
`pipelines/reference_data`.

**Role:** The Reference Gateway is a continuously runnable, low-frequency
reference reconciler. It keeps identity, source mapping, conid/routing evidence,
tradability publications, and market reference publications coherent. It is not
a high-frequency ingest service.

**Sources:**

- Massive active tickers and ticker-detail/reference endpoints.
- IBKR Client Portal contract search and borrow/shortability endpoints.
- Existing q_live canonical identity and SEC tables.
- FINRA daily short-volume publications.
- Massive short interest, splits, dividends, IPOs, market snapshots, floats,
  and presentation assets.
- SEC fails-to-deliver and country evidence where implemented.
- Massive market status and holiday endpoints for source-sync cadence,
  maintenance scheduling, and terminal/API market-state display.

**Sinks and table groups:**

- Reference dimensions: countries, asset classes, exchanges, exchange
  currencies, ticker types.
- Issuer/security/listing/symbol identity tables.
- Source mapping, mapping issue, and SEC-market bridge tables.
- `feature_tradable_universe_v1` and `feature_scanner_static_v1`.
- Market reference publications:
  `market_security_market_snapshot_v1`, `market_security_float_v1`,
  `market_short_interest_v1`, `market_short_volume_v1`,
  `market_stock_split_v1`, `market_cash_dividend_v1`, `market_ipo_v1`,
  `market_presentation_asset_v1`, `market_fails_to_deliver_v1`,
  `market_reg_sho_threshold_v1`, `market_security_borrow_v1`,
  `market_security_country_v1`, and
  `market_reference_publication_coverage_v1`.
- Reference alerts and source schedule rows.
- Canonical fact schemas where implemented; fact fillers must stay compact and
  not mirror every raw source row.

**Source-sync policy:**

- Operational runs always include source sync. It should not be a hidden
  per-endpoint operator flag.
- New Massive active ticker observations drive downstream updates: canonical
  identity graph rows when safe, IBKR conid resolution, current Massive
  ticker-detail/float/snapshot rows, presentation assets, IBKR borrow rows, and
  country assertions.
- If an observation cannot be inserted safely, the gateway writes an issue and
  keeps the affected instrument non-tradable.
- Provider cadence is stored in
  `market_reference_source_schedule_v1` so daemon restarts do not lose source
  sync state.

**Integrity policy:**

- In strict mode, reference issues must immediately block affected instruments
  from tradable publications.
- Deterministic resolvers may close stale issues when canonical evidence is now
  complete and unambiguous.
- Ambiguous mappings or conflicting durable identities require human review and
  remain non-tradable.
- Active trading is allowed during source sync and issue blocking. Heavy
  promotion/maintenance can be deferred during the active collection window.

**Maintenance policy:**

- Auto maintenance can run schema upkeep, deterministic issue resolution,
  SEC bridge rebuilds, full tradable/scanner rebuilds, and publication gap
  fills when policy allows.
- `Maintenance=Force` requires an auditable reason.
- Temp mode reads `q_live` and writes `q_reference_tmp`; production mode reads
  and writes `q_live`.

**Terminal/API:**

- The terminal should show preflight, current operation, source sync per
  endpoint, source coverage, reference table state, audit issue groups,
  tradability blocks, maintenance policy, and recent issues/resolutions.
- Each source endpoint should have a stable terminal row, not just a single
  generic "source sync" line.

**Out of scope:**

- Live quote/trade state. QMD owns live market-state transitions.
- Broker order execution and portfolio state.
- News/SEC text embeddings.

### Text Embed Gateway

**Current code path:** `services/text_embed_gateway`.

**Role:** The Text Embed Gateway is a GPU/model reconciliation service. It keeps
news and SEC text tokenized and embedded using the same tokenizer, model,
pooling, and ClickHouse contracts as the historical builders.

**Sources:**

- `q_live.benzinga_news_normalized_v1`.
- `q_live.sec_filing_v2` and `q_live.sec_filing_text_v2`.
- `q_live.id_sec_market_bridge_v1`.
- Historical-compatible context tables in `market_sip_compact`.
- Local Qwen tokenizer/model artifacts.
- Massive market status and holiday endpoints for live/closed/weekend polling
  cadence and historical-work scheduling.

**Sinks and durable contracts:**

- `market_sip_compact.news_text_tokens`.
- `market_sip_compact.news_text_embeddings`.
- `market_sip_compact.sec_filing_context`.
- `market_sip_compact.sec_filing_text_context`.
- `market_sip_compact.sec_filing_text_tokens`.
- `market_sip_compact.sec_filing_text_embeddings`.
- `market_sip_compact.text_embedding_coverage_v1`.

**Reconciliation policy:**

- The service compares source text rows to token rows, and token rows to
  embedding rows.
- SEC context refresh joins SEC filing/text rows with
  `id_sec_market_bridge_v1`. Rows without a valid bridge are reported as
  blocked and retried later; news and existing-token embedding continue.
- Live lookback is an optimization; historical reconciliation must be broad
  enough to pick up data inserted while the service was offline.
- The configured historical lookback has a code-enforced minimum of 60 days.

**Memory and GPU policy:**

- Load the model at startup and fail preflight/load checks if the model cannot
  be loaded in production mode.
- Do not retain article bodies, filing text, PDFs, or embedding arrays after a
  batch is written.
- On shutdown, cancel active ClickHouse queries where possible, finish the
  current persist step when safe, release model references, and clear CUDA
  cache.

**Terminal/API:**

- The terminal should preserve separate live-news, live-SEC, historical-news,
  and historical-SEC state so a quiet live cycle does not erase historical gap
  context.
- It should show source rows, token rows, embedding rows, coverage rows,
  current mode/source/stage/window, inference timing, insert timing, and GPU
  model status.

**Out of scope:**

- News/SEC ingestion and normalization.
- SEC-market bridge maintenance.
- Prediction labels or trading signals.

### IBKR Gateway Supervisor

**Current code path:** `services/ibkr_gateway_supervisor`.

**Role:** The IBKR supervisor keeps the local IBKR Client Portal Gateway process
running, authenticated, and observable. It does not bypass IBKR authentication
and does not own trading orders.

**Sources:**

- Local Client Portal Gateway install path and `run.bat`.
- IBKR CPAPI auth/status, reauth, accounts, and tickle endpoints.
- Playwright login helper when automated login is enabled.
- Massive market status and holiday endpoints for terminal/API session context
  and any non-auth maintenance scheduling. Broker keepalive/auth checks remain
  on fixed IBKR cadences because they protect the CPAPI session independently of
  equity market hours.

**Sinks and durable contracts:**

- Per-run JSONL event log under `tmp/ibkr_gateway_supervisor/<run_id>/`.
- Optional compact ClickHouse event table
  `q_live.ibkr_gateway_supervisor_event_v1`.
- Terminal telemetry for keepalive and active auth state.

**Runtime policy:**

- Launch CP Gateway if configured and not reachable.
- Check auth status on a fixed cadence.
- Attempt SSO reopen or Playwright login when required and allowed.
- Call `/tickle` on a fixed cadence.
- Routine successful tickles are telemetry only unless status changes; status
  transitions are logged.
- If ClickHouse logging is unavailable, continue JSONL logging and terminal
  supervision.

**Terminal/API:**

- Show gateway process, auth state, account state, login attempts, keepalive
  tickle state, retry counters, active failures, resolved failure history, and
  alert delivery status.

**Out of scope:**

- Portfolio/order/fill persistence.
- Reference identity resolution.
- Market-data ingestion.

### News Intelligence Service

**Current code path:** `services/news-intelligence`.

**Role:** News Intelligence is a request-driven model service. It receives one
normalized article, runs configured fast models and optional local LLM stages,
and returns labels with model/taxonomy/prompt versions.

**Sources:**

- Normalized article request body from a caller.
- Local model artifacts under the configured model root.
- Optional OpenAI-compatible local LLM endpoint such as vLLM.
- Massive market status and holiday endpoints when the service adds scheduled
  warmup, cache refresh, batch inference, or maintenance work. Request-time
  classification remains caller-driven.

**Sinks and durable contracts:**

- Synchronous `/classify` response.
- It does not own ClickHouse writes. The caller maps labels to persistence.

**Runtime policy:**

- Serve `/health`, `/models`, and `/classify`.
- Degrade to deterministic fallback labels when optional models are missing if
  configured to keep ingestion paths non-blocking.
- Keep prompt/model/taxonomy versions explicit in responses.

**Out of scope:**

- Provider polling.
- News normalization/enrichment.
- Canonical news persistence.

### Market AI Service

**Current code path:** `services/market-ai`.

**Role:** Market AI is the ML serving/replay boundary for compact market-event
models. It consumes QMD compact events, builds fixed event chunks, batches
encoder inference, keeps per-ticker embedding state, batches temporal inference,
and publishes predictions when a production publishing contract is defined.

**Sources:**

- QMD compact-event websocket.
- Synthetic or historical replay iterator for validation/training reuse.
- Production model checkpoints once configured.
- Massive market status and holiday endpoints for session labeling, live versus
  closed scheduling, warmup policy, and maintenance/replay timing.

**Sinks and durable contracts:**

- In-memory event rings, chunk batches, embedding rings, and prediction output.
- Future prediction tables/streams must be defined before production write
  mode is enabled.

**Runtime policy:**

- Work is emitted only for tickers that update; the service must not rebuild a
  whole-market batch at every timestamp.
- Live serving and offline training should use the same batching engine so
  representation does not drift.
- Invalid/lossy windows are controlled by model config; strict lossless windows
  are the default.

**Terminal/API:**

- Show source status, events received, chunks created, encoder/temporal batches,
  predictions, queue depths, event/chunk/sample rates, model timing, and recent
  messages/errors.

**Out of scope:**

- QMD market-data persistence.
- Reference identity and tradability.
- Broker execution.

### Maintenance Runner

**Current code path:** `services/maintenance`.

**Role:** The maintenance runner coordinates after-hours checks. It does not
replace the gateways and does not own domain data. It inspects durable source
and coverage tables, records maintenance task rows, and generates or runs the
same service-specific gap-fill commands the gateways use.

**Sources:**

- QMD coverage and `market_sip_compact.events_<year>` source tables.
- News coverage manifest and Benzinga provider.
- SEC coverage manifest and SEC historical sources.
- Reference publication coverage and publication source tables.
- Massive market status and holiday endpoints for the shared after-hours
  maintenance decision.

**Sinks:**

- `q_live.service_maintenance_run_v1`.
- `q_live.service_maintenance_task_v1`.
- `<market-data>/prepared/service_maintenance/<run_id>/` reports.

**Policy:**

- Do not stop or restart live services.
- Use the shared active collection window and Massive market status/holiday
  provider.
- Auto-run is allowed only when the operator enables it and policy allows.
- Generated commands must use the same service-specific code path as live
  gateway gap fills.

### Cross-Service Dependency Rules

These relationships are durable-source relationships, not event-bus
requirements:

| Producer | Consumer | Consumer action |
| --- | --- | --- |
| QMD compact events and 1d bars | Market AI, trading app, maintenance | Consume snapshots/streams for inference and trading context; use q_live coverage for recent repair checks and `market_sip_compact.events_<year>` for historical event history. |
| News normalized rows | Text Embed, trading app, News Intelligence caller | Reconcile missing tokens/embeddings or classify rows without requiring news gateway to emit a durable event. |
| SEC filing/text/XBRL rows | Reference, Text Embed, trading app | Rebuild SEC-market bridge, build SEC context, tokenize/embed text, and expose filing/XBRL context. |
| Reference identity/tradable publications | Trading app, scanner setup, QMD-adjacent consumers | Treat `is_tradable=0` as a hard reference block; live market-state blocks are separate runtime context. |
| Reference SEC bridge | Text Embed | Embed only SEC text rows with valid market bridge; retry blocked rows after bridge updates. |
| IBKR supervisor auth/session | live trading backend, Reference conid/borrow sync | Use CPAPI only after supervisor/preflight reports the broker session is reachable. |
| Text Embed embeddings | Market AI / downstream inference | Use embeddings by source/version; missing embeddings are discovered by source-minus-output reconciliation. |
| Maintenance task rows | operators and service dashboards | Observability only; services must still reconcile durable source/output state. |

The default implementation pattern is:

```text
producer writes canonical table and coverage
consumer periodically reconciles source table minus its output table
consumer writes its own coverage/output
maintenance audits both sides after hours
```

Use explicit service-to-service events only for low-latency live UI/model
delivery. Do not rely on them as the only mechanism for correctness or
historical catch-up.

## Storage Rule

Service data belongs on workstation storage first:

```text
D:/market-data
```

From the laptop, services should use:

```text
\\DESKTOP-SAAI85T\Workstation-D\market-data
```

If that storage is not available, the service should fail with a clear message.
It must not silently write service artifacts to laptop-local storage.

## Market Session Source Of Truth

All services must use the same market-session source for cadence and
maintenance decisions:

```text
Massive market status endpoint
Massive market holidays/upcoming endpoint
```

The local New York extended-hours clock is only a fallback when Massive status
is temporarily unavailable. It must not become a separate source of truth for
one service while other services use Massive. The goal is that QMD, News, SEC,
Reference, Text Embed, IBKR Supervisor, Market AI, News Intelligence, and the
maintenance runner all agree on whether the system is in active collection,
closed-market, holiday, early-close, or maintenance mode.

Service rules:

- Scheduled polling, source sync, background reconciliation, gap fill,
  after-hours maintenance, and terminal/API market-state display use Massive
  status/holiday data.
- Services may use domain-specific fixed cadences only when the task is not a
  market-data cadence. Example: IBKR `/tickle` and auth checks protect the
  broker session and stay on fixed broker cadences.
- If Massive status is unavailable, the service should continue only if it can
  safely use the documented local fallback. The dashboard and JSONL log must
  show that the market state is fallback-derived.
- Market-session state should be exposed in `/snapshot/status` and the standard
  terminal header/current-operation panels.

## Active Collection Window

The shared active collection window is:

```text
04:00-20:00 ET
```

This includes premarket, regular market, and after-hours. Heavy historical
backfills should not auto-run during this window. The maintenance window is
everything outside it.

Python services use `services.gateway_policy` for this rule. Service-specific
overrides are allowed:

```text
NEWS_GATEWAY_COLLECTION_START_ET=04:00
NEWS_GATEWAY_COLLECTION_END_ET=20:00
SEC_GATEWAY_COLLECTION_START_ET=04:00
SEC_GATEWAY_COLLECTION_END_ET=20:00
```

QMD uses the same rule through its Rust session phase logic.

## Backfill Policy

All services should use the same policy:

- Small/recent gaps may be filled inline if they do not threaten live collection.
- Large gaps on a laptop or remote host generate workstation-ready scripts.
- Large gaps on the workstation auto-run only outside the active collection
  window.
- Large gaps found during the active collection window are generated and
  reported, but deferred.
- Generated scripts should end with repair and audit stages when the data domain
  has integrity checks.

Backfill and gap fill should be driven by reconciliation and coverage, not by
short fixed lookbacks alone. A consumer must be able to discover historical work
that was inserted by a script or another service while the consumer was offline.

Examples:

```text
text_embed_gateway:
  q_live.benzinga_news_normalized_v1
  minus market_sip_compact.news_text_tokens/news_text_embeddings

  q_live.sec_filing_text_v2 + q_live.id_sec_market_bridge_v1
  minus market_sip_compact.sec_filing_text_context/sec_filing_text_tokens/sec_filing_text_embeddings

reference_gateway:
  q_live.sec_filing_v2 / issuer identifiers
  minus q_live.id_sec_market_bridge_v1

  Massive active tickers
  minus q_live identity graph / conid mappings
```

Lookback windows are an optimization for live polling, not the authoritative
method for finding durable work.

## Queue Policy

Queue sizes should be large enough that normal bursts do not create lag.
However, a large in-memory queue is not the final reliability mechanism.

Canonical data is lossless. Here, `lossless` means the service must not
intentionally ignore, skip, or discard a source row that is part of the durable
contract. If the service cannot process a required row immediately, it should
slow intake, queue the work, spill to disk, fail loudly, or mark the service
blocked. It should not silently continue as if the row never existed.

- QMD canonical market events require a lossless capture path.
- News canonical article rows must not be silently ignored.
- SEC filing and XBRL rows must not be silently ignored.

Best-effort outputs may skip transient delivery only when the consumer can
recover from snapshots or durable tables. In this context, `drop` means "do not
send this temporary UI/websocket message to this slow or disconnected
consumer"; it must not mean "lose the underlying canonical data."

- UI websocket broadcasts
- preview streams
- transient dashboard updates

Any best-effort skipped delivery must be counted in metrics and logs.

For QMD, the target design is:

```text
Massive websocket
-> large hot memory queue
-> overflow memory queue
-> disk spill queue
-> replay into required processors
-> optional UI streams
```

Bars, scanner primitives, and in-memory/on-demand indicators should derive from
the canonical event stream. If they lag, the service should show replay lag and
queue pressure rather than silently losing required data. QMD should not persist
raw quotes, raw trades, or materialized indicator rows as part of the standard
q_live contract.

## Coverage Policy

Every service should maintain a coverage manifest when the service owns an
interval-based data capture or processing responsibility.

Rules:

- One live service run opens one coverage row.
- The coverage end advances only after durable write succeeds or a provider
  interval is verified empty.
- Adjacent intervals are compacted.
- Gaps are detected from coverage rows, not only from max timestamps.
- A killed service resumes from the last confirmed coverage end.

Coverage is a statement about the service's own responsibility. It does not
replace reconciliation against upstream and downstream tables.

For example, SEC coverage may prove `sec_filing_text_v2` is populated over a
range. Text embedding still needs its own reconciliation and coverage to prove
that the same range has context rows, token rows, and embedding rows.

## Preflight Policy

Preflight should check:

- required environment variables
- source provider reachable
- ClickHouse reachable
- target tables exist or can be created
- artifact and log roots writable
- storage policy available when required

Failing preflight blocks the rest of the service.

## Logging Policy

Every service should write structured JSONL operational logs under:

```text
<data-root>/prepared/<service>/logs/<run_id>/<service>_events.jsonl
```

Logs should include status and identifiers, not raw data or secrets.

Required log classes:

- phase transitions
- dependency checks
- queue pressure
- provider calls
- database write summaries
- skipped and duplicate reasons
- gap decisions
- reconciliation decisions
- error type, message, and enough identifiers to debug

## Terminal Policy

Every service terminal should behave like a structured operations dashboard,
not a custom status page. The terminal must answer these questions quickly:

1. What service is running, in what mode, and against what database/storage?
2. Is it healthy, degraded, blocked, catching up, or failed?
3. What is it doing right now?
4. What tasks has it already completed in this run?
5. What upstream sources/providers is it watching?
6. What downstream tables/streams is it writing?
7. Are there coverage gaps or reconciliation work pending?
8. Are queues and workers healthy?
9. Are dependencies currently available?
10. What are the recent important items, warnings, and errors?

Every service terminal should use the same fixed panel order. Panels may be
compacted on small terminals, but their meaning and relative order should not
change.

```text
Header
Current Operation
Configuration And Mode
Dependencies
Runtime Summary
Work Plan / Task Ledger
Queues And Workers
Coverage / Reconciliation
Sources And Sinks
Recent Domain Items
Warnings And Errors
Service-Specific Detail Panels
```

The terminal is for monitoring. JSONL logs are the debugging source of truth.

### Status Vocabulary

All terminals should use the same high-level service states:

```text
STARTING
PREFLIGHT
RUNNING
IDLE
WORKING
CATCHING_UP
DEGRADED
BLOCKED
STOPPING
FAILED
```

Color policy:

| Color | Meaning |
| --- | --- |
| green | Healthy, running, or idle. |
| blue | Working or catching up. |
| yellow | Degraded, warning, or manual action needed. |
| red | Active critical failure. |
| gray | Disabled, skipped, or not applicable. |

Resolved transient errors should remain visible in history but must not keep the
whole dashboard red.

Task rows should use this status vocabulary:

```text
waiting
running
completed
skipped
deferred
blocked
failed
```

### Required Panels

**Header**

Always visible. It should show:

```text
service name
overall status
run id
host
bind/API URL
mode: prod/temp, once/daemon, execute/dry-run
read database
write database
data root
UTC / ET / local time
market/session state when relevant
```

**Current Operation**

Always visible. This is the "what is it doing right now?" panel.

Required fields:

```text
phase
status
started_at
elapsed
message
current item/range if applicable
progress if measurable
next action / next poll
```

Messages must wrap. Critical paths, commands, and error messages must not be
silently truncated. If the terminal must shorten a value, the full value must be
available in JSONL logs and `/snapshot/status`.

**Configuration And Mode**

Show effective parameters that affect behavior. Do not dump every environment
variable.

Common examples:

```text
poll interval
active/closed schedule
lookback window
gap/backfill policy
worker counts
batch sizes
write mode
storage root
```

Service examples:

- QMD: subscriptions, flush interval, bar timeframes, recent gap-fill days, raw
  persistence enabled/disabled.
- News: active/closed poll cadence, lookback windows, enrichment workers,
  background publish batch size.
- SEC: poll cadence, worker count, request pacing, write database, historical
  auto-run policy.
- Reference: source-sync cadence, maintenance policy, integrity mode, IBKR
  required status.
- Text Embed: model, device, source batch size, embedding batch size,
  historical lookback, SEC context chunk size.

**Dependencies**

Fixed table:

```text
Dependency | Status | Last Check | Latency | Detail
```

Examples:

```text
ClickHouse
Massive REST
Massive WebSocket
SEC endpoint
IBKR Client Portal
artifact storage
model files/GPU
local LLM endpoint
```

A service must not show `RUNNING` if a required dependency has failed. It should
show `BLOCKED` or `DEGRADED` depending on whether live work can continue.

**Runtime Summary**

Small numeric table with total and last-cycle values:

```text
Metric | Total | Last Cycle | Detail
```

Examples:

```text
polls
provider rows
processed rows
written rows
skipped existing
failed rows
active queries
last cycle seconds
```

Numbers should be right-aligned and close enough to labels to scan quickly.

**Work Plan / Task Ledger**

Every service should show a stable list of lifecycle tasks. Rows should update;
they should not appear and disappear randomly.

Standard columns:

```text
Task | Status | Rows | Progress | Started | Elapsed | Detail
```

Common task names:

```text
preflight
schema ensure
coverage bootstrap
startup reconciliation
startup gap fill
live polling / websocket ingest
background publish
audit
maintenance
graceful shutdown
```

If a task does not apply, show `not_applicable` or omit it by documented service
type. Do not hide a running or failed task.

**Queues And Workers**

Required for services with background workers:

```text
Queue/Worker | Status | Depth | Active | Done | Failed | Lag | Detail
```

Examples:

```text
news enrichment
SEC live workers
QMD compact writer
QMD bar writer
text embedding batches
reference source sync
```

**Coverage / Reconciliation**

This panel should show source-vs-output status, not only newest timestamps:

```text
Domain | Source Range | Output Range | Missing | Status | Action
```

Examples:

```text
news normalized -> news embeddings
SEC text + bridge -> SEC embeddings
QMD live events -> bars
SEC filings -> SEC bridge
reference sources -> tradable universe
```

For large gaps, show whether work is inline, deferred to the workstation,
waiting for after-hours, or blocked. Show generated script paths and command
manifests when they exist.

**Sources And Sinks**

This panel tells the operator what the service owns:

```text
Kind | Table/Endpoint | Role | Rows/State | Freshness | Status
```

Examples:

```text
source | q_live.sec_filing_text_v2 | upstream | rows | latest accepted_at | ok
source | q_live.id_sec_market_bridge_v1 | required bridge | rows | latest update | ok
sink | market_sip_compact.sec_filing_text_embeddings | output | rows | latest embed | lagging
```

**Recent Domain Items**

Service-specific table with standard intent:

- News: published time, tickers, title, process status, flags.
- SEC: accepted time, CIK, form, accession, mapped ticker if available, status.
- QMD: event time, ticker, event type, price/bar/state, persist status.
- Reference: source, ticker, action, issue/resolution, status.
- Text Embed: time, source, ticker, source id, tokens, embedding status.
- IBKR: event time, account/session/auth state, keepalive status.
- Market AI: event time, ticker, chunk/inference/prediction status.

**Warnings And Errors**

Separate active problems from history:

```text
Active Critical
Active Warning
Resolved This Run
Recent Error History
Manual Action Required
```

Each error row must include enough identifiers to find the JSONL log entry.
Long errors must wrap. Raw payloads and secrets must never be rendered.

### Rendering Policy

- Use one Rich `Live` instance per service.
- Use fixed panel order and stable row identities.
- Do not print routine logs while the Rich dashboard is active. Logs go to
  JSONL.
- Startup/preflight messages may print before Rich starts.
- Refresh no faster than necessary, normally around one second.
- Do not add/remove rows on every refresh. Update values in stable rows.
- Long text columns must wrap or be deliberately shortened with full value in
  JSONL and `/snapshot/status`.
- Terminals must have compact and full modes based on width/height.
- The Rich terminal is not a full data browser. Large lists should show latest
  or highest-priority rows plus hidden-row counts.

Rich limitations:

- Panels and tables are not independently scrollable.
- `screen=True` usually removes normal terminal scrollback.
- Rich is not suitable for browsing thousands of rows.
- If terminal interactivity or scrolling is needed, use a React UI or a Textual
  application instead of expanding the Rich dashboard.

### Shared Dashboard State

The terminal and React UI should render the same JSON-serializable dashboard
state. Service hot paths update in-memory state; dashboards read cached
snapshots only. Dashboards must never query providers or ClickHouse directly.

Recommended flow:

```text
service internals
-> in-memory DashboardState
-> Rich renderer refreshes every ~1s
-> /snapshot/status returns the same state
-> React dashboard polls or subscribes to the same state
```

The shared state should be shaped around:

```text
header
current_operation
configuration
dependencies
runtime
tasks
queues
coverage
sources_sinks
recent_items
warnings_errors
service_specific
```

React dashboards should use pagination or virtualization for large lists. A UI
dashboard should not slow the service when it reads cached state, caps lists,
and coalesces updates.

### Service-Specific Panels

Service-specific panels are allowed only after the standard panels. They add
domain detail but must not replace the standard operational view.

Examples:

- IBKR keepalive tickle panel.
- QMD bar timeframe, websocket, market-state, and repair panels.
- Text embedding GPU/model timing panel.
- Reference table group and source-coverage panels.
- News enrichment artifact panel.

## API Policy

Every service exposes:

```text
/health
/config
/metrics
/snapshot/status
/snapshot/<domain>/recent
/stream/<domain>
```

Domain-specific endpoints are allowed after these standard endpoints.

The terminal should render from the same state exposed by `/snapshot/status`.
If the terminal shows `blocked`, `catching_up`, `degraded`, or a pending manual
action, the API should expose the same state.

## Audit Policy

Each service must define a post-write audit contract.

- QMD: event continuity, sequence gaps, bar completeness, spill replay lag.
- News: duplicate canonical ids, ticker links, text presence, coverage
  integrity.
- SEC: filing parent integrity, document/text integrity, XBRL parent integrity,
  coverage integrity.
- Reference: identity graph integrity, conid/routing ambiguity, tradability
  publication integrity, market-publication coverage integrity.
- Text Embed: source/context/token/embedding reconciliation, model metadata
  consistency, embedding dimensionality consistency.

Large historical backfills should finish by running the audit contract.

## Shared Config Groups

Future services should use grouped config objects and consistent environment
names. Service-specific settings can extend these groups but should not redefine
their meaning.

Recommended groups:

```text
ServiceIdentityConfig
  service_name
  run_id
  host
  bind
  mode              # prod/temp
  run_mode          # daemon/once/check-only
  execute

ClickHouseConfig
  url
  user
  password_present
  read_database
  write_database
  storage_policy

StorageConfig
  data_root_win
  artifact_root_win
  prepared_root_win
  log_root_win
  require_workstation_storage

ScheduleConfig
  active_start_et
  active_end_et
  active_poll_seconds
  closed_poll_seconds
  weekend_poll_seconds
  market_status_enabled
  market_status_refresh_seconds

CoverageConfig
  coverage_table
  bootstrap_enabled
  compact_on_startup
  max_inline_gap_days
  trusted_start_utc
  trusted_end_utc

BackfillConfig
  auto_run_on_workstation
  defer_during_active_window
  generated_script_root
  worker_count
  batch_size

DashboardConfig
  rich_enabled
  screen_enabled
  refresh_seconds
  compact_height
  recent_item_limit

AuditConfig
  startup_audit
  post_write_audit
  full_audit_frequency
  fail_on_critical

ProviderConfig
  name
  endpoint
  rate_limit
  timeout
  retry_policy
```

Recommended environment naming for new services:

```text
<SERVICE>_BIND
<SERVICE>_MODE
<SERVICE>_RUN_MODE
<SERVICE>_EXECUTE
<SERVICE>_READ_DATABASE
<SERVICE>_WRITE_DATABASE
<SERVICE>_DATA_ROOT_WIN
<SERVICE>_ARTIFACT_ROOT_WIN
<SERVICE>_LOG_ROOT_WIN
<SERVICE>_COVERAGE_TABLE
<SERVICE>_ACTIVE_POLL_SECONDS
<SERVICE>_CLOSED_POLL_SECONDS
<SERVICE>_LIVE_LOOKBACK_SECONDS
<SERVICE>_HISTORICAL_LOOKBACK_DAYS
<SERVICE>_MAX_INLINE_GAP_DAYS
<SERVICE>_AUTO_RUN_HISTORICAL_ON_WORKSTATION
<SERVICE>_TERMINAL_RICH_ENABLED
<SERVICE>_TERMINAL_REFRESH_SECONDS
```

Existing environment names can remain for compatibility. New code should map
legacy names into the grouped config object and expose the normalized values in
`/config` and the terminal `Configuration And Mode` panel.
