# Reference Gateway Operating Model

The reference gateway is a continuously runnable, low-frequency service for
market reference data. It keeps identity, conid, tradability, and market
publication data coherent. It is not a high-frequency ingest service.

## Objectives

1. Source sync

   Download current reference evidence from Massive, IBKR, FINRA, SEC, and
   other configured providers. Source sync is always part of operational runs.
   It is not a separate operator flag.

2. Integrity guardrail

   Audit reference tables, write blocking issues, resolve deterministic stale
   issues, and immediately mark unsafe instruments non-tradable. If any issue
   affects an instrument, that instrument is not tradable until the issue is
   resolved.

3. Maintenance

   Run heavier work: schema upkeep, canonical graph promotion, full tradable
   publication rebuilds, scanner static rebuilds, and recent market publication
   gap fill. In `Auto`, this work is deferred during active market hours.

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

## Market-Hours Policy

Market hours are not a blocker for the service itself.

Allowed during market hours:

- audit
- Massive active ticker sync
- Massive overview evidence fetch
- IBKR conid lookup
- writing new mapping issues
- deterministic issue resolution
- immediate latest-universe replacement rows with `is_tradable = 0`

Deferred in `Maintenance=Auto` during market hours:

- schema changes
- canonical graph promotion
- full tradable/scanner publication rebuild
- recent market-publication gap fill

`Maintenance=Force` can run deferred work only when an auditable reason is
supplied. `Maintenance=Skip` disables deferred work.

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
