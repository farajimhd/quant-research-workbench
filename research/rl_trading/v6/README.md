# RL trading V6: sparse candles and bracketed portfolio decisions

This is a major data and action-contract revision. It replaces the old
Phase 1/2/3 names with three responsibilities:

1. **Candle and Opportunity Compilation** is ticker-local. Read pinned ARTE
   one-second OHLC, notional, volume, trades, and technical indicators once;
   encode actual persisted candles, causal V7 slots, time/session, VWAP,
   same-clock previous-session RVOL, and point-in-time reference fields.
   Compile MACD episodes and fee-aware, discounted candidate scores in the
   same ticker pass. The future close outcome is label-only.
2. **Portfolio Teacher** compares only sparse eligible opportunities across
   listings. It reserves initial-bankroll cash for first-eligible episodes in
   a 15-second window, has no fixed maximum number of positions, and uses a
   three-second minimum hold. It must bind confirmed fills and bracket labels
   before its output may supervise a model. Old V8 artifacts are not V6 labels.
3. **Causal Policy and Replay** consumes the last 120 *actual candles* per
   listing, including a prior certified session's tail where available. It
   learns entry, exit, hold, stop, target, and size from its action/holding
   history. Replay compounds actual model-account cash subject to the existing
   equity/price order-size rules; teacher bankroll segregation does not apply.

The packed bank stores each candle exactly once, as contiguous
`[sum_listing_candles, 37]` scalars and
`[sum_listing_candles, 2 sides, 5 levels, 11 fields]` V7 attributes with
identity offsets and int64 candle close clocks. It does not store overlapping
120-candle windows or pad every listing to 57,601 clock seconds. A reader
references the previous bank for warm-up. `complete.json` binds the plan,
feature schema, shapes, and file hashes. A partial bank has a separate plan
and per-listing progress hashes and can resume only under the same source and
census.

V7 sides mean the five nearest centers below/equal to or above the completed
close, regardless of support/resistance role. Each level records center/lower/
upper distance, total and today-only observation counts, confirmation age,
role, historical origin, and presence. Empty slots are masked. Today's count
separates streaming evidence from carried checkpoint evidence without
duplicating separate groups of levels.
Listings without a prior certified V7 coverage row retain their candles and
opportunities with all ten level slots masked. An existing malformed or
uncertified coverage row still fails closed; the reference certificate records
which case occurred.

The forward training split begins on July 31, 2026. July 30 is used only as
the prior-session candle context for July 31. It contributes no V6 training
labels or trades. The builder's explicit `--context-only` mode compiles its
feature bank without a prior-session RVOL baseline or July 29 dependency; the
RVOL-available mask is zero. This avoids inventing a July 29 ARTE product that
has no certified pre-open population. Development remains August 24–25; August 26
stays sealed until checkpoint selection.

`run_build.py` is the direct entry point. It accepts a certified current and
previous market-day manifest, ledger, dates, exact runtime output path, and an
optional repeatable `--ticker` canary subset. It pins the population, counts
only 1-second rows to allocate exact shapes, then processes listings through
bounded process workers. Each worker uses one read-only ClickHouse socket and
one Polars thread. The cap is 64 concurrent listing workers, at most 128
in-flight tasks, and a 32 GiB host reserve plus 1 GiB per worker. The launcher
fails if those bounds are unavailable. Per-listing progress and sparse
fragments are restartable; final parquet files and hashes certify the day.
This compiler does not build a dense all-listing holding grid.
Its sparse candidate table feeds `allocation.py`, which keeps the first
qualified row per episode, sums scores only at those first-eligible seconds,
and uses a searchsorted prefix sum for the 15-second future reserve. It
normalizes desired cash against the original $10,000 bankroll. This is a
planning input, not an executable teacher trade: confirmed entries, account
cash, bracket exits, and partial fills still require stateful replay.

The bracket oracle and sparse 100 ms event kernel are versioned separately.
The certified ARTE execution-price sidecar supplies per-price volume for
possible target fills. It does not reveal queue position; a touch alone is
not booked as a fill. Future episode extrema never enter policy features.

`model.py` now implements the 120-actual-candle encoder. Training applies a
causal depthwise convolution to each listing's stored sequence once; serving
updates a circular cache only for listings with a new completed candle.
Focused tests prove the two paths agree when listing clocks have gaps. The
encoder alone is not a trainable trading policy. The decoder now has five
action logits and conditional size and
bracket-distance outputs. The account, holding, and OMS adapters must still
bind those proposals to actual fills before training or replay.

`allocation.certify_from_candidates` can derive the sparse first-eligible
15-second reservation sidecar from an already certified candidate table.
It verifies the source certificate and candidate hash without fetching market
data or recomputing the candle bank.

The old Phase 1/2 and Phase 3 builders remain stopped. The new bounded
August 5 compiler benchmark certified 6,122 listings and 26.79 million actual
candles in 1,128.46 seconds at 16 workers. It produced 40,184 qualified
candidates; their allocation sidecar has 7,336 first-eligible intents. These
are not executable fills or a profit result. No V6 model is trained by these
modules alone.

`build_campaign.py` is a serial, restart-safe day controller for context,
training, and development banks. It uses the exact previous trading day and
the two certified market-day manifests, crossing the August 17/18 boundary
explicitly. It stops at a missing certificate. August 26 is absent from its
date list so the sealed holdout is not consumed during data preparation.
