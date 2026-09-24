# ARTE trading journal cutover contract

Status: implementation in progress. This document is the target contract, not a
claim that the current Backtest or live runtime uses it.

## Authority and scope

New Backtest and live/paper trading runs must recover from `arte` ClickHouse
tables alone. SQLite databases, local manifests, JSON files, and checkpoints
must not be required for new runs. Existing local data is not migrated or
deleted. `arte` source code is reserved for the future migration; producer and
runtime implementation belongs outside that folder.

All tables below use `live_market_ssd`. The schema installer is an operator
action. Trading and Backtest accounts can only insert their own journal rows
and select required market and journal rows. They cannot create tables or
insert/update/delete any `arte` market product. Startup checks the table policy
and actual part placement before admitting a run.

The shared typed-journal client uses `TRADING_JOURNAL_CLICKHOUSE_URL`,
`TRADING_JOURNAL_CLICKHOUSE_USER`, and `TRADING_JOURNAL_CLICKHOUSE_PASSWORD`.
It must be a dedicated principal, distinct from market readers. Fixed Backtest
preflight checks the typed `arte.trading_*_v1` tables and grants, not the retired
`arte.bt_*` tables; it remains blocked even when those checks pass until every
runtime record and cold-recovery state is mapped and validated.

No authoritative table has a JSON, blob, raw-payload, or opaque state column.
Data absent from the typed schema must be rejected at publication, not silently
stored as an unqueryable side payload or discarded. `Nullable` means genuinely
unavailable; zero is never a substitute for missing financial evidence.

## Grains and identities

Use a shared logical contract for all modes, with `mode` and `run_id` in every
run-scoped family. Backtest and live may have separate physical tables and
writer privileges, but identical concepts must have identical units, signs,
time semantics, and status vocabularies. Event time and recorded time are UTC
`DateTime64(9)` and `DateTime64(6)` respectively. Prices, quantities, cash,
commissions, and P&L use explicit `Decimal` scales rather than `Float64`.

| Family | One row per | Core typed fields | Partition / order |
| --- | --- | --- | --- |
| run | immutable run revision | run ID, mode, evaluation interval, session scope, config/code/source hashes, start time | month(start), run ID |
| runtime config | one typed `RunConfig` per run | strategy identity/revision, anchor date, plan ID, safety and checkpoint policy | month(run), run ID |
| run account | one ordered account binding per run | run ID, ordinal, account ID | month(run), run ID, ordinal |
| run context commit | one immutable readiness fence per run | hashes of parent run/config/account rows, account count, commit time | month(run), run ID |
| journal event | logical state transition | run, attempt, sequence, record ID, category, entity identity, account, event/recorded times, correlation/causation IDs | month(event), run, attempt, sequence |
| signal and decision | emitted signal or decision | event ID, strategy and revision, ticker, action, direction, reason code, score/confidence when defined, causal boundary | month(event), run, strategy, ticker, boundary, event ID |
| order command | unique command ID | run, account, instrument, side, type, quantity, prices, TIF, parent/OCA, activation boundary, strategy attribution | month(created), account, run, command ID |
| order transition | broker/simulator order status change | order/command IDs, status, filled/remaining quantities, rejection code, source and receipt clocks | month(source), account, order ID, source clock, event ID |
| execution | unique broker or simulator execution ID | account, run, order IDs, instrument, side, quantity, price, venue, event/receipt clocks, strategy attribution | month(event), account, execution ID |
| commission | execution commission revision | execution ID, amount, currency, status, event/receipt clocks | month(event), account, execution ID, event ID |
| portfolio and position | state revision at a committed boundary | run, account, boundary, cash/equity/buying power or instrument position, costs, realized/unrealized P&L | month(boundary), run, account, boundary, instrument |
| assignment and configuration | immutable revision or state transition | typed permissions, parameters, lifecycle fields and revision hash; repeating members go in keyed child tables | month(created), identity, revision |
| OMS and broker recovery | one state component per fence | typed orders, groups, reservations, liquidity consumption, positions, cash and lifecycle fields | month(fence), run, fence, entity identity |
| structural strategy recovery | one state component per fence | typed per-ticker strategy and V7 state, seed/build identity, last completed boundary | month(fence), run, fence, ticker, component |
| journal commit | one durable prefix fence | run, attempt, prior fence, first/last sequence, row counts/hashes per family, source cursor, status, committed time | month(run), run, attempt, last sequence |

