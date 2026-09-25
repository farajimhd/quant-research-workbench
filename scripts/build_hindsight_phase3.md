# Phase 3 long-only portfolio teacher

`build_hindsight_phase3.py` consumes one completed, certified Phase 2 V6
price-action dataset. It reads every market second through `MarketValues`,
including the complete holding/closing grid and sparse eligible openings.
The Phase 2 root winner tables are not used. Missing opening rows forbid a new
entry; they do not forbid holding or closing an existing position.

```powershell
python -B scripts/build_hindsight_phase3.py --phase2 <completed-phase2-root>
```

The default search is **long-only**, starts with $10,000, buys fixed $2,500
capital units (fractional shares), holds at most four lots, and permits up to
two orders per second. These are explicit action-grid constraints. Profits
return to cash and may fund later purchases, subject to the four-lot cap.
`--initial-cash`, `--allocation-step`, `--max-lots`, and
`--max-orders-per-second` change the grid. A buy requires Phase 2 `can_open`
and `open_value_available`; a sale requires `can_close`. Both entry and sale
use Phase 2's current completed-price reference with its configured per-share
cost. No quoted spread, fill probability, slippage, or broker margin is
inferred. At 19:58 ET all lots must be sold, including losing lots. A bounded
canary may use `--start-second` and `--end-second`; its last second is forced
flat and is explicitly recorded as a segment rather than the full session.

The full-session objective is maximum terminal cash within the declared action
grid. Immediate reward is the change in marked account equity, including
transaction costs. Summed rewards equal terminal cash minus initial cash.
Phase 2's discounted local future values are used **only to order the search**;
they are never summed as realized rewards. The default search retains 16
portfolio states and the top three opening tickers per second, so its result
is labeled `approximate_beam`. Set `--beam-width 0 --max-candidates 0` to
disable those two heuristics; the result is then proven optimal within the
declared lot and order grid if it completes. `--max-frontier` fails closed if
an unbounded frontier grows past the configured limit. Identical holdings with
less cash are removed by exact dominance, and a flat-cash path is preserved in
bounded search. Secondary ties prefer fewer orders.

Outputs are immutable under `runtime/hindsight-phase3/<date>/<plan-hash>/`:

- `plan.json`: Phase 2 source hashes, search grid, optimality status, session
  boundary, and causal-observation contract.
- `search.sqlite3`: restart checkpoint with the selected state frontier and
  ancestry. A stopped or interrupted run resumes from the last committed
  60-second block. `progress.json` reports processed seconds, frontier size,
  candidate and beam pruning, and elapsed time.
- `trajectory.parquet`: one row at **every second**, including waits, with
  pre/post cash and equity, action legs, realized P&L, immediate reward,
  positions after, return-to-go from that state to terminal cash, a next-time
  pointer, terminal `done` flag, and the search optimality status.
- `positions_after.parquet`: a normalized long table of held lots after each
  decision. Empty seconds have no rows here; `trajectory.parquet` remains dense.
- `complete.json`: hashes and row counts of the final outputs, terminal profit,
  profit-to-initial-cash ratio, and search statistics. It is written last.

The trajectory is a teacher demonstration for its **visited states**. It does
not claim a value or optimal action for every possible portfolio state, and the
bounded result is not a proven global optimum. For offline RL, join the labels
to a separately whitelisted causal observation stream (current completed bars,
indicators, and account state). Do not expose Phase 1 future targets or Phase 2
hindsight values as model inputs. Phase 2 `can_open` also depends on the
hindsight-selected MACD episode; it is an oracle search constraint, **not** a
causal action mask for a deployed agent. Build any deployed action mask from
available market and broker facts separately. The trajectory can supply
`(observation, action, reward, next_observation, done)` after this causal join;
the final row is terminal. The `return_to_go` column is a label, not an
observation. A full-universe build needs separate measured throughput and
search-quality acceptance before these labels are used as a gold standard.

## Validation canary

The completed 2026-08-21 AAPL/SUGP Phase 2 V6 canary produced 57,481 ordered,
unique trajectory seconds from 04:00 through 19:58 ET: 53,028 waits and 4,453
seconds with trades. The final state was flat, exactly one row had `done=true`,
and the sum of one-step rewards equaled terminal profit. Independent replay of
all trade legs against the Phase 2 entry/close prices reproduced terminal cash
of $85,665.63 from $10,000 initial cash. This is a **zero-cost, approximate
price-action oracle** on two tickers; the large profit is not an executable
return estimate. A separate six-second unpruned canary returned a certified
within-grid optimum. The full-day run was stopped after a committed checkpoint
and resumed successfully; 70 focused Phase 1–3 tests passed. Full-market
throughput and search quality are not established.
