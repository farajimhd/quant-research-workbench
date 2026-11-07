# Ordinal and timestamp campaign, dataset v5

The model is unchanged. `run_balanced_shards.py` owns the new extraction path;
`run_shards.py` remains the historical session-based implementation.

## Recorded requirements

- Freeze the complete 2019–2025 canonical ordinal summary before extraction.
  Use `market_sip_compact.events_YYYY` only; never reopen historical flatfiles.
- Include structurally valid trades regardless of participant/reporting status.
  Reuse ARTE's independent sale-condition rules for OHLC and volume/count.
- Quote-only buckets are not observations. Retain volume-only intermediate
  contributions until each requested timeframe has been aggregated.
- Fine frames are 100ms, 1s, 10s and 1m. Use bounded, variable ordinal chunks,
  allow cross-session contexts, and omit incomplete chunk boundary buckets.
  Missing 64-bar contexts at any timeframe are allowed; never pad or extend a
  chunk merely to force every timeframe to appear.
- Independently extract hourly/daily bars over timestamp spans. Daily means
  09:30–16:00 New York. Intraday observations use 04:00–20:00 New York.
- Sample 20% of complete intraday sliding origins without replacement; daily
  uses sliding origins without that restriction. Preserve UTC clocks and gaps.
- Backward-adjust every cross-split context to its own final share basis.
  Prices multiply by split_from/split_to; volumes/sizes use the inverse.
  Counts are unchanged. Apply no action after the sample's final clock.
- Keep causal quote carry within each session. The latest invalid quote clears
  carry; carried values never manufacture quote counts. Seed status from a
  frozen sparse canonical halt/resume timeline; silence is not normal trading.
- Target 75/25 liquid/less-liquid saved samples, separately per timeframe.
  Liquidity uses the top quartile of ticker-year average source event count,
  an explicit proxy rather than a claim about trade volume or spreads.
- Target equal saved counts for the five non-daily frames. Daily may be fewer;
  the initial default reserves 5% for daily and 19% for each remaining frame.
  Publish actual coverage and report inventory exhaustion rather than repeat
  identical samples to make a nominal storage or class quota.
- Purge contexts crossing the shared train/development boundary. The trainer
  builds its own chronological 80/20 manifest from complete context bounds.
- Keep final, unscaled, mmap-ready `[N,64,21]` samples in at most 32 MiB files.
  Query and I/O concurrency are separately bounded. Checkpoint after durable
  publication and hold an OS controller lock; never overwrite sealed payloads.

## Components

`balanced_plan.py` freezes summary partitions and deterministic ticker order.
`range_sql.py` performs canonical aggregation and causal quote/state handling.
`origin_samples.py` selects origins and packs only selected windows, vectorized.
`balanced_build.py` enforces quotas, checkpoints and asynchronous publication.
`profile_balanced.py` compares bounded chunk and concurrency configurations.

Only final market aggregates leave ClickHouse. NumPy packs selected overlapping
contexts and applies frozen split factors before publication; the training loader
does not construct windows or aggregate raw events.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B research/bar_gpt/v5/run_balanced_shards.py --plan-only
python -B -m research.bar_gpt.v5.shards.profile_balanced --output D:/TradingML/runtimes/bar_gpt/balanced-profile
python -B research/bar_gpt/v5/run_balanced_shards.py --workers 32 --io-workers 16
python -B -m research.bar_gpt.v5.shards.progress
```

The default root is `D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v5`.
The byte limit is a hard cap, including metadata. The sample quota reserves 2%
for the frozen plan, headers and receipts. An explicit `STOP` file drains workers;
restarting the identical immutable code resumes sealed work. Configuration or
code changes require a new dataset root. A pilot must always use a separate root.
