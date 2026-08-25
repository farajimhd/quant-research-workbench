# Unified Service Management

`scripts/services.ps1` is the operator-facing command for starting, stopping,
restarting, planning, and inspecting repository services. The older
`start_*`, `stop_*`, and `run_*` scripts remain internal lifecycle adapters and
compatibility tools; routine operation should use the unified command.

Run every example from the repository root:

```powershell
Set-Location D:\TradingCodes\quant-research-workbench
```

## Prerequisites and defaults

- Windows Terminal (`wt.exe`) is installed.
- The `ml4t` Conda environment exists, or `--python-exe` identifies Python.
- Runtime output is available under `D:\TradingML\runtimes`.
- `.env` contains the required ClickHouse, provider, and broker configuration.
- `historical` and `live` require the promoted BarGPT release manifest. Use the
  corresponding `*-core` profile when BarGPT is intentionally excluded.
- Text Intelligence requires the promoted DeepFM release manifest.
- IBKR credentials and the selected account key must be configured before a
  profile containing `ibkr-supervisor` is started.

```text
Service-manager state: D:\TradingML\runtimes\service_manager
BarGPT releases:       D:\TradingML\runtimes\bar_gpt_service\configuration\releases.json
DeepFM release:        D:\TradingML\runtimes\text_intelligence\serving\news_forecast_funnel_v1\release.json
QMD Live host role:    Laptop
IBKR account key:      paper
Terminal window:       quant-research-workbench-services
```

No secret values are written to the catalog or ownership records.

## Command shape

```powershell
.\scripts\services.ps1 <action> [target] [options]
```

| Action | Behavior |
| --- | --- |
| `status` | Inspect readiness, ownership, and revision state. |
| `start` | Start missing targets and dependencies; preserve running services. |
| `stop` | Stop selected manager-owned targets in reverse dependency order. |
| `restart` | Restart selected running targets; do not start stopped targets unless requested. |
| `plan` | Prefix `start`, `stop`, or `restart` to show actions without mutation. |
| `groups` | List profiles, services, and dynamic selectors. |
| `validate` | Validate catalog entries, launchers, releases, generated commands, and fingerprints. |

```powershell
.\scripts\services.ps1 groups
.\scripts\services.ps1 validate historical-core
.\scripts\services.ps1 status all
.\scripts\services.ps1 status live
.\scripts\services.ps1 status intelligence --json
.\scripts\services.ps1 plan start historical
.\scripts\services.ps1 plan restart dev
```

`status` returns zero only when every selected service is semantically ready.
An open port or HTTP 200 is not sufficient: each service must report its
service-specific ready state. QMD Live additionally requires a running health
contract, healthy required lanes, unsaturated required queues, no active
pipeline degradation, and a fresh event stream while the collection window is
active. IBKR requires a live supervisor thread plus ready gateway,
authenticated session, healthy keepalive, and ready ClickHouse. JSON mode has
the same exit-code meaning.

## Static profiles

| Target | Services |
| --- | --- |
| `app` | Backend and frontend; starting adds missing QMD History dependency. |
| `historical-core` | QMD History, backend, and frontend. |
| `historical` | Historical Core plus BarGPT. |
| `live-core` | Complete live workspace without BarGPT. |
| `live` | Complete live workspace including BarGPT. |
| `gateways` | QMD Live, News, SEC, IBKR Supervisor, and Reference Gateway. |
| `market-data` | QMD Live and QMD History. |
| `intelligence` | Model Gateway, News Hypothesis, Text Intelligence, and BarGPT. |
| `middleware` | Alias for `intelligence`. |
| `documents` | News Gateway, SEC Gateway, and Text Intelligence. |
| `all` | Every registered service. |

Every service ID is also a target:

```text
qmd-live             qmd-history          backend
frontend             news-gateway         sec-gateway
ibkr-supervisor      reference-gateway    model-gateway
news-hypothesis      text-intelligence    bar-gpt
```

## Dynamic development selectors

