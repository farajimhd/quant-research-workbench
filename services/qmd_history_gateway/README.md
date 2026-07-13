# QMD Historical Gateway

This service is the historical market-data source for Replay, Backtest, and
Backtest Debug. It reads `market_sip_compact.events_YYYY` in deterministic
`(sip_timestamp_us, ticker, ordinal)` order and mirrors QMD's compact-event and
bar snapshot resource shapes. Bars are calculated from events; no historical
bar table is used.

Live trading must use `services/qmd-gateway`. This service is deliberately
read-only and cannot connect to Massive or write live QMD state.

Run from the repository root:

```powershell
python -m services.qmd_history_gateway.run
```

Configuration uses `QMD_HISTORY_CLICKHOUSE_URL`, `QMD_HISTORY_DATABASE`,
`QMD_HISTORY_TABLE_PREFIX`, `QMD_HISTORY_CLICKHOUSE_USER`,
`QMD_HISTORY_CLICKHOUSE_PASSWORD`, `QMD_HISTORY_HOST`, and
`QMD_HISTORY_PORT`.
