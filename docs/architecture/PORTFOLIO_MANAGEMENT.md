# Multi-Account Portfolio Management

## Purpose and authority

`PortfolioManagementEngine` is the authority that decides whether and how much
a configured account may trade. It sits between strategy or manual position
requests and `OrderManagementEngine`:

```text
strategy/manual request
  -> portfolio decision, sizing, reservation, allocation
  -> approved StrategyIntent
  -> OrderManagementEngine
  -> exact account-specific broker command
```

The boundaries are strict:

| Authority | Owns |
|---|---|
| IBKR | Live and paper cash, margin, buying power, positions, orders, executions, commissions, and account capabilities |
| Strategy | Desired action, evidence, urgency, reference price, invalidation, target, and optional requested quantity cap |
| Portfolio management | Explicit account binding, final quantity, capital allocation, exposure and loss policy, reservations, strategy attribution, controls, and reconciliation |
| Order management | Broker order shape, command ordering, warning replies, price tactics, submission, modification, cancellation, and ambiguous-outcome recovery |
| Canvas | Versioned policy and broker evidence, operator entry controls, reduce-only control, and reconciliation requests |

Portfolio management never sends a broker command. Order management never
chooses another account or expands a portfolio-approved quantity.

## Account and session model

Every assignment names a stable `account_key`. The externally supplied broker
binding maps that key to one exact IBKR account id. Portfolio management never
silently reroutes an intent to an account with more capacity.

For a configured application, the newest immutable Approved Release owns the
account bindings, portfolio policies, aggregate groups, strategy-account
mandates, and eligible runtime modes. `IBKR_ACCOUNTS_JSON` or
`IBKR_ACCOUNT_<KEY>_ID` remains an external discovery input only: the broker
account returned for a stable key must exactly match the release's
`source_account_id`. A mismatch fails closed before broker operation.

One supervised Client Portal brokerage session belongs to one IBKR username
and mode. A live username may expose cash, margin, and registered accounts in
one session. Paper uses a separate username/session. Every order still carries
the exact account id.

Example:

```json
{
  "policies": {
    "margin-growth@3": {
      "policy_id": "margin-growth",
      "revision": 3,
      "allow_margin": true,
      "allow_short": true,
      "eligible_equity_fraction": 0.9,
      "minimum_cash_reserve": 10000,
      "maximum_buying_power_utilization": 0.75,
      "maximum_gross_exposure": 500000,
      "maximum_net_long_exposure": 400000,
      "maximum_net_short_exposure": 100000,
      "maximum_position_fraction": 0.12,
      "maximum_ticker_fraction": 0.15,
      "maximum_planned_risk_fraction": 0.005,
      "maximum_open_risk_fraction": 0.025,
      "maximum_daily_loss": 5000,
      "maximum_drawdown": 15000
    },
    "registered-long-only@2": {
      "policy_id": "registered-long-only",
      "revision": 2,
      "allow_margin": false,
      "allow_short": false,
      "minimum_cash_reserve": 1000,
      "maximum_buying_power_utilization": 0.95,
      "maximum_position_fraction": 0.08,
      "maximum_ticker_fraction": 0.1
    }
  },
  "accounts": {
    "live-margin": {
      "session_key": "ibkr-live-primary",
      "policy": "margin-growth@3",
      "strategy_allocations": {
        "long-momentum-campaign": 0.35,
        "default": 0.2
      }
    },
    "live-rrsp": {
      "session_key": "ibkr-live-primary",
      "policy": "registered-long-only@2",
      "strategy_allocations": {
        "long-momentum-campaign": 0.2
      }
    }
  },
  "groups": {
    "total-live-capital": {
      "accounts": ["live-margin", "live-rrsp"],
      "maximum_gross_exposure": 650000,
      "maximum_ticker_exposure": 100000
    }
  }
}
```

This JSON shape is retained only as a legacy bootstrap when no Approved
Release exists. New and migrated deployments configure the equivalent fields
on **Configuration -> Portfolio & Risk**, bind exact accounts on **Accounts &
Sessions**, then publish them together. Once an Approved Release exists,
`PORTFOLIO_MANAGEMENT_JSON` is not consulted and cannot override it.

