# Reference Gateway Flow Review

This document is the running review guide for the reference gateway flow. Each
section is intentionally small so comments can be attached to one stage at a
time. When a stage changes after review, this file should be updated with the
fixed version before moving to the next stage.

## Review Comment Ledger

| ID | Stage | Comment | Status |
| --- | --- | --- | --- |
| C1 | Stage 1 | CLI flags should not be converted into environment variables before `ReferenceGatewayConfig` is built. | Fixed |

## Stage 1: Process Start And Configuration

Command under review:

```powershell
.\scripts\run_reference_gateway.ps1 -ReadDatabase q_live -WriteDatabase q_live -Execute -ActiveTickerCheck -Daemon
```

### Flow

1. `scripts/run_reference_gateway.ps1` builds a Python command:

   ```powershell
   python -m services.reference_gateway.main --read-database q_live --write-database q_live --execute --active-ticker-check --daemon
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
   - `ibkr_resolution_enabled = true`
   - `ibkr_required = true`
   - `immediate_tradability_block_enabled = true`
   - `after_hours_writes_only = true`
   - `daemon_active_interval_seconds = 900`
   - `daemon_after_hours_interval_seconds = 3600`

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

