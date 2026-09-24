# V7 historical and causal streaming books

Independent of trained reaction models, Swing Level Book v6, and strategy orders.
The historical algorithm is `historical-session-reaction-mle-1`; the causal
stream is `causal-level-book-v7-mle-1`. Both use canonical one-second price bars
and per-level Student-t maximum likelihood geometry. Confirmed independent
turning prices fit the center and scale; the central 80% fitted interval defines
the lower and upper boundaries. There is no session-noise band-width fallback.
Chart timeframe changes only presentation, never the level calculation.

## All-tradable historical campaign

### Workstation resume (many-core runner)

Run `scripts/run_level_book_v7_workstation.py` from the committed, verified copy
on **DESKTOP-SAAI85T**, in the same numerical environment as the frozen plan.
This launcher resumes the existing campaign; it never changes its population,
source signatures, MLE algorithm, daily receipt chain, or numerical-version pins.
The current campaign requires Anaconda Python 3.12.12 (exact build recorded in
`plan.json`), NumPy 2.4.0 and SciPy 1.16.3. `preflight` fails on a mismatch;
do not edit the plan to bypass it. The existing campaign dependencies plus
`psutil` and `rich` must be installed in that environment.

From a workstation terminal in the synchronized code directory:

```powershell
python -B scripts/run_level_book_v7_workstation.py preflight
python -B scripts/run_level_book_v7_workstation.py run --take-over
```

`--take-over` first verifies the code/numerical pins, resources and ClickHouse
host, then requests that the old controller finish its active session checkpoints.
It waits up to ten minutes for exclusive campaign ownership before resuming.
No simultaneous laptop/workstation writers are allowed. Without `--take-over`,
an active controller blocks startup. If an orphan worker still owns a ticker,
startup fails rather than stealing its lock; let it finish the STOP request
and rerun. Do not force-kill healthy workers or remove lock files.

The legacy checkpoint runner's default runtime on the workstation is
`D:\TradingML\runtimes\level-book-v7\all-tradable-20250101-20260912-mle-v1`.
It retains the old archive for inspection only; corrected V7 publication uses
the separate direct V2 runner described in
`docs/architecture/LEVEL_BOOK_V7_PERSISTENCE.md`.
Laptop `status`, `monitor` and `stop` use the workstation share automatically.

```powershell
# Optional lower concurrency; the launcher rejects settings over the host budget.
python -B scripts/run_level_book_v7_workstation.py run --workers 32 --threads 1
python -B scripts/run_level_book_v7_workstation.py monitor
python -B scripts/run_level_book_v7_workstation.py stop
```

The persistent process pool is bounded by available RAM and logical CPUs, up to
60 workers (below the Windows process-pool limit). It reserves at least 1/8 of
CPUs and 1/4 of free RAM, budgets one Python CPU plus the configured SQL threads
and two GiB per slot, and defaults to one SQL thread per worker. These are
admission budgets, not OS memory caps. Free RAM below two GiB requests a graceful
checkpoint stop. BLAS/OpenMP/NumExpr threads are fixed at one per process.
For example, 128 logical CPUs and 192 GiB free selects 56 workers and 56 SQL
threads. Each worker reuses imports and HTTP connections across tickers; fit
caches are cleared between tickers. The task queue contains at most one task
per active slot. Each ticker's sessions remain chronological and sequential.

The progress table shows **all configured workers**, including idle slots, with
the V6-style overall progress panel and per-worker bars/percentages, durable
counts, freshness, failures/retries and an approximate ETA. Terminals at least
140 columns wide use two side-by-side worker tables. There is no
paging or terminal-height row truncation. Enlarge the terminal or reduce its font
to fit a large worker table on screen. The independent `monitor` command can use
a newer source copy without restarting the controller; do not overwrite source
files in a running campaign's pinned code directory.
Redirected output uses plain text. Execution manifests under `executions/` pin
the scheduler source hashes, calculation hashes, numerical runtime, host and
resource budget separately from the unchanged calculation plan. Completed
tickers are verified before being skipped; partial tickers use the original
receipt-by-receipt recovery. Current code still processes historical bars through
the shared streaming state machine; this is a scheduling/I/O improvement, not a
new batch approximation of the MLE algorithm.

### Original laptop runner

