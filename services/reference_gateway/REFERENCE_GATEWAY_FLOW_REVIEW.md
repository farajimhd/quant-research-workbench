# Reference Gateway Flow Review

This document tracks the reviewed public flow for the reference gateway. It is
kept self-contained so each step can be reviewed without reading the Python
source first.

## Review Comment Ledger

| ID | Stage | Comment | Status |
| --- | --- | --- | --- |
| C1 | Startup | CLI flags should not be converted into environment variables before `ReferenceGatewayConfig` is built. | Fixed |
| C2 | Docs | The guide must explain every argument/config value before asking for comments. | Fixed |
| C3 | CLI | Routine operation needs one prod knob and one temp/debug knob. | Fixed |
| C4 | Objectives | Explain the gateway by objectives: source sync, integrity, maintenance, and observability. | Fixed |
| C5 | CLI | Remove stale defensive knobs. IBKR is required for conid resolution and should not have a bypass flag. | Fixed |
| C6 | CLI | Active ticker sync is an internal source-sync task, not a public flag. | Fixed |
| C7 | CLI | Replace low-level write switches with high-level operator knobs. | Fixed |
| C8 | Stage 2 | Add the real runtime validation sequence before moving to UI/terminal polish. | Added |
| C9 | Stage 3 | Add live Rich status panels and structured runtime events for operator visibility. | Fixed |
| C10 | Flow Stage 1 | Process entry and mode resolution reviewed and accepted. | Accepted |
| C11 | Flow Stage 2 | Preflight and dependency gate reviewed and accepted. | Accepted |

## Reviewed Flow Stages

This section is the stage-by-stage operating flow under active review. Each
stage should be reviewed before the next stage is finalized.

## Stage 1: Process Entry And Mode Resolution

This stage answers: when the reference gateway command starts, what mode does
it enter and what databases can it touch?

User-facing entry commands:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod
.\scripts\run_reference_gateway.ps1 -Mode Temp
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once
.\scripts\run_reference_gateway.ps1 -Mode Prod -Maintenance Skip
.\scripts\run_reference_gateway.ps1 -Mode Prod -Maintenance Force -MaintenanceReason "reviewed repair"
```

### Mode

`Prod`:

- reads from `q_live`
- writes to `q_live`
- default run mode is `Daemon`
- intended for real service operation

`Temp`:

- reads from `q_live`
- writes to `q_reference_tmp`
- default run mode is `Once`
- intended for validation/testing without production writes

### Run

`Daemon`:

- starts a parent process
- parent loops
- each cycle starts a one-shot child run

`Once`:

- runs one gateway cycle
- exits after the cycle finishes or fails

### Integrity

`Strict`:

- writes discovered issue rows
- resolves deterministic stale issues
- immediately blocks unsafe instruments by publishing non-tradable replacement
  rows when needed

`ReportOnly`:

- inspects and reports
- does not write guardrail rows

### Maintenance

`Auto`:

- runs maintenance only when policy allows

`Skip`:

- skips maintenance
- still runs source sync and integrity checks

`Force`:

- runs maintenance with an explicit reason
- production force requires `-MaintenanceReason`

### Stage 1 Rules

- Source sync is not optional in an operational run.
- IBKR is required because conid resolution is part of source sync.
- Low-level switches such as active ticker check, write canonical graph, and
  rebuild tradable are not public controls. They are internal consequences of
  mode, integrity, and maintenance policy.

Review status: accepted.

## Stage 2: Preflight And Dependency Gate

This stage answers: before the gateway does useful work, what must be
available, and what happens if something is missing?

Preflight runs at the start of every operational run:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod
.\scripts\run_reference_gateway.ps1 -Mode Temp
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once
```

The gateway should check dependencies before source sync, audit writes,
maintenance, or publication updates begin.

### ClickHouse

Required because the gateway must read the canonical reference graph and write
issues/updates when allowed.

Checks:

- ClickHouse endpoint is reachable
- read database exists, usually `q_live`
- write database exists, either `q_live` or `q_reference_tmp`
- required reference tables exist or can be created if maintenance/schema
  policy allows it

Failure behavior:

- gateway exits
- gateway must not continue in partial mode

### Artifact/Data Root

Required because reports, runtime logs, plans, and generated scripts must be
written somewhere predictable.

Expected root:

- workstation: `D:/market-data`
- laptop/remote: `\\DESKTOP-SAAI85T\Workstation-D\market-data`

Failure behavior:

- gateway exits
- gateway must not write code or data into random fallback folders

### Massive API

Required for source sync.

Used for:

- active ticker list
- ticker overview/reference metadata
- detecting new or changed Massive-side symbols

Failure behavior:

- gateway exits
- gateway must not run source sync from stale data

### IBKR Client Portal

Required because new tradable candidates need conid resolution.

Used for:

