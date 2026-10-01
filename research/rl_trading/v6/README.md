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
certified tick. It uses only actual observed trade candles and records gaps
without inventing missing prices. Historical tick-size authority is required before final
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
Price-action teacher certificates are generated independently of quote fills.
`environment.py` and `rollout.py` now collect actual policy trajectories and
reconstruct chronological likelihoods for bounded-memory PPO. These modules
must not be presented as a trained V6 model before a campaign is completed.
`replay_metrics.py` separates realized P&L, unrealized marked P&L, fees,
period-by-period marked P&L, drawdown, turnover, holding time, stale marks,
and terminal open positions. `replay_artifacts.py` persists immutable order,
closed-position, and equity ledgers bound to the checkpoint, bank and quote
evidence. A sealed-test replay requires a prior development-selection
certificate. `environment_source.py` projects pinned quote/extrema evidence
only for submitted/held tickers, and reads target price-level evidence lazily.

## Audited training launcher

Run `python -m research.rl_trading.v6.prepare_training --help` for the coverage
audit and ranked-teacher preparation command. It tests R=500/1000/2000,
chooses the smallest meeting 99% entry coverage on every training day, and
recompiles the account after excluding entire outside-R entries. Original
feature banks and teachers stay immutable. An incomplete campaign produces
only a blocked audit state, never a training-ready certificate.
The user selected N=1000 after reviewing missed-entry profit. Pass
`--fixed-rank 1000` to preserve this choice; coverage stays audited and reported,
while the automatic 99% expansion criterion no longer chooses the universe.
All source, label reconciliation and full-split launch gates still apply.

Run `python -m research.rl_trading.v6.run_train --help` for the Python launcher.
Required arguments are the complete dataset certificate, runtime run root,
pinned early/late ARTE manifests and ledger. `--audit-only` checks causal
encoding and real rollout reconstruction without optimizer updates. Normal
launch performs teacher initialization followed by on-policy PPO, logs W&B
metrics and immutable replay ledgers, and checkpoints completed sessions.
Resume requires the same source/configuration and explicit `--resume`.
Development replays and a representative in-sample replay run each epoch;
August26 stays unopened. See `RL_DESIGN.md` for execution assumptions.

Replay runs after every completed teacher/PPO epoch by default
(`--replay-every 1`): Aug24/25 development and Jul31, Aug10, Aug21 training
diagnostics (`--train-replay-days`). These three training days are in-sample.
PPO also collects environment trajectories on every training day for learning;
these are distinct from the deterministic checkpoint replays. Win rate is the
fraction of fully closed ticker/entry positions with positive net P&L after
fees. Partial exits are combined, open remainders excluded, and breakeven
positions remain in the denominator (absolute net <=1e-8 is numerical zero).
No closed positions yields null win rate rather than an artificial zero.

Progress metrics are emitted every 60 seconds at safe processing boundaries
(`--log-every-seconds`), plus session start/end and each finished replay.
The local `metrics.jsonl` is registered for W&B live file upload; immutable
order/position/equity artifacts upload after each replay. W&B uploads are
asynchronous and depend on connectivity; blocking source reads can delay the
next processing-boundary progress event. Local evidence is preserved.

Modeled LULD sidecar and risk shaping
-----------------------------------

`build_luld.py` performs SELECT-only canonical trade-price aggregation and
pinned ARTE quote projection, then saves only native 100ms band/pause changes under
the runtime root. Rolling means use transaction counts, never candle-close
averages or VWAP. The prior regular-session last sale is split-adjusted and
identity-bound; it is not asserted to be the official primary opening price.
An unavailable prior price stays explicitly unknown. Tier membership must be
supplied with `--tier-map`; `--tier2-scenario` is an explicitly labeled research
fallback. Neither output is official exchange halt evidence. Five-minute
reopening is modeled. An observed eligible canonical trade ends an inferred
pause at its actual SIP timestamp; corrections to the timer are counted, and
a new limit-state run is required before another inferred pause. The event
never erases earlier pause history or enters earlier policy observations.
Absent such evidence, late pauses remain blocked through the regular close.
Regular-session bands stop applying at 16:00. News halts require source status
events and cannot be inferred by this estimator. A modeled pause conflicting
with observed eligible trades fails the execution audit and blocks training.

