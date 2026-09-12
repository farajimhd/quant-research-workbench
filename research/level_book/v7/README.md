# V7 historical and causal streaming books

Independent of trained reaction models, Swing Level Book v6, and strategy orders.
The historical algorithm is `historical-session-reaction-zones-2`; the causal
stream is `causal-level-book-v7-1`. Both use canonical one-second price bars,
noise-sized bands, confirmed rejections, and immutable historical geometry.
Chart timeframe changes only presentation, never the level calculation.

## Historical build

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:\Users\g835l\miniconda3\envs\ml4t\python.exe -B scripts/build_level_book_v7.py `
  --tickers JUNS SUGP --start 2025-01-01 --end 2026-08-20 --test-day 2026-08-21
```

Outputs: `D:\TradingML\runtimes\level-book-v7\jan2025-aug2026-v1\{ticker}`.
Each ticker is sequential; at most two ticker workers and two query threads per
canonical aggregation are used. Certified imported SIP is the sole market-data
authority. Verified cached canonical inputs can avoid a new aggregation. Quotes
and prediction-model features are not needed. Split actions are applied once at
each effective boundary and retained in the checkpoint audit.

`plan.json` pins the dates, settings and splits. Compressed `inputs`, `extractions`
and `books` retain complete evidence. Daily `receipts` pin input, parent and output
hashes; `status.json` exposes active date, completion, empty sessions and failures.
The ready manifest is published only after all historical dates and the test
input are ready. Rerun the same command to verify and resume; changed immutable
inputs/plans/checkpoints fail. Ctrl+C stops at a session checkpoint boundary.

The full-session extractor is retrospective and only available after session
end. Optimization shares canonical arrays across candidates and visits touch
indices rather than scanning Python rows for every level. It preserves existing
encounter and consolidation outcomes; it does not downsample prices or discard
levels for speed. Checkpoint geometry is not silently upgraded between versions.

## Causal stream

`StreamingLevelBook` accepts a verified prior-session book and completed 1s bars
through `update(bar, observed_at=...)`. Bar `t` is the close timestamp. It rejects
future, duplicate, invalid and out-of-session data. Noise uses a running median
of observed nonzero bar ranges; reaction prominence uses only the observed
session range. A confirmed directional reversal proposes a band. Unlike the
historical extractor, it has no full-session extrema or volume profile.

New bands require two resolved rejections meeting the configured rejection
fraction. Their presentation begins when that evidence becomes available, never
at a backdated pivot. Existing historical bands start at session opening, retain
their geometry/identity, and change role only through observed acceptance/retest
evidence. Matching day proposals remain historical; they cannot count the same
rejection twice. Significant gaps reset incomplete encounters and swing tracking.
Snapshot results contain only qualified levels, with separate candidate counts.

Streaming is an estimator, not a claim of equality with retrospective extraction:
candidate discovery and thresholds can differ because future data is absent.
The noise-based bands are not calibrated confidence intervals.

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