- searching ticker candidates
- filtering to US stock/USD contracts
- identifying ambiguous or missing conid cases

Failure behavior:

- gateway exits if IBKR Client Portal is unavailable or unauthenticated
- gateway must not create or promote candidates that lack conid evidence

### Runtime Log Initialization

Required for observability.

Runtime log path:

```text
<market-data>/prepared/reference_gateway/logs/<run_id>/reference_gateway_events.jsonl
```

Failure behavior:

- if runtime logging cannot be initialized, that is a startup failure
- the gateway should not run a maintenance service with no durable runtime
  trace

### Stage 2 Output

If preflight passes:

- gateway proceeds to Stage 3
- terminal shows dependency status as OK
- JSONL log records preflight status

If preflight fails:

- gateway exits non-zero
- terminal shows the failed dependency
- JSONL log records the failure if logging was initialized

### Stage 2 Rules

- Preflight is strict for operational runs.
- There should not be a real-operation bypass such as `--no-preflight`.
- Diagnostics that do not require dependencies should live under
  `-Diagnostics`, not under operational modes.

Review status: accepted.

## Public Operator Knobs

The wrapper exposes only high-level behavior groups.

| Knob | Values | Default | Meaning |
| --- | --- | --- | --- |
| `-Mode` | `Prod`, `Temp` | `Prod` | `Prod` reads/writes `q_live`. `Temp` reads `q_live` and writes `q_reference_tmp`. |
| `-Run` | `Daemon`, `Once` | `Prod=Daemon`, `Temp=Once` | Controls process lifetime. |
| `-Integrity` | `Strict`, `ReportOnly` | `Strict` | `Strict` writes issue rows, resolves deterministic issues, and blocks tradability. `ReportOnly` audits without guardrail writes. |
| `-Maintenance` | `Auto`, `Skip`, `Force` | `Auto` | `Auto` runs heavy maintenance only when policy allows. `Skip` disables heavy maintenance. `Force` allows maintenance with a required reason. |
| `-MaintenanceReason` | text | empty | Required with `-Maintenance Force` in production. |
| `-Diagnostics` | `None`, `Rules`, `TableGroups`, `Config` | `None` | Prints a diagnostic view and exits without operational writes. |

Normal production:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod
```

Temp write test:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Temp
```

One-shot production pass:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once
```

Forced maintenance:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once -Maintenance Force -MaintenanceReason "reviewed after-hours repair"
```

Diagnostics:

```powershell
.\scripts\run_reference_gateway.ps1 -Diagnostics Rules
.\scripts\run_reference_gateway.ps1 -Diagnostics TableGroups
.\scripts\run_reference_gateway.ps1 -Diagnostics Config
```

## Objectives

| Objective | What it does | Market-hours behavior | After-hours behavior |
| --- | --- | --- | --- |
| Source sync | Fetch current evidence from Massive, IBKR, FINRA, SEC, and related providers. | Runs normally. It may write compact evidence and issues. | Runs normally. |
| Integrity guardrail | Audit reference data, resolve deterministic issues, and block unsafe instruments. | Runs normally. Risk-reducing issue writes and `is_tradable=0` blocks are allowed. | Runs normally. |
| Maintenance | Schema upkeep, canonical graph promotion, full publication rebuilds, and historical publication gap fill. | Deferred in `Auto`; allowed only with `Force` and a reason. | Allowed in `Auto`. |
| Observability | Preflight, runtime JSONL logs, reports, and terminal output. | Always enabled for operational runs. | Always enabled for operational runs. |

## Startup Flow

1. The PowerShell wrapper builds:

   ```powershell
   python -m services.reference_gateway.main --mode prod --run daemon --integrity strict --maintenance auto --diagnostics none
   ```

2. Python loads `.env` files.

3. CLI values are passed to `ReferenceGatewayConfigOverrides`.

4. `ReferenceGatewayConfig.from_env(...)` merges env defaults and CLI
   overrides into one immutable config object.

5. Diagnostics modes exit before dependency checks or writes.

6. Operational modes run preflight. ClickHouse, artifact storage, Massive, and
   IBKR Client Portal must be available for source sync.

7. Daemon mode starts a parent loop. Each cycle launches the same gateway with
   `--run once`; the child uses the same high-level knobs and the same write
   policy.

8. One-shot mode performs:

   - dependency preflight
   - safe schema upkeep if maintenance policy allows it
   - deterministic issue resolution
   - optional publication rebuild if policy allows it
   - audit
   - source sync
   - issue writes and immediate tradability blocking in strict mode
   - canonical graph promotion if maintenance policy allows it
   - recent market-publication gap fill if maintenance policy allows it
   - final report and runtime log events

## Config Groups