Example, from an immutable source snapshot with runtime environment loaded:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.rl_trading.v6.build_luld --early-manifest D:\TradingML\runtimes\market-day-jul30-aug17\latest.json --late-manifest D:\TradingML\runtimes\market-day\latest.json --ledger D:\TradingML\runtimes\build-ledger-v2.sqlite3 --output D:\TradingML\runtimes\rl-v6-modeled-luld --workers 8 --tier2-scenario
```

Training requires `--luld-root` and validates every train/development sidecar
before the optimizer is created. Teacher initialization defaults to and
requires at least 10 epochs. Existing price-action teachers remain unchanged;
their nine holding fields are padded in memory with two zero risk channels.
The new actor receives causal pause status and elapsed pause age for holdings;
the critic receives detached exposure-weighted risk channels. New checkpoints
are intentionally incompatible with the older nine-field architecture.

The reward subtracts separately recorded risk shaping: 0.10 of trapped marked
notional / initial bankroll once per position/pause, plus 0.01 per trapped
minute, and 0.25 of residual marked notional / initial bankroll once at 20:00.
These are explicit, uncalibrated research defaults, exposed by CLI arguments.
They are never booked as fees or financial P&L. A canary cutoff incurs no
terminal-close cost. Never fabricate liquidation prices for stuck positions.
Quote reads prefetch bounded 15-second chunks per active ticker; filtering
prevents future cached evidence reaching execution or policy observations.

### Independent episode-window supervision

`python -B -m research.rl_trading.v6.episode_windows --dataset <audited-complete.json> --output <fresh-runtime-root> --days <train/dev dates>` reuses certified banks, episodes and candidate scores. It writes only three sparse target tables per day; no candle bank or overlapping feature shards are regenerated. Set `PYTHONDONTWRITEBYTECODE=1` before invocation.

Entry-vs-WAIT probabilities are existing fee-aware scores normalized by the episode's best score, subject to at least 1% upside to the episode maximum high. EXIT-vs-ticker-HOLD probabilities use net profit relative to the best exit, with observed terminal liquidation. Held examples describe a hypothetical one-share position, never a jointly executable portfolio. Each episode branch has total optimization weight one. No profitable episode is erased because the portfolio did not select it.

Rolling `[t,t+15s)` hindsight allocation takes one maximum qualifying score per episode, thresholds it and normalizes all surviving scores. Repeated candidate times do not multiply allocation weight. Future scores are sizing labels only, never observations. Allocation and entry/exit quality remain separate; the peak-entry guard applies before allocation.

A fresh `--teacher-only --action-contract wait-hold --episode-supervision-root <root>` run requires all audited day sidecars. Local soft cross entropy compares only that ticker's alternatives, so another profitable ticker is not an implicit negative. Causal market ranking/attention remains unchanged; this explicit supervision mode permits labels outside portfolio top-R. Its local label loss/recall is not comparable to historical portfolio-token metrics and does not establish portfolio selection or profitability. Existing replay, broker, PPO and legacy teacher behavior stay unchanged. This path does not enable repeated live orders, adding or reducing positions.

### Bps and execution-cost feature contract

`--feature-normalization <normalization.json>` opts into `rl-v6-bps-execution-features-v1`. Existing certified banks remain unchanged. The adapter expresses candle O/H/L geometry, VWAP/EMA distances, MACD and ATR relative to the completed candle close in bps. Level distances use the same close reference. Absolute log close in USD is retained explicitly. RSI/exposure stay fractions, masks stay 0/1, and log counts/ages retain their documented units. Training-bank-only mean/std normalize candle channels; development never fits these statistics. Held returns/bracket distances use bps with fixed 1,000-bps numerical preconditioning.

Eleven fresh causal channels describe completed trailing-one-second spread, adverse VWAP-versus-mid slippage proxy, quote age/availability, volume availability, and fill fractions/round-trip fee bps for $100/$1,000/$10,000 probes. This is a participation proxy, not a measured order-book impact curve. Capacity sums per-100-ms rounded participation quantities only in executable buckets, excludes modeled pauses/band failures, and uses commission-affordable requested quantities. Unknown evidence has explicit masks, distinct from known zero capacity. PPO stores the causal snapshot for strict reconstruction; future execution windows never enter observations.

Prepare sparse sidecars with `PYTHONDONTWRITEBYTECODE=1` and `python -B -m research.rl_trading.v6.prepare_execution_features --dataset <audited-complete.json> --episode-root <episode-label-root> --early-manifest <early-latest.json> --late-manifest <late-latest.json> --ledger <source-ledger> --luld-root <modeled-LULD-root> --output <fresh-runtime-root> --days <unique-train/dev-days> --fit-normalization`. All paths must be under the runtime root. `--max-candidates` explicitly creates a bounded diagnostic that training rejects. Reads are pinned SELECT-only; no candle banks or raw quote shards are generated. Teacher use additionally requires `--execution-feature-root <all18-sidecar-root>` and independent episode supervision; execution-aware PPO requires `--broker-engine tensor-100ms`. Missing sidecars fail before training. Old feature/checkpoint contracts or different normalization hashes cannot silently resume.

`netbps` is undiscounted matched round-trip estimated net profit / proposed $1,000 capital * 10,000, using next-second participation/VWAP plus observed half-spread and canonical buy/sell fees. Unfilled capital contributes zero; missing costs stay null. It uses hindsight only for labels. Scores >=100 bps enter the existing rolling 15-second normalized allocation only for completely known rolling cohorts. Unknown cohort costs mask the size-regression targets instead of fabricating zero shares. Opportunity classification targets survive even if allocation is zero. The original score and its original 30-second discount remain unchanged in `old_score`/`old_score_bps`. The explorer overlays old score and netbps on one bps axis, then compares allocation fractions; these formulas differ beyond their display units. Neither teacher loss nor this score proves trading profitability.

### Certified sparse-bucket semantics

The producer emits only event-containing buckets. Its `volume_valid` flag means an eligible trade occurred, not complete interval coverage. Execution-aware consumers recheck each selected completed broker unit's persisted row count, unique keys and full-row hash against the producer ledger once per source identity, in bounded batched SELECTs. Only absence inside that verified 04:00�20:00 session is known no-event/zero-volume evidence. Incomplete or changed source units fail closed.

Cost aggregation keeps trade presence separate from this coverage. A vectorized backward as-of join retains fresh causal quotes over quiet boundaries; quotes older than one second remain unusable. Future score prices aggregate per-bucket execution notional and half-spread, avoiding a stale boundary quote. A verified zero matched capacity has zero netbps without inventing a quote or fill. Unknown coverage still masks allocations. The tensor replay uses the same certificate and a vectorized ten-bucket quote reduction; its ordinary fill eligibility gates and financial arithmetic are unchanged. Broker shard namespace v2 and an explicit execution-evidence version reject incompatible cached/runtime contracts. No dense empty-bucket shards or database writes are introduced.


### Independent four-action ticker heads (opt-in)

`--ticker-heads --action-contract wait-hold` replaces the local teacher decision with raw ENTRY/WAIT/HOLD/EXIT logits. Flat positions permit ENTRY/WAIT; held positions permit HOLD/EXIT. Soft cross-entropy uses log-softmax directly; no classification output activation precedes loss. Value is a separate signed net-bps opportunity prediction, never the PPO portfolio critic. WAIT and unavailable targets mask value loss. Entry-only bracket distances have independent masks. Existing geometry is supervised only at its certified entry time; it is not transplanted across an expanded entry window. Teacher sizing targets are removed. The exact candidate-return and remaining-to-exit value definitions and available counts are recorded in `ticker_target_certificates`.

For compatibility with shared broker identity decoding, permanently masked STOP/TARGET transport slots remain in the serialized proposal axis. They are never classifier labels or sampled actions. The four-action contract version rejects incompatible parent checkpoints. Per-ticker teacher metrics do not measure market-wide ticker selection. During PPO, local action margins enter a one-proposal-per-second market race; portfolio sizing and the separate critic are learned from rollouts. Bracket predictions are attached to accepted entry intents and armed after actual fills, never against earlier extrema in that fill bucket. `--outside-macd-per-minute` optionally applies separately reported exposure-weighted shaping after completed MACD indicates a nonpositive long regime; zero preserves legacy behavior.

Execution feature preparation now keeps only a 60-second sparse source block resident. `prepare_execution_features --resume` verifies completed day hashes and source bindings before reuse; incomplete days still fail closed. Neither candle banks nor episode action labels are regenerated by that preparation. Long teacher/PPO runs remain gated on real target audits, end-to-end learning and representative replay performance.
