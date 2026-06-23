# Market SIP Event Pipelines

This folder contains ClickHouse pipelines that derive reusable event-level
training tables from `market_sip_compact.events`.

## QMD-Compatible Market Bars

`clickhouse_build_trade_bars.py` builds one qmd-gateway-compatible
`live_market_bars` table from `market_sip_compact.events`. The schema mirrors
`services/qmd-gateway/src/bars.rs` `BAR_SCHEMA_VERSION = 2`, so historical
flatfile-derived bars and live qmd bars can be queried with the same column
contract.

The default timeframes are:

```text
1s,5s,1m,5m,1d,1w,1mo
```

The builder uses trade events for OHLCV and quote events for bid/ask, midpoint,
spread, quote counts, quote rates, and displayed-size fields. Tape-side,
effective-spread, LULD, and some intra-bar path fields are filled with
deterministic historical proxies or neutral values where the live qmd writer
depends on stream-local state that is not durable in the compact events table.

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
inserting. Use `--drop-table` only when intentionally rebuilding the whole
selected bar table from scratch.
