# Strategy 43 implementation status

Status: **rules implemented and tested; app integration and publication incomplete**.
This document does not authorize launch or claim that Strategy 43 is selectable.

## Selected research configuration

The corrected September 3, 2026 premarket campaign ranked candidate
`3da6e376d107a979c18e96332386b0401d0797a472ce6bcf6226f1c9ced766b2`
first among fully exited configurations. Its configuration is signal entry,
15 independent orders, decreasing logarithmic sizing, structural targets,
confirmed swing stop, adaptive trailing and no replacement. Three other
configurations tie its score; this implementation uses the first ranked
structural, replacement-disabled configuration without changing that selection.

The reconciled source commit is
`273124420277b4a7f80b5900c78c94b1d4e0d17b`. The campaign is under the
workstation runtime root at
`vectorized_backtest/torch_backtest_v2/jobs/156e49cd57fd48e295b64acbc7aed207`.
Its `FINAL_REPORT.json` records net P&L of -371.26932136827236 on 10,000
initial cash, fees 163.87, two ticker acquisition batches, 16,387 acquired
shares and 390 fill rows. This is the least loss among eligible runs, not
evidence of profitability. The full grid has 1,046 eligible losing runs and
3,274 ineligible terminal-exposure runs.

The user explicitly selected **1-second strategy decisions with the app's
100 ms broker fills**. The research execution used completed 1-second broker
intervals, so execution and P&L equality are not promised. The comparison must
identify differences in decision facts, submissions, fill clocks, quantities,
prices, commissions, stop amendments and terminal exposure separately.

## Implemented rules

`src/trading_runtime/strategy_forty_three_rules.py` has no research dependency,
market builder, broker mutation, persistence or registration side effect.
It supplies typed source inputs, exact selection identity, immediate-signal
entry geometry, incoming ticker score, conservative quantity proposals,
15 separate single-slice native `StrategyIntent` objects, the adaptive stop
formula, and the protective fee reserve. Portfolio remains sizing/admission
authority; OMS and broker remain order and fill authorities.

The signal mode admits only the signal's rounded completed second. A failed
liquidity or geometry check does not defer it to a later second. This mode
does not require an open MACD or price above VWAP. Every entry still needs
a fresh executable quote, spread at most 1%, completed dollar volume at least
1,000 and at least five trades. The initial stop uses the previously confirmed
2-left/2-right 1-second swing low minus 0.01. It cannot use a swing first
confirmed by the current decision's bar because the research updates that
history after deciding.

The 15 nearest resistance lower prices above ask are selected at the current
causal structural boundary. Each target is its selected lower price minus
0.01 and must exceed ask times 1.01. Distinct level identities can share a
lower price: the current research preparation does not deduplicate those
prices, and the rules preserve that behavior. Targets freeze at submission.
Initial quantity weights are proportional to `1 / log(1 + rank)`. Free cash
must already exclude pending acquisitions and protective commissions. The
new batch additionally reserves 75 in minimum entry/exit fees, including
the research reserve for an unused rotation exit role.

Adaptive trailing uses peak price strictly after the first-filled 1-second
bucket and a producer-owned mean of ten consecutive absolute close changes.
No missing second may be carried forward to complete that window. After
ten elapsed seconds from the actual native broker fill, the proposed stop is
`peak - max(3 * mean_change, 0.01 * average_entry)`. It is capped at fresh
bid minus 0.01 and never decreases. Each amendment must affect one leg only.
The session lock is acquired at submission of the one 15-order batch and
survives rejection, cancellation, partial fill and all exits.

## Remaining runnable integration

The existing numbered 1–42 path cannot be reused by merely appending 43 to
its allowlists. It would inherit other entry rules, the Strategy 1 financial
view and ticker-scoped management. Its source certificates pin shared files
and AST behavior. Those proofs must continue to pass without accepting
arbitrary new source hashes or changing sealed older trading behavior.

Required before publication:

1. A separate Strategy 43 fixed executor, immutable release manifest and
   normalized configuration publication, with 1-second decision and 100 ms
   execution clocks explicitly distinguished.
2. A producer-owned, coverage-last fact product for signal admission,
   completed swing availability and ten-second movement evidence. Bind the
   full pinned tradable listing population and explicit LGHL exclusion.
3. General-population causal V7 authority: prior-session checkpoints available
   before session open, then completed intraday 1-second updates. Reuse a
   certified normalized interval derivative where its population and product
   policy match; never use the tested session's end-of-day checkpoint or invoke
   a private V7 calculation inside Strategy 43/Backtest.
4. One deterministic shared Portfolio admission transaction for the selected
   batch, no independent shadow account, per-leg OMS protection/partial-fill
   state and session submission lock. Native broker holdings may aggregate by
   ticker; that is distinct from the 15 independent bracket/order groups.
5. Normalized source evidence and leg state in the Keeper-fenced ClickHouse
   journal, including recoverable submission outcomes and group-specific
   stop amendments. No metadata blob or SQLite journal substitute.
6. App listing, preflight and controller routing; completed-input causality,
   missing-coverage, shared cash/liquidity, partial-fill/protection and restart
   checks, plus a bounded real app launch-to-terminal qualification.

No app backtest, Strategy 43 publication, market-product write, workstation
deployment or modification of strategies 1–42 was performed in this stage.

## Validation

The initial focused rules suite passed 29 tests, including the app's actual
`IbkrStrategyOrderPlanner`: each of the 15 native intents produces one buy
parent, one full-quantity stop and one full-quantity limit target. This proves
the intent/planner representation, not Portfolio admission, broker execution,
durable journaling, app selection or complete strategy readiness.
