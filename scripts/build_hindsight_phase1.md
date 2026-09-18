# Phase 1: one trading session

Run from the repository with its Python environment:

```powershell
python -B scripts/build_hindsight_phase1.py --date 2026-08-21
```

This processes **all listings marked is_tradable=1 in the requested date's published q_live.feature_tradable_universe_v1 snapshot**. It never substitutes the latest snapshot. The default is two listing workers, each with two ClickHouse query threads; the shared QMD MACD reader can use four hourly requests per active listing. There is no portfolio optimization or account cash parameter.

Use `--plan-only` to freeze and inspect the dated universe without calculating opportunities. Use `--tickers SUGP AAPL` for an explicitly marked canary; it must be a subset of the same universe and cannot publish a full-market completion. `--workers 1` reduces concurrent load. `--lookback-seconds` is an integer from 0 through 30, default 2, matching the base MACD hindsight swing lookback.

## Inputs and calculation

- QMD History supplies certified completed 1-second MACD intervals through the same aggregate-backed reader as the base hindsight indicator. Existing aggregate/indicator caches are reused; incomplete coverage must be repaired by that authority or the listing fails. This script does not trust arbitrary latest rows in an indicator cache.
- Read-only ClickHouse queries use the certified ticker/day ordinal range in `market_sip_compact.events_YYYY`. No flatfiles are read and no ClickHouse tables are written.
- Eligible trade extrema are compressed on the server into exact min/max timestamp-price pairs per second, with exact whole-second boundary trades grouped separately. This preserves the base hindsight extrema and earliest-tie rules for integer lookbacks without transferring the full tape.
- Trade eligibility matches the base canonical SIP reader: current last-price condition rules, Form T extended-hours handling, and the 04:05 derived-state cutoff. No recovered execution-clock sidecar is added to this SIP labeling contract.
- A server-side ASOF join samples the latest NBBO at each decision second and exact target timestamp, including invalid quote updates. Freshness/depth validity is stored; missing quotes are never replaced with later targets.
- Polars constructs the dense 04:00 through 20:00 New York decision grid and raw, independent, one-share long/short values. Target timestamps use integer microseconds in Parquet. No 90-second cap, fees, spread cap, volume gate, sizing or capital selection is applied.

## Persistent output

The machine's configured runtime root contains:

`hindsight-phase1/<date>/<all-or-canary>/<run-name>/`

- `plan.json`: immutable dated listing population, scope, condition rules, code hashes, QMD fingerprint, library version and parameters.
- `listings/<identity-hash>/opportunities.parquet`: one row per decision second (57,601 per listing), ticker/listing identity, bid/ask/depth/freshness, trade count and eligible volume, rolling 10-second activity, spread, per-direction target IDs/timestamps/quote values, gross P&L, hold time, availability time and explicit missing-data status.
- `targets.json`: exact base MACD targets and rejection counts, including moves below the chart's visual display filter.
- `source.json`, `macd.json.gz`, `extrema.json.gz`, `quotes.json.gz`: pinned provenance and reusable calculation inputs. Intermediate inputs carry SHA-256 receipts.
- `ready.json`: per-listing atomic completion and file hashes. Only published after source revalidation and complete Parquet writing.
- `progress.json` and `summary.json`: completed/reused/active/failed counts and individual failure reasons.
- `complete.json`: published only when every selected listing succeeded or was verified reusable. Phase 2 must validate its plan hash and each listing's ready/file hashes, and inspect whether scope is full-market or canary.

These are intermediate ticker opportunities, not final market-wide policies. `volume` and `session_eligible_volume` count price-eligible trades, matching the action sampler; they are not unrestricted SIP reported volume. Short availability/borrow is not established. A listing without canonical source certification is an explicit failure, not assumed to have no trades.

## Resume and stop

Run the same command again. Complete listing files are hash-checked and reused; failed listings retry and valid intermediate inputs are reused. Changed inputs, source metadata, parameters, runtime/library fingerprint or code fail closed. Use a new `--run-name` for a new version; never edit the frozen plan.

Press Ctrl+C once, or create a file named `STOP` in the run directory. The script finishes active listings and stops scheduling new ones. Remove `STOP` before resuming. A process-level OS lock prevents two writers for the same run; interrupted partial output is never a completed listing. Exit status is 0 only for a successful plan-only operation or a complete selected dataset; failures/interruption return 2.

No complete all-market run is implied by the canary tests. Missing dated population, uncertified canonical days, unavailable MACD coverage, source drift and corrupt checkpoints remain explicit failures.
