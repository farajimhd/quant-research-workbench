# Current app price precision review

TASK-0223 source review. No schema, grants, release or operational backtest has
been published by this review. Existing releases and V1 tables remain unchanged.

## Confirmed failure

The native prepared-cohort test reaches Portfolio/OMS orders and actual broker
fills at100ms and1s. Full V4 journal projection then rejects the protective
order's9.989999771118164 price. That value is the Float32 history value9.99
widened to a Python float. `arte_oms_projection.oms_group_state_batch` calls
`arte_journal_projection._exact_decimal` with scale10, which rejects the value
rather than round it. The publisher fence remains unchanged.

The programme's semantic stop and the existing planner's submitted stop are
currently identical. `strategy_orders._order` passes the resolved stop directly
to `OrderRequest.auxPrice`. `InstrumentContract` currently has no declared tick
size; a new order-price policy cannot infer an instrument-specific increment
from that contract. The existing OMS adaptive execution lane uses observed
`ExecutionMarketSnapshot.tick_size`, which is a separate contract.

## Central fields requiring consistent treatment

The registered `_CONTRACTS` inventory contains these11 families and24 price or
reprice fields at `Decimal(38,10)`, including nullable variants. This is a
bounded price-field inventory, not certification of every financial field.

| Shared family | Price fields |
| --- | --- |
| trading_strategy_signal_v1 / v2 | invalidation_price, decision_reference_price |
| trading_execution_v1 | price, average_price, signal_price, arrival_midpoint |
| trading_order_command_v1 | limit_price, aux_price |
| trading_order_transition_v1 | average_fill_price |
| trading_oms_group_state_v1 | deferred_reprice_from, deferred_reprice_to, high_water_price, low_water_price, current_limit_price |
| trading_oms_order_state_v1 | limit_price, aux_price |
| trading_oms_execution_step_v1 | price |
| trading_order_reprice_v4 | requested_price |
| trading_order_modify_command_v1 | limit_price, aux_price |
| trading_protection_reconciliation_action_v4 | stop_price, target_price |

Other existing shared families use `Decimal(38,18)`, including intent protection
slices, protection changes and proposal order-group images. Updating only the
OMS auxiliary price would leave command, transition, modification, execution
and cold-recovery paths inconsistent.

## Representation evidence

A local deterministic NumPy/Decimal probe, without market/database reads:

| Input converted to Float32 | Widened value | Fits scale10 | Fits scale18 | Fits scale38 |
| --- | --- | --- | --- | --- |
| 0.0001 | 9.999999747378752e-05 | no | no | yes |
| 0.8 | 0.800000011920929 | no | yes | yes |
| 9.99 | 9.989999771118164 | no | yes | yes |
| 50 | 50.0 | yes | yes | yes |
| Smallest positive Float32 | 1.401298464324817e-45 | no | no | no |

The last row is a representation stress case, not a claim that the value is a
valid market price. Scale18 fixes the observed9.99 failure but is not a general
lossless Float32 solution. Any decimal contract needs explicit supported
domain/precision bounds and rejection behavior. The existing broker-match
snapshot's UInt64 binary64 codec provides another already-used exact scalar
representation to evaluate for a versioned central contract.

## Required source and qualification scope

The operator choice remains pending: preserve exact native values through a
versioned shared schema, or explicitly normalize submitted orders while
retaining raw programme thresholds. The first option preserves current
programme/planner semantics. The second changes submitted prices and may
change fills; it needs an explicit increment source and rounding direction.

Whichever option is selected must cover central schema definitions, projectors,
binary insert/readback hashes, compound family seals, grants/storage audits,
order command/modify/reprice links, broker execution attribution, OMS actor
restoration and cold replay. Price, quantity, money and nonfinancial timer
fields must be classified separately; elapsed-time rounding is not authority
to round financial values.

New contracts must be shared across future strategies, have named typed fields
and relationships, and contain no JSON/blob trading columns or strategy-owned
tables. Existing tables and releases must not be silently migrated. Source
validation must include exact round trips, nullable prices, malformed/nonfinite
values, tampered hashes, complete native cohort publication and cold recovery.
All30 frozen optimizer comparisons remain required; a synthetic schema test
does not establish real-session parity or throughput.

DDL, storage/grants changes, deployment, producers and actual session runs
remain subject to the user's operational approval boundary.
