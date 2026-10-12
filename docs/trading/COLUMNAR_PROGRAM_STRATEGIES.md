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
  `columnar_strategy_publication.py` supplies the concrete operator-only transport:
  distinct least-privilege connections, persistent single-use Keeper claims bound
  to creation receipts, coverage-last publication and exact committed retry.
  Ambiguous/unfinished claims are never adopted automatically. Cancellation drains
  the owned worker. Operator installation/grants and approval integration remain open.
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

## Current typed publication path

The shared V4 worker now accepts native-qualified program completion envelopes,
including a one-event tail and mixed normalized batches. This is implemented
source; it has not been installed or qualified on operational sessions.

- `rule_decision_basis_schema.py` owns the two shared typed reference tables.
  No strategy defines a table, JSON column, journal or disk authority.
- `backtest_program_publication.py` joins a pending completion to its retained
  causal basis, delegates qualification to the controller's isolated native
  financial/source checks, and retains the matching proof. Ownership changes
  during qualification reject admission. The actual projector receives only
  the proofs for its bounded prefix; acknowledgements release fenced proofs.
- `rule_decision_compound.py` and `rule_decision_single_publication.py` use
  ordinary V4 preparation and the existing fenced publisher. Mixed batches
  rekey child hashes and the dependent parent digest together. The shared
  writer owns insertion, complete readback, retries and Keeper compaction.
- `rule_decision_commit_graph.py` verifies parent/child coverage and exact
  normalized completion-event correspondence during shared cold readback.
  Independent prepared-source, intent and financial reselection checks remain
  required; a valid row hash alone does not establish trading correctness.
- Program writers use the dedicated `backtest_v4_program_runner` principal,
  selected with `program_decisions=True` in the existing client factory.
  Startup audits schema, grants and actual storage placement. Publication
  reuses that audit only for the same client, endpoint, principal, dispatch,
  lease and contracts. Each new startup audits again.
- `scripts/clickhouse/provision_backtest_v4_program_runner.py` defaults to a
  local plan without connections or secret access. Applying the reviewed plan
  requires operational approval and its explicit confirmation flag. No grants,
  credentials or schema have been provisioned by these source changes.

Synthetic tests exercise actual shared INSERT/commit/cold-readback code using
in-memory transport. They verify retry deduplication and failure before commit
or compaction when detail readback is corrupt. Qualification-proof fixtures in
transport tests do not qualify the strategy's financial or causal behavior.

The app launch boundary remains closed: `_backtest_launch_blocker` admits only
the certified numbered 100ms path. `ReplayRunController` can construct a program
actor and explicitly dispatch prepared boundaries, but the main launch/compute
funnel does not yet orchestrate the full program source, qualification and
publication lifecycle. The frozen winner is not selectable or launchable in
the app. Do not weaken that gate to expose partially integrated components.

