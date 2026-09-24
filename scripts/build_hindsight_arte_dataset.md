# Hindsight dataset from persisted arte products

Use `build_hindsight_arte_dataset.py` for the new Phase 1 → Phase 2 workflow.
All market observations come from `arte.bars_v1` and `arte.indicators_v1`.
Phase 1 defines labels exclusively from price action. It never reads quotes,
liquidity tables, canonical events or QMD History.
Population identity comes from the source build's pinned V2 snapshot; completed
stage certificates come from its runtime SQLite ledger.

From the synchronized workstation checkout and its `ml4t` environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/build_hindsight_arte_dataset.py preflight --date 2026-08-21
python -B scripts/build_hindsight_arte_dataset.py benchmark --date 2026-08-21 --tickers AAPL SUGP --workers 2
python -B scripts/build_hindsight_arte_dataset.py run --start 2026-08-18 --end 2026-09-18 --workers 4
```

By default the script opens local `D:/TradingML/runtimes/market-day/latest.json`
and `D:/TradingML/runtimes/build-ledger-v2.sqlite3` on the workstation. A configured
`QW_RUNTIME_ROOT` is honored. To pin a retained build, pass
`--manifest D:/TradingML/runtimes/market-day/<build-id>.json`.
Use `--ledger` if its certificate ledger is in a different location. Missing
authority fails; no alternate build, source, environment or root is selected.
The script needs read access to the existing configured ClickHouse connection.
It does not start services or alter source tables.

## Meaning of the new version

`hindsight-phase1-arte-price-action-v3` uses price-action labels and forced
liquidation at **19:58 ET**, 120 seconds before the extended session's 20:00 close.
Prior datasets remain immutable and retain their versions.

- MACD uses completed, sparse 1-second indicator rows with the persisted build's
  seed history. Missing seconds do not create synthetic indicator updates.
- Long entries select the lowest 100 ms bar low in the inclusive MACD-start
  lookback window; short entries select the highest high. Exits select the
  highest high / lowest low within `[MACD start, MACD end)`, strictly after the
  selected entry. Equal extrema select the earliest completed bar.
- The target timestamp is the **100 ms bar close**, not the unknown intrabar
  extremum time. A high and low in the same bar cannot form a round trip.
- The decision reference is the latest completed eligible 100 ms trade close.
  Before the first price it is unavailable. Sparse periods carry the last
  observed close; `price_us` and `price_age_seconds` expose its age without an
  arbitrary freshness gate. Future bars cannot supply the current reference.
- Long gross labels equal the future swing high minus the decision price;
  short gross labels equal the decision price minus the future swing low.
  Targets use their selected high/low, not the close of the target bar.
  No bid/ask, spread, quote-age, depth, fillability or transaction-cost condition
  changes Phase 1 labels. Negative values are retained.
- MACD intervals and swing selection stop at 19:58. When no qualifying target
  remains before then, either direction receives a terminal target at 19:58,
  valued at the latest eligible completed trade close at or before that time.
  It is retained even if it loses money. The target kind is `session_liquidation`
  with reserved target ID 0; its label-availability timestamp is the cutoff.
  No bar or indicator after the cutoff can affect labels or the liquidation price.
- Current prices and terminal prices can still be unavailable if no eligible
  trade has been observed. No price is invented. Sparse terminal prices carry
  their observation timestamp (`liquidation_price_us`) and age; this timestamp,
  like all future target information, is label-side data.
- Activity uses completed 1-second bar volume/counts. Missing seconds contribute
  zero activity, ten-second volume is a rolling sum, and session volume starts
  at 04:00 ET. Different timeframe volumes are never added together.
- Delayed trades and unknown-condition eligibility are handled by the certified
  upstream builder. Do **not** drop buckets with `reporting_delayed_trades > 0`:
  valid trades in those buckets already contribute clean OHLC and volume.
- Positive-swing target selection, next-target selection and Phase 2's fractional
  local objective are retained. This change does not redesign RL rewards or
  claim to produce account trajectories. Future target fields remain labels.
- Phase 2 explicitly records `valuation_basis=price_action` for this version.
  Its coefficients use the same decision/target prices, without inventing
  bid/ask fields. Optional Phase 2 costs remain explicit. These are price-action
  values, not executable returns; `can_open`/`can_close` mean that a current
  reference price exists under the comparison model, not that an order can fill.
  Legacy Phase 1 versions still use their original quote-based Phase 2 contract.
- At and after 19:58, `session_terminal=true` and `can_open=false`. The portfolio
  action evaluator rejects holding, partial liquidation or opening a new position;
  existing holdings must be completely liquidated. The flat-state table selects
  wait because it has no holdings. The grid retains all 57,601 seconds through
  20:00, with a terminal flag on its final 121 rows. Post-cutoff rows do not
  represent additional trading opportunities. This is a labeling/evaluation
  rule; the builder does not place real broker orders.
  Mandatory full liquidation preserves negative cash and reports insolvency and
  the cash deficit when losses exceed the synthetic reserve.

Phase 2 defaults to a discount half-life of **30 MACD bars**, configurable with
`--half-life-bars`. This reader currently uses 1-second MACD, so the default is
30 seconds: a profit at 30 seconds receives 50% weight, at 60 seconds 25%.
The compiler scales the half-life using the source plan's MACD resolution;
it does not infer a horizon from future episode lengths. `--gamma` is a mutually
exclusive per-second override. The saved plan records the resolved policy.
Opening and holding value availability are calculated independently.

## Certification and population

The manifest and ledger must agree on a completed `market-day-core-v5` build.
Each read pins `build_id`, date, ticker and the exact certified **stage attempt**.
The table policy and active part locations must be `live_market_ssd`. Each
listing's counts, unique keys and output hashes are rechecked before extraction
and before publication; resumed outputs also undergo source and file checks.

V1 only named a universe date. V2 additionally records capture/availability times
and a content certificate proving that membership was available before 04:00 ET.
The reader verifies the pinned snapshot rather than consulting today's list.
An earlier snapshot explicitly admitted by the source build remains labeled
`carried_forward`, not reclassified as exact.

Default scope is the **source build population**, not every exchange listing.
The market-day builder excludes tradable tickers without certified events; its
population/exclusion counts are retained in each Phase 1 plan. A source build
that itself selected a subset remains that subset. Explicit `--tickers` is a
canary and cannot establish full-market coverage. Ambiguous listing identities
fail instead of being collapsed arbitrarily.

## Efficiency, output and restart

Dates run sequentially with a bounded shared listing pool. Queries push down
build/date/ticker/attempt/resolution filters. Only 100 ms price bars and 1-second
activity/MACD cross the network. Polars assigns exit bars
with an ASOF join and selects extrema in columnar operations; entry range joins
are bounded by the 0–30 second lookback. It does not expand all interval/bar pairs
or all fractional portfolio actions. Integrity scans are additional work and are
included in measured time; no claim of globally optimal throughput is made.

Outputs live under `runtime/hindsight-arte/<configuration-hash>/`. Each session
has a Phase 1 directory compatible with the shared Phase 2 compiler. Phase 2
outputs remain under `runtime/hindsight-greedy/`; their paths and market-wide
available/unavailable counts are recorded in the campaign `summary.json`.
Phase 1 summaries retain terminal and missing-price counts per ticker/side.
Every decision grid includes the terminal 20:00 row. Where a current price
exists, terminal targets close the former end-of-session target gaps. Missing
prices remain null; no eligible candidate is silently ignored.

Plans and successful listing outputs are immutable. Source/configuration/code
changes produce new dataset identities. Rerun an identical command to reuse
verified listings. Failed listings are retained in summaries and retried only
on an explicit rerun. No automatic retries occur.

Ctrl+C or a campaign `STOP` file stops admission and drains active work. Phase 2
receives its own STOP marker when active. Remove any campaign/Phase 1/Phase 2
STOP markers shown by the saved output paths before resuming. A campaign
completion marker is published only after every requested session completes.
Exit 2 means incomplete, interrupted or failed work.

## Validation

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m pytest tests/test_hindsight_arte.py tests/test_hindsight_phase1.py tests/test_hindsight_greedy.py tests/test_hindsight_batch.py -q -p no:cacheprovider --basetemp=D:/TradingML/runtimes/hindsight-arte-tests
```

