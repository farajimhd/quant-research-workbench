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

The registered `_CONTRACTS` inventory contains these11 families and26 price or
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
| trading_protection_reconciliation_action_v4 | stop_price, target_price, from_stop, to_stop |

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
families with twenty-six named UInt64 price fields (the initial inventory had
twenty-four; the reconciliation review added from_stop and to_stop). Existing table definitions,
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

Exact-price OMS envelopes now retain explicit shared tactic state and ordered
steps, including recorded absence. The existing bounded committed OMS page and
latest-state readers accept an explicit `exact_prices=True` mode requiring a V4
prefix, read only the selected price tables, verify their hashes, and decode
native prices before their unchanged lineage/count/tactic checks. There is no
legacy-table fallback. The asynchronous program OMS cold join forwards that
explicit mode while retaining prefix-before/after, reader ownership, normalized
admission/decision, protection-history and reselection requirements. Synthetic
tests cover present and absent tactics, missing tactic rejection, legacy-mode
rejection of exact-only records and corrupted price readback. Full program actor
recovery, writer-queue/main-path integration and real-session qualification are
still incomplete. Tactic quote/step decimal contracts remain unchanged; their
own price precision must be reviewed before claiming complete exact-price
coverage for every execution tactic.

`ArteJournalWriter.submit_exact_oms_v4` now submits the explicit envelope to the
existing bounded persistence worker. The actor-side method performs no network
I/O; storage/grant admission and V4 publication stay on the worker. Synthetic
blocked-worker tests confirm pending acknowledgement, immediate queue overflow,
publication on a distinct thread, idempotent queued retries, failure receipts,
and rejection of further writes after failure. Mixed-prefix compaction and
main-path projection still need exact-family support before this route can be
enabled in the app. No full-session throughput result is implied.

Mixed V4 compaction now accepts exact OMS envelopes alongside ordinary source
events. It rekeys and hashes their named group/order/tactic children, reuses the
shared merged OMS seal, emits each price family once and preserves source rows.
The Backtest receipt dispatcher recognizes exact single tails as well as their
compounds. Synthetic tests commit a source intent plus exact OMS state under one
fence, cold-read the state/tactic, retry it and reject altered children before
reservation. Main-path selection of the exact price contract remains unfinished;
these transport tests are not a full native session or speed qualification.

The Backtest prefix projector and receipt publisher now accept an explicit
`exact_oms_prices=True` selection. Default behavior remains legacy. The native
100ms/1s cohort test confirms that this selection passes OMS projection and
then fails losslessly at shared `order_command_batch` for the same protective
`order.aux_price`. Order-command precision is therefore the next confirmed
integration gap. This source-only selection has not been enabled in the app;
complete price-family coverage and immutable run-setting/recovery binding are
required before main-path admission or any real session qualification.

Shared `order_command_batch` now offers an explicit exact-price projection and
returns `ExactPriceOrderCommandBatch`. Existing unknown-field, command/context,
intent-hash and canonical-lineage checks remain in the same projector. The
shared journal seal accepts its named exact command family and uses a decoded
internal semantic view for the existing relationship checks; quantities and
nonprice instructions retain their previous contracts. Projection/readback and
tampering tests pass. Main native-prefix selection remains unfinished.

The exact command envelope now uses the shared audited V4 publisher and bounded
writer worker, and can enter mixed-prefix compaction. Its context and exact
intent-use rows rekey with the source intent under one commit. The existing
committed command reader has an explicit `exact_prices=True` mode requiring a
V4 prefix, selecting only the new table and verifying row hashes before decoding
native prices. Synthetic tests cover direct and mixed publication, cold price
and context readback, retry, corruption, nonblocking queued submission, overflow
and worker failure. No schema installation or real broker run has occurred.

One broader projection test currently fails independently: it constructs an
uninitialized `TradingRuntime` without `strategy`, then calls unchanged
`_record_executions`. This fixture/runtime mismatch is separate from exact-price
projection and has not been modified as part of this change.

The native Backtest prefix now forwards the explicit OMS price selection to
order-command projection as well. The next native rejection exposed a source
action mismatch: the existing shared planner emits `exit`, while the legacy
journal's six-action list rejects it. New exact-price projection now uses the
planner-owned `ORDER_EMITTING_ACTIONS` declaration; legacy projection keeps its
original action checks. Control-only `hold`, `wait` and `cancel_entry`, and
unknown actions remain rejected as command sources. There is no order rounding
or strategy-number special case in this change.

Both native 100ms/1s cohort fixtures now prepare their complete journal prefix,
publish it through the qualified mixed V4 path and cold-verify the committed
sequence. They use actual Portfolio/OMS/simulated-broker code, native decision
proofs and synthetic QMD/liquidity/storage/transport; the operational startup
audit is mocked. This is bounded integration evidence, not full-session,
partial-fill, all30 comparison or workstation throughput qualification. Full
price-family coverage, immutable run-setting/recovery selection and main launch
integration remain required before enabling real runs.

The additional partial-fill cohort exposes the existing OMS event
`entry_acquisition_frozen_before_exit`: cancellation of a still-open acquisition
before risk reduction. A shared parent table and ordered source-group table now
represent all emitted fields, retaining correlation/causation in the event.
Their codecs reject unknown fields, empty/duplicate/foreign groups, orphan
children and mismatched counts/hashes. V4 projection, worker submission, mixed
seals, readback and the program provisioning/audit catalog include these two
central contracts; no strategy-owned schema or JSON column is introduced.
Standalone synthetic publication/retry/corruption tests pass. The native partial
cohort now reaches the next confirmed gap: protection-reconciliation stop price
still rejects the Float32-derived value at Decimal(38,10). That path remains
unqualified until its exact-price action contract is integrated. No operational
tables were installed or real backtests run.

The reconciliation projector/envelope now selects its staged exact action
contract explicitly, retaining all four price fields without conversion through
legacy decimals. Quantity, parent and broker-reply contracts remain unchanged.
The existing V4 worker, mixed compactor and cold commit graph seal the selected
physical action family; exact publication requires the program writer audit and
is rejected by live queue admission. Standalone synthetic readback/retry and
price-corruption tests pass. Both complete-fill and partial-fill native cohorts
at100ms/1s now publish and verify their full bounded prefixes; partial exits
preserve the original Float32 stop during protection repair. These are synthetic
bounded tests, not full sessions or all30 comparison. Remaining price families,
full actor recovery, immutable setting selection and main launch qualification
still require completion before real runs.

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

The prepared programme module now provides `run_fenced_program_cohort`.
It holds controller cohort ownership through publication and checks the exact
sequence, batch ID and source-cursor receipt against the publisher, plus runtime,
journal, actor and source ownership. Publication uncertainty or cancellation
latches the controller recovery requirement; it does not cancel the publisher's
shielded task or authorize a replacement run. Empty stopped cohorts do not
publish. The returned policy packet is preserved for the next cohort.
Lifecycle fault tests use controlled publisher doubles. Native synthetic cohorts
also exercise the actual ArteJournalWriter worker and BacktestTypedJournalPublisher
against an in-memory V4 transport, for both liquidity resolutions and complete or
partial fills. Two consecutive cohorts verify publication, released pending
records, preserved policy packets and cold verified-prefix sequence extension.
This exposed and corrected a post-commit OMS cache assumption: exact-price OMS
envelopes keep strategy identity in their exact group row, while their legacy
decimal rows are empty. The existing legacy accessor remains unchanged.
Storage audits and market transports are synthetic; main app launch, actual
ClickHouse publication, full-session recovery and all30 qualification remain pending.
