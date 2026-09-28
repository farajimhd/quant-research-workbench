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
profiled Keeper close cost motivated a process-local, read-only Keeper
transport in commit `70d0a0d51`. It retains no certificate or lease, rereads
the attestation on every preflight, and retires disconnected sessions only
after their current readers exit. A fresh-process, unprofiled plan-only check
with that code took 19.987s cold; a separate profiled check took 33.634s cold
and 3.335s warm. These are observations under different instrumentation and
load, not a controlled claim of seconds saved. Kazoo still logs the connection
drop while closing at process exit; the Backtest request no longer waits for
that per-preflight close. A later unprofiled run measured Keeper close at
0.105s; the profiled cumulative shutdown time is not evidence of a practical
Keeper bottleneck.

After syncing that source and restarting the workstation backend, the actual
`POST /api/trading/historical-preflight` route returned `ready=true` with no
blocked checks for the full Aug 18 04:00–09:30 ET market. The first request
took 23.202s and the identical warm request took 2.640s. These are route
latencies, not Backtest execution times. The backend remained healthy on
port 8000 after both requests; no Backtest run was created.

A further unprofiled full-market Aug 18 run, using the same 100ms contract,
completed execution in 31.818s after a 23.143s cold preflight. Run
`b6c36e59-fd62-4a65-9c66-a047490f38b9` processed 7,381 persisted
liquidity rows. Its measured terminal journal confirmation took 7.814s,
while writer close took 0.001s and Keeper close took 0.105s. The asynchronous
writer committed 10 units and 2,217 event rows with zero failures. A fresh
`v4-terminal-page` request independently confirmed completed status, verified
sequence 2,217 and market cursor, a flat account, and no review limitations.

Read-only sparse-market profiles on that certified Aug 18 build fetched the
same 62,072 candidate rows for 957 tickers from 200 bounded shards at each
worker setting. The exact-key fetch took 6.849s with 4 workers, 3.753s with
8, and 3.125s with 16. The Backtest's sparse-read ceiling is now 16, but it
opens no more lanes than the vectorized static gate leaves surviving shards.
This raw-source profile does not include that gate, preflight, brokerage, or
journal completion.

The first end-to-end run after the ceiling change, run
`dcc439aa-4113-4005-ae9e-d06d4ccc0812`, took 32.081s of execution.
Its actual post-gate sparse load was only 0.189s, so the worker increase did
not measurably accelerate this session relative to the preceding 31.818s run.
Both runs cold-verified 2,217 committed journal events with identical category
counts and final account balances, no open positions, and no review limitations.
The adaptive-pool full-market rerun `ed91a04a-0954-4d96-80cb-b95e7bccd704`
completed in 31.964s after a 24.040s cold preflight, with 7,381 processed
liquidity rows and a 0.198s sparse load. Its cold terminal page independently
verified sequence 2,217, the market cursor, the same $10,297.95 flat account,
and no limitations. The three unprofiled runs cluster near 32s; the sparse
worker ceiling is not the dominant bottleneck for this session.

Terminal-phase instrumentation on another full-market Aug 18 run
(`8f5bbb79-eecd-4e01-a59f-b01952b4d870`) measured 32.561s execution
and 7.532s terminal confirmation. Of that terminal time, 5.433s fenced
the prior completed-market cursor and its queued typed journal/snapshot work,
2.091s awaited the final ClickHouse account-snapshot receipt, and runtime
finish/capture took under 0.01s. The writer committed 10 units and 2,217
events with zero failures. A fresh terminal-page read verified the complete
sequence and cursor, the same flat account balance, and no limitations. This
wait is a durability/causality fence after the engine has advanced, not
SQLite or run-local disk I/O on the hot path.
The same run's read-only commit-header profile found six V4 commits for all
2,217 events, with no singleton commit. The two manager and two broker-match
snapshot units are separately attested recovery state; removing them merely
to lower the measured wall time would weaken the current journal contract.
