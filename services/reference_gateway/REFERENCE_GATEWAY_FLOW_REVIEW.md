# Reference Gateway Flow Review

This document is the running review guide for the reference gateway flow. Each
section is intentionally small so comments can be attached to one stage at a
time. When a stage changes after review, this file should be updated with the
fixed version before moving to the next stage.

## Review Comment Ledger

| ID | Stage | Comment | Status |
| --- | --- | --- | --- |
| C1 | Stage 1 | CLI flags should not be converted into environment variables before `ReferenceGatewayConfig` is built. | Fixed |
| C2 | Stage 1 | The guide must be self-contained and explain what each argument/config value does before asking for comments. | Fixed |
| C3 | Stage 1 | Keep advanced knobs, but provide one normal prod knob and one normal temp/debug knob so routine operation is not a long argument matrix. | Fixed |
| C4 | Stage 1 | Explain the gateway by objectives, not by defensive flags: source sync and integrity can run during market hours; maintenance should run after hours. | Fixed |
| C5 | Stage 1 | Remove stale defensive knobs and group the remaining controls by objective. IBKR is required for active ticker conid resolution, so it should not have a bypass flag. | Fixed |

## Stage 1: Process Start And Configuration

Command under review:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod
```

Temp/debug command under review:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Temp
```

### What This Command Means

The wrapper command is a convenience layer around Python. It does not do the
gateway work itself; it only builds the Python command and runs it from the repo
root.

### Operator Modes

These are the normal entry points. Use these first. The lower-level arguments
remain available only when a run needs an intentional override.

| Mode | Command | What it is for | What the wrapper sets |
| --- | --- | --- | --- |
| `Prod` | `.\scripts\run_reference_gateway.ps1 -Mode Prod` | Normal production daemon against `q_live`. | `ReadDatabase=q_live`, `WriteDatabase=q_live`, `Execute=true`, `ActiveTickerCheck=true`, `Daemon=true`. |
| `Temp` | `.\scripts\run_reference_gateway.ps1 -Mode Temp` | One-shot test/debug run that reads production data but writes to a temp DB. | `ReadDatabase=q_live`, `TestWriteDatabase=q_reference_tmp`, `Execute=true`, `ActiveTickerCheck=true`, `EnsureMarketPublicationSchema=true`, `MarketHoursWriteOverride=true`, `MarketHoursWriteReason="reference gateway temp mode"`. |
| `Custom` | explicit advanced flags | Manual override mode. | The wrapper only passes the flags you provide. |

Mode safety rules:

1. `Prod` cannot be combined with `-TestWriteDatabase`.
2. `Temp` cannot be combined with `-WriteDatabase`; temp mode always writes
   through `-TestWriteDatabase`.
3. `Temp` is one-shot by default. If a continuous temp daemon is needed, pass
   `-Daemon` explicitly so it is an intentional choice.
4. Advanced flags still work with modes, but the mode sets the normal defaults
   first.
5. Removed/stale wrapper flags fail parameter binding instead of being ignored.

### Gateway Objectives

The gateway should be understood by these objectives first. Command-line flags
exist to override details, not to define the service's purpose.

| Objective | What it does | Market-hours behavior | After-hours behavior |
| --- | --- | --- | --- |
| 1. Source sync | Pull active tickers and reference evidence from Massive, IBKR, FINRA, SEC, and other configured providers. Keep q_live reference inputs current. | Allowed. The service can download new evidence, compare it to q_live, and persist observations/issues. | Allowed. Runs with lower urgency and can also feed maintenance. |
| 2. Integrity guardrail | Audit q_live reference tables, find identity/conid/exchange/tradability issues, resolve deterministic issues, and block unsafe instruments. | Allowed. Issue rows, deterministic resolutions, and targeted `is_tradable=0` blocks reduce trading risk. | Allowed. Same checks plus deeper repair context. |
| 3. Maintenance | Schema upkeep, heavy publication gap fill, full tradable/scanner publication rebuilds, and clean canonical graph promotion. | Deferred by default. These can reshape downstream publications while trading is active. | Allowed by default. This is the normal window for heavy or promotion-style work. |
| 4. Observability and control | Preflight, runtime logs, reports, terminal output, and failure handling. | Always allowed. This is service safety infrastructure. | Always allowed. |

