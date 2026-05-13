# Strategy Architecture

Strategies define trading logic. They do not prepare market data, build bars, or
calculate provider-owned features during a backtest.

## Strategy Contract

Each strategy should declare what it needs before the backtest starts:

- event timeframe, usually `1m`
- feature groups and columns required from the data provider
- session scope or session-filtering preference
- lookback context needed for provider-built features
- strategy parameters used for scoring, entries, exits, and sizing

The backtest engine uses this declaration to load provider-built data. If the
requested range or features have not been built, the run should fail before
simulation.

## Input Shape

The standard strategy input is a Polars table where rows are ticker bars for the
event timeframe:

```text
session_date | bar_time_market | ticker | open | high | low | close | volume | ...
```

Multi-timeframe indicators should arrive as columns on the event frame when
that is the natural trading surface. For example, a strategy that trades 1-minute
bars using 5-minute MACD or TEMA should request provider-built 5-minute
indicator columns attached to the 1-minute rows. It should not require the
backtest engine to pass a separate 5-minute bar stream unless the strategy truly
trades 5-minute bars.

The strategy trades on the event rows it receives. Indicator alignment must be
safe against lookahead bias; provider-built features should only expose values
that were available at the current event bar.

## Execution Style

Strategies should be table-slice event strategies:

1. Prepare session-level state from provider-backed frames.
2. Add strategy-specific columns with Polars expressions when useful.
3. Receive one timestamp slice at a time from the engine.
4. Use Polars expressions to filter, rank, and score the current slice.
5. Convert only selected candidates or held positions to Python objects.
6. Return order requests to the engine.

This keeps the strategy readable and state-aware while preserving most of the
performance benefit of columnar processing.

Avoid this pattern for large slices:

```python
for row in bars.iter_rows(named=True):
    evaluate_every_rule_in_python(row)
```

Prefer this pattern:

```python
candidates = (
    bars
    .filter(pl.col("entry_eligible"))
    .sort("live_score", descending=True)
    .head(max_orders)
)
```

## Freshness Rules

Strategies must be explicit about fresh versus stale rows.

Entry signals and stop or limit order crossing should usually use fresh updates
only, because they depend on the current bar high, low, or close. Latest-known
rows can be useful for marking positions or context, but they must carry
freshness information such as `is_fresh_bar`, `last_bar_time`, and
`stale_minutes`.

## ORB 5M Momentum Direction

`orb_5m_momentum` should trade over 1-minute bars. It may use provider-built
5-minute indicator features, but those features should be columns on the
1-minute event frame.

The strategy's expected responsibilities are:

- build or select the opening-range setup from provider-built columns
- score and rank setup candidates with Polars expressions
- maintain a watchlist and armed state for the session
- evaluate live 1-minute slices for entry eligibility
- use provider-built 5-minute indicator columns as context
- emit stop, market, cancel, and end-of-day order requests
- record candidate, signal, rejection, and ranking artifacts

The strategy should not ask the backtest engine to consolidate 5-minute bars or
calculate MACD, TEMA, VWAP, ATR, relative volume, or similar provider-owned
features.

## Discovery Path

New strategies should keep setup and signal logic as expression-oriented as
possible. This allows the same strategy code to support:

- the standard timestamp-slice simulator for correctness and debuggability
- future vectorized discovery over full sessions or parameter grids

Stateful portfolio behavior can remain in the event simulator. Signal
generation, ranking, and filtering should be written in a way that can later be
lifted into larger Polars pipelines.
