# Real Live Market Data Gateway

This package is intentionally separate from backtest and semi-auto trading code. It owns the live market-data path for the real live trading page:

- ClickHouse-backed tradable universe with `ticker` and IBKR `conid`
- Massive stock websocket subscriptions using targeted `T.{ticker}` and `Q.{ticker}` channels
- in-memory quote/trade state and Polars universe frame
- backend market-status rows, signal rows, and 1-minute bar state
- optional ClickHouse replay persistence for trades, quotes, and bars in a separate app-owned database

Massive websocket protocol used here:

- connect to `wss://socket.massive.com/stocks`
- auth message: `{"action":"auth","params":"<api key>"}`
- subscribe message: `{"action":"subscribe","params":"T.AAPL,Q.AAPL"}`

Primary environment variables:

- `MASSIVE_API_KEY`
- `REAL_LIVE_CLICKHOUSE_READ_URL` or `REAL_LIVE_CLICKHOUSE_URL`
- `REAL_LIVE_CLICKHOUSE_READ_DATABASE` or `REAL_LIVE_CLICKHOUSE_DATABASE`
- `REAL_LIVE_CLICKHOUSE_READ_USER` or `REAL_LIVE_CLICKHOUSE_USER`
- `REAL_LIVE_CLICKHOUSE_READ_PASSWORD` or `REAL_LIVE_CLICKHOUSE_PASSWORD`
- `REAL_LIVE_CLICKHOUSE_WRITE_URL` or `REAL_LIVE_CLICKHOUSE_URL`
- `REAL_LIVE_CLICKHOUSE_WRITE_DATABASE` or `REAL_LIVE_APP_CLICKHOUSE_DATABASE`
- `REAL_LIVE_CLICKHOUSE_WRITE_USER` or `REAL_LIVE_CLICKHOUSE_USER`
- `REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD` or `REAL_LIVE_CLICKHOUSE_PASSWORD`
- `REAL_LIVE_UNIVERSE_SQL`
- `REAL_LIVE_MAX_UNIVERSE_SYMBOLS`
- `REAL_LIVE_MIN_PRICE`
- `REAL_LIVE_MIN_AVG_DAILY_VOLUME`
- `REAL_LIVE_CLICKHOUSE_WRITES`

`REAL_LIVE_UNIVERSE_SQL` should return at least:

- `ticker`
- `conid`

Recommended columns:

- `primary_exchange`
- `sec_type`
- `currency`
- `last_price`
- `avg_daily_volume`
- `float`
- `short_interest`
- `short_interest_date`
- `short_volume`
- `short_volume_date`

The frontend should display derived categorical fields such as `short_setup` and `float_profile`, not raw `sec_type` or `currency`.

Use the read database for the external app's ticker/conid universe. Use the write database for this app's replay/session tables. The write path creates `REAL_LIVE_CLICKHOUSE_WRITE_DATABASE` when ClickHouse permissions allow it.
