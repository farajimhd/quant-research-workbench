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
`src/trading_runtime/trading_price_codec.py` now exposes that shared scalar
representation for staged future contracts. It validates positive/nonnegative
and nullable domains explicitly, rejects inexact integer conversions, and
reuses the broker-match canonical-zero convention. Eight synthetic checks
verify bit-exact Float32 round trips (including representation stress cases),
nullable values and rejection of malformed/nonfinite/out-of-domain inputs.
This codec creates no table and does not select an order-price policy or change
existing decimal writers. It can retain raw thresholds under either option.

## Required source and qualification scope

Source development proceeds with exact native values through versioned shared
schemas, following the first-version requirement to preserve programme/planner
outputs. No order-price rounding policy is introduced. Operator approval is
still required before any schema publication or real run.

`trading_price_contracts.py` defines a closed registry of eleven shared price
families with twenty-four named UInt64 price fields. Existing table definitions,
partitioning, sorting and nonprice columns remain unchanged. These staged
contracts are registered only with the scalar journal codec. They are absent
from admitted commit families and confer no writer grants or publication right.
`trading_price_rows.py` projects native prices before legacy decimal conversion
and verifies the selected schema's canonical content hash before restoration.
Malformed/nonfinite price bits are rejected by shared canonicalization as well
as by restoration. This is scalar transport integration, not yet complete OMS
publication, snapshot recovery or operator provisioning.

The existing `oms_group_state_batch` now has an explicit source-only
`exact_prices=True` mode. It runs the same intent/admission/order checks and
returns an `ExactPriceOmsStateBatch` with separately hashed group/order price
rows and the original nonprice envelope. It retains decimal quantities and
timers, binds price children to the original event, and rejects mixed legacy
price rows or tampered hashes. Default projection remains unchanged. This
staged envelope is not yet accepted by the existing writer queue. Complete cold
recovery and main-path integration remain to be implemented.

`seal_exact_oms_state_batch` now validates its new physical group/order families
through the shared journal seal, including contiguous event sequence, parent
scope, partition, intent-use links and all OMS component counts/ordinals. Exact
prices are decoded only into internal semantic views for these existing checks;
only new physical families and their exact hashes are emitted. No legacy
decimal rows or surrogate rounded prices are published. Order readback uses
the same extracted `order_request_from_oms_row` kernel as the existing cold
reader; hashed wire rows round-trip into the original native `OrderRequest`.
This still confers no committed-prefix, writer or actor-restoration admission.

The direct exact-OMS publisher now uses the existing V4 commit publication,
acknowledgement, family seal, readback and idempotent retry implementation.
Publication requires the dedicated program writer audit before reservation or
INSERT. Its opt-in catalog and provisioning plan include all eleven exact-price
contracts, with shared schema, SSD-placement and least-privilege grant checks;
the existing V4 principal and legacy catalogs are unchanged. Provisioning remains
plan-only unless explicitly applied by an approved operator. Synthetic in-memory
commit tests prove prior-intent fencing, exact physical family readback, retry
and corruption rejection. They mock the operational startup audit and transport;
they do not prove database installation or full-session runtime integration.

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
