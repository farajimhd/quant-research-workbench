# Shared columnar program integration

Status: implementation in progress. The components below are source-level
foundations, not a selectable, published or runnable app strategy. Existing
numbered releases and their certification gates remain intact.

## Delivered components

- `columnar_rule_program.py`: bounded typed DAGs evaluated with native Polars
  expressions over producer-supplied fields, availability clocks, validity
  masks, listing identities and explicit evaluation blocks. Programs cannot
  read entry price, cost, position P&L or other execution-derived fields.
  Position-open state and age belong in the chronological coordinator.
- `volume_rank_rules.py`: a shared scanner rule consuming certified elapsed
  rolling-volume snapshots and split-adjusted prior-session closing prices.
  Ranking is volume descending, listing identity ascending. It does not
  derive history or remove an otherwise eligible listing for lacking a
  current trade. The coordinator must retain held positions independently.
- `optimized_program_specification.py`: translates the previously frozen
  generation24, candidate872 winner into the shared DAG representation.
  Both the file identity and canonical program content are pinned. It keeps
  all five effective programs, policy values and minimum-age restrictions.
  It is a specification adapter, not an executor or a market input reader.
- `columnar_strategy_schema.py`: centrally owned, staged relational contracts
  for release, input fields, rule programs/nodes, volume-universe settings
  and broker settings. Fields have explicit types; no JSON/blob column,
  dynamic value payload or strategy-owned DDL is permitted. These contracts
  are not installed or added to the existing active journal table set.
- `columnar_strategy_release.py` and `columnar_strategy_reader.py`: normalized
  release projection, exact child foreign-key/count/hash readback, immutable
  number collision checks, children-before-seal publication, bounded asynchronous
  caller I/O, explicit SELECT-only reader grants and schema/physical SSD checks.
  The publisher accepts an operator-owned storage/ownership adapter; its concrete
  Keeper-fenced ClickHouse publication bootstrap remains to be integrated.
  Reader admission requires an explicitly pinned approval/content/code/winner
  identity. These components do not register or publish a number on import.
- `filled_position_state.py`: a shared in-memory projection of verified execution
  and commission facts, with actual weighted fill cost, position age, partial
  acquisitions/reductions, exact execution deduplication, late-fee win-count
  correction and cold-prefix replay. Pending fees block finalized financial
  results. It owns no tables and is not yet connected to normalized OMS lineage.
- `columnar_program_coordinator.py`: pure sequential selection of precomputed
  stage masks using actual filled position/age and supplied OMS management state.
  It retains held tickers outside top10, checks pending acquisition/reduction,
  requests $1,000/$200 through the existing `CapitalRequest`, and emits only
  existing semantic intent types with explicitly supplied app execution and
  protection contracts. It does not advance position or OMS state on a signal.
  Protection/target lifecycle transport and app coordinator wiring remain open.
- `causal_market_history.py`: shared producer-side dense elapsed-second history
  calculation. Synthetic parity covers all332 original channels and the search's
  relative-value rounding order. Missing elapsed clocks and future availability
  fail closed. This helper is not a certified persisted product or a Backtest
  source; the existing QMD History producer must publish and seal its output.
- The existing simulated broker accepts an explicit100ms or1000ms liquidity
  resolution.100ms remains the default.1s matching conservatively requires
  delayed order activation before the entire eligible bucket begins.
  Actual orders, shared liquidity, positions and commissions remain owned
  by the existing broker.1s source metadata names the shared
  `arte.liquidity_bars_v2` product described below.
- `liquidity_resolution_sql.py` and `liquidity_resolution_producer.py`:
  central scalar SSD schemas and an operator-only coverage-last1s derivation
  from pinned certified100ms ARTE liquidity and eligible-price rows. Publication
  verifies parent certificates, children, bucket volume parity and readback.
  Ambiguous writes leave unsealed attempts; no raw-event fallback or deletion
  is available. Cancelled asynchronous publication drains its owned unit and
  closes both connections. Concrete producer ownership/bootstrap remains open.
- `backtest_liquidity_resolution.py`: SELECT-only schema/placement/parent/hash
  certification and bounded256-bucket reads, preserving quote-only buckets and
  normalized eligible prices. Read-only ClickHouse synthetic SQL output was
  fed through this reader into the existing broker and confirmed partial fills
  and commissions. This does not certify real-session products or app wiring.
- `backtest_broker_contract.py`: exact normalized projection/recovery of the
  selected broker resolution, plan identity, commissions, slippage, activation
  delay and participation. Recovery rejects changed settings and verifies an
  exact round-trip into the existing broker. Run-journal wiring remains open.

## Frozen source behavior

The verified optimization market-producer contract uses a30-second elapsed
volume window including the current completed second, top10, and inclusive
$0.80–$50 eligibility based on the split-adjusted prior-session close.
Price eligibility precedes volume ranking. Zero-volume seconds participate
in the producer's elapsed window. Equal scores use ascending listing
identity. Keep these semantics distinct from current-price filtering,
cumulative-session volume, and observed-candle history windows.

Desired entry cash$1,000 and each add$200 come from the frozen execution
reference. They must become immutable release parameters, with changed
values producing a new strategy behavior identity. Actual affordable sizing,
reservations, order negotiation and fills remain Portfolio/OMS/broker-owned.
Commissions are$0.005/share with$1 minimum/order. The optimizer's assumed
fills are comparison expectations, not an app execution policy.

## Required integration before publication

1. Implement and certify the shared QMD History/ARTE history, scanner and
   signal-product bridge. Verify reusable existing coverage first; do not
   calculate private history inside a strategy or read research files as
   Backtest market inputs.
2. Integrate the central release publisher/reader with the concrete operator
   Keeper/ClickHouse transport, normalized approval and least-privilege grants.
   Pure readback/publication contracts alone provide no installed authority.
3. Add the shared chronological program coordinator, actual position/age
   projection, pending-order handling, semantic intents and normalized
   lifecycle recovery through Portfolio/OMS. No assumed-position updates.
4. Install and qualify the reusable1s liquidity producer/read plan on real
   sessions and wire typed broker-setting publication/recovery. Thread the chosen resolution
   through the current Backtest configuration, preflight, launch and UI.
5. Qualify a new shared execution/projection certificate and wire the generic
   app route. The existing `TradingRuntime` module is whole-source certified;
   changing it invalidates old admission. Do not bypass its certificate or
   route future programs through legacy observation callbacks.
6. Publish a new immutable strategy number through the app registry and UI
   only after the complete source, journal, recovery and admission contracts
   are implemented. No new number has been registered by this change.
7. Retain all30 frozen optimizer session results in the comparison. First
   compare universe membership and rule/proposal outputs, then actual
   positions, fees and P&L. Report fill-model differences explicitly.

## Verification and operational boundary

Focused synthetic tests exercise rule validity, chronology, future-tail
independence, listing isolation, search-evaluator rule/mask parity, volume
selection and real simulated-broker order filling. These do not establish
full-session real-data parity or throughput. The installed numbered source
certification and registry checks must continue to pass.

Review finding: `PortfolioEngine.on_order_group_update` currently supplies
reservation reference prices to its allocation projection. Future program
management must obtain execution cost and position age from verified actual
execution/OMS attribution, rather than interpret that allocation price as the
actual fill cost. The execution-derived projection above is not yet a change
to the existing Portfolio allocation implementation.

No schema installation, data producer, service, optimization, app Backtest,
workstation deployment or monitor is authorized merely by importing these
components. The user requires approval before operational runs. Prepare a
concrete migration/publication and bounded comparison plan before asking.
