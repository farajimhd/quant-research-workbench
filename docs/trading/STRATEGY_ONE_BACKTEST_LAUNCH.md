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

An additional workstation app-API run selected the full Aug 18 session by
passing the exclusive `anchor_date=2026-08-19`. Its preflight returned
`strategy_run_ready=true`, `execution_interval=100ms`, zero blocked checks,
and the same certified market token above. POST `/api/trading/backtest/runs`
created run `75ad9d96-8a92-4eed-9b7b-de89c4b5a097`; it completed in
32.233s from creation to terminal update, processing 7,381 liquidity rows.
The writer drained 10 units and 2,217 event rows with zero failures. A fresh
`v4-terminal-page` read verified sequence 2,217, its market cursor, and the
terminal account state with no limitations. The run created no directory under
the workstation Backtest runtime root. The test backend and SSH tunnel
were stopped afterward. This validates the app routes, not visual browser
interaction or live trading.

On 2026-09-28, a separate backend on workstation loopback port 8001 using the
synced source opened that saved V4 run in the actual Backtest UI. The WFF chart
loaded 1,000 persisted 1-second bars and 1,000 closed MACD rows through the
verified boundary. Targeted browser review passed at 1440×900/dark/100% and
1024×768/light/125%, with no layout redesign or objective UI issues. The
temporary backend, tunnels, and frontend were stopped; the pre-existing
workstation backend on port 8000 was left running. This verifies cold saved
review and chart rendering, not live order admission.

A read-only full-market Aug 18 preflight profile on the workstation ran twice
in one process and once in a fresh process. With profiling overhead, the two
cold calls took 32.113s and 33.178s; the in-process warm call took 3.883s.
Both cold profiles included a Keeper connection drop during Kazoo session
shutdown, with 11.615s and 12.592s spent in `ManagedKeeperSession.close`.
These profiled values are not the uninstrumented app latency above. The
probe used plan-only mode and inserted no market or journal rows. The
repeatable Keeper close cost motivated a process-local, read-only Keeper
transport in commit `70d0a0d51`. It retains no certificate or lease, rereads
the attestation on every preflight, and retires disconnected sessions only
after their current readers exit. A fresh-process, unprofiled plan-only check
with that code took 19.987s cold; a separate profiled check took 33.634s cold
and 3.335s warm. These are observations under different instrumentation and
load, not a controlled claim of seconds saved. Kazoo still logs the connection
drop while closing at process exit; the Backtest request no longer waits for
that per-preflight close. The existing app backend must restart to load this
new source.
