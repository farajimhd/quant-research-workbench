# Market SIP Event Pipelines

This folder contains ClickHouse pipelines that derive reusable event-level
training tables from `market_sip_compact.events`.

## Trade OHLCV Bars

`clickhouse_build_trade_bars.py` builds reusable trade-based OHLCV tables:

```text
bars_1s
bars_5s
bars_1m
bars_5m
bars_1d
bars_1w
bars_1mo
```

Each table is written to the selected database, normally
`market_sip_compact`. Bars are based only on trade events:

```text
open   = first trade price in the bar
high   = max trade price in the bar
low    = min trade price in the bar
close  = last trade price in the bar
volume = sum(trade size) in the bar
```

The table schema also stores `trade_count`, first/last trade timestamps, and
microsecond bar boundaries. Quote-derived liquidity bars should be added as a
separate table later so trade OHLCV labels keep clean semantics.

Run on the workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py
```

Inspect the exact command without running:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py --print-only
```

Preview DDL/DML without mutating ClickHouse:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py --dry-run
```

By default, the builder replaces rows in the requested date range before
inserting. Use `--drop-tables` only when intentionally rebuilding selected bar
tables from scratch.
