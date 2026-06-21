# Service Gateway Standard

This document defines the operating convention for QMD, News, SEC, and future
data gateways in this repo.

## Required Lifecycle

Every gateway should follow this order:

```text
load config
-> run dependency preflight
-> open structured run log
-> prepare coverage manifest
-> plan startup gaps
-> start live ingest or polling
-> start background workers
-> expose API and terminal status
-> graceful shutdown
-> drain required queues
-> finalize coverage
```

No live polling, provider fetch, database write, or historical backfill should
start before preflight succeeds.

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

Every service should maintain a coverage manifest.

Rules:

- One live service run opens one coverage row.
- The coverage end advances only after durable write succeeds or a provider
  interval is verified empty.
- Adjacent intervals are compacted.
- Gaps are detected from coverage rows, not only from max timestamps.
- A killed service resumes from the last confirmed coverage end.

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
- error type, message, and enough identifiers to debug

## Terminal Policy

Every service terminal should use the same high-level layout:

```text
Header
Current Operation
Dependencies
Runtime
Queues / Workers
Coverage / Gap Handling
Latest Items
```

The terminal is for monitoring. JSONL logs are the debugging source of truth.

## API Policy

Every service exposes:

```text
/health
/config
/metrics
/snapshot/<domain>/recent
/stream/<domain>
```

Domain-specific endpoints are allowed after these standard endpoints.

## Audit Policy

Each service must define a post-write audit contract.

- QMD: event continuity, sequence gaps, bar completeness, spill replay lag.
- News: duplicate canonical ids, ticker links, text presence, coverage
  integrity.
- SEC: filing parent integrity, document/text integrity, XBRL parent integrity,
  coverage integrity.

Large historical backfills should finish by running the audit contract.
