# Phase-one hourly/daily supplement v1

This is an independent append-only component, not a replacement for the corrected
phase-one cohort. It does not edit or activate a training split, calibration,
checkpoint, W&B run, parent publication or phase-two campaign. The existing v3
trainer continues reading its frozen original cohort and split until a separately
authorized transition.

## Selection and contract

- Read only `market_sip_compact.events_2019` through `events_2025` and the exact
  frozen source-day/ordinal plan inherited from the corrected base. No flatfiles,
  new tickers, synthetic prices, alternative daily definitions or shifted buckets.
- Use the shared `range_sql`, `bar_semantics_v1`, final quote quarantine v3,
  v6 `[64,18]` Float32 market values/masks, UTC clocks and existing corporate
  action adjustment. Daily bars are 09:30–16:00 New York; hourly bars retain the
  original extended-session semantics. Nine UTC features remain loader-derived.
- Preserve the active split's exact cutoff (`2024-04-16`, UTC microseconds from
  its sealed manifest). A training window ends strictly before cutoff; validation
  requires first close minus nominal timeframe >= cutoff. Purge crossing windows.
  Do not use the older packing helper's hardcoded October-2025 cutoff.
- Per split, target each coarse frame at the largest of the four existing fine
  frame counts. Counts are windows, not unique bars. Current additional targets:
  training hourly 14,073,005 / daily 36,177,639; validation hourly 1,156,708 /
  daily 9,685,896. Availability may be much smaller.
- Verify/hash parent coarse payloads from BOTH corrected members. Anti-join exact
  ticker/frame windows; verify all 64 ordered clocks against the canonical bar
  sequence before trusting endpoint identity. Parent context overlap is allowed.
- Prefer windows containing entirely parent-uncovered bars. Within each priority
  tier, first choose nonoverlapping windows; then add novel windows at >=32
  observed-bar spacing. Thus NEW windows share at most 32/64 bars with one another.
  This is observed-bar overlap, not a fixed calendar-time gap. No exact duplicate
  window is republished. Train/validation are selected separately after purging.
- Report finite-source shortfalls as `inventory_exhausted`; never duplicate to
  achieve balance. The original coarse extractor already used all eligible sliding
  endpoints in processed spans, so this supplement may find few novel windows.

## Execution and efficiency

`run_coarse_supplement_v1.py prepare` performs offline metadata preparation only.
`extract` is a separate explicit action, guarded by an OS-exclusive writer lease.
There is no background queue or automatic launch behind phase two.

One CPU thread owns an entire ticker chronologically. Default 16 CPU / 4 separate
publication I/O workers. Each query groups complete source sessions, default soft
500,000-event / five-calendar-day pages. A dense single session remains whole;
the page target never truncates data. Queries are readonly, one thread, 60-second
execution limit, 1 GiB per-query memory. Transport failures retry identical SQL
three times; query/memory errors fail explicitly without dropping a session.

The ticker worker persists unique UNADJUSTED coarse bars in source-bound,
receipt-sealed NPY pages. Restarts verify hashes and reuse pages instead of
repeating warmup queries. Corporate-action adjustment happens once, only on copied
accepted 64-bar windows. Cache pages are not training shards. No SQLite is used.

Futures and ticker memory are bounded. The coordinator accepts results in frozen
ticker order, so selection under global quotas does not depend on query timing.
Completed ticker receipts resume without queries or repeated parent scans. Keep
64 GiB RAM plus requested query reservations, and 150 GiB free disk. Hard payload
budget defaults to 512 GB. These limits do not alter scientific eligibility.

Published NPY parts are <=32 MiB, fsynced, hashed and receipt-sealed. Ticker cursors
advance only after all publication futures finish. Preserve STOP files; closing
the viewer does not stop the writer. Source/contract changes require a new root,
not in-place upgrades. Never clear someone else's STOP.

## Commands

Run from a tested, committed, pushed and hash-verified native snapshot. Set
`PYTHONDONTWRITEBYTECODE=1`. Both commands require the SAME full source revision.
No command below touches the running trainer.

```powershell
python -B research/bar_gpt/v5/run_coarse_supplement_v1.py prepare --source-revision <FULL_COMMIT_SHA>
python -B research/bar_gpt/v5/run_coarse_supplement_v1.py extract --source-revision <FULL_COMMIT_SHA>
python -B -m research.bar_gpt.v5.coarse_supplement_panel_v1 --output D:/TradingML/runtimes/bar_gpt/phase1-coarse-supplement-v1
```

Defaults: cohort `phase1-v6-cohort-quote-v4`, split
`corrected-restart-20261110/split.json`, independent output
`phase1-coarse-supplement-v1`, all under `D:/TradingML/runtimes/bar_gpt`.

`progress.json` separates durable completed tickers, active query/cache/selection
stages, retries, failures and saved windows versus targets. The Rich viewer shows
stale snapshots and terminal shortfalls. `result.json` always leaves
`training_ready=false`: a full receipt reconciliation plus representative
canonical quote/content/split audits and explicit split/cohort/checkpoint handoff
are required before adoption. Keep the original validation benchmark separately.

## Validation before full launch

Focused tests exercise actual packing/publication/resume with controlled source
fixtures, parent SHA/clocks, exact duplicate exclusion, new-window overlap,
cutoff edges, quote policy and inverse split sizing. Render panel states at 80
and 120 columns; test redirected output. A bounded real canonical pilot and
readback audit must follow native snapshot verification before full extraction.
Unit tests alone do not certify source parity, availability or extraction speed.