Account-class capability is fail-closed. Cash and registered accounts cannot
be made margin-enabled or short-enabled by a portfolio policy. Configuration
may narrow broker/account capability but never broaden it.

## Policy contract

`PortfolioPolicy` is immutable by `(policy_id, revision)` and covers:

- eligible equity and minimum cash reserve;
- buying-power utilization;
- gross, net-long, and net-short exposure;
- position, ticker, and strategy allocation fractions;
- planned risk per trade and total open risk;
- open-position, order-quantity, and order-notional limits;
- daily loss and peak-to-current drawdown;
- long, short, margin, outside-RTH, and overnight capability;
- allowed security types and restricted symbols;
- broker-state freshness and unattributed-position behavior.

The effective permission is always the intersection of broker capability,
account class, immutable portfolio policy, assignment permission, and current
operational control.

## Decision and sizing

A `StrategyIntent` remains broker-neutral. Portfolio management treats its
quantity as a requested cap, not an account entitlement. It creates a durable
`PortfolioDecision` containing:

- account key and exact account id;
- policy revision and broker snapshot id;
- approved, resized, rejected, or deferred status;
- requested and approved quantity;
- notional and planned loss;
- pre- and post-decision portfolio metrics;
- limiting or rejection reasons;
- reservation identity.

For an entry or add, approved quantity is the smallest positive ceiling from:

```text
requested quantity
order quantity and notional
available funds after reserve
gross and directional net exposure
position and ticker concentration
strategy allocation
planned-risk and total-open-risk budgets
optional aggregate group limits
```

When an invalidation price is present:

```text
risk_per_unit = abs(reference_price - invalidation_price)
risk_quantity = eligible_equity * maximum_planned_risk_fraction / risk_per_unit
```

Reduction requests are bounded by the broker-known position and may never
reverse it. A stale live snapshot blocks entries and adds but can permit a
strictly broker-bounded reduction.

## Reservations and fill allocation

Approval durably reserves notional, planned loss, account capacity, ticker
capacity, strategy allocation, and configured group capacity before OMS
submission. Concurrent account decisions serialize through an account lock;
accounts in an aggregate group also acquire the group's lock.

- A partial fill converts only the filled portion into an allocation.
- The unfilled portion remains reserved.
- A confirmed terminal state releases the remainder exactly once.
- Cancellation acceptance alone does not release capacity.
- An ambiguous outcome retains its reservation until reconciliation.
- A pending exit does not free capital before its fill.
- Protective and OCA siblings do not create entry exposure.

Allocation lots are keyed by account, strategy revision, assignment, and
ticker. IBKR remains authoritative for the net position. Internal lots explain
strategy ownership. Any difference is explicit unattributed/external inventory
and, under the default policy, blocks new exposure in that ticker until
resolved.

## IBKR synchronization

Before entries are enabled, each configured account must pass:

1. authenticated session;
2. account discovery and exact binding;
3. view/trade permission checks;
4. account summary and ledger reads;
5. complete position snapshot;
6. open-order and recent-execution reads;
7. operational-state restore;
8. broker-versus-internal reconciliation.

The account lifecycle is:

```text
initializing
  -> synchronized
  -> degraded / reconciling / entries_blocked / fully_blocked
  -> synchronized
```

`TradingStateProjector` retains the last complete position snapshot when a new
snapshot is incomplete. A complete empty snapshot clears positions. Freshness
is tracked by account and source component rather than inferred from one
global connection flag.

The live controller uses websocket order, execution, account-value, and ledger
updates plus periodic authoritative positions, orders, executions, summary,
and ledger reconciliation. Reconnect, ambiguous submission, and unknown broker
state request immediate reconciliation.

Recommended fail-closed behavior:

| State | Entry/add | Broker-bounded reduction | Existing broker protection |
|---|---:|---:|---:|
| Account values stale | Block | Permit | Retain |
| Positions stale | Block | Reconcile or use last confirmed quantity | Retain |
| Orders/executions uncertain | Block | Reconcile first | Retain |
| Gateway disconnected | Block | Cannot submit | Broker-held protection remains |
| Policy invalid | Block | Permit reduce-only | Retain |