The manager calculates a desired fingerprint from each service's owned source,
shared code, launcher contract, selected non-secret environment, and promoted
model manifest. The startup ownership record contains the running fingerprint.
Uncommitted edits therefore count; a Git commit or version bump is unnecessary.
Generated caches and build outputs (`__pycache__`, pytest caches, `node_modules`,
and `dist`) are excluded so runtime activity cannot create false source drift.
The manager also persists normalized launch inputs and separate `launch`,
`environment`, `host_role`, `source`, and `artifacts` component hashes. Stale
status identifies the changed components instead of reporting only an opaque
whole-service revision.

| Selector | Selection |
| --- | --- |
| `dev` | Running manager-owned services whose fingerprint changed. |
| `stale` | Changed running services and previously started stopped services. |
| `unhealthy` | Manager-owned services that are starting, degraded, or not ready. |
| `stopped` | Services that are not running. |

Normal development loop:

```powershell
.\scripts\services.ps1 status all
.\scripts\services.ps1 plan restart dev
.\scripts\services.ps1 restart dev
.\scripts\services.ps1 restart dev --within live
.\scripts\services.ps1 restart stale --within historical --start-missing
```

`start dev` is rejected because start cannot refresh a stale running process.
By default, restart does not unexpectedly start stopped services.

## Historical workflows

```powershell
# Complete historical application, including BarGPT.
.\scripts\services.ps1 start historical

# Replay, Backtest, Debug, Configuration, and Canvas without BarGPT.
.\scripts\services.ps1 start historical-core

# Refresh only changed running Historical services.
.\scripts\services.ps1 restart dev --within historical

# Explicitly restart or stop the complete profile.
.\scripts\services.ps1 restart historical
.\scripts\services.ps1 stop historical
```

Profile start is convergent: existing services are preserved. A stale service
is reported and remains running until `restart dev` or an explicit restart.

## Live workflows

```powershell
.\scripts\services.ps1 status live
.\scripts\services.ps1 plan start live
.\scripts\services.ps1 start live --qmd-live-host-role Laptop
.\scripts\services.ps1 start live-core
.\scripts\services.ps1 plan restart dev --within live
.\scripts\services.ps1 restart dev --within live
.\scripts\services.ps1 stop live
```

The designated workstation must opt in explicitly:

```powershell
.\scripts\services.ps1 start live --qmd-live-host-role Workstation
```

Text Intelligence starts with manual LLM review, and News Hypothesis starts in
manual trigger mode. The manager never enables automatic AI mode.

IBKR readiness requires both a ready gateway and authenticated session before
Reference Gateway starts. Select another configured account key with:

```powershell
.\scripts\services.ps1 start gateways --ibkr-account live-account-key
```

## Individual and layer operations

```powershell
.\scripts\services.ps1 restart backend
.\scripts\services.ps1 restart frontend
.\scripts\services.ps1 restart text-intelligence
.\scripts\services.ps1 restart intelligence
.\scripts\services.ps1 stop gateways
.\scripts\services.ps1 start documents
.\scripts\services.ps1 stop model-gateway
```

Starting a service adds missing declared dependencies. Stopping an individual
service stops only that service; dependents may become degraded and identify the
missing dependency. Target a whole layer/profile when dependents should stop.

## Planning, automation, and overrides

Both plan forms are supported:

```powershell
.\scripts\services.ps1 plan restart dev
.\scripts\services.ps1 restart dev --plan
```

Plans calculate fingerprints and ordering but do not open terminals, write
ownership state, or stop processes.

```powershell
# Machine-readable status.
.\scripts\services.ps1 status live --json

# Explicit interpreter and runtime root.
.\scripts\services.ps1 start historical `
  --python-exe C:\Users\g835l\miniconda3\envs\ml4t\python.exe `
  --runtime-root D:\TradingML\runtimes\service_manager

# Explicit promoted releases.
.\scripts\services.ps1 start intelligence `
  --bar-gpt-release-manifest D:\TradingML\runtimes\bar_gpt_service\configuration\releases.json `
  --text-intelligence-release-manifest D:\TradingML\runtimes\text_intelligence\serving\news_forecast_funnel_v1\release.json

# Put tabs in the calling Windows Terminal window.
.\scripts\services.ps1 start app --terminal-target current

# Allow a legitimately slow bounded startup.
.\scripts\services.ps1 start live --timeout-seconds 900
```

