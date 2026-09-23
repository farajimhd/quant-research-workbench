# Strategy, portfolio, OMS, and broker

## Authorities

- Strategy consumes causal features and emits typed intents.
- Portfolio assigns account quantities and reserves account cash.
- Risk validates exposure, freshness, prices, and protection.
- OMS owns order groups, submission, modifications, reconciliation, and recovery.
- The broker adapter translates contracts. It does not invent strategy decisions.

Strategies are compiled Rust implementations with validated configuration.
Pin the effective strategy source and configuration. A candidate number alone is
not an implementation identity. An existing experimental strategy is not approved
for Live merely because it has been ported.

Strategy 350 is the current replacement candidate. The historical prior-close
gate remains in the executor and fails closed when unavailable. Its exact copied
source and configuration must be pinned; later source revisions cannot silently
replace a running or backtested strategy.
The Strategy 350 effective configuration is one typed, content-hashed bundle
per run consumer. It names the execution interval and hashes of its signal,
screen, price gate, MACD, noise, BOS, level book, rules, account risk, and
optional Watchlist contracts. It includes the frozen-gap configuration.
The run manifest pins the bundle hash. A decision must match that hash and
must check each supplied component against its bundle entry before exposure
can increase. A bundle hash alone does not prove a producer ran or that its
output is fresh.

## Multiple accounts

Position-dependent state is keyed by mode, broker session, account, strategy instance,
and instrument. Market features may be shared. Fill-dependent strategy state may not.

Each account declares cash budget, maximum allocation, risk budget, currency policy,
margin permission, position limits, and eligible instruments. Reserve cash across
all ticker intents before submission. Use fresh broker balances and existing holds.
Do not spend the same account cash concurrently from independent ticker workers.

One signal may create independent orders for several accounts. Group them with a
common decision ID but distinct command and bracket IDs. Fills are not atomic across
accounts. Record each account's result. Do not undo another account automatically
because one rejected its order. Define account-group failure policy in configuration.

## Mandatory brackets

Reject every exposure-increasing order without a valid broker-held stop and target.
This includes entries, additions, and the exposure-increasing part of reversals.
Reject missing prices, invalid ticks, mismatched quantities, wrong accounts, invalid
links, or unsupported protection behavior. No stop-only experimental bypass exists.

The approved order graph must cover partial fills and remaining exposure. Validate
child activation, quantities, cancellation relationships, and protection after
disconnects on the exact broker adapter. A locally constructed bracket is not proof
that all children are working at the broker.

Reduce-only exits, cancellations, and protective actions remain possible when new
exposure is blocked. They must not accidentally reverse the position.
Broker-held protection does not guarantee a fill price or eliminate halt risk.

## LULD policy

During the applicable regular session, require fresh official LULD bands.
Let `L` be the lower band, `U` the upper band, and `b` the approved price buffer.

| Side | Initial price requirements |
|---|---|
| Long | `L + b <= stop < entry < target <= U - b` |
| Short | `L + b <= target < entry < stop <= U - b` |

For market entries, validate against a conservative current entry-price bound.
The buffer is at least several valid ticks and may widen under an approved latency
policy. Exact settings require validation. If no feasible bracket remains, reject.
Do not move a strategy stop or target silently to pass these checks.

Trailing/profit-lock stops may cross the original entry. Apply a distinct protective
modification contract. Monitor changing bands and existing protection continuously.
Do not cancel the only protection while attempting a replacement.

Outside applicable LULD hours, use the explicit extended-session risk policy.
Estimated historical bands are not official bands. Record that capability difference.

## IBKR session contract

- Package the selected gateway and required authentication helper.
- Verify authentication, brokerage readiness, mode and tradeable account allowlists.
- Maintain keepalive and order/execution/connection streams.
- Discover trade permissions separately from portfolio visibility.
- Serialize complete unresolved confirmation chains within the brokerage session.
- Coordinate all account traffic under endpoint and session pacing limits.
- Reserve request capacity for protective actions and reconciliation.
- On uncertain submission, reconcile before retrying. Never blindly resubmit.

Client Portal is the initial adapter candidate. Its gateway authentication constraints
and exact deployment mode must be verified before implementation is released.
A bundled browser-login helper is not proof of unattended broker authentication.
Document required user login/MFA intervention. Do not bypass broker security controls.

Order-reply suppression uses a reviewed message-category allowlist. Apply it before
trading and after session renewal. Do not suppress every warning automatically.
Unknown prompts remain explicit. Order, fill, and connection updates stay enabled.
Cosmetic notifications are separate from trading protocol messages.

Current documentation lists a global Web API limit of 10 requests per second and
stricter endpoint limits. Recheck before release. Multi-account throughput must fit
the selected adapter. Rust concurrency cannot bypass broker pacing.

## Durability

Persist the authorized command envelope before broker submission. Include idempotency
identity, reservation, bracket graph, source decision, account, and config hash.
ClickHouse is the only durable command store. Wait for the configured durable insert
acknowledgment; do not acknowledge only an asynchronous server buffer.

Record states such as authorized, durable, submitting, acknowledged, unknown,
partially-filled, filled, rejected, and reconciled. Recovery validates reservations
against broker reality. The journal must represent unknown outcomes explicitly.
No claim of distributed exactly-once execution is made.

## Modes

Live, Paper, Shadow, and Backtest are distinct modes with distinct permissions.
Paper uses explicit paper accounts and credentials. Shadow submits no orders.
Backtest has no live broker capability. Paper validates protocol behavior, not real
execution quality. Deployment never arms Live automatically.
