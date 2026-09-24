# Greedy fractional action-value labels

For `hindsight-phase1-arte-price-action-v2`, `v3`, and `v4` inputs, the compiler explicitly uses
`valuation_basis=price_action`: current completed trade close and future swing
high/low prices, with no quotes or spread. Phase 2's optional per-share cost is
still applied, and its fractional allocation/discount formulas are unchanged.
Here `can_open` requires both an available reference price and an active MACD
entry; `can_close` requires an available reference price. Neither claims
executable liquidity. The quote-based descriptions below apply to legacy Phase 1 datasets.
V3 adds forced liquidation at 19:58 ET. V4 also carries the selected MACD
episode entry time: `can_open` stays false while that entry is in the future,
and when no active swing remains. Holding values are still calculated for an
existing position when a future target or terminal liquidation price exists.
Terminal coefficients cannot open new
positions, and the state evaluator requires all existing holdings to be closed.
Flat-state tables retain a terminal flag and select wait after liquidation.
Mandatory full liquidation can settle with negative cash: the evaluator preserves
the loss and reports `settlement_status=insolvent` and `cash_deficit`. It does not
inject cash. Other actions remain subject to the existing funding constraints.

For both phases across multiple dates, see [the dataset campaign](build_hindsight_dataset.md).
The standalone `build` also accepts `--workers`; compilation is bounded and
parallel, while market reduction remains deterministic. Per-listing summaries
use direct column comparisons on their aligned grids.

This is the first **local greedy** training-label validator. It reads a completed
Phase 1 dataset offline. It does not run backward dynamic programming, simulate
future reallocations, fit a model, or place orders. Existing Phase 1 is unchanged.

## Run

From the repository's Python environment:

```powershell
python -B scripts/build_hindsight_greedy.py example
python -B scripts/build_hindsight_greedy.py build --phase1 D:\TradingML\runtimes\hindsight-phase1\2026-08-21\all\default
```

The example reproduces the B/D action-size table without any market services.
The build requires `complete.json` from Phase 1. Substitute a completed canary
directory to validate a subset; its canary scope is retained. Missing completion,
source hashes, exact dense time grids or listing identity fail explicitly.

For arte price-action Phase 1, Phase 2 V6 defaults to requiring at least 20,000
shares and 11 trades in the last 60 completed one-second bars before opening a
new position. Set `--min-volume-60s` and `--min-trades-60s` to change either
threshold; set both to zero to disable the gate. Both thresholds must pass.
Before a full minute has elapsed, the window contains only completed session
seconds since 04:00 ET. Phase 2 computes it from Phase 1's persisted `volume`
and `trades`; there is no lookahead or Phase 1 rebuild. Missing or invalid
activity fails the filtered build. Legacy quote-based Phase 1 requires both
thresholds explicitly set to zero. This is a price-action activity filter, not
a guarantee of executable liquidity. It affects `can_open` and the sparse
opening table; held positions retain their hold/close values and forced exit.
The holding grid records the observed 60-second counts and
`liquidity_below_threshold` when an entry is filtered. The completion marker
and terminal output report the number of liquidity-gated rows.

Defaults: fractional shares, `--half-life-bars 30` **MACD bars**, and
`--cost-per-share 0` per transaction. Costs, if configured, are incorporated once
into entry and exit prices. A positive cost uses ask+cost / bid-cost for a long,
and bid-cost / ask+cost for a short. Prices already include the bid/ask spread.
Time remains exact to microseconds when discounting Phase 1 targets.
The discount is `0.5 ** (remaining_seconds / (30 * macd_resolution_seconds))`:
30 seconds at 1-second MACD resolution, or 150 seconds at 5-second resolution.
The compiler reads the resolution from the Phase 1 plan; supported older plans
without this field use their original 1-second resolution. `--gamma` remains an
explicit per-second override, mutually exclusive with `--half-life-bars`.
Plans record the resolved policy and effective gamma. The illustrative `example`
command retains its explicit 0.99 gamma for reproducibility.

Three order modes are generated: `long`, `short`, and `long_short`. They share
coefficients instead of storing duplicate market data. The combined mode allows
long and short positions in different tickers and a close-then-reverse action,
but no simultaneous long and short in the same ticker.

Shorts use a **synthetic 100% reserve of bid+cost per share**, with short-sale
proceeds locked. Covering releases the reserved capital plus realized P&L.
This is an explicit comparison convention, not a broker margin/borrow model.
Capacity, borrow availability, nonlinear impact and position limits are not
established by this first validator. Linear fractional values are counterfactual
supervision, not proof that arbitrary quantities could have filled.

## Every size without an infinite table

At one timestamp, let `q_i` be existing shares and `d_i` the proposed change.
Positive changes enter/add; negative changes reduce/exit. Zero is hold/wait.

The user's funding rule is applied once, before comparing alternatives:

```text
open_cost = sum(existing_quantity * original_all_in_capital_per_share)
budget = max(open_cost, maximum current all_in_candidate_capital_per_share)
available_cash = budget - open_cost
```

The maximum uses **current quote eligibility**, including opportunities whose
future label is unavailable; it never uses future profit to set the budget.
Selling releases actual close proceeds (or short reserve plus P&L). Funding is
not recalculated during an action. Separate state evaluations use the rule again;
this synthetic rule is not an account-equity simulation.

For each direction and ticker, persist two coefficients:

```text
new_value = profit_per_new_share * gamma ** remaining_hold_seconds
hold_value = future_profit_above_closing_now_per_held_share * gamma ** remaining_hold_seconds

baseline = sum(q_i * hold_value_i)
action_increment = sum(max(d_i, 0) * new_value_i + min(d_i, 0) * hold_value_i)
total_future_value = baseline + action_increment
```