Qualification batching is also unfinished. The historical native qualifier
requires `basis_sequence` within the verified committed prefix. Current
per-assignment dispatch captures the journal's latest sequence, so a later
assignment in one clock can include earlier uncommitted decisions in its basis.
It cannot acquire that historical proof until its predecessor is committed.
Pending qualification now rejects this mismatch before any qualification reads.
The source-level `qualify_captured_rule_decision_runtime` provides a separate
pending-suffix qualification without claiming a committed prefix: it verifies
controller custody and capture content, independently reconstructed native
broker/Portfolio/OMS finances, sealed replay metadata, exact pre-decision policy
content, actual filled-position geometry, and prepared-source intent reselection.
Synthetic controller tests cover this qualification at100ms and1s, including
fee-decimal restoration and rejection of shared runtime or altered replay metadata.
`qualify_captured_pending_program_decision` joins that proof to the exact pending
completion and retains its qualified envelope for the existing V4 publisher.
It checks the publisher fence remains unchanged across qualification and does
not write or advance that fence. Synthetic controller tests retain all three
clock decisions before any publication, without per-decision predecessor commits.
The main launch lifecycle does not yet invoke this route automatically.
The source-level `dispatch_qualified_program_boundary` now coordinates capture,
native financial reconstruction, existing controller dispatch, normalized intent
reconstruction, qualification and proof retention, and always closes its detached
comparison runtime. It requires an independently replayed policy prefix from the
caller, or is reconstructed by `rebuild_owned_pending_program_policy` from the
bounded retained journal suffix and an optional verified continuation packet.
The reconstruction checks the current owned actor content before returning;
compacted earlier records require an explicit cold-restored packet and never
fall back to an unfenced memory history. Synthetic tests exercise this automatic lifecycle alongside explicit
dispatch at100ms and1s, including prefetch and rejection of altered replay
metadata before order submission. The fixture also retains the existing native
dispatch uncertainty-latch checks.
`ReplayRunController.dispatch_prepared_program_clock(qualify=True, ...)` now
coordinates this route for the complete assignment order and carries each
verified packet into the following assignment. The caller supplies explicit
prepared replay-block coverage and event bounds and retains the returned packet
across clocks. The default unqualified prepared-dispatch behavior is unchanged.
The returned packet includes the completed decision suffix, rather than only
its pre-decision basis. This allows the next clock to replay after a publisher
fence has compacted that suffix. Synthetic tests verify continuation with an
empty compacted journal and reject a stale pre-decision packet that would need
removed records; no memory fallback or operational receipt is invented.
`ReplayRunController.run_prepared_program_cohort` now schedules a bounded
prepared window through these APIs. It reads the chosen certified100ms/1s
liquidity lane, advances sparse clocks through native OMS timers, publishes
one-second source boundaries and retains qualified decisions in the caller's
explicit assignment order. Prepared masks and held listings remain available
across every clock. The caller supplies the configured protection-profile
resolver; the scheduler creates no sizing or fill policy. Native tests cover
three source/decision clocks, a sparse middle interval, actual fills and fees,
and preflight/stop checks. The admitted main lifecycle still must bind cohort
preparation, publication fences and terminal recovery to launch.
The controller permits one active cohort owner at a time. After awaited market
I/O, the scheduler rechecks its runtime, broker, Portfolio/OMS, configuration,
journal sequence, source cursor and publisher fence before any clock effect.
Native tests reject owner replacement during the read and parallel cohort
requests, and confirm cancellation releases controller ownership.
Main launch admission and operational full-session qualification remain unfinished.
Native cohort publication now resolves generic program intents earlier in the
same pending prefix only after verifying their retained native-qualified
completion proofs. Default legacy projection remains unchanged.
After a matching writer acknowledgement, the existing publisher now caches the
exact committed program intent revision and reconstructs its semantic intent
from the normalized rows. Later OMS projection can reuse that committed source
after journal compaction. Transport tests use synthetic proof/receipt fixtures
to confirm no cache/fence advance before acknowledgement or on writer failure;
they do not establish native price-schema publication success.
The publisher's existing event budget now chooses a cut before an intersected
qualified decision span rather than split its intents/OMS actions from its
completion. A requested fence inside a decision, or one decision larger than
the existing budget, fails before publication. Native cohort tests check these
cut boundaries without publishing the unresolved exact-price prefix.
The full native prefix test exposed a persistence boundary: Float32-derived
stop9.989999771118164 does not fit the existing OMS `Decimal(38,10)` price
column losslessly. Publication currently fails closed and leaves the fence
unchanged. Exact program outputs must be preserved; a versioned central price
contract or an explicit approved order-price policy is still required before
operational publication. No implicit rounding or schema installation occurred.
The bounded central-field inventory and exactness probe are recorded in
[TRADING_PRICE_PRECISION_REVIEW.md](TRADING_PRICE_PRECISION_REVIEW.md). They
identify11 affected families and explain why merely switching to scale18 is
not a general lossless solution.
The existing certified1s liquidity consumer now offers bounded concurrent cohort
reads through `read_liquidity_cohort_async`. It validates all requests before
opening clients, preserves sparse/empty windows and closes every owned client
before cancellation returns. It consumes existing sealed ARTE products only;
it neither builds missing coverage nor changes the100ms reader or broker clocks.
`read_fixed_liquidity_cohort_async` adds the corresponding bounded100ms adapter
using the existing certified market-day and eligible-price plans, SQL compiler,
row iterator and completed-boundary validator. It rejects duplicate/out-of-window
boundaries and mismatched price parents, and closes stream/client ownership on
failure. Both resolution adapters use the same bounded worker pool; neither
selects sizing or supplies strategy market features from liquidity bars.
`TradingRuntime.advance_program_session_clock` handles a completed sparse
program clock after the admitted liquidity window has been scanned. It advances
the runtime clock and shared OMS trigger/reprice/deadline operations without
inventing quotes, bars or executions. Native100ms/1s tests verify deadline
cancellation of an unfilled entry with unchanged cash, quotes and broker bar
boundaries. This source API is not yet called by the main cohort scheduler.
`prepare_program_scanner_projection` joins certified population ranking to the
prepared history once per block, checks the immutable volume-universe rule and
ranked listing/clock coverage, and exposes constant-offset per-clock scanner
frames with the app's canonical price/volume/reference aliases. Held listings
outside top10 remain in the separate prepared management blocks.
`publish_prepared_program_source_clock` feeds those rows through the configured
Signal Stream resolver, data-field plan and typed publication queue, promoting
source state only after acknowledgement. Synthetic native controller tests now
use this shared bridge instead of assembling scanner candidates manually.
The main clock lifecycle must resolve this dependency while preserving actual
filled-state/OMS semantics; per-decision cold database replay is not established
as an efficient production execution path.

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
3. Connect the implemented chronological coordinator, execution-derived
   position/age, pending-order handling, semantic intents, historical native
   recovery and qualified decision publication to the main compute funnel.
   The controller must orchestrate qualifications and receipts, not require
   manual helper calls for every decision. Keep Portfolio/OMS/fills authoritative.
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
