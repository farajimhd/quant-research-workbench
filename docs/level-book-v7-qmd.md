# QMD Level book V7

The app's level-book selector and main `indicator.qmd_unified_structure`
presentation use V7. Archived trade journals keep their original identities;
opening a saved run does not authorize resuming its retired level-book engine.

## Authority and session lifecycle

QMD Live and QMD History each own a bounded persistent Python MLE worker.
Requests advance state from the last consumed completed second, rather than
reconstructing the book in the browser or backend. History consumers have
independent cursors so frame prefetch cannot expose future levels to trade events.

At the first request for a 04:00–20:00 America/New_York session, the worker
verifies the exact preceding source-session receipt and checkpoint. Verified
empty sessions may carry the preceding nonempty checkpoint. It validates source
ordering, ticker identity, source hashes, checkpoint hashes and campaign lineage.
Missing checkpoints do not fall back to V6 or an arbitrary older session.

The explicit workstation catalog combines the main campaign, deferred common-share
supplement and URG recovery. Coverage is verified per session when loading; a
catalog entry alone does not certify that its entire planned range is complete.
`QMD_LEVEL_BOOK_V7_ROOT` can override the shared root; the default laptop root is
`\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\level-book-v7`.

Split adjustments apply to inherited observations and fitted geometry. Streaming
uses QMD's completed causal 1s bars in SIP availability order and excludes delayed
reports. Confirmed independent reactions refit the existing Student-t MLE, including
its adaptive dispersion and mixture partitioning. Chart timeframe never enters
the level-construction contract. Historical merges retain historical identity.

Live closing checkpoints are immutable files under `qmd-live-closing-v7` in the
same shared root. Advancing into a later session finishes the owned previous
session first. After a restart, at most five missing post-campaign live sessions
can be reconstructed from complete QMD durable bar history with the same causal
engine. Missing bars, gaps in the certified campaign or a longer catch-up fail
explicitly. In-memory eviction changes performance, not the input cutoff.

## APIs and projections

- Both gateways: `GET /level-book-v7/catalog`.
- Both gateways: `POST /level-book-v7/snapshot` with `ticker`, timezone-aware
  `as_of`, optional `include_segments`, and optional historical `cursor_id`.
  The gateway determines live versus historical mode.
- QMD History: `GET /level-book-v7/seconds/{ticker}?start=...&end=...` supplies
  causal completed bars and their source revision. This uses a separate cache
  profile from retrospective execution-time chart candles.
- Backend `/api/research/level-book-v7/book` is an HTTP projection only.

Strategies receive qualified active support/resistance bands, fitted centers,
fit evidence, historical identity and causal timestamps. V7 bands are not
collapsed to point prices or filtered using V6 prominence/selection scores.
MLE dispersion is not a calibrated reaction probability. Source revision and
checkpoint provenance are pinned in the backtest data-authority record.

The chart draws time-bounded geometry segments, with separate historical/day
support/resistance colors and visibility controls. Role transitions remain gray
and can be hidden independently. A rewind clears later geometry while its causal
prefix is reloaded.

## Operation and bounds

Each gateway admits at most eight worker requests, retains at most sixteen
session/cursor engines and four completed historical source-day buffers. A cold
closed-day load may prefetch the whole source day for efficiency; only bars whose
close is at or before the requested cutoff enter inference. Source revision is
retained with that frozen input buffer. Live/in-progress days are not prefetched
as complete days. BLAS worker threads are limited to one.

Use `scripts/services.ps1 restart qmd-history` or `restart qmd-live` after source
changes. Service fingerprints include the Python worker, MLE source and V7
environment overrides. `QMD_LEVEL_BOOK_V7_PYTHON` selects its Python executable;
`QMD_LEVEL_BOOK_V7_CODE_ROOT` selects an explicitly deployed code root. Gateway
shutdown terminates its child worker. Computation errors are reported without
discarding unrelated ticker state or substituting a legacy book.
