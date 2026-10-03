# Current V6 label authority

Current training requires `rl-v6-swing-opportunity-dataset-v6`, using the
`price-action-long-opportunities-v5` algorithm with horizon audit attributes. Old portfolio
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

## Time-aware single EXIT cluster
Current positive liquidation gain is compared with maximum positive future gain from the same reference entry, discounted to the current close with the configured half-life. Earliest candle whose current gain dominates continuation is the reference EXIT. Only its first contiguous run of dominant candles has in_exit_cluster=true. Later candidate clusters are suppressed to HOLD in hypothetical held supervision; the reference trade already ended. Raw exit_gain remains undiscounted. liquidation_quality retains the current/maximum(current,continuation) comparison; exit_quality is gated to zero outside the selected cluster. HOLD targets witness discounted continuation; hold_target_gain stays raw, hold_discounted_gain is discounted. No profitable reference means no forced exit target or trade. This changes teacher targets, but not feature timing or loss definitions.

Minimum position duration defaults to 5 seconds, measured between actual candle closes (not row count). ENTRY searches only L targets at least 5 seconds later; absent profitable eligible targets become WAIT. Reference EXIT cannot precede reference ENTRY plus 5 seconds. Entries within 5 seconds of the selected EXIT cluster start are also removed, avoiding adjacent entry/exit arrows. Hypothetical held rows before eligibility stay HOLD. Config/version bindings reject previous duration-free artifacts.
