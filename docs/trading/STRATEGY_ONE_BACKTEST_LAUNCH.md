# Strategy 1 Backtest app connection

The Strategy 1 Backtest backend must run on `DESKTOP-SAAI85T`. Its dedicated
ClickHouse principals and private WSL Keeper endpoint stay there. Starting the
backend on the laptop does not supply those authorities and must not fall back
to the general ClickHouse account.

On the workstation, start the synced repository backend (with the `ml4t`
environment) using `python -B scripts/run_backend.py`. It listens on workstation
loopback port 8000. On the laptop, keep this connection open in a terminal:

```powershell
python -B scripts/connect_workstation_backtest.py
```

The launcher requires the dedicated SSH key and known workstation host key.
It forwards laptop loopback port 8000 to workstation loopback port 8000; it
does not read, copy, or log any ClickHouse secret. In a second laptop terminal:

```powershell
$env:VITE_API_PROXY_TARGET='http://127.0.0.1:8000'
python -B scripts/run_frontend.py dev
```

Open the printed local frontend URL. Closing the connection terminal stops
only its SSH tunnel, not the workstation backend. `--check-only` verifies the
tunnel and `/api/health` then disconnects. If the workstation backend is absent,
the launcher fails and tells the operator to start it; it never starts a local
SQLite-backed replacement. The app's visual layout is unchanged.

## Measured full-market premarket validation

On 2026-09-28, the workstation's certified ARTE build
`1521ba7702a9ee0783916f706f4885a24a3f32a91630b04ff738a90e65bc9dd5`
passed read-only preflight for all 6,100 Aug 19 tickers. With Strategy 1's
completed-second V7 geometry cache, full 04:00–09:30 ET Backtests completed:

| Session | Cold preflight | Backtest execution | Persisted liquidity rows | Run ID |
|---|---:|---:|---:|---|
| 2026-08-19 | 23.755s | 32.053s | 6,809 | `5116ec93-bc36-454c-bd49-d56b8254e1c3` |
| 2026-08-18 | 24.867s | 32.816s | 7,381 | `daaaacd4-4723-4318-bf9f-e86e4cf4dae0` |

The Aug 19 pre-change run `b05c309d-f2c7-401e-9819-99cd9721775e`
took 38.787s of execution on the same full session. A cold, read-only V4
journal comparison confirmed matching final account state, 45 portfolio
decisions, 45 strategy intents, 97 executions, 97 commissions, and 39 order
commands before and after the optimization. The optimized runs' asynchronous
ClickHouse journal queues drained with zero failed units; neither produced a
run-local directory. These are observed workstation timings, not a throughput
guarantee. Live Strategy 1 order admission and Candidate 350 re-entry parity
remain separate acceptance gates.
