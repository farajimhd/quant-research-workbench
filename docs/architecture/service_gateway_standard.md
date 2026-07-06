# Service Gateway Standard

This document defines the operating convention for QMD, News, SEC, Reference,
Text Embed, IBKR Supervisor, Market AI, and future data services in this repo.

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

Canonical data is lossless:

- QMD canonical market events require a lossless capture path.
- News canonical article rows must not be silently dropped.
- SEC filing and XBRL rows must not be silently dropped.

Best-effort outputs may drop only when the consumer can recover from snapshots:

- UI websocket broadcasts
- preview streams
- transient dashboard updates

Any best-effort drop must be counted in metrics and logs.

For QMD, the target design is:

```text
Massive websocket
-> large hot memory queue
-> overflow memory queue
-> disk spill queue
-> replay into required processors
-> optional UI streams
```

Bars, indicators, and scanners should derive from the canonical event stream.
If they lag, the service should show replay lag and queue pressure rather than
silently losing required data.

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
