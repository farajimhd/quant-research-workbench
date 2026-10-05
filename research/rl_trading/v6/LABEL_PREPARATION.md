# Current V6 label authority

Current training requires `rl-v6-swing-opportunity-dataset-v9`, using the
`price-action-long-opportunities-v7` algorithm with horizon audit attributes. Old portfolio
teachers, rolling-15 episode windows, fee-adjusted candidate targets, bracket
sidecars and checkpoints from their datasets are rejected by current loaders.
Historical files remain audit evidence, not current supervision.

## Target and feature timing (mandatory)

`label_timing.CONTRACT` is the versioned training alignment authority. A label
belongs to its candle **close_us**, never its open (open = close_us - 1 second).
For a target at close time **t**, all market features, OHLC, indicators, levels,
ranking and held-position mark prices must come from candles with
**close_us < t**. Exclude the target candle entirely. The trainer evaluates
all targets at t before advancing that candle's encoder/ranking state; the
held teacher account uses the previous observed valid price, without padding.
The clock and elapsed holding age are known coordinates, not current-price inputs.

These are hindsight targets: future prices determine their values offline.
Timestamp alignment does not assert that hindsight labels are available live
at t. Do not place qualities, gains, future pair geometry or selected future
exit prices in observation tensors. Raw labels are unchanged by this feature
alignment. New training manifests bind the timing version, preventing resume
or initialization from checkpoints with the old inclusive feature contract.

## App audit

ENTRY stores `entry_target_us` and `entry_horizon_seconds`: the strictly future
long-episode close attaining its existing maximum positive discounted gain.
HOLD stores `hold_target_us`, `hold_horizon_seconds`, and `hold_target_gain`:
the strictly future long-episode close maximizing discounted continuation, with raw gain measured from the
reference entry. Existing raw EXIT/HOLD gain remains the current close minus
reference entry; the future HOLD target is separate, not a reinterpretation.
Both searches stay within the same short-to-long pair. Equal maxima choose
the earliest close. Horizons are elapsed close-to-close seconds, including
gaps, and absent future targets are null. EXIT's selected decision horizon is
zero. The teacher adapter retains these audit attributes without adding a
prediction head, changing its losses, or exposing hindsight in input features.
The repaired NVDA RTH preview is experimental; full-session certified labels
remain exclusively behind the active registry after publication audit.

Research → **Current V6 labels** reads `rl-v6-active-labels.json` and its
published dataset/audit certificates from the workstation runtime. Select
any of the 19 saved sessions and a ticker/listing. The three existing Canvas
containers show algorithm/timing, session/pair statistics, and saved candles
with 1s MACD shading. Arrow rows show quality and raw dollars per share.
The default chart shows both ENTRY and EXIT opportunities, with EXIT taking
priority on a candle qualifying for both. ENTRY/WAIT and EXIT/HOLD remain separate conditional teacher branches at the
fixed saved 90% threshold; held candles without an exit target have no marker.
The reference view is a chronological comparison, not an extra teacher target.

`/api/research/models/v6/saved-labels` exposes the catalog; `/listings`,
`/metadata` and `/chart` select published identities only. Shard receipts,
file hashes and counts verify before selected parquet rows are displayed.
Content-addressed copies live under `D:/TradingML/runtimes/rl-v6-app-label-cache`;
there is no old-label fallback or recomputation. The deployment mapping defaults
to `\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes` and can be explicitly
configured with `RL_V6_LABEL_AUDIT_RUNTIME`. Historical audit and the single
NVDA experiment retain their separate paths. No new teacher or PPO run is launched.

## Episode liquidity admission (dataset V9)

The S-to-L opportunity pair is admitted only at its original start boundary,
using exact pinned repaired ARTE activity in `[start-60 seconds, start)`:
at least 20 trades, 2,000 shares, 10 distinct active seconds, and a prior trade
no older than five seconds. Freshness and internal gaps use conservative
upper bounds for the one-second trade buckets; exact tick times are not claimed. The target candle is excluded. Rising liquidity
inside an existing move cannot reopen it. Any trade inactivity interval longer
than five seconds through the pair invalidates its hindsight ENTRY/EXIT
supervision; every price candle remains present as WAIT, with explicit reasons.
This later invalidation is a hindsight audit attribute, never a causal feature.
Original MACD sign runs are retained as diagnostic geometry, not tradable episodes.

Generation additionally requires `--bar-manifest` and `--bar-ledger` pointing
to the certified source used by the unchanged banks. Exact bar output hashes,
attempt identities and all activity clocks are verified; compressed feature
volume/counts are not used for threshold decisions. New outputs must use a fresh
runtime directory. Prior bars, banks and label publications remain immutable.
Research shows accepted/rejected pair counts, rejection reasons, and per-candle
prior trade count, share volume, active seconds and trade age.

