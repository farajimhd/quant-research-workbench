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

The default launcher uses a Rich terminal layout with:

- run summary and expanded build range
- overall build progress across delete/insert stages
- current operation
- recent messages

Use plain line-based output when redirecting logs or when Rich rendering is not
useful:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py --progress-layout text
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

## Bar Boundaries

All bar boundaries are computed in UTC from `events.sip_timestamp_us`.

- Intraday bars use exact UTC interval starts: `1s`, `5s`, `1m`, and `5m`.
- Daily bars start at UTC midnight.
- Weekly bars start on Monday UTC.
- Monthly bars start on the first day of the UTC month.

`--expand-boundaries` is enabled by default. If the requested range intersects a
weekly or monthly bar, the standalone builder expands the build range to the full
affected week/month before deleting and inserting rows. For example, requesting
`2026-06-03` with `1w,1mo` builds `2026-06-01` through `2026-06-30`; this avoids
silently writing a partial weekly/monthly bar for a range that only covers the
middle of a period. Use `--no-expand-boundaries` only when intentionally
building partial-period bars.

The first bar in a build window has no earlier bar inside that same window, so
history-derived fields such as `return_1_bar`, `return_3_bar`, `return_5_bar`,
and acceleration fields are zero at the leading edge. If exact leading-edge
history fields matter, include enough prior dates in the build range and query
only the final desired slice afterward.

The last bar is built from all events currently present through the requested
end date. If the source event table does not yet contain the complete current
day/week/month, the last higher-timeframe bar is necessarily partial and should
be rebuilt after the remaining events arrive.
