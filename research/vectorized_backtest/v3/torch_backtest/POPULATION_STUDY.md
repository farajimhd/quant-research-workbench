# Risk and population comparison

The previous campaign is stopped. This experiment uses new immutable source,
random initial tensors and training data only. It does not read the six held-out
evaluation sessions and does not claim to measure distance to a global optimum.

## Objective and execution boundaries

For independent daily accounts starting with $10,000, maximize:

`mean(return) - 0.25 mean(drawdown / initial_cash)`
`- 0.25 sqrt(mean(min(return, 0)^2))`
`- 0.10 mean(stop_risk_hours) - 0.002 mean(capital_hours)`.

Stop-risk hours integrate `quantity * max(average_entry - current_stop, 0)`;
capital hours integrate marked invested dollars. Both divide dollar-seconds by
`initial_cash * 3600`. Costs accrue from entry, with no five-minute grace period.
Profitable upside is no longer penalized as return dispersion. Costs and gross
return are saved separately in every generation receipt.

The three-second minimum discretionary hold remains; protective and terminal
exits are exempt. After 3,600 seconds the engine submits a market exit for the
following interval. The approximate broker can delay actual completion through
limited liquidity. There is no fabricated fill at the timeout boundary.

New orders and pending parents share a stop-risk admission budget of 2% of
current equity. Reservation uses the worst permitted entry price. Repricing
rechecks that budget. An adverse market move can put an existing position above
an admission limit; this is not a guaranteed realized-loss bound. Gaps, fees and
limited fills can exceed the quoted stop loss. Shared completed-interval volume
participation remains 25%. Causal decision and fill timestamps remain governed
by TIME_AND_SEARCH_CONTRACT.md.

At least one filled ticker batch is required on every selection session, and
terminal exposure must be flat. Accounts reset between dates. Legacy activity
and five-minute holding penalties default to zero. Clause-wise crossover and
bounded local numeric mutations preserve atomic expression semantics. A bounded
training-only archive retains profit and objective leaders for inspection; it
does not inject them into selection.

## Fixed experiment grid

| Population | Generations | Candidate evaluations per seed |
|---:|---:|---:|
| 64 | 24 | 1,536 |
| 128 | 12 | 1,536 |
| 192 | 8 | 1,536 |
| 256 | 6 | 1,536 |
| 512 | 3 | 1,536 |

Seeds are 20261004, 20261005 and 20261006. Selection uses July 30, August 18
and September 3, 2026 premarket. Each generation evaluates every candidate on
all three dates. Initial populations share a fully random prefix within each
seed. All fifteen winners are frozen before assessment on all thirty training
dates. Report feasibility counts, objective distributions and gap to the best
observed feasible finalist; three seeds provide limited statistical evidence.

Timing uses three complete September 3 replays per population. Compilation is
reported separately from median prepared replay, with peak GPU memory and
throughput. The first 64 accounts must match the B=64 metrics and complete fill
ledger for every population. A memory or parity failure stops the study rather
than silently reducing the requested batch.

The workstation `study` launcher imports existing sealed CPU tapes into a new
runtime job. This explicit migration permits only the execution grammar version
and the new risk/holding settings to differ. Input grammar, source certificate,
session, preparation algorithm and blob hash must still match. Original dataset
bytes and creator receipts remain unchanged. Checkpoints, generation receipts,
timing records and finalist hashes belong to the new job, not the old campaign.

The visible progress panel identifies population, seed, generation, objective
components and financial metrics. Restart uses the same immutable source and
study identity. Evaluation results must never influence this comparison.
