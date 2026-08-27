# Long momentum squeeze optimizer v1

This package runs a causal, event-time optimization of the unapproved long-momentum
squeeze Test Candidate on 2026-08-21 premarket data. It keeps the exact persisted
5% squeeze milestone and the hard liquidity Watchlist contract fixed. Five minutes
is the squeeze episode TTL, not a closed five-minute candle.

The chronological tuning fold is 04:00-07:30 ET. The untouched validation fold is
07:30-09:30 ET. Each finalist is rerun with baseline and stress execution costs.
The objective first rejects liquidity or causal-clock violations, then ranks by net
P/L, realized drawdown, and execution coverage. Results remain unapproved Test
Candidate evidence; the optimizer never promotes a live configuration.

Artifacts are restart-safe JSON plus the engine journals under:

`D:\TradingML\runtimes\strategy_optimization\long_momentum_squeeze_v1\<run-id>`

Run from the repository root:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\g835l\miniconda3\envs\ml4t\python.exe -m research.strategy_optimization.long_momentum_squeeze_v1.run_optimize --run-id aug21-premarket-v1
```
