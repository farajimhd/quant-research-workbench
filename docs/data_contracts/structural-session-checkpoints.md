# Structural V7 session checkpoints

AH remains the retrospective daily book produced by the existing historical
fitter. PM (09:30 New York) and REGULAR (the pinned XNYS close, including early
closes) capture the default causal streaming kernel seeded by the preceding AH.
Only completed canonical seconds ending at or before the boundary are consumed.
An existing AH is preserved; when it exists streaming stops at regular close.
A missing AH is calculated with the historical fitter, separately from the
intraday stream. This distinction is intentional.

All outputs remain in `arte.structural_levels_v7`,
`arte.structural_level_observations_v7`, and
`arte.structural_level_coverage_v7`, on `live_market_ssd`. The additive migration
appends `checkpoint_boundary` to each sorting key. Its implicit empty-string
value identifies original AH rows without rewriting their data. PM and REGULAR
have separate keys. Intraday snapshots expire at 20:00 New York. Coverage also
stores a compressed lossless streaming state and its integrity hash; normalized
geometry alone cannot recover pending discovery/contact state. Coverage is
published last, after normalized rows pass exact read-back comparison.

`structural_v7_seed.load_seed` explicitly reads AH. It supports the old schema
with one cached metadata probe per connection. `structural_checkpoint_storage.
load_boundary` restores a same-day PM/REGULAR state available at a requested
timezone-aware cutoff. Consumers still need to pin the certified market source;
the loader does not authorize a new population or an authority cutover. Old
archive migration tools deliberately reject the extended schema until reviewed.

The backfill resumes from the closest AH preceding each ticker's first target
day, processes missing trailing AH history, and fills PM/REGULAR for the target
range. A ticker without an AH starts at its first canonical history from 2025
(or the earlier explicitly requested start). Warmup before the target range
persists AH only. All three boundaries are persisted inside an explicitly
requested earlier range. No historic flatfiles are read. Tradable selection is
the completed bar funnel's frozen, reviewed population; identities and listing
intervals are inherited rather than rediscovered from a current ticker list.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/run_market_data_pipeline.py backfill --step structural `
  --start-date 2026-01-01 `
  --bars-run-dir D:/TradingML/runtimes/market-data-pipeline/bars-2026-funnel-32 `
  --run-dir D:/TradingML/runtimes/market-data-pipeline/structural-2026-sessions-v1 `
  --endpoint http://192.168.1.218:18123 --plan-only
```

The default end is the latest certified canonical source day; an explicit end
is inclusive. Full-scope planning fails if the completed bars do not cover that
range. After reviewing the plan, omit `--plan-only` and add
`--migrate-structural-schema`; subsequent attempts require `--resume`. Keep the
same immutable source release and request. A `STOP` file gracefully stops before
the next ticker-day; remove it deliberately before resuming. The global
`structural-session-arte.lock` prevents duplicate structural campaign writers.

The standalone launcher also supports `--profile-only`: it verifies and reads
three completed bar units, measures fetch/stream/serialization times and payload
sizes, and writes only a runtime report. It may profile individually certified
units while the bar campaign continues. Full writes wait for `core_complete`.
The writer uses eight process lanes by default (at most sixteen), one ticker
per lane, one query thread and 2 GiB per query. Its queue holds at most one
pending ticker per lane. Each ticker's days remain sequential. A parent identity
and per-worker leases prevent a replacement owner from starting before orphaned
workers drain. It does not use CUDA. Optimize concurrency from real profiles
rather than restarting a healthy campaign for speculative tuning.

`scripts/run_structural_after_bars.py` is the persistent scheduled controller.
It waits for the bar owner to release its lock, then runs full-scope planning,
the bounded profile, and the resumable backfill. It writes controller state and
a durable build log under the structural runtime, with no additional popup.

Forwardfill version 2 orders canonical ingestion, certified tradable bars,
daily session products, then structural checkpoints. A day remains pending
until every stage succeeds. Version 1 frozen requests are not silently migrated
or marked structurally complete.
