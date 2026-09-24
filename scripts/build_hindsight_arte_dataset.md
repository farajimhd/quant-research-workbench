# Hindsight dataset from persisted arte products

Use `build_hindsight_arte_dataset.py` for the new Phase 1 → Phase 2 workflow.
All market observations come from `arte.bars_v1`, `arte.indicators_v1` and
`arte.liquidity_100ms_v1`. It never reads canonical events or calls QMD History.
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

`hindsight-phase1-arte-100ms-v1` is explicitly different from event-exact labels:

- MACD uses completed, sparse 1-second indicator rows with the persisted build's
  seed history. Missing seconds do not create synthetic indicator updates.
- Long entries select the lowest 100 ms bar low in the inclusive MACD-start
  lookback window; short entries select the highest high. Exits select the
  highest high / lowest low within `[MACD start, MACD end)`, strictly after the
  selected entry. Equal extrema select the earliest completed bar.
- The target timestamp is the **100 ms bar close**, not the unknown intrabar
  extremum time. A high and low in the same bar cannot form a round trip.
- Quotes use the latest **completed** liquidity bucket at or before the decision
  or target. A bucket is never exposed at its last event time before it closes.
  Persisted valid-quote carry behavior is retained. Both sides need finite,
  positive depth and valid prices; the source quote must be at most one second
  old at the actual sampled time.
- Activity uses completed 1-second bar volume/counts. Missing seconds contribute
  zero activity, ten-second volume is a rolling sum, and session volume starts
  at 04:00 ET. Different timeframe volumes are never added together.
- Delayed trades and unknown-condition eligibility are handled by the certified
  upstream builder. Do **not** drop buckets with `reporting_delayed_trades > 0`:
  valid trades in those buckets already contribute clean OHLC and volume.
- Positive-swing target selection, next-target selection and Phase 2's fractional
  local objective are retained. This change does not redesign RL rewards or
  claim to produce account trajectories. Future target fields remain labels.

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
build/date/ticker/attempt/resolution filters. Only 100 ms extrema, 1-second
activity/MACD, and sampled quotes cross the network. Polars assigns exit bars
with an ASOF join and selects extrema in columnar operations; entry range joins
are bounded by the 0–30 second lookback. It does not expand all interval/bar pairs
or all fractional portfolio actions. Integrity scans are additional work and are
included in measured time; no claim of globally optimal throughput is made.

Outputs live under `runtime/hindsight-arte/<configuration-hash>/`. Each session
has a Phase 1 directory compatible with the shared Phase 2 compiler. Phase 2
outputs remain under `runtime/hindsight-greedy/`; their paths and market-wide
available/unavailable counts are recorded in the campaign `summary.json`.
Phase 1 summaries retain missing-target and invalid-quote counts per ticker/side.
Every decision grid includes the terminal 20:00 row, which may have no future
target. Missing labels remain null; no eligible candidate is silently ignored.

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
MACD, neutral intervals, volume/freshness, certification failure, and the runnable
Phase 1 → Phase 2/resume/STOP path. A real canary measures data coverage and
runtime; it does not prove full-universe usable-label coverage or RL performance.

The September 24 validation used the completed workstation build
`1521ba7702a9ee0783916f706f4885a24a3f32a91630b04ff738a90e65bc9dd5`, with AAPL and
SUGP on August 21. Both phases passed in 12.5 seconds including 8.4 seconds of
source preflight (warm infrastructure; no old/new throughput comparison).
Combined-mode labels were available for 26,568 of 37,270 seconds with eligible
candidates; 10,702 seconds had unavailable candidate values. The remaining
20,331 seconds had no eligible candidates and a known wait label. This confirms
that persisted source completeness does not eliminate future-label gaps.
