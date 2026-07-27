# IBKR Order Management

## Authority boundary

`OrderManagementEngine` is the only strategy-runtime component allowed to call
the broker's place, modify, cancel, warning-reply, or reconciliation methods.
A strategy receives observations and emits `StrategyIntent` values such as
`enter_long`, `add_long`, `take_profit`, or `exit`. It never receives a broker
adapter and a strategy evaluation containing an `OrderRequest` is rejected.

The authorities are deliberately separate:

| Authority | Owns |
|---|---|
| Strategy | What position change is wanted, urgency, evidence, invalidation, and optional re-entry |
| Portfolio management | Exact account binding, final quantity, capital and risk allocation, capacity reservation, and portfolio reconciliation |
| Order management | Risk reservation, execution tactic, command ordering, warning policy, broker state, reconciliation, and durable command evidence |
| Order planner | IBKR-compatible bracket/OCA shape for one semantic intent |
| Broker adapter | Authenticated transport and exact Client Portal API resources |
| Canvas | Assignment commands and read-only presentation, never broker commands |

The former direct real-live order HTTP routes are retired. Manual order entry
creates the same semantic assignment/intent used by automatic trading, so
manual and automatic actions cannot bypass order management.

## Latency contract

The hot path does not request account summaries, live-order lists, or previews.
Risk and account state are primed before trading is enabled and refreshed in
the background. IBKR HTTP connections are pooled and reused. One asynchronous
command lane serializes place/modify/cancel with any mandatory warning reply,
because IBKR permits only one unresolved order reply chain at a time.

`decision_to_submit_ms` measures application time from entering submission to
the broker response. It includes broker/network response time and is persisted
with the acknowledgement. It is not an exchange-fill SLA. For the most urgent
tactic, all configured price steps are scheduled within 600 ms by default;
actual modification and fill time remain controlled by IBKR, network latency,
marketability, market state, and exchange conditions.

No blanket confirmation flag exists. Suppression and automatic confirmation
are both explicit message-ID allowlists, configured before enabling a paper or
live session.

## Price tactics

All aggressive actions use price-protected limit orders rather than unbounded
market orders. Prices are rounded toward execution using the contract tick.

| Urgency | Buy behavior | Sell behavior | Default schedule |
|---|---|---|---|
| `very_urgent` | Ask, then ask plus one tick per step | Bid, then bid minus one tick per step | 0, 150, 300, 450, 600 ms; maximum four crossed ticks |
| `urgent` | Ask once | Bid once | Immediate |
| `regular` | Midpoint, then progressively toward ask | Midpoint, then progressively toward bid | 0, 250, 500, 750 ms |
| `patient` | Bid, then midpoint | Ask, then midpoint | 0, 500 ms |

The tactic requires a positive, non-crossed NBBO and a positive tick size.
Live/paper quotes older than the configured maximum are rejected before a
command is sent. Repricing stops on a terminal order state or a full fill.

## Entry and protection

A fresh entry is submitted as one IBKR bracket:

1. a bounded limit parent;
2. an optional profit-target limit child;
3. a hard stop child;
4. an optional trailing-stop child.

Only the parent carries `cOID`; children use its value as `parentId`. The
children remain broker-held protection if the application disconnects.
Order-role tracking distinguishes entry, profit target, hard stop, trailing
stop, and managed exit. A protective child fill is sent back to the strategy
as an `exit`, never as another entry fill.

## Profit pocket and re-entry

A full profit pocket modifies the existing bracket's profit-target child to
the current bounded sell tactic, normally bid for `very_urgent`. This preserves
the existing stop/trailing siblings and avoids a cancel-then-replace interval
with no protection. If `buy_back` is true, re-entry becomes eligible only
after the exit fill is broker-confirmed and the strategy assignment moves to
re-entry cooldown.

Partial profit pockets are blocked in revision 2. The current Client Portal
`isSingleGroup` contract does not document an atomic proportional reduction of
all remaining protective siblings. Sending a partial sell without first
reconciling every child quantity can over-sell or leave stale protection.

If no managed target child exists, order management submits the planner's
standalone protected exit group. The strategy still does not place it.

## Short orders