Repeating data (signal sources, reason codes, annotation tags, order/execution
links, and configuration members) lives in child tables keyed by its parent
identity plus ordinal or stable member ID. Do not duplicate the entire parent
row in each child. Execution and commission revisions preserve their source
identities; a trade episode is a derived, rebuildable projection, not the
execution authority.
The runtime config/account fence is published last and verified before a
typed writer may start. It covers `RunConfig`, not yet the full approved trading
configuration or strategy/OMS checkpoint; those still need normalized families
before the runtime cutover can open.

## Durability and replay

ClickHouse `MergeTree` does not provide a cross-table transaction or a unique
constraint. Each row therefore has a deterministic identity; publication uses
bounded batches and retry-stable deduplication tokens. Readers accept only the
longest verified contiguous commit-fence chain. A fence is inserted last,
after all typed family rows and their counts and hashes have been read back or
otherwise verified. Unfenced rows are ignored for recovery and review. A
retried prefix with conflicting content is fatal, not last-write-wins.
Row content hashes use canonical persisted types, including UTC `DateTime64`
precision and fixed-scale decimals, rather than the producer's timestamp
spelling. Cold recovery reads bounded groups of complete typed rows, recomputes
each row hash, and then verifies each commit's row count and identity digest.

A dedicated bounded writer lane batches records; the market-data callback and
Backtest simulation loop never perform ClickHouse I/O or wait for an insert
receipt. Publication and verification run on separate workers. In Backtest,
the engine may finish simulation before persistence catches up; the run is not
durably **completed** or available for review until every prefix fence and
terminal state is acknowledged. Queue lag and the final drain time are reported
separately from engine runtime.

For live trading, the market-event callback only enqueues decisions. A separate
OMS dispatcher awaits the durable command-intent receipt before sending the
external order; this durability gate must never stall market-data ingestion or
strategy evaluation. Broker execution publication is likewise asynchronous,
with pending-versus-durable state made explicit. A full bounded queue or
unavailable ClickHouse disables new order admission and marks persistence
unhealthy; it never drops a record or falls back to disk. The engine must not
claim durability merely because an enqueue succeeded. Portfolio admission and
campaign ownership require a single fenced coordinator, not an assumed
`MergeTree` compare-and-swap.
The bounded typed command dispatcher transport now enforces receipt-before-send
and poisons its lane on persistence failure, broker uncertainty, or interruption.
It requires a verified running-run recovery audit before startup; that audit
checks committed command identities against open broker orders, recent
executions, and the latest committed order transition. An absent broker order
is never proof of non-delivery. A run with any earlier committed command, or a
terminal run, cannot restart this dispatcher until complete typed OMS-state
recovery and single-coordinator fencing exist. It is deliberately not wired
into live OMS: those facilities and complete typed strategy-intent evidence
are still missing.

Recovery loads the latest verified fence, restores typed state components,
reconciles live broker orders/executions by stable IDs, and resumes from the
stored causal boundary. Source market arrays are reloaded from certified
`arte` products, never checkpointed as opaque arrays. Resume must yield the
same orders, fills, positions, cash, and journal sequence as an uninterrupted
run. Review uses the same committed prefix without running strategy code.
The read side can now page typed order commands only after full-prefix
verification and checks each command against its event envelope. A committed
command is not proof that the broker received it: a future live dispatcher must
reconcile by stable client/order IDs before deciding whether to send again.

## Cutover gate

### Verified live-source mapping still required