## Reporting-certified source requirement

V3 label datasets require rebuilt feature banks bound to verified ingestion
reporting coverage. The bar builder requires completed, source-matching
`q_live.historical_trade_reporting_coverage_v1` records and idle canonical
mutations; it records those receipts in its immutable build definition.
V6 preparation rejects legacy builds without those receipts. Old V2 label
datasets and their checkpoints cannot be used by the V3 training loaders.
Unknown-clock trades remain a separately counted classification under the
existing reporting policy; completion does not mean every clock is known.
Bar corrections require indicator recomputation in session order because
EMA/MACD seeds carry between sessions. Label timing remains strictly causal:
features at target close t exclude that target candle.

Generate every saved forward-bank listing on the workstation:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B research/rl_trading/v6/run_prepare_labels.py --source-manifest D:/TradingML/runtimes/rl-v6-reporting-repair-20261002/banks/day-roots.json --output D:/TradingML/runtimes/rl-v6-reporting-repair-20261002/labels --workers 8 --listings-per-shard 32
```

The same command and exact producer source resume verified completed shards.

The complete workstation repair is launched with
`python -B research/rl_trading/v6/run_reporting_repair.py --source-commit FULL_PUSHED_SHA`.
It awaits an existing July migration, verifies all 56 certified July–September
source sessions, runs an NVDA/AAPL bar canary and independent audit, rebuilds
the existing July 30–September 18 market-day range immutably, audits the full
population's stage counts and representative OHLC/MACD, then regenerates the
19 V6 banks and labels. It never trains. `progress.json` and per-stage logs
live beneath `D:/TradingML/runtimes/rl-v6-reporting-repair-20261002`; failures
stop downstream stages, and rerunning resumes the pinned build/shards.
Archive snapshots must also pass `--source-commit` with their full pushed SHA.
`progress.json` reports
day, active, queued, completed and failed units. All 19 saved days (context,
16 train, two development) must complete before `dataset.json` and
`D:/TradingML/runtimes/rl-v6-active-labels.json` are published. August 26 is
not read. Immutable bank bytes are verified before worker dispatch. Every
valid observed OHLC/MACD candle in the full saved day is labelled; invalid
price rows and all-invalid listings are counted. There is no old candidate
filter, filling, fee adjustment or synthetic padding.

Labels retain raw `entry_gain`, `exit_gain`, qualities, reference actions,
conditional alternatives and separate next-pair carry. Raw gains are dollars
per share. Teacher value heads keep their existing bps interface through
explicit `entry_gain/current_close*10000` and
`exit_gain/reference_entry*10000` conversion, never quality-as-value.
Stop/target references are not silently attached as old bracket targets.
Classification keeps quality/complement soft targets; hard actions use 90%.
Teacher training requires ticker heads and WAIT/HOLD transport. Preparation
does not train a teacher/PPO model. Old normalization and checkpoint dataset
bindings remain invalid until explicitly rebuilt.

Final publication additionally requires `publication-audit.json`: a separate
full source census, clock/scalar byte verification, receipt audit, exact
recomputation of representative liquid/sparse listings on every day, and
bounded real training-adapter checks. Existing rank settings (top 1000,
one-second refresh) are retained. The held observation is hypothetical;
reference quantities may be fractional for prices exceeding the $10,000
bookkeeping balance. Quantities never scale raw per-share value targets or
claim a fill.

Atomic JSON replacement retries transient Windows reader locks for at most
two seconds. Explicit `--reuse-receipts-from-source <immutable snapshot>`
allows controller-only recovery when every shard is already complete, the
numeric algorithm files match byte-for-byte, and both worker function ASTs
are unchanged. All receipt bytes/counts still verify. Missing receipts or
changed numerical producers fail closed; no mixed-producer generation occurs.

The legacy candidate/fee-allocation execution-cost compiler and its old
sidecars are also rejected. They cannot be mixed into the new price-only
labels. A future causal execution-observation preparation must be versioned
and bound to the new label certificate; it must not revive old label targets.

## Research model-bank candle audit

Saved Research charts accept `candle_offset` for an observed-candle window:
up to 120 valid-price historical context candles, followed by the next 120
valid-price session candles (all remaining when fewer exist). Context has no
current-session teacher markers. This 240-candle audit view is not one model
input. The main V6 teacher uses up to 120 actual bank rows strictly before
each target; unpriced rows retain masks and may not be drawn as candles.
The inspector exposes those input clocks and padding. The existing local
ResNet diagnostic instead includes the target in its 120-row window; the
chart reports that difference without changing either training path.

Prices, bar/session VWAP, one-second MACD and five V7 slots per side are decoded
from the published float32 feature bank, including historical bank values.
No indicator or level engine is rerun. Stored scalar/tensor fields remain
available in the inspector; bps conversion and train-only normalization are
separate encoder operations. Ticker names come from the saved compiler
receipts even for zero-episode/context-only listings. Empty-price membership
is certified by the label shard receipts and is labelled explicitly in the
dropdown. No bars, banks, labels or model checkpoints are rewritten.

## Time-aware single EXIT cluster
Current positive liquidation gain is compared with maximum positive future gain from the same reference entry, discounted to the current close with the configured half-life. Earliest candle whose current gain dominates continuation is the reference EXIT. Only its first contiguous run of dominant candles has in_exit_cluster=true. Later candidate clusters are suppressed to HOLD in hypothetical held supervision; the reference trade already ended. Raw exit_gain remains undiscounted. liquidation_quality retains the current/maximum(current,continuation) comparison; exit_quality is gated to zero outside the selected cluster. HOLD targets witness discounted continuation; hold_target_gain stays raw, hold_discounted_gain is discounted. No profitable reference means no forced exit target or trade. This changes teacher targets, but not feature timing or loss definitions.

Minimum position duration defaults to 5 seconds, measured between actual candle closes (not row count). ENTRY searches only L targets at least 5 seconds later; absent profitable eligible targets become WAIT. Reference EXIT cannot precede reference ENTRY plus 5 seconds. Entries within 5 seconds of the selected EXIT cluster start are also removed, avoiding adjacent entry/exit arrows. Hypothetical held rows before eligibility stay HOLD. Config/version bindings reject previous duration-free artifacts.

Combined chart HOLD markers require in_reference_hold: the retained reference position must actually be active. Outside it, use WAIT unless an ENTRY/selected EXIT opportunity applies. Explicit held view still shows conditional teacher HOLD targets, including hypothetical held states after reference exit. This presentation change does not alter teacher gains, soft probabilities or losses.

Universe policy rl-v6-us-exchange-listed-stocks-v1 independently verifies canonical stock product, USD, US exchange country and shared OTC exclusion across exchange aliases. Historical membership remains pinned to certified pre-open snapshots; current active status or prices are never used for scope. Static canonical metadata evidence is retained in the bank plan, hash-bound, with every rejected identity and reason. Unknown country/product/currency metadata fails closed. Missing MIC alone does not exclude valid US exchange aliases. IBKR overnight venues are explicitly excluded as nonstandard. Label generation/load rejects banks without a valid scope receipt. Old banks remain immutable audit artifacts. Rebuild outputs use banks-us-listed-v1 and labels-us-listed-v1; sealed-test labels remain excluded.

## Isolated supervised 1b preview

Research → **1b selection & sizing** exposes a provisional session preview,
without publishing training data or changing approved 1a labels. Prepare one
approved session with frozen settings, then inspect selected/rejected pairs,
group members, cash ratios and exact original/copied candle targets.

For fee `f` per share per side, score is `(entry_gain - 2*f)/(close + f)`.
`entry_gain` already discounts actual elapsed time. The adjustable fee rule
uses `score > m*2*f/(close + f)`; `m=2, f=0.005` means `entry_gain > 0.03`.
The comparison return rule uses `score >= threshold`, default `0.01` (1%).
Neither default is approved for full extraction yet. Each listing/S→L pair
contributes its first qualifying **existing 1a ENTRY**, once, rather than all
qualifying candle rows. Rejected pairs retain their best ENTRY candidate for
the decision audit; liquidity rejection and absence of 1a ENTRY are explicit.

Chronologically ordered candidates are partitioned by exact dynamic programming
to minimize `sum_g sum_i score_i*(time_i-weighted_mean_g)^2 + penalty*group_count`.
Times are elapsed seconds; `penalty=median(selected_scores)*grouping_seconds^2`.
Equal timestamps remain atomic. An explicit adjustable maximum group span
bounds the search. Defaults are 30-second strength and 300-second maximum span.
This objective is score-weighted temporal clustering, not a session-P&L
optimizer, and both grouping parameters remain subject to user validation.

Each selected pair receives `allocation_ratio=score/group_score_sum`, summing
to one per group. It is a target fraction of available cash, not a share count.
The UI's $10K equivalent is illustrative. Selected pairs preserve 1a actions;
rejected pair actions ENTRY/HOLD/EXIT become WAIT in the copied preview with
zero sizing target. Context remains context. Only selected ENTRY rows enable
sizing loss; HOLD/EXIT rows mask that loss. Raw gains and other 1a audit fields
are retained for comparison. No PPO cash accounting or training is invoked.

Compact source caches and hash-bound decision receipts live exclusively under
`D:/TradingML/runtimes/rl-v6-market-teacher-preview`. The chart reads the same
saved bank indicators as 1a and applies the copy/suppression view on demand.
No full copied candle dataset is extracted until validation authorizes it.
