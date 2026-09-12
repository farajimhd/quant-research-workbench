# V7 historical and causal streaming books

Independent of trained reaction models, Swing Level Book v6, and strategy orders.
The historical algorithm is `historical-session-reaction-mle-1`; the causal
stream is `causal-level-book-v7-mle-1`. Both use canonical one-second price bars
and per-level Student-t maximum likelihood geometry. Confirmed independent
turning prices fit the center and scale; the central 80% fitted interval defines
the lower and upper boundaries. There is no session-noise band-width fallback.
Chart timeframe changes only presentation, never the level calculation.

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
