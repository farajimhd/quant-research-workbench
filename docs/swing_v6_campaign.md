# V6 tradable-universe campaign

Run `scripts/build_swing_book_campaign.py` from the repository on the execution
host. It builds V6 directly from canonical seconds, with selected daily survivors
as the only carry. It does not create a V4 candidate history or a V5 conversion.

Run on the workstation using its configured Python environment and synchronized
checkout under `\\DESKTOP-SAAI85T\Workstation-D\TradingML\codes` (the source root
specified in `AGENTS.md`). Set the working directory to that checkout, containing
`scripts/build_swing_book_campaign.py`. Source synchronization is separate from
campaign execution and follows laptop validation, commit and push.
The default secret file is
`\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets\.env`.
Both the controller and ticker workers accept the workstation runtime UNC root
below. They also accept the documented laptop runtime root for explicit local
validation. An unavailable requested root fails; there is no fallback.
Using a workstation UNC destination does not move execution to the workstation:
the Python command must run on that machine to use its CPUs and memory.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$campaign='\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\structure-validation\v6-tradable-20250101-20260904'
python -B scripts/build_swing_book_campaign.py plan --runtime $campaign --start 2025-01-01 --end 2026-09-04 --workers 4 --threads 2
python -B scripts/build_swing_book_campaign.py run --runtime $campaign
```

`plan` freezes the latest published `q_live.feature_tradable_universe_v1`
membership with `is_tradable=1`, retains identity and membership provenance, and
checks canonical coverage. This is the **current tradable universe**, not a
historical survivorship-free research universe. Ambiguous identities, differing
market symbols and missing canonical history are explicitly deferred with reasons.
No source flatfiles are used. Storage must pass `live_market_ssd` checks.

Four worker processes each process one ticker in chronological session order.
Each query uses at most two ClickHouse threads by default. There is
one aggregation query at a time per ticker worker. New plans read one complete
session using the certified source-day ordinal range and the original timestamp
and trade-condition predicates. Thus 64 workers with two
threads each request at most 128 query execution threads, excluding ClickHouse
background work and other applications. This is a query budget, not a limit on
all server threads. Scheduling starts
larger event histories first. Workers have isolated logs and checkpoints;
failures do not strand other queued tickers. Configure concurrency at planning
time; the plan pins source-code hashes and settings. Changed code needs a new plan,
except for the explicitly verified reader migration described below.
The launcher supports up to 64 workers and a combined query-thread budget of
128 (`workers * threads`). On the 128-core workstation, explicitly select
`--workers 64 --threads 2` when planning; the conservative default remains four
workers. This is a supported limit, not a measured 64-worker speedup.

Interactive progress refreshes in place every second using a Rich dashboard.
The top panel shows overall completed tickers, a progress bar, status counts and
an estimated ETA. Each worker has a stable numbered row showing its ticker,
state, checkpointed sessions, progress bar and elapsed time for this attempt.
Wide terminals use two columns; short terminals use manual N/P paging so all
workers remain accessible without scrolling or rotating their positions.
Ctrl+C still stops at session boundaries. Detail stays in per-worker logs.
Redirected output emits compact status summaries only when counts change.
ETA is unavailable until measurements exist and is approximate:
liquid tickers can cost much more than SUGP/JUNS. `--progress-seconds` changes the
report interval. `--tickers SUGP AAPL` on `plan` creates an explicit bounded pilot.
Pass `--progress-seconds` to `run` to override the display interval. The display
refresh does not rewrite the full manifest; durable state is saved on worker
transitions and controller shutdown. Session counters advance when each session
finishes, while elapsed times refresh every second. Temporarily unreadable reports
retain their last snapshot marked stale. A session count of zero before the first
report is available is not evidence of a stalled worker.

The Rich display update accepts the exact prior df562ae5 controller fingerprint
(LF or CRLF) while verifying every other pinned source file unchanged. Existing
campaigns can resume without replanning; this exception does not admit engine,
source-reader, persistence-builder, or algorithm changes.

```powershell
python -B scripts/build_swing_book_campaign.py status --runtime $campaign
python -B scripts/build_swing_book_campaign.py stop --runtime $campaign
python -B scripts/build_swing_book_campaign.py run --runtime $campaign
python -B scripts/build_swing_book_campaign.py run --runtime $campaign --retry-failed
```

Stop requests wait for the current session to finish and checkpoint; no forced
process termination is used. Resume verifies completed daily state and continues
unfinished tickers. Completed tickers are skipped. OS locks prevent duplicate
controllers and writers; if orphan workers still hold locks, wait for them to
finish or request stop before resuming. Exit codes: 0 completed, 1 failure,
2 deferred tickers remain, 130 stopped.

The campaign directory contains `manifest.json`, planning profiles and
`workers/<ticker>/worker.log` plus `progress.json`. V6 reports are in the adjacent
`<campaign-name>-v6/<ticker>` directory, discoverable by the existing book registry.
The manifest records per-ticker elapsed seconds, database, status and failure
reason. No full-universe runtime estimate is certified from the two-ticker test.

## Windows ticker path repair

Windows reserves names such as `CON`, including when they have an extension.
Workers and book outputs now use a collision-free encoded directory for those
tickers (for example `_ticker_434f4e`); ticker identity and all safe existing
directory names remain unchanged. Existing stopped campaigns must opt in once:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/build_swing_book_campaign.py run --runtime '\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\structure-validation\v6-all-df562ae5' --upgrade-paths --workers 8 --threads 2 --progress-seconds 1 --env-file 'D:\TradingML\secrets\.env'
```

