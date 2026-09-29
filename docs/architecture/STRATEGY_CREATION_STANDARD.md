# Strategy creation and numbering standard

Status: binding creation policy. Strategy 1 has an immutable, selectable
**100 ms Backtest-only** release. Its legacy Candidate 350 source identity is
not a second user-facing version. Strategy 1 is not approved for live order
admission or as evidence of profitability.

## One trading identity

`strategy_number` is the sole user-facing version of a complete trading
behavior. It includes activation, entry and re-entry, additions, exits,
stop/target selection and precedence, sizing, permissions, input clocks, and
evaluation interval. Once a numbered release is sealed or used for a run,
**any behavioral change requires the next number**. Do not edit or replace the
old release, silently redirect its number, or reuse a rejected number.
Historical runs retain their original number and content digest.

Configuration, code, QMD product, broker, schema, rule-set, and run revisions
are technical identities. Pin them in the release and run, but do not present
them as competing Strategy versions. A content digest is an integrity seal,
not a cryptographic signature unless a signing authority actually exists.

## Input and execution authority

Define every input as a typed field with product, resolution, event/observed
clock, completed-boundary availability, freshness, and missing-data behavior.
QMD or another producer-owned pipeline publishes bars, indicators, liquidity,
and structural products. A Strategy only consumes them. For example, a new
one-minute MACD rule first requires a certified one-minute QMD indicator; a
Strategy may not calculate or approximate that MACD from trades or other bars.

Backtest SELECTs only certified immutable `arte` products with a market
principal that cannot INSERT, ALTER, CREATE, repair, or materialize them.
Missing coverage fails preflight before entering the Backtest workspace.
Expensive reusable derivatives may be published once by a separate producer
as normalized, typed, coverage-sealed `arte` tables; no JSON/blob substitute.

The default evaluation interval is completed 100 ms boundaries. Event mode
must be declared explicitly. A completed higher-timeframe indicator is not
available until its own boundary closes; a forming value is a distinct
producer product. Sparse and quote-only buckets must not become invented
trades or candles. The fixed broker consumes completed persisted liquidity
buckets without fabricating intra-bucket order. Pin the fill contract,
activation delay, spread/quote freshness, liquidity budgets, and ambiguous
stop/target policy independently of the interval.

## Reusable rules and bounded state

Compile shared versioned scanner, Watchlist, Signal Stream, and Strategy
predicates over bounded typed columnar batches. Emit candidate masks, compact
reason codes, and exact causal boundary references. Do not evaluate every
rejected row through a Python strategy state machine or repeatedly copy rich
row dictionaries. A decision-changing rule-set revision also requires a new
number for each published consuming Strategy.

Only survivors enter sequential causal activation, position, stop/target,
portfolio, OMS, and broker state. Never vectorize across future time or
reorder shared cash and liquidity mutations. A single deterministic global
coordinator owns those mutations; results must match across worker counts.
Use a bounded, asynchronous, normalized ClickHouse journal with Keeper
fences and the same typed fact contracts in Backtest and live. No Strategy 1
SQLite or run-local disk journal is a durability authority. A full queue or
failed writer stops new admission rather than silently dropping facts.

## Required implementation pattern for a new number

Follow this sequence for every new or changed trading behavior. Reuse the
contracts and architecture, **not** Strategy 1's number, sealed rows, or
candidate identity. A requested change to Strategy 1 becomes Strategy 2;
the old release and its runs remain executable for exact reproduction.

| Stage | Required artifact and boundary | Strategy 1 example |
|---|---|---|
| 1. Specify | One reviewed behavior specification and `NumberedStrategyRelease` seal. Declare activation, entry, re-entry, additions, stop/target precedence, sizing, permissions, interval, fill policy, and technical dependencies before registering the number. | `strategy_one_contract.py`, `strategy_registry.py` |
| 2. Source | Typed QMD/producer products with resolution, timestamp semantics, build/attempt identity, coverage, and an explicit missing-data result. Request a new producer product before a rule can consume an unavailable indicator. A reusable expensive derivative is calculated once by producer-owned code and published coverage-last in normalized `arte` columns. | `backtest_market_data.py`, `backtest_strategy_one_candidate_store.py` |
| 3. Preflight | Compile the release's minimal dependency plan and verify every required source, revision, interval, identity, storage policy, and read-only market grant before the Backtest page or run can start. Missing data is an error, never an invitation to build it in Backtest. | `replay_run_service.py` fixed preflight |
| 4. Filter | Apply reusable, revision-pinned pure rule functions to bounded columnar batches. Emit immutable candidate indexes, causal boundary references, and reason-bit masks. Keep data extraction and candidate materialization bounded and measured. The mask is a necessary condition, never order authorization. | `strategy_one_contract.py`, `backtest_strategy_one_static_gate.py` |
| 5. Execute | Advance only surviving candidates through sequential per-ticker state and one deterministic global Portfolio/OMS/broker coordinator. Read only completed rows; order activation and fills use the declared broker contract. No state transition may see a future bar, indicator, quote, level, or fill. | `backtest_strategy_one_scheduler.py`, `strategy_one_stateful.py` |
| 6. Persist | Emit the same normalized typed decision, command, acknowledgement, fill, and financial contracts in Backtest and live. Use bounded asynchronous ClickHouse writers and Keeper ownership; the execution callback never waits on ClickHouse. A broker side effect waits in its separate command lane for its exact durable receipt. Do not use SQLite, run-local disk, JSON/blob evidence, or a generic event payload as the authority. | `arte_journal_writer.py`, `arte_command_dispatcher.py` |
| 7. Publish | Test the sealed number through preflight, full-session all-ticker app launch, causal replay, fill and capital determinism, cold recovery, and read-only enforcement. Record observed runtime and unresolved differences. Enable live separately only after broker/OMS recovery and all live gates pass. | `STRATEGY_ONE_BACKTEST_LAUNCH.md` |

