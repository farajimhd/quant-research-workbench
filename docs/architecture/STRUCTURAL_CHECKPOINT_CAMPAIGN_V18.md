# Structural checkpoint campaign v18

The campaign launcher builds algorithm 18 with the `structural-prominence-v18`
feature and runs `structure_checkpoint_campaign_v18`. Campaign protocol version
12 is distinct from structural algorithm version 18. The old v16 executable and
default service builds retain their existing algorithms. A service consuming
these new checkpoints must also use algorithm 18; renaming a checkpoint set
does not make older checkpoints compatible.

The construction contract and validation evidence are in
[STRUCTURAL_PROMINENCE_V18_VALIDATION.md](STRUCTURAL_PROMINENCE_V18_VALIDATION.md).
Scores remain derived metadata. They do not select, construct, merge or evict
levels. The strategy's deferred quality-gate policy is unchanged.

## Priority and scope

Reconstruct the existing tradable-stock universe from **2025-01-01 through
2026-08-31 inclusive**, using canonical ClickHouse SIP events only. No old
algorithm-16/17 checkpoint can seed this reconstruction. Preserve each ticker's
causal event order and checkpoint certification chain.

The first ten tickers below are claimed first, in ranking order. Remaining workers
immediately claim the rest of the universe without waiting for those histories to
finish. A shared durable queue balances work across up to **96 worker processes**;
one ticker's history is never split across racing workers. Protocol 11 removes the
protocol-10 completion barrier, which limited useful initial concurrency to ten.

The terminal distinguishes processes alive from workers doing work (including
source reads, validation and recovery) and workers waiting or starting. Protocol
12 publishes a separate recovered-event counter. Replay throughput is the change
in covered events minus recovered events; recovery throughput is measured separately.
Primary ETA uses `elapsed_seconds * (total_events - covered_events) / covered_events`,
including replay and recovered coverage. Both counters come from the same supervisor
attempt snapshot; reopening a monitor never resets elapsed time. It is labeled
average coverage and can be optimistic while recovery dominates. Recent replay and
recovery rates remain separate diagnostics. Worker directories are rediscovered
on every refresh so startup cannot freeze the monitor at an incomplete worker count.
Failed work suppresses the estimate.
Event coverage alone does not imply finished
checkpoint certification. Changed throughput or ticker complexity can change ETA.
Each priority ticker's full-history certification marker produces a one-time
terminal completion message with the requested date range. The completed list
and messages remain in `campaign-status.json` and reappear in a reattached monitor;
finishing an individual day or merely exhausting event coverage does not qualify.

Recovered write failures produce structured records in each worker's log with
attempt number, request/response phase, HTTP status when available, and bounded
error details. Session replay retries include ticker and session date. Successful
retries remain historical diagnostics, not active blockers. Synchronous checkpoint
INSERTs require both successful HTTP status and an empty response body; a trailing
ClickHouse exception or unexpected body fails closed and cannot certify a day.

Priority is scheduling metadata, not an input to structure construction. Rank
the August 21, 2026 **04:00–20:00 Eastern** session by reported trade dollar
turnover. This is a liquidity proxy, not a spread/depth estimate. Use the existing
tradable-stock universe and historical market caps **at or below $2 billion**,
including microcaps. Observation and insertion timestamps must precede the
session cutoff. Missing caps are excluded explicitly; no current cap is substituted.

| Rank | Ticker | Session reported turnover |
| ---: | --- | ---: |
| 1 | JUNS | $444.82 million |
| 2 | PURR | $404.46 million |
| 3 | SDOT | $298.09 million |
| 4 | ASST | $280.61 million |
| 5 | HOWL | $273.46 million |
| 6 | RFAI | $160.52 million |
| 7 | FBRX | $156.53 million |
| 8 | VZLA | $155.56 million |
| 9 | FCEL | $150.21 million |
| 10 | FLO | $149.98 million |

