# V66 hindsight labels for review

This offline sidecar adapts the existing RL V6 `price_action_opportunities`
(1a) and `market_teacher_preview` / `market_teacher_dataset` (1b) authorities.
No optimizer, existing RL publication, or sealed market shard is modified.
The new Research path is **Research → V66 labels · 1a / 1b**.
The direct app route is `/?research=v66-labels#research-workspace`. Choose the
phase, session and ticker; in 1b, expand the selection table and click a pair
to jump to its saved entry candles. The existing flat/held/reference views,
liquidity audit, quality/gain inspector and fullscreen chart remain available.

The source is the V66 `2026-top5-v1` manifest pair: 150 training and 38
validation sessions. All stored ticker-union candles are considered, not only
moments when the scanner selects a ticker. `scanner_member` preserves the
causal top-five membership separately. 1b grouping uses the stored ticker
union, not a causal tradable portfolio or live allocation.

MACD uses V66's exact 12/26/9 recurrence, session-reset initialization and
float32 relative outputs decoded with raw close. Prices/activity come directly
from sealed one-second trade shards. The existing liquidity and quality rules,
minimum duration, discount, fee floors and overlap sizing algorithm are unchanged.
Missing seconds are not filled. Invalid OHLC and MACD warm-up rows are saved in
`excluded.parquet` with reasons and reconciled with the complete source census.

The horizon ends at the last stored premarket candle (no post-09:30 prices).
Final pairs carry `right_censored`; these are bounded-horizon algorithm outputs,
not a claim that the MACD pair completed. Optimizer use, feature/target offset,
boundary eligibility and reward design await user review. Labels attach to
candle close timestamps and must never enter observation tensors.

Each session saves one compressed label file per ticker (both original 1a
actions/values and copied 1b actions/targets), plus episodes, pairs, reference
trades, selection decisions and exclusions. Every file has a hash/row receipt;
day receipts bind source manifests, algorithm settings and producer file hashes.
1b preservation and independent active-set allocation checks run before sealing.
Reference trade P&L remains 1a price-only evidence, not a 1b portfolio return.

Run on the workstation from a committed, pushed, hash-verified deployment:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
C:/Users/Mehdi/miniconda3/envs/ml4t/python.exe -B -m research.market_labels.v1.run_build --publish
```

Defaults: source `D:/TradingML/runtimes/market_shards/datasets/2026-top5-v1`,
output `D:/TradingML/runtimes/market_shards/labels/v66-1a1b-v1`, four workers.
Use `--days YYYY-MM-DD --output <fresh-runtime-root>` for a canary; partial
builds cannot publish. Repeat the exact command to verify and reuse sealed days.
A different producer requires a fresh output root. Unsealed partial days are
rebuilt in place. `progress.json` reports queued, active, completed, reused and
failed days. Create `STOP` to drain active days without admitting more work.
A kernel-owned writer lock prevents concurrent controllers.

Only full successful inventory publishes `v66-active-labels.json`. The app maps
workstation paths through the established explicit runtime mapping, verifies
certificates and selected file hashes, and reads content-addressed local copies.
It never recalculates numerical labels or falls back to older RL publications.
Validation labels are reviewable, but are not used to tune/select any optimizer.