Tests cover independent bar-target arithmetic, exact close boundaries, sparse
MACD, neutral intervals, completed-price timing, volume, certification failure, and the runnable
Phase 1 → Phase 2/resume/STOP path. A real canary measures data coverage and
runtime; it does not prove full-universe usable-label coverage or RL performance.

The earlier quote-valued V1 validation used the completed workstation build
`1521ba7702a9ee0783916f706f4885a24a3f32a91630b04ff738a90e65bc9dd5`, with AAPL and
SUGP on August 21. Both phases passed in 12.5 seconds including 8.4 seconds of
source preflight (warm infrastructure; no old/new throughput comparison).
Combined-mode labels were available for 26,568 of 37,270 seconds with eligible
candidates; 10,702 seconds had unavailable candidate values. The remaining
20,331 seconds had no eligible candidates and a known wait label. This confirms
that persisted source completeness does not eliminate future-label gaps. Those
V1 coverage figures do not describe the corrected price-action V2 dataset.

The corrected V2 canary on the same build/date/tickers passed both phases in
11.9 seconds including preflight. Its swing targets matched V1 exactly, while
the output schema contained no quote fields. Combined-mode labels were available
for 56,615 of 57,600 seconds with an observed current price; the remaining 985
seconds had no future qualifying target. Each ticker also had one initial second
without a completed trade price. Fifty focused tests passed, including the
price-only Phase 1 → Phase 2 handoff and resume/STOP behavior.

The V3 19:58-liquidation canary on the same build/date/tickers passed both phases
in 12.1 seconds including preflight, with zero unavailable market-policy rows.
Each listing had 57,479 available pre-cutoff price-action rows, one initial row
before its first observed price, and 121 terminal rows. Forced liquidation used
AAPL 309.5259 and SUGP 1.55 from completed bars available by 19:58. Real coefficient
artifacts rejected holding and accepted complete liquidation at the cutoff.
Fifty-two focused tests passed, including negative terminal outcomes, sparse
terminal prices, no post-cutoff influence and daylight-saving time behavior.

The Phase 2 V2 canary with the 30-bar half-life passed on the same source/date/
tickers in 11.5 seconds including preflight. All three modes had 57,601 available
market-policy rows and zero unavailable rows, including known wait rows.
Sixty-four focused tests passed, including insolvent mandatory liquidation,
independent hold eligibility, negative after-cost targets and resolution scaling.
Account trajectories and portfolio funding redesign remain Phase 3 work.