## Multiple accounts and groups

Accounts are independent by default. The same strategy in three accounts uses
three explicit assignments and receives three separate portfolio decisions.

Optional groups enforce aggregate gross and ticker caps. They do not imply:

- automatic account selection;
- mirrored orders;
- atomic cross-account execution;
- rollback of a fill in another account.

Each account's result remains separately durable and visible.

## Live, Paper, Replay, and Backtest

The portfolio engine and decision schema are shared:

| Mode | State authority | Commands |
|---|---|---|
| Live | IBKR live account | Real OMS |
| Paper | IBKR paper account | Paper OMS |
| Replay | `SimulatedBrokerAdapter` at replay clock | Simulated OMS |
| Backtest / Debug | `SimulatedBrokerAdapter` at historical clock | Simulated OMS |

Historical modes configure starting capital, account class, margin, commission,
settlement, and policy revision. They never fabricate IBKR fields. The same
policy decisions, reservations, and rejection reasons make historical capacity
comparable to deployable behavior.

## Persistence, APIs, and Canvas

SQLite WAL remains the synchronous crash boundary. The `portfolio_states`
table stores account controls, reservations, allocations, and recovery
watermarks. Every decision, reservation, allocation, control transition, and
reconciliation event also enters the generic journal/outbox under
`category=portfolio_management`.

Operational artifacts default under `D:\TradingML\runtimes\trading`; use
`TRADING_RUNTIME_ROOT` or `TRADING_JOURNAL_PATH` to override the laptop default
with the correct runtime root for the executing machine.

APIs:

- `GET /api/trading/portfolio-management`
- `POST /api/trading/portfolio-management/{account_key}/commands`
- `GET /api/trading/portfolio`, which includes management evidence in
  Live/Paper
- `GET /api/trading/configuration/effective?mode=<mode>&approved=true`, which
  shows the exact release, eligible deployments, account bindings, portfolio
  policy identities, and OMS identities projected for a mode

Supported operator commands are pause entries, resume entries, reduce-only,
reconcile, kill open entry remainders, confirmation-gated emergency flatten,
select a configured policy revision, and enable or disable one configured
strategy allocation. Policy selection always pauses new entries until the
operator explicitly resumes them. Capability narrowing is reapplied when a
policy is selected, so a cash or registered account cannot acquire margin or
short authority through a broader policy. Portfolio commands are durably
queued; the trading runtime is the only consumer allowed to ask OMS for a
broker action. The Portfolio Canvas container shows account policy,
synchronization and control state, capital and risk headroom, reservations,
allocations, reconciliation differences, optional aggregate groups, and the
broker watermark. It also shows continuous-risk state/reasons, daily
loss/drawdown, protection required/covered/deficit, managed OMS groups and
their policy/profile identities, working limits, reaction latency, and pending
operator commands. Replay and Backtest render the same evidence read-only with
a simulated authority.

Continuous risk maps account observations to `normal`, `entries_paused`,
`reconciling`, `reduce_only`, `emergency_exit`, or `fully_blocked`. Thresholds
and emergency-auto-liquidation permission are per account. Escalation is
automatic; recovery is latched and requires an explicit operator resume after
a fresh normal evaluation. A disconnect or stale account state never
auto-enables entries.

## Deployment invariants

- Every live/paper entry has one explicit account key and exact IBKR account.
- Portfolio approval and reservation commit before OMS sees the intent.
- OMS cannot increase approved quantity or reroute the account.
- Unknown/stale account state cannot open exposure.
- Pending exits do not prematurely release capacity.
- Internal allocation plus explicit unattributed inventory reconciles to the
  broker position.
- Cash/registered capability cannot be broadened through configuration.
- Replay and Backtest run the same portfolio policy code.
- Risk sizing uses the worst permitted execution-envelope price and the
  weighted loss of every protection slice.
- Account policy allowlists exact execution policies and protection profiles,
  maximum slice count, stop-limit use, partial pockets, reaction latency, and
  emergency-auto-liquidation.
