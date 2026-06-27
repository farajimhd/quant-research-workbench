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
