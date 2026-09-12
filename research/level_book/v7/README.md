# V7 historical and causal streaming books

Independent of trained reaction models, Swing Level Book v6, and strategy orders.
The historical algorithm is `historical-session-reaction-mle-1`; the causal
stream is `causal-level-book-v7-mle-1`. Both use canonical one-second price bars
and per-level Student-t maximum likelihood geometry. Confirmed independent
turning prices fit the center and scale; the central 80% fitted interval defines
the lower and upper boundaries. There is no session-noise band-width fallback.
Chart timeframe changes only presentation, never the level calculation.

## All-tradable historical campaign

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