Before `enter_short` or `add_short`, order management requests IBKR market-data
snapshot fields:

- `7636`: shortable shares;
- `7644`: shortability classification.

Missing authority, a non-shortable classification, or fewer available shares
than requested blocks the order before submission. The skipped intent,
required shares, returned fields, and reason are journaled. Covering an
existing short does not require a new borrow check. Sell exits from a long are
not classified as short entries.

## Broker communication policy

The policy is versioned and configured with:

| Environment variable | Meaning |
|---|---|
| `IBKR_SUPPRESS_ORDER_MESSAGE_IDS` | Known messages requested for session suppression |
| `IBKR_AUTO_CONFIRM_ORDER_MESSAGE_IDS` | Known warning IDs allowed to receive `confirmed=true` |
| `IBKR_MAXIMUM_REPLY_CHAIN` | Maximum sequential replies before fail-closed |
| `IBKR_MAXIMUM_EXECUTION_QUOTE_AGE_MS` | Maximum NBBO age for live/paper submission |
| `IBKR_MAXIMUM_REPRICE_TICKS` | Maximum crossed ticks for `very_urgent` |

For each warning:

1. persist the complete broker warning;
2. confirm only when every returned message ID is allowlisted;
3. reply `confirmed=false` to every unknown or missing ID;
4. persist the decision and policy version;
5. continue the sequential reply chain only after the broker responds;
6. reject repeated reply IDs, parallel unresolved warnings, or an overlong
   chain.

Suppression removes an avoidable round trip only for reviewed message IDs; it
does not weaken an unknown warning. The allowlists must first be exercised and
reviewed in paper trading. Live should use a separately approved policy.

## State, failure, and recovery

Managed groups use:

`created -> risk_reserved -> submitting -> warning_pending -> acknowledged ->
working/partially_filled -> filled/cancelled/rejected`

Additional states are `cancel_pending`, `outcome_unknown`, and
`policy_blocked`.

- A cancellation response means the request was accepted, not that the order
  is cancelled. Protection remains `cancel_pending` until broker state proves
  the terminal result.
- A transport exception after submission is `outcome_unknown`; the same intent
  is never blindly resubmitted.
- Disconnect freezes new intents. Broker websocket order/trade messages and
  periodic live-order reconciliation recover authoritative state.
- Commands, acknowledgements, warning transcripts, price changes, shortability
  denials, state transitions, fills, and measured latency are committed to the
  trading journal and exposed in Canvas Strategy.

## Deployment gates

Automated live execution remains disabled until all of the following are
completed:

1. authenticated IBKR paper tests cover entry bracket, each protective fill,
   modify, cancel, warning chains, disconnect/reconcile, and short denial;
2. application decision-to-submit latency is measured under representative
   load, including 100 ms strategy observations;
3. each suppressed/auto-confirmed message ID has a documented paper transcript
   and explicit approval;
4. positions and every open protective quantity reconcile after partial fills,
   restart, and network loss;
5. the operator can pause entries while exits and broker-held protection remain
   active.

The deterministic simulator validates state and grouping semantics, but it
does not prove IBKR gateway latency, exchange fills, borrow availability, or
warning behavior.

## IBKR references

- [Client Portal Web API v1.0 documentation](https://www.interactivebrokers.com/docs/web-api/web-api-v-1-0-documentation/introduction)
- [Place-order replies and warning suppression](https://www.interactivebrokers.com/docs/web-api/web-api-v-1-0-documentation/endpoints/orders/place-order-reply-confirmation)
- [Bracket orders and OCA groups](https://www.interactivebrokers.com/docs/web-api/web-api-v-1-0-documentation/endpoints/orders/bracket-orders-oca-groups)
- [Modify order](https://www.interactivebrokers.com/docs/web-api/web-api-v-1-0-documentation/endpoints/orders/modify-order)
- [Market-data fields, including 7636 and 7644](https://www.interactivebrokers.com/docs/web-api/web-api-v-1-0-documentation/endpoints/market-data/market-data-fields)
- [Client Portal Gateway websocket connection](https://www.interactivebrokers.com/docs/web-api/web-api-v-1-0-documentation/websockets/connection-guide/establishing-the-websocket-with-client-portal-gateway)