So the extra objective is not another data objective; it is operational
observability/control. The business objectives are source sync, integrity, and
maintenance.

| Wrapper argument | Python argument | Meaning | Effect in this command |
| --- | --- | --- | --- |
| `-Mode Prod` | expands to several Python arguments | Normal production mode. | Runs a q_live daemon with execution, active ticker reconciliation, preflight, IBKR resolution, and immediate tradability blocking. |
| `-Mode Temp` | expands to several Python arguments | Normal test/debug mode. | Runs one temp-database execution pass, with schema setup and a market-hours test override. |
| `-ReadDatabase q_live` | `--read-database q_live` | Database used as the canonical source of existing reference data. | The gateway reads existing identity, issue, and publication rows from `q_live`. |
| `-WriteDatabase q_live` | `--write-database q_live` | Database where allowed writes go. | The gateway writes issues, blocks, graph updates, and publication updates to `q_live` when policy allows. |
| `-Execute` | `--execute` | Allows write operations. Without this, the gateway is report-only. | The gateway may mutate ClickHouse, but write policy still blocks risky market-hours operations. |
| `-ActiveTickerCheck` | `--active-ticker-check` | Poll Massive active US tickers and compare them to the canonical symbol graph. | The daemon will look for new/missing Massive tickers every cycle. |
| `-Daemon` | `--daemon` | Run continuously instead of a single pass. | The parent process loops forever until stopped or until a child cycle fails. |

Useful wrapper arguments not used in the command:

| Wrapper argument | Python argument | Meaning | When to use |
| --- | --- | --- | --- |
| `-TestWriteDatabase q_reference_tmp` | `--test-write-database q_reference_tmp` | Reads from the normal source DB but writes to a temp DB. | Testing schema/write behavior without touching `q_live`. |
| `-NoDaemon` | removes `--daemon` | Forces a one-shot run even when a mode would normally enable daemon mode. | After-hours production maintenance with `-Mode Prod -NoDaemon`. |
| `-NoPreflight` | `--no-preflight` | Skip dependency checks. | Only for offline documentation/tests; unsafe for real daemon. |
| `-NoImmediateTradabilityBlock` | `--no-immediate-tradability-block` | Do not write targeted non-tradable replacement rows for open issues. | Debug only; unsafe for live trading protection. |
| `-PrintRules` | `--print-rules` | Print hard tradability rules and exit. | Diagnostics. |
| `-PrintTableGroups` | `--print-table-groups` | Print reference table ownership groups and exit. | Diagnostics. |
| `-EnsureMarketPublicationSchema` | `--ensure-market-publication-schema` | Creates/updates market reference publication tables. | After-hours setup or temp DB setup. |
| `-MarketHoursWriteOverride` | `--market-hours-write-override` | Allows normally blocked market-hours promotion writes. | Rare emergency or controlled temp test. Requires a reason. |
| `-MarketHoursWriteReason "..."` | `--market-hours-write-reason "..."` | Auditable explanation for market-hours override. | Required with `-MarketHoursWriteOverride`. |
| `-NoWriteDiscoveredIssues` | `--no-write-discovered-issues` | Do not insert discovered open issues. | Report-only issue discovery, not safe for live protection. |
| `-NoWriteCanonicalGraph` | `--no-write-canonical-graph` | Do not promote clean candidates into canonical issuer/security/listing/symbol tables. | Useful during market hours or cautious testing. |
| `-NoResolveStaleIssues` | `--no-resolve-stale-issues` | Do not close issues even if deterministic evidence now exists. | Debugging resolver behavior. |
| `-NoRebuildTradable` | `--no-rebuild-tradable` | Do not rebuild `feature_tradable_universe_v1` and scanner static publications. | Market-hours cycles or narrow tests. |
| `-RebuildTradableInTestMode` | `--rebuild-tradable-in-test-mode` | Allows tradable rebuild when read DB and write DB differ. | Only after the temp DB has required source tables cloned. |
| `-NoMarketPublicationGapFill` | `--no-market-publication-gap-fill` | Skip FINRA/SEC publication maintenance. | Market-hours daemon cycles or narrow tests. |

