# Market SIP Event Pipelines

This folder contains ClickHouse pipelines that derive reusable event-level
training tables from `market_sip_compact.events`.

## QMD-Compatible Market Bars

`clickhouse_build_trade_bars.py` builds three qmd-gateway-compatible bar tables
from `market_sip_compact.events`. The schema mirrors
`services/qmd-gateway/src/bars.rs` `BAR_SCHEMA_VERSION = 2`, so historical
flatfile-derived bars and live qmd bars can be queried with the same column
contract.

The three tables contain identical rows and columns but use different physical
layouts for the main access patterns:

| Table | Partition | Order key | Primary use |
| --- | --- | --- | --- |
| `live_market_bars` | `session_date` | `(session_date, timeframe, sym, bar_start)` | QMD/chart-compatible date slices |
| `bars_by_symbol_time` | `toYYYYMM(bar_start)` | `(sym, timeframe, bar_start)` | per-ticker temporal training windows |
| `bars_by_time_symbol` | `toYYYYMM(bar_start)` | `(timeframe, bar_start, sym)` | market-wide time-snapshot training |

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

- run summary and per-timeframe build ranges
- overall build progress across delete/insert stages
- current operation
- recent messages

Ctrl+C exits with status code `130`, closes the Rich layout cleanly, appends an
`interrupted` row to the JSONL report, and best-effort sends `KILL QUERY ... SYNC`
for the active ClickHouse delete/insert query. If the interruption lands during
a ClickHouse mutation, check `system.mutations` before restarting.

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
inserting in all three layouts. Use `--drop-table` only when intentionally
rebuilding the selected bar tables from scratch.

## Bar Boundaries

All bar boundaries are computed in UTC from `events.sip_timestamp_us`.

- Intraday bars use exact UTC interval starts: `1s`, `5s`, `1m`, and `5m`.
- Daily bars start at UTC midnight.
- Weekly bars start on Monday UTC.
- Monthly bars start on the first day of the UTC month.

`--expand-boundaries` is enabled by default. Boundary expansion is applied per
timeframe before that timeframe is deleted and inserted. Intraday and daily
timeframes use the requested date range. Weekly timeframes expand to the full
affected Monday-Sunday week, and monthly timeframes expand to the full affected
month. For example, requesting `2026-06-03` with `1s,1w,1mo` builds `1s` only
for `2026-06-03`, `1w` for `2026-06-01 -> 2026-06-07`, and `1mo` for
`2026-06-01 -> 2026-06-30`. Use `--no-expand-boundaries` only when
intentionally building partial-period bars.

The first bar in a build window has no earlier bar inside that same window, so
history-derived fields such as `return_1_bar`, `return_3_bar`, `return_5_bar`,
and acceleration fields are zero at the leading edge. If exact leading-edge
history fields matter, include enough prior dates in the build range and query
only the final desired slice afterward.

The last bar is built from all events currently present through the requested
end date. If the source event table does not yet contain the complete current
day/week/month, the last higher-timeframe bar is necessarily partial and should
be rebuilt after the remaining events arrive.
