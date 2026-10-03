# Strategy 43 implementation status

Status: **native execution and normalized journal bridge implemented and tested;
historical publication and app routing remain incomplete**.
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

## Native execution and journal

The concrete native port uses shared TradingRuntime, Portfolio, OMS and the
existing simulated broker. It submits fifteen independently reserved parent
orders, each with one full-size stop and target. Per-leg stop amendments retain
the current OCA-reduced quantity and affect no sibling. Fill facts come from
actual group-owned 100 ms executions, including dedicated leg liquidations;
ticker holdings are checked against their summed inventories. Terminal
qualification rejects residual holdings or working orders.

The independent in-memory journal, bounded V4 projector and publisher reuse
native typed rows, mandatory command lineage, writer receipts and Keeper
fences. Sources are metadata-free normalized intents; no Strategy 1 BOS rows,
SQLite journal or serialized coordinator substitute is created. The cold reader
reconstructs typed entry lineage; full historical cold-review qualification is
still required. The native order journal requires ten-decimal precision: buy
caps and stops floor, targets ceil, while source Float64 facts remain unchanged.

Historical 1-42 behavior hashes remain checked after removing only three exact
reviewed Strategy 43 extensions: identity recognition, mandatory command
lineage inclusion, and independent cold-order dispatch. The extension adapter
and its lineage implementation have pinned AST hashes. Strategy 42 certifies
with current source receipt
`753062b970ef4d3748a1f93fac8d7f5fef52c66efa8b17823f714eebee4780c5`.
This is a changed technical source receipt, not a replacement historical policy.

## Historical sources and operator setup

Producer-owned Polars features preserve the prior confirmed swing, prior
attention and current ten-change movement clocks. Three normalized tables bind
features, selected listing/signal identities and coverage-last seals. The
publisher resumes only an exact unsealed prefix, never repairs a sealed product,
and never deletes a successor's Keeper lock after session expiry.

The SELECT-only reader verifies market, identity and causal V7 parent plans,
exact IEEE 754 feature hashes, dense clocks, population proof and attempts.
It retains first-signal facts for rejected candidates and loads complete
histories only for active tickers, rechecking their seals.

The operator launcher is
`scripts/clickhouse/provision_strategy_forty_three_facts.py`. Its default is a
plan with no mutations. Explicit application creates only the three versioned
SSD tables, a dedicated producer with six SELECT/INSERT privileges and three
SELECT grants for the existing Backtest reader. Credentials stay in workstation
secrets. The user approved this scope after asking what the tables contain.
Application succeeded on the isolated workstation deployment of pushed commit
`5dcfd357050c84bedccd29ddf7a02b12a9f82727`. All three SSD layouts and the
exact six producer privileges were verified; the real Backtest reader can
SELECT all three tables. They currently contain zero rows.

A producer-only entry-point V7 witness prototype streams the previous certified
checkpoint through the entry boundary using the native FixedV7Stream. It is not
a published interval product or an admitted release input. The current release
still pins the existing normalized full-session causal V7 interval contract.

## Validation and remaining work

155 focused/regression tests passed before provisioning. Subsequent focused
checks passed for bounded producer extraction, late-signal population retention,
pinned empty-session cutoff and the Keeper-protected V7 campaign. The native integration test uses actual
Portfolio, OMS, planner and simulated broker: fifteen parents produce 45 orders,
fill on a later 100 ms bucket, liquidate independently, and finish with zero
holdings/working orders and a fenced sequence. Its writer transport is a test
fixture; this is not an actual ClickHouse or historical financial Backtest.

Real workstation input inspection independently reproduced 6,066 tradable
listings and 833 first-signal candidates from 5,949 episode starts. All 6,066
native identities and 833 prior-session structural seeds certify. The missing-only
campaign published 435 derivatives and certified all 833 causal V7 interval units. A
read-only AAPG full-session native derivation took 0.112 seconds for 24 valid
seconds and 57 intervals; this single sparse ticker is not a throughput claim.
`scripts/clickhouse/publish_strategy_forty_three_v7.py` prepares only missing
units, defaults to check-only, preserves covered seals, uses bounded workers
and retains restart receipts outside source. The real campaign completed successfully.
Producer feature extraction uses Polars over one certified
listing/session and does not fill missing prices.

All 833 dense historical units are now published and fully certified. The immutable
source token is `27cea50921e8be48e5451014cac524dfd291a340172bee5f9fdfbf63cd4fbcd6`.
Arrow inserts preserve exact Float64 bits; ordered 100ms sums remove parallel
aggregation ambiguity. Failed unsealed rows remain retained and excluded.

Independent configuration certification/publication, app preflight/controller
dispatch, and a bounded active-ticker session runner are implemented. The route
is limited to the approved September 3 full premarket comparison with $10,000.
It supports play, pause and stop; public resume and event navigation are unqualified.
119 focused/regression tests passed; four admission/controller checks passed after
explicitly reading the 1s trade count that the native sparse reader omits.

Remaining: verify deployment and real inputs, publish the configuration, qualify
the complete real app route and cold journal review, then compare the actual financial
results. The source reader requires independently
certified identity and causal V7 plans, not arbitrary strings as authority.
Do not append 43 to the old executor allowlists or inherit its trading rules.
Strategy 43 is not yet selectable, and no app historical run was performed.