Removed stale controls:

| Removed control | Why it was removed |
| --- | --- |
| `-NoIbkrResolution` / `--no-ibkr-resolution` / `REFERENCE_GATEWAY_IBKR_RESOLUTION_ENABLED` | Active ticker sync must find IBKR conids. Running without IBKR creates known-unusable candidates. |
| `-NoIbkrRequired` / `--no-ibkr-required` / `REFERENCE_GATEWAY_IBKR_REQUIRED` | If active ticker sync is enabled and IBKR is unavailable, the gateway should fail preflight instead of running partial work. |
| `REFERENCE_GATEWAY_ACTIVE_TICKER_CHECK_MARKET_HOURS_ONLY` | Source sync is a core objective and can run during both active and after-hours daemon cycles. |

### Important Config Values In Plain English

These values are loaded from env unless overridden by CLI.

| Config value | Default | Meaning | Behavior if `true` | Behavior if `false` |
| --- | --- | --- | --- | --- |
| `execute` | `false` | Whether the gateway may write. | Writes are possible if policy allows. | Report-only. |
| `daemon_loop_enabled` | `false` | Whether the process repeats cycles. | Parent daemon keeps launching one-shot child cycles. | Single pass only. |
| `active_ticker_check_enabled` | `false` | Whether to poll Massive active tickers. | Detects new/missing Massive tickers. | Skips ticker reconciliation. |
| `preflight_enabled` | `true` | Whether startup dependency checks run. | Fails fast if required dependencies are down. | Gateway may fail later or run partial work. |
| `write_discovered_issues` | `true` | Whether discovered problems become rows in `id_mapping_issue_v1`. | Problems become durable blockers. | Problems stay in reports only. |
| `write_canonical_graph` | `true` | Whether clean candidates can be promoted into identity tables. | After-hours clean candidates can become canonical rows. | No identity graph promotion. |
| `immediate_tradability_block_enabled` | `true` | Whether open issues immediately block currently tradable latest-universe rows. | Inserts replacement `is_tradable=0` rows for touched symbols. | Waits for full tradable rebuild; unsafe during live trading. |
| `resolve_stale_issues` | `true` | Whether deterministic open issues can be closed. | Issues close when the missing evidence now exists. | Issues remain open even if fixed. |
| `rebuild_tradable_on_execute` | `true` | Whether full tradable/scanner publications are rebuilt in execute mode. | After-hours cycles refresh the full publication. | No full publication refresh. |
| `after_hours_writes_only` | `true` | Whether promotion and heavy maintenance writes are blocked during active collection hours. | Source sync and integrity writes still run; promotion/heavy maintenance waits. | Promotion and maintenance writes can run during market hours. |
| `market_publication_gap_fill_enabled` | `true` | Whether recent FINRA/SEC publication gaps are filled. | After-hours maintenance fills recent reference publication gaps. | Publication gaps are not filled by this service. |

### Write Categories

Not all writes have the same risk.

| Write category | Examples | Market-hours policy |
| --- | --- | --- |
| Source-sync writes | provider observations, active ticker issue evidence, compact reports | Allowed because they keep the service aware of current provider state. |
| Integrity writes | open issue rows, deterministic issue resolution, immediate `is_tradable=0` replacement rows | Allowed because they prevent trading unsafe instruments. |
| Promotion writes | new issuer/security/listing/symbol rows, full tradable rebuilds | Deferred by default during market hours. |
| Maintenance writes | FINRA short-volume/SEC FTD publication gap fill | Blocked by default during market hours. |
| Schema writes | creating/altering reference publication tables | Blocked by policy unless explicitly allowed. |

