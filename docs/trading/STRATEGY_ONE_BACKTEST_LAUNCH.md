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
