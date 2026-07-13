# Shared Trading Runtime

## Boundary

`src/trading_runtime` is the single order, execution, account, position,
portfolio, risk, and run-journal authority. Runtime modes change only three
dependencies:

| Mode | Market source | Broker | Clock |
|---|---|---|---|
| Live / Paper | live `qmd-gateway` | `IbkrClientPortalAdapter` | wall clock |
| Replay | Rust `qmd_history_gateway` | `SimulatedBrokerAdapter` | controllable historical clock |
| Backtest | Rust `qmd_history_gateway` | `SimulatedBrokerAdapter` | maximum-speed historical clock |
| Backtest Debug | the exact backtest event window/cursor | `SimulatedBrokerAdapter` | stepped historical clock |

The broker-facing contract retains Client Portal names such as `acctId`,
`cOID`, `conid`, `orderType`, `auxPrice`, `tif`, and `outsideRTH`. Strategies
must not construct a different order type for simulation. Live and simulated
brokers both expose accounts, preview, place, warning reply, modify, cancel,
live orders, executions, positions, account summary, and ledger resources.

## Historical semantics

The date selected for Backtest is an exclusive anchor: the configured number
of prior exchange sessions is used. The Replay date is inclusive and replay
starts at 04:00 America/New_York on that session. Backtest Debug resolves the
same window as Backtest and must use the original strategy revision,
configuration, event cursor, simulation configuration, and checkpoints.

Only strategies persisted with `automatic=true` can run in Backtest or
Backtest Debug. Strategy definitions are immutable by `(strategy_id, revision)`.

Live and historical market sources are separate Rust binaries. The existing
QMD crate exports `qmd_core`, and both binaries compile against its canonical
event decoder and enriched-bar engine. Historical condition/indicator tokens
and tape ids are restored through the canonical ClickHouse reference tables
before canonical events reach the runtime.

## Simulation

The simulated broker implements IBKR order states and request fields. It
supports MKT, LMT, STP, STOP_LIMIT, MIDPRICE, TRAIL, and TRAILLMT validation;
quote-aware execution; trade fallback; deterministic liquidity participation;
partial fills; brackets; OCA sibling cancellation; DAY/GTC handling;
`outsideRTH`; commissions; per-account state; cash, positions, summary, and
ledger. It does not imitate network/session faults; those are validated against
the paper Client Portal Gateway.

## Persistence

Every runtime fact first commits to a SQLite WAL journal and outbox. This is the
crash-recovery boundary for order commands and checkpoints. The trading journal
gateway mirrors records to `q_live` tables with the fixed `tr_` prefix:

- `tr_strategy_v1`, `tr_run_v1`, `tr_run_account_v1`
- `tr_journal_v1`, `tr_signal_v1`, `tr_order_event_v1`
- `tr_fill_v1`, `tr_trade_v1`
- `tr_portfolio_v1`, `tr_position_v1`
- `tr_checkpoint_v1`, `tr_reconcile_v1`

ClickHouse is the durable audit/analytics store, not the synchronous command
queue. Outbox rows are acknowledged only after the generic journal row and the
corresponding typed row are accepted.

## Current cutover status

The new authorities and services are implemented. The old `/api/backtests/*`
jobs still execute `src/backtest` while feature-dependent strategy inputs are
migrated from prepared provider bars to event-derived bars. Until that cutover
is complete, those routes are legacy research paths and must not be treated as
proof of replay/live brokerage parity.