Launch on the laptop from the repository. The defaults freeze the September 12,
2026 published tradable membership and request January 2025 through September 12.
The certified source currently ends September 11 (September 12 is Saturday).

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:\Users\g835l\miniconda3\envs\ml4t\python.exe -B scripts/build_level_book_v7_campaign.py run
```

`run` creates a frozen plan if absent; otherwise it resumes that exact plan.
Defaults: four laptop processes and two ClickHouse query threads each, bounded
by eight laptop processes / sixteen total query threads. `--workers` and
`--threads` can change on resume; dates, universe, code and numerical-library
versions cannot. Long tickers start first. There are no writes to ClickHouse.

The workstation ClickHouse server filters certified canonical trades, applies
historical SIP condition eligibility, orders trades and computes exact 1s OHLCV.
Certified ticker/day ordinal ranges match its `(ticker, ordinal)` index. Only
aggregated seconds cross the network; unused volume profiles and raw trade caches
are not materialized. Persistent HTTP connections avoid per-query socket churn.
Python runs the unchanged V7 turning-point and Student-t MLE state machine.

The default output is
`\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\level-book-v7\all-tradable-20250101-20260912-mle-v1`.
It requires that root; it never redirects to another drive. Compressed daily
books, source plans, receipts and ready markers preserve source, parent and
checkpoint hashes. Writes are fsynced and atomically replaced with bounded SMB
retries. A runtime free-space guard stops new work below 10 GiB. A crash after a
book write but before its receipt safely recomputes and compares that book.

```powershell
# Read progress from another terminal; the original run has a live monitor too.
& C:\Users\g835l\miniconda3\envs\ml4t\python.exe -B scripts/build_level_book_v7_campaign.py monitor
# Graceful stop (also Ctrl+C in the controller window).
& C:\Users\g835l\miniconda3\envs\ml4t\python.exe -B scripts/build_level_book_v7_campaign.py stop
# Resume, explicitly including failed ticker attempts.
& C:\Users\g835l\miniconda3\envs\ml4t\python.exe -B scripts/build_level_book_v7_campaign.py run --retry-failed
```

Each worker owns one ticker and processes its sessions sequentially. Stop finishes
the active session before exiting. Controller/worker locks prevent duplicate
writers. The monitor has fixed worker rows, current session/stage, heartbeat age,
durable session counts, rate, approximate ETA, retries and explicit deferred /
failed / interrupted counts. Non-terminal output is periodic plain text. Full
errors remain in `tickers/<ticker>/error.json` and `worker.log`.

The population is the published tradable universe as of the pinned date, **not**
a historical tradability decision for every backtest date. Ambiguous published
identities, unsupported ticker mappings and absent certified history are recorded
as deferred, never silently substituted. Historical ticker renames are not
inferred or spliced together. This campaign builds symbol-keyed historical books;
backtest admission still needs its own point-in-time identity/tradability gate.
Historical SIP time is intentionally allowed. Current-day streaming must receive
separately filtered completed candles; the campaign does not fix or reuse its
historical input policy for streaming. These campaign outputs do not automatically
change an existing strategy's V6 selection or the two-ticker chart catalog.

## Historical build

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:\Users\g835l\miniconda3\envs\ml4t\python.exe -B scripts/build_level_book_v7.py `
  --tickers JUNS SUGP --start 2025-01-01 --end 2026-08-20 --test-day 2026-08-21