### Flow

1. `scripts/run_reference_gateway.ps1` builds a Python command:

   ```powershell
   python -m services.reference_gateway.main --read-database q_live --write-database q_live --execute --active-ticker-check --daemon
   ```

   With `-Mode Prod`, the wrapper expands to the command above.

   With `-Mode Temp`, the wrapper expands to:

   ```powershell
   python -m services.reference_gateway.main --read-database q_live --test-write-database q_reference_tmp --execute --active-ticker-check --ensure-market-publication-schema --market-hours-write-override --market-hours-write-reason "reference gateway temp mode"
   ```

2. `services.reference_gateway.main` starts and parses command-line arguments.

3. The service loads `.env` files through the repo ClickHouse environment
   discovery path.

4. CLI arguments are converted into a typed
   `ReferenceGatewayConfigOverrides` object. They are no longer written back
   into `os.environ`.

5. `ReferenceGatewayConfig.from_env(overrides)` builds the final immutable
   config:

   - `.env` and process environment provide defaults.
   - CLI overrides replace only the fields explicitly passed by the command.
   - The final config is the only object later stages should use for gateway
     decisions.

6. For this command, the important final config values are:

   - `clickhouse_read_database = q_live`
   - `clickhouse_write_database = q_live`
   - `execute = true`
   - `active_ticker_check_enabled = true`
   - `daemon_loop_enabled = true`

7. Important default config values:

   - `preflight_enabled = true`
     - run dependency checks before useful work
   - IBKR Client Portal is required when active ticker reconciliation is active
     - active ticker sync cannot be run without conid evidence
   - `immediate_tradability_block_enabled = true`
     - immediately publish non-tradable replacement rows for currently tradable
       rows touched by open issues
   - `after_hours_writes_only = true`
     - block promotion/heavy maintenance writes during active collection hours
   - `daemon_active_interval_seconds = 900`
     - wait 15 minutes between market-hours daemon cycles
   - `daemon_after_hours_interval_seconds = 3600`
     - wait 60 minutes between after-hours daemon cycles

8. The service resolves the data root:

   - on the workstation: `D:/market-data`
   - otherwise: `\\DESKTOP-SAAI85T\Workstation-D\market-data`

9. The service resolves standard output locations:

   - reports:

     ```text
     <market-data>/prepared/reference_gateway/reports
     ```

   - daemon JSONL runtime log:

     ```text
     <market-data>/prepared/reference_gateway/logs/<run_id>/reference_gateway_events.jsonl
     ```

10. Because `daemon_loop_enabled = true`, `main.py` hands control to:

    ```python
    run_reference_daemon(config, sys.argv[1:])
    ```

    A one-shot run would continue directly into preflight, audit, reconciliation,
    issue writes, blocking, and maintenance.

### Current Read

The config flow is now explicit enough to reason about: CLI is the override
layer, env is the default layer, and downstream code receives one final config
object. This is better than mutating env during startup because it avoids hidden
side effects and makes daemon parent/child command behavior easier to audit.

The guide now explains each argument/config before using it. This should make
the next stages reviewable without reading source code.

### Possible Comments For Stage 1

1. Require `--write-database q_live` for daemon mode unless explicitly running
   temp/test mode.
2. Refuse daemon mode if `--execute` is not set.
3. Require `--active-ticker-check` in daemon mode, because otherwise the service
   is mostly just periodic audit.
4. Make data root explicit and fail if it is inferred from the workstation share.
5. Keep the current data-root inference because it matches news/sec gateway
   behavior.
6. Reduce active daemon interval from `900s` to a shorter interval.
7. Keep active daemon interval at `900s` because reference data is slow-moving.
8. Make IBKR required only when active ticker reconciliation is enabled.
9. Keep IBKR required by default for all daemon runs because unresolved conids
   are trading blockers.
