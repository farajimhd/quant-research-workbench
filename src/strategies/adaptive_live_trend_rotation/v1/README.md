# Adaptive Live Trend Rotation v1

Adaptive Live Trend Rotation is a continuous long-only momentum strategy. It is
designed for the case where a scanner can identify upward-trending stocks at
1-minute resolution from premarket through after-hours.

## Purpose

The strategy keeps scanning throughout the session instead of making a single
setup decision. Every completed `1m` bar, it scores all symbols that updated on
that bar, filters to symbols whose entry state is open, ranks them, and tries to
hold the best `top_n` opportunities. When a new candidate is materially stronger
than a weak current position, the strategy closes the weak position and rotates
capital into the stronger name.

## Data Requirements

- Event bars: `1m`
- Event feature groups: `core`, `momentum`, `session`, `volume_liquidity`
- Context feature groups: `5m` `momentum`
- Daily context: none

The strategy expects provider-built data. It does not calculate indicators from
raw bars. The 1-minute scanner uses provider momentum features such as MACD,
TEMA, and VWAP. The entry-open gate uses provider-built 5-minute MACD and TEMA
context that is joined to each 1-minute bar only after the 5-minute bar is
complete.

## Trading Window

The default window is 04:00 to 20:00 exchange time:

```text
trading_start_minute = 240
trading_end_minute = 1200
```

This lets the same logic operate across premarket, regular session, and
after-hours as long as provider data exists for those bars.

## Live Momentum Scanner

Each ticker has a live state that is updated every completed 1-minute bar. The
scanner calculates:

```text
session_return_bps = (close / first_seen_close - 1) * 10000
recent_return_bps = (close / close_N_minutes_ago - 1) * 10000
macd_pressure_bps = sum(macd_hist_1m / close * 10000)
tema_spread_bps = (tema9_1m - tema20_1m) / close * 10000
volume_score = log1p(recent_dollar_volume / min_recent_dollar_volume) * 10
overextension_penalty = max(0, vwap_distance_bps - max_vwap_extension_bps)
```

The final scanner score is:

```text
momentum_score =
  session_return_weight * session_return_bps
+ recent_return_weight * recent_return_bps
+ macd_pressure_weight * macd_pressure_bps
+ tema_spread_weight * tema_spread_bps
+ volume_weight * volume_score
- overextension_penalty_weight * overextension_penalty
```

The score changes every minute as price, MACD pressure, TEMA spread, VWAP
distance, and liquidity change.

## Entry-Open Gate

A symbol can be ranked for entry only when all of the following are true:

- price is between `min_price` and `max_price`
- session dollar volume is at least `min_session_dollar_volume`
- recent rolling liquidity passes `min_recent_dollar_volume`,
  `min_recent_volume`, and `min_recent_transactions`
- recent return is at least `min_recent_return_bps`
- momentum score is at least `min_momentum_score`
- when enabled, price is above VWAP
- 5-minute MACD is open:

```text
macd_ready_5m
macd_line_5m > macd_signal_5m
macd_line_5m > 0
macd_hist_5m > 0
```

- 5-minute TEMA is open:

```text
tema_ready_5m
tema9_5m > tema20_5m + tema_entry_buffer
```

The scanner may track many symbols, but only entry-open symbols compete for
capital.

## Rank-Based Allocation

At each minute, entry-open candidates are sorted by `momentum_score`. The top
`top_n` candidates receive rank weights:

```text
rank_weight = exp(-rank_decay * (rank - 1))
```

The target capital for a rank is:

```text
target_capital = total_equity * max_gross_exposure_pct * normalized_rank_weight
```

The strategy also caps size by risk:

```text
quantity_by_risk = total_equity * risk_per_trade_pct / initial_R
```

The final quantity is the minimum of target-capital sizing, cash sizing, and
risk sizing.

## Rotation

If open slots are available, the strategy opens the highest-ranked candidates
that are not already held.

If all slots are full, the strategy compares new high-ranked candidates with
current positions. A rotation can happen when:

- the new candidate is not already held
- the new candidate score exceeds the weakest held score by
  `replacement_score_buffer`
- the weak position has been held at least `rotation_min_hold_minutes`
- the weak position is not progressing, defined as score decay or weak R
  progress

The strategy closes the weak position first, then submits a buy for the stronger
candidate on the same completed 1-minute bar.

This first version performs full-position rotation into new names. It avoids
adding to existing positions because the current portfolio model opens a new
position record rather than averaging into an existing one.

## Stops and Exits

Each long position receives an initial risk unit `R`:

```text
R = max(entry_price * initial_risk_pct, min_initial_risk_dollars)
R = min(R, entry_price * max_initial_risk_pct)
initial_stop = entry_price - R
```

The trailing stop starts at `initial_stop`. Once maximum open profit reaches
`trailing_activation_r`, the stop trails by R units:

```text
trailing_stop = max(
  current_stop,
  entry_price + trailing_lock_r * R,
  max_price - trailing_giveback_r * R
)
```

The strategy exits when:

- price closes at or below the trailing stop
- 5-minute TEMA closes
- 5-minute MACD closes
- a stronger candidate rotates into the portfolio
- the backtest reaches the end of the day

## Observability

The strategy records scanner rows, rankings, entry intents, exits, rotations,
and rejection reasons. Important scanner columns include:

- `momentum_score`
- `entry_open`
- `entry_state`
- `session_return_bps`
- `recent_return_bps`
- `cumulative_macd_pressure_bps`
- `tema_spread_bps`
- `vwap_distance_bps`
- `recent_dollar_volume`
- `session_dollar_volume`
- `rank`

These fields are meant to make it clear why a symbol was ranked, skipped,
opened, held, or rotated out.