```

Outputs: `D:\TradingML\runtimes\level-book-v7\jan2025-aug2026-v2-mle\{ticker}`.
Each ticker is sequential; at most two ticker workers and two query threads per
canonical aggregation are used. Certified imported SIP is the sole market-data
authority. Verified cached canonical inputs can avoid a new aggregation. Quotes
and prediction-model features are not needed. Split actions are applied once at
each effective boundary and retained in the checkpoint audit.

`plan.json` pins the dates, settings, fit configuration and splits. Compressed
`inputs` and `books` retain observations and fitted geometry. Daily `receipts` pin input, parent and output
hashes; `status.json` exposes active date, completion, empty sessions and failures.
The ready manifest is published only after all historical dates and the test
input are ready. Rerun the same command to verify and resume; changed immutable
inputs/plans/checkpoints fail. Ctrl+C stops at a session checkpoint boundary.

Historical discovery uses full-session noise and range and is retrospective,
available only after session end. The shared engine processes observations in
time order. Noise determines reversal discovery and candidate association only;
it does not determine displayed width. Fits are cached by exact observations and
resolution. Canonical inputs are reused only after source and content validation.
Old noise-band checkpoints are not accepted by the MLE streaming engine.

## Causal stream

`StreamingLevelBook` accepts a verified prior-session book and completed 1s bars
through `update(bar, observed_at=...)`. Bar `t` is the close timestamp. It rejects
future, duplicate, invalid and out-of-session data. Noise uses a running median
of observed nonzero bar ranges; reaction prominence uses only the observed
session range. A confirmed directional reversal supplies an observation even
outside the current fitted band. It has no full-session extrema or volume profile.

New bands require at least three independent confirmed turning observations.
Sparse candidates remain unpublished. Their presentation begins when the fit
becomes available, never at a backdated pivot. Historical bands start at session
opening. New observations can move their center and change width; every update
appends a geometry segment, preserving the prior segments. Matching day proposals
and split children retain historical identity. Contact outcomes use the geometry
frozen at contact, not a subsequently updated band. Significant gaps reset
incomplete encounters and swing tracking.
Snapshot results contain only qualified levels, with separate candidate counts.

Streaming is an estimator, not a claim of equality with retrospective extraction:
candidate discovery and thresholds can differ because future data is absent.
The fit uses fixed Student-t degrees of freedom 4, with a numerical scale floor
of half the observation price resolution. Split adjustments scale observations,
resolution, center and boundaries together. Distinct price modes can split when
two fitted, nonoverlapping components improve mixture BIC by more than 10. The
three largest eligible gaps supply candidate partitions; this is not a global
mixture optimum. Role and timestamps remain attached to every observation.
The 80% interval is distribution coverage, not a calibrated probability of a
future reaction or a confidence interval for the center. A failed fit never
silently substitutes a fixed-width band.

`checkpoint()` / `restore()` preserve all pending evidence and incremental state,
with a version and checksum. Market/strategy code can call this class directly
without a chart or HTTP. The prepared historical adapter `level_book_feed.book_at`
provides the same snapshots to chart and other callers. It holds at most two
sessions, advances incrementally, and rebuilds the causal prefix on rewind.
It does not read the finalized test-day extraction. A real live source must pass
completed canonical bars to the same engine; this change does not alter existing
strategy orders or wire a new live-market subscription.

## Test in the app

Open a saved Debug or Replay/Backtest chart for JUNS or SUGP on August 21, 2026.
Enable **Reaction book**. Its settings select **V7 historical + streaming**;
Automatic prefers V7 for prepared sessions. Move the chart/replay clock to inspect
the book at that time. Historical and current-day layers have separate toggles.
The source selector can also show the original **Prediction model book**.
Prediction labels keep their separately frozen model inputs; they have not been
retrained on V7.

API: `GET /api/research/level-book-v7/catalog` and
`POST /api/research/level-book-v7/book` with `book_id`, `ticker`, `session_date`,
and `time_et`. Prepared dates are explicitly listed in the catalog. The initial
campaign prepares August 21; it does not claim every historical date is already
published for chart serving.


## On-demand filtered preparation

Backtest preparation runs independent ticker processes concurrently. Sessions within a
ticker remain sequential. `FILTERED_V7_WORKERS` defaults to `4` and accepts `1` through
`4`; processes also share a four-slot budget across runs on the API event loop. Each
child uses one BLAS/OpenMP thread and one ClickHouse query thread. Failure or cancellation
reaps all children owned by that run. A per-successor OS lock serializes duplicate requests.

`filtered_worker --before YYYY-MM-DD` builds only source sessions strictly before the
latest requested Backtest session. It preserves the frozen full source plan and the
existing numerical/source-file identity. Verified earlier receipts are reused; later
requests extend the same chain. Full `ready.json` histories remain reusable. A partial
build publishes an immutable `prefixes/YYYY-MM-DD.json`, never a full readiness marker.
Catalog readers check marker identity, source manifest, cutoff, terminal receipt, and
the requested checkpoint. Missing or corrupt published filtered checkpoints cannot fall
back to unfiltered books. A history containing only verified empty sessions is unavailable,
not permission to serve an unfiltered book.

Receipt and checkpoint writes remain immediate. Replaceable UI progress snapshots are
limited to two per second, including during resume; terminal state is always written.
Backtest Details exposes fixed worker slots, session counts, retry/resume counts, update
age, completed/reused/unavailable/failed ticker counts, throughput, and an approximate ETA.
ETA starts after two rebuilt tickers and conservatively treats remaining tickers as builds.

Validation on 2026-09-18 used AEG, AEHL, AEON, and AESI with certified histories through
2026-08-13, extending through 2026-08-18. Before UI-write throttling, one/two/four workers
took 51.0/27.4/15.8 seconds. All twelve terminal checkpoint hashes exactly matched the
existing full histories. With progress throttling the repeat measured 38.0/24.3/14.1
seconds, again with identical hashes. This bounded local-checkpoint benchmark supports the four-worker
default; it is not a cold 895-ticker runtime forecast. Profiles of AEON and AESI on August
18 showed checkpoint deep-copy work dominating those sessions. The numerical kernel is
unchanged so existing prepared histories retain their identities.

Focused validation: `test_filtered_v7_prefix`, `test_filtered_v7_pool`,
`test_filtered_v7_history`, `test_preparation_process`, `test_v7_catalog`, and
`test_level_book_v7_campaign`. Visual validation uses the frontend launcher's
`ui:review -- --filtered-v7-preparation` fixture (running, starting, failure states).


## Prepare every filtered V7 history on the workstation

Use `scripts/prepare_filtered_v7.py` for the filtered successor campaign; the older
`run_level_book_v7_workstation.py` resumes its original frozen campaign only.
The laptop exports a consumer-pinned manifest once with `prepare_filtered_v7.py plan`.
Run the synchronized source from the workstation's existing V7 environment:

```powershell
python -B scripts/prepare_filtered_v7.py preflight
python -B scripts/prepare_filtered_v7.py run
```

The single `run` command performs preflight, verifies/resumes every eligible ticker,
and builds its full frozen source history. Default scope is 6,441 eligible tickers,
2,484,145 sessions, and 239 separately reported deferred identities in the September 18
inventory. It does not invent missing identity authority or overwrite original histories.
All price reads come from canonical ClickHouse; output uses the same workstation store
through local `D:/TradingML/runtimes/level-book-v7`, so the laptop discovers the results.
The frozen source interval ends September 12 (last trading session September 11).

Default concurrency is the CPU/RAM budget capped at 16 persistent processes. Explicit
`--workers 32` is accepted only if the host budget permits it, up to 60 workers. Each
process has one SQL and one BLAS/OpenMP thread. The budget reserves OS/service capacity
and admits two GiB per slot; it is not a memory hard limit. A low-free-memory guard stops
at checkpoint boundaries. Four workers were exercised with canonical data; higher
concurrency is resource-gated, not yet throughput-benchmarked on the workstation.

```powershell
python -B scripts/prepare_filtered_v7.py run --workers 32
python -B scripts/prepare_filtered_v7.py monitor --page 2
python -B scripts/prepare_filtered_v7.py status
python -B scripts/prepare_filtered_v7.py stop
```

Ctrl+C/stop finish current session checkpoints. Rerun the same command to verify receipts
and resume; failed tickers are retried while complete checkpoints are reused. The fixed
worker table pages to fit terminal height. Status persists every second, includes full
failure/deferred reasons, and preserves the final outcome. Exit code 0 means all rows
complete, 1 means failures, and 2 means interrupted or complete with deferred gaps.
The controller records each execution and cannot run twice under the same campaign lock.
Backtest and campaign writers also share per-successor locks.

Plans/status live under `filtered-preparation-campaigns/filtered-v7-workstation-v1`.
A manifest pins the laptop consumer's exact source bytes, Python 3.12.12 Anaconda build,
NumPy 2.4.0, SciPy 1.16.3, scheduler hashes, population, and parent campaign identities.
Mismatch fails before any fitting. Never edit a frozen manifest to bypass a mismatch;
use the matching environment/source or export a new explicitly named `--campaign` from
the laptop after an intentional contract change. No ClickHouse tables are created.
