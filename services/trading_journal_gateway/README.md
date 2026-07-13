# Trading Journal Gateway

This service mirrors the crash-safe SQLite WAL trading journal into ClickHouse
`q_live.tr_*` tables. Order commands are committed locally before transport;
the outbox is marked delivered only after both the generic journal row and its
typed order/fill/portfolio/position/signal row are accepted by ClickHouse.

```powershell
python -m services.trading_journal_gateway.run
```

Use `TRADING_JOURNAL_PATH`, `TRADING_CLICKHOUSE_URL`,
`TRADING_CLICKHOUSE_USER`, `TRADING_CLICKHOUSE_PASSWORD`,
`TRADING_JOURNAL_FLUSH_SECONDS`, and `TRADING_JOURNAL_BATCH_SIZE` to configure
the service. Secrets are read from the environment and are never journaled.