Retained shares are not charged the entry spread again. Profit accrued before
the decision is excluded from future value; realized P&L from reductions is
reported separately. A short uses the opposite price-difference sign.
`open_value_available` and `hold_value_available` are independent: inability to
open a new position does not erase a known value for an existing holding.
Legacy `value_available` continues to describe opening/ranking availability.
After-cost negative target proceeds remain known losses, not missing outcomes.

These piecewise-linear formulas represent **every fractional joint action**,
including arbitrary partial reductions funding multiple purchases. No list of
sampled sizes replaces them. The JSON evaluator calculates any requested vector
and rejects overselling, insufficient cash, invalid quotes and same-ticker
hedging. Unknown future outcomes remain null, not zero. Negative outcomes are
retained. Gamma discounts losses too; it is not a separate risk penalty.

## Outputs and meaning

Outputs belong under the configured runtime root:

```text
hindsight-greedy/<date>/<configuration-and-source-hash>/
  plan.json
  market_hold_values.parquet
  market_open_values.parquet
  listings/<identity>/coefficients.parquet
  listings/<identity>/{long,short,long_short}.parquet
  listings/<identity>/ready.json
  {long,short,long_short}.parquet
  progress.json, summary.json, complete.json
```

The market-wide Phase 2 values use two time-ordered Parquet tables. Their logical
axes are MACD resolution, decision time, stable `listing_index` (the order of
`plan.selected`), and direction. `market_hold_values.parquet` has every listing
and both directions at each of the 57,601 decision seconds, retaining holding,
closing, target, status, and availability values even when an entry is forbidden.
`market_open_values.parquet` contains only rows where `can_open=true`, retaining
all opening prices, capital, profit, and value coefficients. Missing from the
opening table means opening is forbidden, **not** that holding/closing data is
missing or that a market-wide policy must wait. `MarketValues` verifies both
file hashes and row counts, reads their time slices using row-group statistics,
and reconstructs the full action table. On non-opening rows, opening prices and
capital are null in the reconstructed table. The `evaluate` command uses this
reader. Row groups target about 30 decision seconds; external-sort spill stays
under the runtime root. The completion marker certifies both files.
The per-listing files are restart checkpoints; a consumer comparing or splitting
capital among tickers should read the root tensor, not just the winners below.

The three root tables give the maximum comparison price and the best **flat-state**
local action at every second. With fractional size and linear costs, allocating
the flat budget to the greatest positive discounted value per dollar is exact
for this local objective. Ties resolve lexically. Cash/wait has zero value.
If any currently eligible candidate has an unavailable value, the winning label
is marked unavailable rather than silently ranking only the known subset.
The diagnostic best-known score is retained separately.

**These root winners are not decisions for an already invested portfolio.**
Use the coefficients and the explicit state evaluator for that portfolio.
No complete portfolio trajectory, all-state winner table, or future-policy value
is claimed. An infinite fractional action table is a function plus constraints.

Input/context columns include current quotes, position quantities and original
costs. Target prices/times, remaining duration, discount, hindsight availability
and action values are **label-side information**, not causal model features.

## Inspect a real state and its proposed actions

Save a request JSON under the runtime root. `time_us` is a UTC microsecond
decision timestamp present in the dataset. Aggregate each position to one
cost-basis row. This example asks for hold, add, reduce and exit values:

```json
{
  "time_us": 1787299864000000,
  "positions": [
    {"ticker": "SUGP", "side": "long", "quantity": 1,
     "entry_price": 4.0, "capital_per_share": 4.0}
  ],
  "actions": [
    {"name": "Hold", "changes": {}},
    {"name": "Add half", "changes": {"SUGP:long": 0.5}},
    {"name": "Reduce half", "changes": {"SUGP:long": -0.5}},
    {"name": "Exit", "changes": {"SUGP:long": -1}}
  ]
}
```

The position is a hypothetical input, not an assertion of a historical fill.
Add other ticker changes to the same action to evaluate a joint reallocation.

```powershell
python -B scripts/build_hindsight_greedy.py evaluate --dataset <printed-build-directory> --request <request-json> --mode long
```

The terminal displays transaction sizes, total raw/discounted future profit,
and the difference versus holding. The full persisted JSON includes feasibility,
remaining cash, resulting holdings, missing-value reasons and realized P&L.

## Complexity, interruption and validation

For N listings and T seconds, coefficient creation and incremental market
aggregation are O(N*T). The holding grid and sparse opening table each require
an external time sort;
its worst-case comparison work is O(N*T*log(N*T)) and may spill to runtime disk.
Working memory remains bounded by one listing plus streaming sort buffers and
small manifests; no full N-by-T frame is loaded.
Disk output is O(N*T). There is no cross-product of tickers or action sizes.
An in-memory action evaluation costs O(H+K), where H is held positions and K is
changed positions. Reading and verifying a whole market snapshot costs more
I/O; the inspection command is not a production latency path. Full-market sort
time and temporary-disk requirements still need measurement.

Immutable plans include source and code hashes. Each listing is published with
file checksums and can be reused. Rerunning retries failed listings, never skips
them. Ctrl+C or a `STOP` file in the output directory stops after the active
listing. Remove `STOP` and rerun to resume. Different configuration/code produces
a new output directory. Exit code 2 indicates failure or interruption; no complete
dataset is published until all selected listings succeed.

Tests compare arbitrary fractional changes with independent cash-flow arithmetic,
an exhaustive small allocation grid, short reserve accounting, spread/cost
handling, exact discount duration, three modes, missing labels, and market
reduction parity. Full-market runtime must be measured separately from a canary.