Keep shared rules independent of strategy orchestration so a scanner, Signal
Stream, and another strategy can pin the same rule revision. Put only
position/order-dependent logic in the causal state machine. Do not convert a
rule into a Python per-row hot loop merely to reuse it; expose a typed
columnar evaluator and verify its output against scalar edge-case fixtures.
Changing a shared rule does not retroactively change a sealed consumer: pin
the old rule for that consumer and publish a new Strategy number if the new
rule changes its decisions.

Before editing, inspect the numbered release, its frozen input and rule
contracts, prior experiment results, and the relevant `AGENTS.md` files.
When a required product is unavailable, stop at preflight and implement its
producer and coverage certificate first. Do not fork a private bar, MACD,
signal, V7, or liquidity calculation inside the new strategy. Never remove a
live or Backtest safety gate merely because a unit test or a small ticker
probe passed; the complete runnable path must be verified.

## Publication checklist

Before registering the next number, seal a canonical manifest containing the
approved human-readable rules, exact rule-set revisions, typed input product
and build contracts, evaluation interval, broker/fill policy, code revision,
and content digest. Store approval and code commit. Runtime registration,
preflight, and deployment must verify the same seal; published registration
cannot use `replace=True`.

Verify future-tail independence, missing-input failure, completed-boundary
availability, rule and Strategy tests, live/Backtest decisions where input
contracts match, partial fills and shared liquidity, cross-ticker cash and
worker-count determinism, checkpoint/resume equality, and an app
launch-to-terminal run. A Backtest completing without error is implementation
evidence, not live release or profitability acceptance.

## Strategy 1 sealed Backtest behavior and current limits

Strategy 1 consumes certified `arte.bars_v1`, `arte.indicators_v1`,
`arte.liquidity_100ms_v1`, V7 structural intervals, and normalized
producer-owned candidate, pivot, HOD, and entry-evidence products. The
configuration is an exactly-one, coverage-last normalized `arte` release.
Its 100 ms Backtest uses completed 1s/5s/10s/30s MACD, a vectorized
necessary-condition mask, sparse candidate work, causal V7 and BOS state,
and the shared portfolio/OMS/broker coordinator. It does not dispatch
Candidate 350's event-time evaluator or compute forming MACD itself.

For bar-mode re-entry, a prior-position high must be captured only from
completed price-bearing bars after the filled entry bucket and checkpointed
as typed scalar state. Rapid (under ten seconds) or same-resistance re-entry
requires the previous completed 100 ms bar close at/below that high and the
current completed close above it. The preceding close must come from the
certified bar attempt, not the preceding sparse candidate or an inferred
intrabar trade sequence. If the prior state or either close is unavailable,
re-entry fails closed. A different-resistance entry after ten seconds retains
the ordinary entry rules. This is the approved bar-resolution adaptation of
the historical event-native crossing, not an assertion of event-fill parity.

Entry requires an immediately preceding completed price-bearing 30s low;
one tick below it is the initial stop. An absent/empty latest 30s bucket
does not carry a prior low forward. Stops only ratchet upward; after each
disjoint group of three accepted resistance breaks, the resistance path can
raise the stop, and it wins a simultaneous qualifying 30s-low update. The
target follows the historical 3/2/1 overhead-resistance ordinal rule and
never moves down. These exact rules are in
`src/trading_runtime/strategy_one_contract.py` and
`src/trading_runtime/strategy_one_position.py`.

Re-entry after a completed position is currently **fail-closed** in
`strategy_one_stateful.py`. Candidate 350 required an event-native crossing
of the prior position's high; aggregate 100 ms bars cannot prove that exact
sequence. Do not infer it from an unordered bucket, and do not enable a new
completed-bar crossing by changing Strategy 1. If approved, specify and
publish that changed behavior as Strategy 2, with its own seal and parity
tests. Live Strategy 1 order admission also remains closed until durable
completed-liquidity replay/watermark, typed journal, broker reconciliation,
and cold-recovery acceptance pass. Neither limitation may be hidden by
falling back to the legacy event/SQLite runtime.

The workstation full-market Aug 18/19 Backtest measurements, API launch,
normalized journal validation, and remaining operational limits are recorded
in `docs/trading/STRATEGY_ONE_BACKTEST_LAUNCH.md`.