| Group | What it controls |
| --- | --- |
| `service` | Operator mode, run mode, bind, host, port, storage roots, report root. |
| `database` | ClickHouse URL/user, read DB, write DB, temp-write detection. |
| `providers` | Massive endpoint/key presence and IBKR Client Portal endpoint. |
| `execution` | Execute mode, daemon mode, diagnostics mode, daemon intervals, preflight. |
| `source_sync` | Page limits and new-candidate caps. Source sync itself is not optional in operational runs. |
| `integrity` | Strict versus report-only issue handling and immediate tradability blocking. |
| `maintenance` | Canonical graph writes, publication rebuilds, schema upkeep, and publication gap fill. |
| `terminal` | Rich terminal display settings. |

## Removed Public Controls

These are intentionally no longer public operator knobs:

| Removed control | Replacement |
| --- | --- |
| `-ActiveTickerCheck` | Source sync always runs in operational modes. |
| `-Execute` / `-NoExecute` | `-Diagnostics` controls report-only diagnostics; operational modes execute. |
| `-ReadDatabase`, `-WriteDatabase`, `-TestWriteDatabase` | `-Mode Prod` and `-Mode Temp`. Env overrides remain for deployment-specific defaults. |
| `-NoPreflight` | Operational modes require preflight. |
| `-NoImmediateTradabilityBlock` | Use `-Integrity ReportOnly` only for diagnostics. |
| `-NoWriteCanonicalGraph`, `-NoRebuildTradable`, `-NoMarketPublicationGapFill` | `-Maintenance Skip` disables maintenance; `Auto` uses market-hours policy. |
| `-EnsureMarketPublicationSchema` | Schema upkeep is maintenance. |
| `-MarketHoursWriteOverride` | `-Maintenance Force -MaintenanceReason "..."`. |

## Current Review Read

The public contract now matches the service objectives:

- `Mode` chooses production versus temp safety.
- `Run` chooses daemon versus one-shot.
- `Integrity` chooses whether guardrails write or only report.
- `Maintenance` chooses whether heavy/promotion work is allowed.
- `Diagnostics` prints read-only explanatory output.

The detailed implementation controls remain internal fields on
`ReferenceGatewayConfig`, but the operator no longer has to assemble them by
hand.

## Validation Notes: Runtime Behavior

Goal: prove the simplified public controls behave correctly in real gateway
execution before adding more functionality.

Run these in order.

### 1. Temp One-Shot

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Temp
```

Expected behavior:

- reads from `q_live`
- writes only to `q_reference_tmp`
- runs preflight
- runs source sync
- runs reference audit
- writes temp reports/logs
- does not mutate `q_live`

Pass condition:

- no `q_live` writes are observed
- temp report clearly shows `read_database=q_live`,
  `write_database=q_reference_tmp`, and `test_write_mode=true`

### 2. Temp Force Maintenance

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Temp -Maintenance Force
```

Expected behavior:

- reads from `q_live`
- writes to `q_reference_tmp`
- allows schema/maintenance paths in the temp database
- uses the wrapper's default temp maintenance reason
- does not mutate `q_live`

Pass condition:

- temp schema/maintenance paths complete or fail with an actionable temp-DB
  missing-table reason
- no production table is changed

### 3. Production One-Shot

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once
```

Expected behavior:

- reads and writes `q_live`
- runs strict integrity guardrails
- writes issues and immediate non-tradable blocks if needed
- defers maintenance if market-hours policy blocks it

Pass condition:

- any blocked maintenance is reported as policy-blocked, not silently skipped
- any discovered blocking issue makes affected rows non-tradable

### 4. Production Daemon

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod
```

Expected behavior:

- parent daemon performs startup preflight
- each child cycle runs with `--run once`
- runtime JSONL logs are written
- a child failure stops the daemon instead of being hidden

Pass condition:

- daemon logs show parent start, child cycle command, child result, and next
  interval
- stopping/restarting is clear and does not leave an orphaned child process

## Implemented Notes: Runtime Status And Terminal UX

Status: implemented.

The reference gateway now uses a live Rich terminal session during one-shot
child cycles when Rich output is enabled. The terminal updates after each major
operation and keeps a stable panel structure:

- header with UTC, ET, Vancouver time, mode, read/write DBs, data root, and
  report path
- current operation
- dependency status
- runtime summary
- source-sync counters
- integrity guardrail status
- maintenance policy/state
- full operation log
- prioritized audit findings

The compact layout removes lower-priority panels when the terminal height is
short, keeping the current operation, summary, maintenance, and audit findings
visible.

Structured JSONL events were added for audit summaries and source-sync
summaries. Existing `operation` events continue to back the per-step terminal
rows.

Validation performed:

- `python -m py_compile services\reference_gateway\terminal.py services\reference_gateway\main.py`
- `python -m services.reference_gateway.main --help`
- `python -m services.reference_gateway.smoke_test`
- normal-height terminal render smoke
- compact-height terminal render smoke
