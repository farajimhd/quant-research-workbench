# Reference Gateway Operating Model

The reference gateway is a continuously runnable, low-frequency service for
market reference data. It keeps identity, conid, tradability, and market
publication data coherent. It is not a high-frequency ingest service.

## Objectives

1. Source sync

   Download current reference evidence from Massive, IBKR, FINRA, SEC, and
   other configured providers. Source sync is always part of operational runs.
   It is not a separate operator flag. Startup source sync reconciles active
   tickers first, writes accepted canonical graph rows, refreshes current
   Massive snapshot/float rows for newly accepted tickers, then takes a current
   IBKR borrow/shortability snapshot for active US stock listings with valid
   conids. If the observation cannot be inserted safely, source sync writes an
   open issue and keeps the instrument non-tradable. Expensive provider jobs are
   gated by the DB-backed `market_reference_source_schedule_v1` table so daemon
   restarts do not lose cadence state.

2. Integrity guardrail

   Audit reference tables, write blocking issues, resolve deterministic stale
   issues, and immediately mark unsafe instruments non-tradable. If any issue
   affects an instrument, that instrument is not tradable until the issue is
   resolved.

3. Maintenance

   Run heavier work: schema upkeep, canonical graph promotion, SEC market bridge
   rebuilds, full tradable publication rebuilds, scanner static rebuilds, and
   coverage-aware market publication gap fill. Maintenance also resolves issues
   opened by source sync when they become deterministic. In `Auto`, this work is
   deferred during active market hours.

4. Observability and control

   Preflight, runtime JSONL logs, reports, terminal summaries, and explicit
   failure handling.

## Operator Controls

The service is controlled through grouped knobs.

| Control | Values | What it means |
| --- | --- | --- |
| `Mode` | `Prod`, `Temp` | `Prod` reads/writes `q_live`. `Temp` reads `q_live` and writes `q_reference_tmp`. |
| `Run` | `Daemon`, `Once` | `Daemon` repeats cycles. `Once` runs one cycle. Defaults are `Prod=Daemon`, `Temp=Once`. |
| `Integrity` | `Strict`, `ReportOnly` | `Strict` writes issue rows, resolves deterministic issues, and blocks tradability. `ReportOnly` does not write guardrails. |
| `Maintenance` | `Auto`, `Skip`, `Force` | `Auto` runs maintenance when policy allows. `Skip` disables it. `Force` runs it with a required reason. |
| `MaintenanceReason` | text | Required when forcing maintenance in production. |
| `Diagnostics` | `None`, `Rules`, `TableGroups`, `Config` | Prints read-only information and exits. |

## Commands

Production daemon:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod
```

Temp write test:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Temp
```

One-shot production cycle:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once
```

Report-only integrity cycle:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once -Integrity ReportOnly
```

Skip maintenance:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod -Maintenance Skip
```

Force maintenance:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once -Maintenance Force -MaintenanceReason "reviewed after-hours repair"
```

Diagnostics:

```powershell
.\scripts\run_reference_gateway.ps1 -Diagnostics Rules
.\scripts\run_reference_gateway.ps1 -Diagnostics TableGroups
.\scripts\run_reference_gateway.ps1 -Diagnostics Config
```

## Startup Flow

1. The wrapper builds a Python command using high-level values, for example:

   ```powershell
   python -m services.reference_gateway.main --mode prod --run daemon --integrity strict --maintenance auto --diagnostics none
   ```

2. The Python entrypoint loads `.env` files.

3. The entrypoint builds `ReferenceGatewayConfigOverrides` from the CLI values.

4. `ReferenceGatewayConfig.from_env(...)` merges env defaults and CLI overrides
   into one immutable config object.

5. Diagnostics modes print and exit before dependency checks or writes.

6. Operational modes run preflight. ClickHouse, artifact storage, Massive, and
   IBKR Client Portal must be available. IBKR is required because conid
   resolution is part of the source-sync objective.

7. Daemon mode starts a parent loop. Each child cycle is launched as the same
   command with `--run once`, so parent and child behavior use the same config
   contract.

8. One-shot mode performs preflight, maintenance policy checks, audit, source
   sync, issue writes, immediate tradability blocking, allowed maintenance, and
   report/log writing.

9. Each child cycle records memory snapshots in the runtime JSONL log at start
   and finish. The parent daemon records its own memory after each child exits.
   `REFERENCE_GATEWAY_DAEMON_CHILD_TIMEOUT_SECONDS` stops hung child cycles, and
   `REFERENCE_GATEWAY_DAEMON_CHILD_MAX_RSS_MB` can fail a cycle that exits above
   the configured RSS ceiling.

## Market-Hours Policy

Market hours are not a blocker for the service itself.

Allowed during market hours:

- audit
- Massive active ticker sync
- Massive overview evidence fetch
- IBKR conid lookup
- accepted canonical graph rows for safe new ticker observations
- current Massive snapshot/float rows for newly accepted tickers
- IBKR borrow/shortability snapshot writes to `market_security_borrow_v1`
- country assertion writes to `market_security_country_v1` from canonical
  listing/exchange evidence
