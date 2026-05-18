# Long Momentum v6

Long Momentum v6 is an oracle-supervised benchmark strategy. It intentionally
uses provider-built `supervision_oracle` labels, so it is a lookahead strategy
for supervision validation and hyperparameter experiments. It is not a
live-safe production strategy.

## Data Contract

The strategy requires provider artifacts for:

- `bars` on `1m`
- feature groups: `core`, `momentum`, `session`, `volume_liquidity`
- supervision group: `oracle`

Build oracle supervision before running v6:

```text
Build Data -> Build oracle supervision
```

## Entry

A symbol is eligible when all configured gates pass:

- price is between `min_price` and `max_price`
- `oracle_long_enter_signal == true`
- `oracle_long_supervision_score >= min_oracle_entry_score`
- `long_expected_profit >= min_oracle_expected_profit`
- `abs(min(long_drawdown_before_best, 0)) <= max_oracle_drawdown_before_best`
- spread passes the configured spread limit when `require_spread_ok` is true
- expected profit is greater than estimated round-trip per-share fee drag when
  `require_positive_expected_profit_after_fees` is true

Eligible rows are ranked by oracle score, expected profit, and recent dollar
volume. `max_active_positions` controls how many symbols can be held at once.

## Exit

The preferred exit is the oracle best-horizon label:

- `oracle_long_exit_signal == true`
- `oracle_long_supervision_score >= min_oracle_exit_score`
- `long_exit_realized_profit >= min_oracle_exit_realized_profit`

At an oracle exit bar, v6 submits a same-bar limit sell at `best_exit_price`.
This deliberately uses the future-informed label to test whether the
supervision can identify the top of the move.

The strategy can also exit when short supervision becomes strong:

- `oracle_short_enter_signal == true` or `oracle_short_supervision == true`
- `oracle_short_supervision_score >= short_supervision_exit_score`

When no oracle exit is present, v6 keeps an expiring protective stop. The stop
moves to breakeven after `breakeven_activation_return` and trails after
`trail_activation_return`.

## Hyperparameters

The key tuning parameters are:

- `min_oracle_entry_score`
- `min_oracle_expected_profit`
- `max_oracle_drawdown_before_best`
- `min_oracle_exit_score`
- `min_oracle_exit_realized_profit`
- `short_supervision_exit_score`
- `max_active_positions`
- `capital_fraction_per_trade`
- `max_initial_risk_pct`
- `breakeven_activation_return`
- `trail_activation_return`
- `trail_buffer_pct`

For May 1, 2024 experiments, start with the defaults and then tighten entry
score/profit thresholds if the strategy takes too many overlapping labels.