Use the synchronized `quant-research-workbench-043b71b3` checkout on the
workstation. The migration accepts only the exact prior path/controller code
with unchanged engine and source files, journals path changes in the manifest,
and retains database fingerprints and checkpoint verification. It is idempotent;
later resumes may omit `--upgrade-paths`. Do not run `plan` on the existing runtime.

Completed tickers skip. Interrupted tickers automatically requeue and resume
their verified daily prefixes. Queued tickers start normally. Deferred tickers
remain deferred until their identity/syntax/source-history reasons are resolved;
`--retry-failed` does not resolve them. Failed tickers require `--retry-failed`.
Controller dispatch/render errors now append their timestamp and traceback to
`controller-errors.jsonl` and preserve the stop reason in `manifest.json`.

## Indexed reader migration

After synchronizing validated source, resume a stopped legacy campaign with:

```powershell
python -B scripts/build_swing_book_campaign.py run --runtime $campaign --upgrade-reader
```

This accepts only known legacy builder/controller fingerprints, with all other
previously pinned files unchanged. It preserves the frozen universe, database
identity and completed checkpoints, and pins the new execution fingerprint.
Completed tickers remain skipped; failed tickers require `--retry-failed`.
Subsequent resumes use the ordinary `run` command.

## Connection-failure recovery

The transport reuses a bounded pool of HTTP connections (up to eight per client,
normally one for a campaign worker). Failed reads retry up to six attempts with
exponential backoff and jitter. Writes are never automatically replayed after an
uncertain response: the worker fails, and the next explicit resume verifies its
certified prefix before repeating deterministic unfinished-session work.

The controller stops dispatch after four transport failures in one invocation,
requests active workers to stop at session boundaries, and preserves queued work.
The manifest records the stop reason. Fix the connection problem before retrying;
do not repeatedly restart against an unavailable server.

After the validated transport repair is synced, run this in workstation
PowerShell from the synced code directory, with the Python environment active:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$campaign = '\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\structure-validation\v6-all-df562ae5'
python -B scripts/build_swing_book_campaign.py run --runtime $campaign --upgrade-transport --retry-failed --workers 8 --threads 2 --progress-seconds 1 --env-file 'D:\TradingML\secrets\.env'
```

`--workers` and `--threads` now override and persist the saved concurrency on
resume. Omitted values preserve it; planning defaults remain four workers and
two query threads. The startup line reports the effective values and retried
ticker count. Eight workers is a conservative starting point, not a measured
optimal setting.

The explicit upgrade accepts only the known prior transport/controller hashes
with unchanged engine and source files. It retains existing book fingerprints
and requires reference-reader and saved-checkpoint parity. Completed tickers
remain completed; failed/interrupted tickers retain their previous attempt
summary and resume from verified daily checkpoints. Identity-deferred tickers
remain deferred. The controller clears the STOP marker only after identity and
exclusive-worker-lock checks. Do not re-plan, delete the runtime, or edit hashes.

Before any new book/session writes, each indexed worker compares reference and
indexed candles for the last completed and next unfinished session. It also
reproduces the last completed V6 state, including intervening splits, and checks
its saved hash. Any mismatch fails closed. This is a bounded migration gate,
not a claim of exhaustive historical parity. Every completed prefix checkpoint
still receives the existing integrity check. New tickers verify their first day.

The dashboard shows `verifying` during this gate. Runtime evidence is saved as
`reader-verification-<timestamp>.json`; session profiles separate `read_seconds`
from computation and total time. The initial reference checks still perform
eight queries per sampled session. Steady-state reads use one ordinal-bounded
query, with a hard 57,600-row result limit. No flatfile fallback or table migration
is introduced. Measure actual throughput after verification before increasing
concurrency; 64 workers is a supported ceiling, not a recommended starting load.
