# Native programme execution assembly

The generic programme path uses `TradingRuntime`, its existing OMS, the shared
Portfolio account/group authority and the selected native broker. It consumes
completed contextual QMD/Signal Stream boundaries; it does not recreate market
features or infer fills from a programme proposal.

Cold QMD feature and volume certification retain one owned persistent
ClickHouse connection per admitted reader worker across listings. Every listing
still receives its original seal, source and content checks; cancellation drains
in-flight I/O before closing the worker connection. The volume census retains
its independent source verification before and after the complete packet.
The Backtest runner's exact read catalogue includes the shared contextual Signal
Stream tables. Its storage preflight verifies those schemas and SSD placement;
the journal principal has no INSERT authority on the source tables.

Audit admission preserves the selected broker's fill model. V19 `cost_bps`
deducts a modeled notional charge; native market slippage changes fill prices
and is reported separately, even when both numbers happen to match. Commission
and fee-inclusive risk contracts must still match. Nonzero modeled notional
charges remain blocked until an explicit native charge implementation exists.

## Current composition

1. Load the explicitly selected certified configuration, execution binding,
   execution-risk contract and installed programme release. Acquire
   `ProgramV4KeeperLease` with those installed executable and configuration pins.
2. Open `open_program_v4_writer(lease, query_limits=...)`. It opens the managed
   `program_v4_runner` connection off-loop and audits exact grants, normalized
   storage and the actual Keeper lease. This runner is not a generic publication
   role. Its optional private credential file is `program_v4_runner.env` with
   exactly the three `PROGRAM_V4_RUNNER_CLICKHOUSE_{URL,USER,PASSWORD}` keys.
3. Construct the native `ArteJournalWriter`, `ProgramExecutionJournal`, recovered
   shared Portfolio image and `ProgramPortfolioTransitionAuthority`. Existing
   shared account heads identify the actual source run; they are not per-program
   capital accounts. The account and every contributing group image must still
   match its committed shared head. Stale images require explicit cold recovery.
4. `build_program_runtime(...)` binds the actual actor, planner, native broker,
   journal and shared Portfolio authority. Bootstrap the contextual Signal Stream
   source, then call `assembly.require_source_context(...)` with its actual
   `SignalExecutionContext`, certified configuration and source runtime.
5. Paper/Live composition installs that concrete runtime using the existing
   `LiveStrategyRuntimeSupervisor.install_program_runtime`, starts that same
   worker, and submits prepared boundaries using `submit_program_boundary`.
   This uses its existing bounded queue. It neither hydrates SQLite activations
   nor sends legacy strategy observations to the programme actor. An installed
   programme is rejected by the legacy runtime builder before SQLite opens.
   Replay can call the same native boundary dispatcher after its native broker
   has consumed the actual admitted liquidity boundary.

The native dispatcher commits original semantic intents and the qualified
decision completion before Portfolio admission or OMS submission. Portfolio
source records, snapshot readback and Keeper receipts precede installation.
Every broker mutation passes through `ProgramBrokerGateway`, with an exact
typed command receipt before the selected native broker is called. New repair
requests first publish their actual new managed plan/OMS sidecar and commands;
they cannot borrow an old client-order identity.

Paper/Live boundary completion reads actual IBKR executions and final
commissions, deduplicates their exact contents and commits normalized source
facts before projecting the owned actor ledger. External account fills retain
their source facts without becoming programme-owned positions. Broker fills
newer than the completed source clock remain explicitly deferred and block
programme dispatch at that clock. Outstanding commissions block acquisition.
Actual post-fill risk excess is separately recorded and pauses new entries;
it does not resize a fill or alter its protective stop.

## Qualification boundary

Source tests cover the concrete factory, contextual source, qualified decision
receipt, shared Portfolio admission/snapshot/Keeper receipts, actual native OMS
and receipt-before-simulated-broker submission. Isolated native IBKR transport
tests cover actual fill/commission ingestion, deduplication and deferral.
These tests do not qualify a real Paper/Live account, broker warning surface,
operational latency or a production host deployment.

Managed operator provisioning must apply the exact programme runner plan and
credential, then construct and install the selected native composition. Merely
publishing a programme or selecting a mode does not construct that host.

An existing financial event prefix is rejected by `build_program_runtime`.
`recover_program_policy_async` has useful normalized event readers, but the
current recovery assembly `build_recovered_program_runtime` explicitly requires
Backtest, `BacktestMemoryJournal`, a simulated broker checkpoint and a typed
Backtest OMS image. Paper/Live restart requires a separately verified real-broker
OMS/execution/commission recovery composition. The source does not discard the
old prefix or quietly start an empty actor. Existing Backtest recovery remains
unchanged.