## Ownership and QMD Live safety

The manager uses `run_windows_terminal_service_tab.ps1`, one external ownership
record per service, and a kill-on-close Job Object. Start refuses to adopt an
unregistered listener. Stop validates repository, registry path, host identity,
process creation time, and service role before Ctrl+C. Bounded fallback applies
only to that validated host and its child Job Object. Foreign listeners remain
untouched. Only one mutating manager operation can run at a time.

Every manager-launched tab writes bounded per-run evidence below
`D:\TradingML\runtimes\service_manager\logs\<service-role>\<run-id>`:

- `run.json` contains normalized non-secret launch inputs and fingerprint components.
- `stdout.log` and `stderr.log` preserve the child streams while output remains visible in the terminal.
- `exit.json` records start/finish timestamps, exit code, and the normalized exit reason (`operator_stop`, `process_exit_zero`, `process_exit_nonzero`, or `host_exception`).

The newest ten runs per service are retained by default. A dead ownership
record is archived under `dead-registry` before a mutating operation proceeds;
it is never treated as authority over a current listener. If another process
owns the port, foreign-listener refusal still applies.

QMD Live keeps both layers of evidence without changing its specialized
lifecycle authority: the registered tab host writes under the manager's
QMD-live runtime root, while `run_qmd_gateway.ps1` writes the gateway process's
own `run.json`, `stdout.log`, `stderr.log`, and `exit.json` below
`D:\TradingML\runtimes\qmd_gateway\logs\<run-id>`.

QMD Live retains its specialized lifecycle behind this command. The manager
uses its dedicated start/stop authorities, requires an explicit host role,
snapshots active computation-target leases before restart, restores them with
their remaining TTL, and verifies restored target IDs after readiness. An
unrelated frontend or middleware change cannot restart QMD Live.

## Migrating from legacy launchers

A service started with an older launcher appears as `foreign`; a listening port
is not shutdown authority. Stop it once with its original authority, then use
the unified manager:

```powershell
.\scripts\stop_workspace_services.ps1
.\scripts\stop_live_gateway_services.ps1
.\scripts\stop_qmd_live_gateway.ps1
.\scripts\services.ps1 start live
```

Do not delete an ownership record for a running process. Managed stop validates
and cleans stale records after the corresponding process is confirmed gone.

## Troubleshooting

### `foreign`

Another or legacy manager owns the port. Stop it through its original lifecycle;
the unified manager will not adopt or kill it.

### `stale`

```powershell
.\scripts\services.ps1 plan restart dev
.\scripts\services.ps1 restart dev
```

### `degraded` or `unhealthy`

Inspect the service terminal and status detail, then:

```powershell
.\scripts\services.ps1 status unhealthy
.\scripts\services.ps1 restart unhealthy
```

Use the status detail to distinguish transport failure from semantic
degradation. QMD queue/freshness failures and IBKR authentication/keepalive
failures remain visible even when their HTTP endpoints return 200. Historical
failure counters alone do not make a recovered service unhealthy.

BarGPT reports `warming` while admitted tickers are progressing through their
initial warmup. The manager treats that state as successful and excludes it
from `restart unhealthy`. A warmup with an active error, or a top-level
`degraded` state, remains degraded and eligible for operator action.

### Exit evidence

Inspect the newest per-run directory under
`D:\TradingML\runtimes\service_manager\logs\<service-role>`. `exit.json` is
the lifecycle authority for whether the child exited cleanly, was stopped by
the operator, returned nonzero, or failed in the tab host. Terminal scrollback
is not the only evidence path.

### Missing release manifest

Pass the promoted immutable manifest or choose `historical-core`/`live-core` if
BarGPT is intentionally excluded. Text Intelligence cannot run its DeepFM
funnel without the promoted release manifest.

### Readiness timeout

The service terminal stays open with its actual startup error. Correct the
dependency or configuration and restart that service. Use `--timeout-seconds`
only when startup is legitimately slow.

### Manager lock

Wait for the active start/stop/restart operation. A lock owned by a dead process
is detected and replaced automatically.
