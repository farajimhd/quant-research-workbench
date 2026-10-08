# Native candle channel research v3

Projects the existing `arte.bars_v1` schema into relative OHLC channels and
relative execution volume. Absolute source prices, source identities, validity
flags and completed-boundary timestamps are retained. The caller must load
and verify exact certified build/day/ticker/attempt products; this pure function
does not certify a DataFrame or grant trading authority.

Rows are grouped by complete source identity and resolution, preventing a
baseline from crossing sessions, builds or source attempts. Current volume is
excluded from the trailing mean. Invalid candles retain their rows with null
price channels. Missing buckets remain missing. Resolutions must be declared
by the existing native producer; 15-minute research aggregation is not a native
15-minute product.

`through_day_boundary_ms` is milliseconds since the local source midnight.
The Backtest clock is offset from 04:00 and must be explicitly translated by
the verified source adapter. Never pass that relative clock as a day boundary.
Feature availability is the completed bucket end, not its start. The strategy
decision interval is separately declared and does not alter source clocks.

No market writer, installer, Backtest integration or financial evaluation is
included. Split/fundamental certification and native source coverage remain
pending. Generated data and evidence belong under `D:\TradingML\runtimes`.