The current runtime records fills from `ibkr_schema.Execution.to_cpapi()` in
`runtime.py` and `order_management.py`, while broker reconciliation uses the
distinct `domain.Execution` canonical model from `ibkr_normalizer.py`.
`trading_execution_v1` currently covers the canonical trade and decision-quality
measures, but an execution projection is not complete merely because those
columns exist. Before switching either producer, map and test:

- the source execution ID, order reference, broker order ID, source and receipt
  clocks, instrument identity/currency, side, quantity, price, venue, and the
  added cumulative/average/net/liquidity/decision-quality measures;
- commission and its status/currency as a separate typed event linked by
  execution ID, including later broker revisions;
- source-only broker attributes and instrument/provider identifiers, either as
  named typed columns/child rows or as explicitly non-authoritative fields
  justified by an audited source contract. No generic JSON or key/value
  escape hatch is allowed.

The live producer's `raw` map and strategy signal/intent `metadata` can contain
additional nested evidence. Until every authoritative member has an explicit
typed representation and a losslessness test, these categories must remain
unmapped and the ClickHouse runtime cutover must fail closed. The installed
table alone is not evidence of complete live-event coverage.
The `arte_intent_projection` flattens the declared `StrategyIntent`,
capital request, execution policy, envelope, and protection profile/slices
into scalar parent and child rows. It refuses nonempty open-ended metadata.
The two typed intent families are installed in `arte` on `live_market_ssd` and
participate in the same commit fence as live and Backtest journal families;
an isolated one-intent, one-slice ClickHouse publication and cold prefix
verification passed. A bounded reader now rechecks the stored row hashes,
normalizes ClickHouse decimal and UTC wire values, validates parent/slice
relationships, and reconstructs the intent; a read-only recovery of that real
integration run passed. This does not enable the runtime cutover. Source-field
inventory tests
fail if a declared intent or policy field is added without reviewing the
projection. The reverse projection checks row counts, ordinals, parent intent
identities, and exact canonical round-trip equality before reconstructing an
intent; original ticker spelling is preserved. Five staged OMS families now
represent one group-state revision and its ordered order requests, broker
bindings, warning IDs, and cancelled OCA group IDs. Their row counts and hashes
participate in the same typed commit fence; the cold reader verifies parent
and child identities, order/broker ordinals, batch membership, and a total
child-row budget. Operator DDL installed the families on `live_market_ssd`, and
a journal-only two-batch intent-to-OMS publication and cold read passed. This
is not a live OMS cutover: strategy orders with `raw` canonical metadata or
broker algo parameters fail closed; remaining intent metadata, tactic/runtime
state, and broker reconciliation must be normalized and restored before SQLite
can be removed.
The simple `OrderRequest` projection now preserves the broker's named flat
instructions, including security type, listing exchange, `auxPrice`, trailing
settings, manual/single-group flags, operator/referrer, strategy, and parent
broker-order identity. It rejects nonempty `raw` and `strategyParameters`;
strategy-originated orders commonly carry nested canonical metadata, so the
live OMS command gate is **not** enabled by this projection alone.
A separate typed `trading_order_command_context_v1` child row links a command
to its strategy intent, OMS group, and policy version without changing or
rehashing older command rows. Publication requires that the intent be in the
same batch or exactly one earlier committed intent for the run/account. The
journal-only two-batch ClickHouse test and SSD placement check passed; this
link does not yet encode the remaining nested OMS strategy evidence. Cold
command audit now pages the context children, checks their hashes and parent
identities, and proves each referenced intent is one earlier fence-certified
strategy-intent event. The real two-batch integration run passed that read-only
audit; missing or ambiguous links fail closed.

Before a new mode uses this authority, verify: schema, SSD policy and actual
parts; writer/reader grants; all runtime state families mapped without JSON;
idempotent retries; interrupted multi-table publication; conflicting retries;
live command acknowledgment before send; broker reconciliation; Backtest
continuous/resumed equality; and cold restart after deletion of run-local
files. Until those checks pass, the existing live runtime must not be switched
to an incomplete journal and the fixed Backtest execution guard stays closed.