The report ranked 3,497 eligible tickers; 388 lacked usable historical caps and
2,068 exceeded the cap. Full-session ranking does not require positive turnover
in every sub-session. Reported extended-hours trade conditions are included.
Market-cap observations in the selected rows are dated August 18, 2026.

The immutable report includes source revision, historical reference hash,
per-session turnover, caps, listing identities and exclusions. The launcher
pins a copy and SHA-256 in the campaign runtime. Changing the priority report,
source commit, executable hash or algorithm requires a new campaign identity.
Unless explicit liquidity dates are supplied, the report's session also sets
the remaining-ticker ranking window, avoiding the older whole-month raw scan.

## Build and run on the workstation

Deploy committed laptop source first. From the workstation repository root,
with its Python environment activated:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:DOTENV_PATHS = 'D:\TradingML\secrets\.env'
$env:CARGO_TARGET_DIR = 'D:\TradingML\runtimes\cargo-target\quant-research-workbench'
cargo build --release --manifest-path services/qmd_history_gateway/Cargo.toml --features structural-prominence-v18 --bin structure_liquidity_priority
New-Item -ItemType Directory -Force D:\TradingML\runtimes\structure-validation\prominence-v18 | Out-Null
& "$env:CARGO_TARGET_DIR\release\structure_liquidity_priority.exe" 2026-08-21 D:\TradingML\runtimes\structure-validation\prominence-v18\liquidity-smallcap-20260821.json 2000000000
```

Run the read-only storage/identity preflight before launch:

```powershell
python scripts/run_structure_checkpoint_campaign.py --preflight-only --start-date 2025-01-01 --end-date 2026-08-31 --checkpoint-set-id canonical-tradable-20250101-20260831-prominence-v18-v1 --runtime-dir D:\TradingML\runtimes\qmd_gateway\structure-checkpoint-campaign-v18
```

Then launch the requested scope:

```powershell
python scripts/run_structure_checkpoint_campaign.py --start-date 2025-01-01 --end-date 2026-08-31 --workers 96 --process-workers 96 --checkpoint-set-id canonical-tradable-20250101-20260831-prominence-v18-v1 --runtime-dir D:\TradingML\runtimes\qmd_gateway\structure-checkpoint-campaign-v18 --priority-ranking D:\TradingML\runtimes\structure-validation\prominence-v18\liquidity-smallcap-20260821.json
```

Default launches ask Cargo to validate/build the current source rather than
silently choosing an old runtime binary. `--binary` and `--no-build` are explicit
prebuilt-executable choices. For a deployment without Git metadata, additionally
supply `--source-commit` with the exact committed laptop revision. Never invent
that revision. Each runtime pins its executable hash and source revision.

The PowerShell wrapper also defaults to 96 workers. The Python commands specify
96 explicitly and avoid depending on a particular user's Python installation.

## Resource use, progress and stopping

Historical v18 HTTP reads default to one ClickHouse query thread and 1 GiB per
query. Explicit bounded SQL settings for small shared coverage/ranking queries
remain effective. These are query budgets, not a whole-process memory guarantee.
Event batches and retry concurrency remain bounded. Monitor host memory, SSD
space, query failures and event throughput before increasing any other budget.
The coordinator aggregates event counts and verifies completed sessions in
contiguous calendar-month windows, combining checked integer totals and unique
session dates. Single twenty-month aggregations exceeded
1 GiB during full-plan validation; month windows bound aggregation cardinality
without omitting days or raising the workers' memory allowance.

On September 5, ClickHouse reported 128 logical CPU frequency entries, about
251 GiB total memory and 5.7 TB free on `live_market_ssd`. This is the ClickHouse
host's capacity snapshot, not a measured 96-worker campaign benchmark. Windows
remote process management returned access denied from the laptop session.

The dashboard retains active stages, queued/completed/certified/failed units,
worker failures, restarts and logs. A fatal worker error stops peers, including
other workers. Recoverable transport failures retain
bounded retries. A stopped run is not reported as sealed.

A fast stop enters the supervisor's shutdown path immediately, allows 60 seconds
for cooperative worker exits, then terminates remaining owned child processes.
Closing a detached monitor does not stop that supervisor. For an older supervisor
with the unreachable shutdown deadline, run `scripts/stop_structure_checkpoint_campaign.ps1`
on the campaign host with its local `RuntimeDir` and `CheckpointSetId`. It verifies
executable and command-line ownership, stops matching workers after a bounded grace
period, and checks for no remaining matching processes. If the supervisor died with
a stale running status, it archives that evidence before marking the local run
interrupted. It does not delete or certify any checkpoint. Windows liveness checks
use process handles, never `os.kill(pid, 0)`.

```powershell
python scripts/run_structure_checkpoint_campaign.py --stop-existing fast --checkpoint-set-id canonical-tradable-20250101-20260831-prominence-v18-v1 --runtime-dir D:\TradingML\runtimes\qmd_gateway\structure-checkpoint-campaign-v18
```

Stop control does not require a working/current executable. Fast stop is checked
at bounded ordinal-chunk boundaries; graceful stop finishes the current unit.
Restart the same immutable command to revalidate and reuse compatible certified
prefixes. Algorithm changes require a fresh set rather than a recovery shortcut.

## Storage and rollout boundary

Checkpoint tables and actual active parts must be on `live_market_ssd`.
`default` is backup-only. Preflight fails rather than falling back to it.
The interrupted old migration contained originals plus an SSD staging copy;
resuming that old journal after a reset is invalid because table UUIDs change.

Old checkpoint deletion is separate from ordinary campaign launch. A reset
must identify exact tables/UUIDs, verify no structural queries, queued inserts,
mutations or moves, journal each exchange/drop, and preserve canonical SIP and
unrelated structural state/events/focus data. Other tasks' validation databases
are outside the production checkpoint reset.

The September 5 reset completed for `q_live.qmd_structure_daily_checkpoint_v2`,
`qmd_structure_daily_checkpoint_v1` and `qmd_structure_checkpoint_set_registry_v1`.
All three were recreated empty on `live_market_ssd`. The abandoned
`qmd_structure_daily_checkpoint_v2_ssd_a2f043ab453b` copy was deleted. Original
UUIDs, DDL, row counts and each completed exchange/drop are recorded in
`D:\TradingML\runtimes\structure-validation\checkpoint-reset-20260905\reset.json`.
The actual launcher preflight passed after the reset. No structural queries,
queued inserts, mutations, moves or merges remained at the final reset check.

The isolated runnable campaign check processed 131,198 canonical events for
JUNS and SUGP on August 20 using two supervised workers and the priority barrier.
Both SSD checkpoints were complete and certified under algorithm 18. A native
resume then validated and skipped both checkpoints with zero advanced events,
zero retries and zero failures. Its temporary database was removed after
verification; evidence remains under
`D:\TradingML\runtimes\structure-validation\v18-write-resume`.

Regression results: 34 launcher tests, 240 v18 core tests, 104 historical gateway
tests and 17 native campaign tests passed. Shared changes also passed 233 v16
core tests and 235 default-service core tests. These include compact dashboard
coverage, exclusive work claims, priority claim ordering, fatal-worker propagation,
immutable recovery and source-build selection.
The actual PowerShell wrapper is exercised too: PowerShell boxes nullable dates
as `DateTime`, so presence checks use null comparisons rather than `.HasValue`.
Its banner and forwarded date/worker arguments match campaign v10/algorithm 18.

The complete plan also validated successfully: **6,489 tickers and 2,699,424
ticker-session units**, with the requested ten tickers first and coverage
through August 31, 2026. The plan is saved under
`D:\TradingML\runtimes\structure-validation\v18-full-plan\campaign-plan.json`.
This validates planning and workload identity; the full rebuild has not run.

Bounded JUNS/SUGP validation is not proof of all-ticker, twenty-month capacity
or a guarantee of no bugs. The first ten histories provide the next acceptance
stage. No strategy orders are submitted by this campaign.
