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
   three-second minimum hold. Its idealized entry, exit, stop, and target
   labels depend on certified one-second candle price action, never quotes.
   Old V8 artifacts are not V6 labels.
3. **Causal Policy and Replay** consumes the last 120 *actual candles* per
   listing, including a prior certified session's tail where available. It
   learns entry, exit, hold, stop, target, and size from its action/holding
   history. Quote-aware replay and environment learning compound actual
   model-account cash subject to existing equity/price order-size rules;
   teacher bankroll segregation does not apply.

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

The price-action-only V6 bracket oracle uses the minimum of three pre-entry
one-second lows and observed held-period lows, minus an explicit tick offset,
for the stop; the target is the held-period maximum high rounded to a
certified tick. It withholds a perfect-stop label when a held second is
unobserved. Historical tick-size authority remains required before final
teacher certification. Future episode extrema never enter policy features.
The older quote-dependent oracle is a separate prior version and is not used
for V6 teacher labels. Quote and certified execution-price evidence affect
the model environment and replay only; displayed size and price-level volume
are optimistic fill bounds, never queue-position proof.

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

`build_entry_quotes.py` reads one pinned ARTE 100 ms broker-liquidity bucket
after each first-qualified *long* entry or hindsight exit decision (`--clock
entry|exit`) and persists only the necessary quote fields and availability
reason. Missing arrival buckets may use a quote known at the decision close
only if it remains fresh at arrival; the certificate counts both sources
separately. It does not write a dense quote grid.
`run_diagnostic.py` joins certified sparse intentions to entry and exit quote
evidence and writes an order and closed-position ledger. Its reported P&L is
only realized modeled quote P&L: stops, targets, unresolved holdings, terminal
liquidation, spread impact, and broker execution are not certified by this
diagnostic. It is not teacher supervision or a full-session profit result.
`oms.py` uses fresh displayed bid/ask size as an optimistic fill cap and the
existing price-based share caps, with modeled IBKR fees but no extra assumed
half-spread. Target fills can only use certified price-level volume upper
bounds. A same-bucket stop/target collision credits no target fill and queues
a later stop-market attempt. The resulting account and position ledgers are
scenario results, never evidence of broker fills.

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
Its `--reuse-day-root` mapping binds an independently certified day, such as
the August 5 full-market benchmark, so its 26.79 million feature rows are
not recomputed. The controller still asks the day builder to verify that the
reused root's source plan matches before accepting it, and records the actual
root for each date in a restart-safe `day-roots.json` runtime manifest.

`session_data.py` opens the certified identity-packed bank with full hashes,
checks the prior-session authority, and yields only observed close-clock
events. `candle_stream.py` carries 119 actual prior-session candles and
updates only listings with a new candle. Its indexed tensor update avoids
per-listing Python/autograd operations; the full state is detached at each
bounded chronological chunk. A workstation August 5 smoke with 32 observed
clocks, width 128, and 1,007–4,828 changed listings per clock measured 28.34 s
and 4.39 GiB peak GPU allocation, versus 45.66 s and 6.88 GiB for the prior
dictionary implementation (including the same 20 s bank load and 5 s context
seed). This is a local smoke, not a full-epoch throughput claim. Unlabeled
chunks advance state without an autograd graph. `model.BracketPolicy` adds execution-outcome action memory to
the five-action decoder. `objective.py` scores the selected action, entry
cash fraction, or label-only stop/target distance conditionally.
`build_identity_map.py` projects only listing ID and ticker from the pinned
pre-open population into a small hash-bound sidecar. Replay uses this mapping
to address the OMS; it never guesses a ticker from the packed row order.

`teacher_data.py` requires a separately audited V6 price-action-only teacher
certificate bound to the exact bank hash. It rejects quote execution evidence
in that certificate. Its sparse decisions contain causal pre-action account
and holding snapshots; later idealized candle-path outcomes reference their
originating decisions. `training.py` is the teacher-forced pretraining core.
Each held snapshot contains quantity, cost basis, age, marked return,
distances to armed stop/target, two armed flags, and a stop-pending flag. The
decoder scales dollars and share counts before projecting them so realistic
account magnitudes do not saturate the action logits. The replay observation
also includes cash reserved by pending entries and their count; submitted
orders cannot masquerade as confirmed holdings. A chronological quote queue
books fills at later buckets and retries partial exits while the source has
certified evidence. The August 5 single-attempt diagnostic left eleven
positions open; a bounded follow-up check found fresh bids for all eleven
within 60 seconds. This is a diagnosis, not a completed replay result.
No V6 price-action teacher certificate, quote-aware policy rollout, or
environment-learning campaign is available yet; these modules must not be
presented as a trained V6 model.
`replay_metrics.py` separates realized P&L, unrealized marked P&L, fees,
period-by-period marked P&L, drawdown, turnover, holding time, stale marks,
and terminal open positions. `replay_artifacts.py` persists immutable order,
closed-position, and equity ledgers bound to the checkpoint, bank and quote
evidence. A sealed-test replay requires a prior development-selection
certificate. The live quote/price-level policy rollout remains to be built.
