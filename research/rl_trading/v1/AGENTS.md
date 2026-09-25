# RL trading V1 data authority

- Market observations and label price action come only from certified `arte.bars_v1`, `arte.indicators_v1`, and the pinned causal V7 state/levels/coverage products derived from completed `arte` bars. Do not read retrospective V7, event tables, QMD History, flatfiles, quote tables, or alternative bars.
- The certified pre-open tradable population may be read from `q_live.feature_tradable_universe_snapshot_v2` solely to resolve listing identity and verify its certificate. `system.tables` and `system.parts` may be read solely to verify the `live_market_ssd` policy and active part placement.
- ClickHouse connections must use `readonly=1` and pass statements through `arte_sql.ArteReader`. Never add ClickHouse `INSERT`, DDL, mutation, or indirect writing capabilities. The campaign's local runtime manifests, Parquet files, and Phase 3 SQLite restart checkpoint are filesystem artifacts, not ClickHouse writes.
- Preserve the Phase 1 price-action V4, Phase 2 V6, and Phase 3 V1 contracts unless a separately versioned algorithm change is requested. Do not silently reinterpret older QMD/event datasets as arte data.
- All generated outputs and checkpoints belong under `D:/TradingML/runtimes` or the configured machine runtime root, never in this package.
