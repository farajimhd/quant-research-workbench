# Phase 2: market action values

`build_phase2.py` accepts a completed certified `hindsight-phase1-arte-price-action-v4` directory. Phase 1 was built from completed `arte.bars_v1` and `arte.indicators_v1`; Phase 2 reads only its verified local files and writes runtime Parquet/JSON output. It never accesses ClickHouse.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B research/rl_trading/v1/build_phase2.py build --phase1 <completed-arte-phase1-root>
```

The Phase 1 campaign runs this automatically after each completed session. Direct `build` is useful for rebuilding action values with a different discount or liquidity gate. The default discount half-life is 30 MACD bars, or 30 seconds at the 1-second MACD resolution. The optional cost is per share per transaction. Default new-entry liquidity requires at least 20,000 shares and 11 trades in the last 60 completed one-second bars. Override with `--min-volume-60s` and `--min-trades-60s`. Holding and closing values remain available when new entries fail the liquidity gate.

The completed Phase 2 root publishes `market_hold_values.parquet` for the full market-wide second-by-listing holding/closing grid and `market_open_values.parquet` for eligible non-wait openings. A missing opening row means `can_open=False`. `MarketValues` verifies the plan and file hashes, then reconstructs the action values for every listing and side at each second. The root `long.parquet`, `short.parquet`, and `long_short.parquet` tables are greedy projections; they do not replace the full values used by Phase 3.

Each output has a plan hash, completion marker, listing order, scope, provenance, row counts, and file hashes. Outputs are under the configured runtime root, normally `D:/TradingML/runtimes/hindsight-greedy/<date>/<plan-hash>`. Repeated builds reuse verified listing results. A `STOP` file or Ctrl+C stops further admission; incomplete runs do not publish completion.

For a completed Phase 2 dataset, `evaluate --dataset <root> --request <request-json> --mode long` scores explicit joint share changes. This evaluator and the flat greedy tables are diagnostic; Phase 3 uses the full action-value tensor for cash-constrained sequential search.
