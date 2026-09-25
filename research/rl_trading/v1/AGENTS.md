# RL trading V1 data authority

- Market observations and label price action come only from certified `arte.bars_v1` and `arte.indicators_v1`. Seed V7 at 04:00 from the prior-session `arte.structural_level*_v7` tables, then advance the Backtest streaming V7 engine from completed certified `arte.bars_v1` seconds. Never read V7 from disk, event tables, QMD History, flatfiles, quote tables, or alternative bars.
- The certified pre-open population and identity-matched float, outstanding-share, and split facts may be read from `q_live`; apply effective-date and recorded-at cutoffs. `system.tables` and `system.parts` may be read solely to verify the `live_market_ssd` policy and active part placement.
- ClickHouse connections must use `readonly=1` and pass statements through `arte_sql.ArteReader`. Never add ClickHouse `INSERT`, DDL, mutation, or indirect writing capabilities. The campaign's local runtime manifests, Parquet files, and Phase 3 SQLite restart checkpoint are filesystem artifacts, not ClickHouse writes.
- Preserve the Phase 1 price-action V4, Phase 2 V6, and Phase 3 V1 contracts unless a separately versioned algorithm change is requested. Do not silently reinterpret older QMD/event datasets as arte data.
- All generated outputs and checkpoints belong under `D:/TradingML/runtimes` or the configured machine runtime root, never in this package.