- writing new mapping issues
- immediate latest-universe replacement rows with `is_tradable = 0`

Deferred in `Maintenance=Auto` during market hours:

- schema changes
- deterministic issue resolution
- full SEC bridge plus tradable/scanner publication rebuild
- coverage-aware market-publication gap fill from the configured deep backfill
  start date

`Maintenance=Force` can run deferred work only when an auditable reason is
supplied. `Maintenance=Skip` disables deferred work.

The publication coverage bootstrap records existing migrated source-table
ranges for Massive short interest, Reg SHO threshold rows, and Massive
presentation assets. Bootstrap coverage does not fabricate source
rows; it only records trusted coverage for data already present in the database.

## Issue Resolution Classes

### Automatically Resolvable

The gateway can close the issue because canonical evidence is now complete and
unambiguous.

Example: Massive active ticker `ABCD` opened an issue because no active
canonical symbol existed. A later run finds `ABCD` joined to an active USD US
stock listing with a positive IBKR conid. The resolver records resolved
evidence and deletes the open row so `FINAL` queries no longer see it as open.

### Auto-Block Until Resolved

The gateway cannot safely fix the issue yet, but it can safely keep the
instrument blocked.

Example: an issuer has active US stock candidates but no durable CIK/LEI/EIN.
The related tradable-universe rows remain `is_tradable = 0`.

### Human Review Required

The gateway sees conflicting plausible evidence and must not guess.

Example: IBKR returns multiple plausible US stock/USD contracts for one ticker.
The candidate remains blocked until a stronger resolver or human review picks
the correct mapping.

### Historical Repair

The issue no longer affects current trading, but historical joins can still
benefit from repair.

Example: an old weak issuer issue points to an issuer that no longer has active
US stock candidates. The resolver can close the current blocker as historical
housekeeping.

## Why Resolved Issues Delete Open Rows

`id_mapping_issue_v1` is a `ReplacingMergeTree` ordered by fields that include
`issue_status`. Inserting a resolved row alone does not hide the old open row
under `FINAL`. The resolver therefore inserts compact resolved evidence and
then deletes the matching open row with a synchronous mutation.

## Table Groups

The reference gateway owns or maintains:

- issuer identity tables
- security identity tables
- listing identity tables
- source symbol and mapping tables
- exchange alias/mapping tables
- mapping issue tables
- tradable/scanner publication tables
- market reference publication tables

See:

```text
services/reference_gateway/TABLE_GROUPS.md
```

## Reports And Logs

Reports:

```text
<market-data>/prepared/reference_gateway/reports
```

Runtime JSONL logs:

```text
<market-data>/prepared/reference_gateway/logs/<run_id>/reference_gateway_events.jsonl
```

The daemon exits if a child cycle exits non-zero. It does not silently continue
after a failed dependency or maintenance cycle.

## Stage 2: Runtime Behavior Validation

Before adding more behavior, validate the simplified controls against the real
service path.

### 1. Temp One-Shot

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Temp
```

This should read from `q_live`, write only to `q_reference_tmp`, run preflight,
run source sync, run the audit, and avoid production writes.

Check:

- report/config says `read_database=q_live`
- report/config says `write_database=q_reference_tmp`
- `test_write_mode=true`
- no `q_live` table changes were made by this run

### 2. Temp Force Maintenance

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Temp -Maintenance Force
```

This should exercise schema and maintenance paths against `q_reference_tmp`.
The wrapper supplies a default temp maintenance reason. It must still read from
`q_live` and must not mutate production tables.

Check:

- schema/maintenance operations target `q_reference_tmp`
- failures, if any, are actionable temp-DB dependency errors
- production tables remain unchanged

### 3. Production One-Shot

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once
```

This should run one strict production pass. Source sync and integrity guardrails
should run. Heavy maintenance should obey the market-hours policy.

Check:

- strict integrity writes issue rows when issues are discovered
- affected latest-universe rows are blocked with `is_tradable=0`
- maintenance is either completed or explicitly policy-blocked

### 4. Production Daemon

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod
```

This should start the continuous production daemon. The parent process should
preflight once, then launch child cycles with `--run once`.

Check:

- runtime JSONL log is created under
  `<market-data>/prepared/reference_gateway/logs/<run_id>/`
- each child command and result is logged
- a child failure stops the daemon instead of being hidden
- no orphaned child process remains after shutdown

## Stage 3: Runtime Status And Terminal UX

Implemented.

When Rich output is enabled, one-shot gateway cycles now render a live terminal
dashboard and refresh it after each major operation. The dashboard is organized
into stable panels:

- header with UTC, ET, Vancouver time, mode, read/write DBs, policy, data root,
  and report path
- current operation
- dependency status
- runtime summary
- source-sync counters
- integrity guardrail status
- maintenance state
- operation log
- prioritized audit findings

The compact layout keeps the current operation, summary, maintenance, and audit
findings visible in shorter consoles. Long values fold inside their panels
instead of changing column counts.

Runtime JSONL logs now include structured `audit_completed` and
`source_sync_completed` events in addition to per-operation events. These events
record status and counts, not raw provider payloads.
