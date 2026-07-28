# Design and implement adaptive OMS, portfolio protection, and continuous risk

- Chat started: 2026-07-27, exact time unavailable
- Chat ended or last activity: 2026-07-28, exact time unavailable
- Summary written: 2026-07-28 07:42 PDT
- Chat/task identifier: `019fa4ce-2ab7-7490-a297-e38b4e496102`
- Repository or scope: `D:\TradingCodes\quant-research-workbench`, shared trading runtime, IBKR OMS, portfolio management, risk, and Canvas
- Related task-history entries: `TASK-0145`, `TASK-0149`
- Source completeness: Partial; the current task, repository, and bounded predecessor evidence were accessible, but some earlier assistant design responses were not available verbatim

## Narrative

The conversation began by separating remaining order-management work from
portfolio management. The user deferred most broad OMS test expansion but
required the entire portfolio design. Portfolio management had to serve Live
and Paper accounts backed by IBKR, support multiple accounts with different
capabilities and policies, and preserve Replay/Backtest compatibility.

The first completed outcome, recorded as `TASK-0145`, established portfolio
management between strategies and OMS. IBKR remained authoritative for live
account state and positions; strategy remained responsible for semantic
intent; portfolio owned explicit account binding, sizing, reservations,
allocations, and reconciliation; OMS alone owned broker commands. The
implementation added versioned per-account policies, canonical broker
synchronization, aggregate account groups, durable controls, API/Canvas
evidence, and simulated-mode parity.

Discussion then focused on how operators and developers could understand the
order-placement rule flow under quote changes, partial fills, cancellation
races, protective fills, and disconnects. The user initially argued that
current price should remain entirely within strategy because strategy already
uses QMD. They later retracted that constraint: strategy should select an
adaptive order policy, while OMS may access current price and other
execution-time observations needed to implement that policy. This
supersession is important. OMS does not decide the trade thesis or expand the
strategy/portfolio envelope, but it does own fast QMD/IBKR/simulated quote
consumption, broker order state, and repricing.

The user required partial entry fills to complete the remainder at a different
price when the chosen policy says so, with a subsecond response. The aligned
design used broker cumulative fill as authority. OMS derives an exact
incremental fill, retains reservation for the remaining quantity, reads the
newest allowed quote, and modifies the same broker root order. It does not
submit the original quantity again. Fill/modify/cancel races reconcile
cumulative quantity before another command.

Risk discussion expanded from fixed initial stops to strategy-selectable
protection profiles. A first entry may use one fixed stop, a causal pullback
swing low, volatility, or three/four independently sized slices tied to
different swing lows. Profit pocketing may preserve protection, move to
breakeven, lock profit, start broker or OMS-managed trailing, tighten/replan
remaining slices, or fully exit with optional re-entry. Adds may use an
independent slice, inherit the position stop, rebase all, tighten only, or
preserve existing protection. Account policy must allowlist policy/profile
identities and riskier choices such as stop-limit protection or emergency
auto-liquidation.

After design alignment, the user requested a complete implementation without
stopping. `TASK-0149` implemented versioned execution envelopes, partial-fill
policies, execution quote authority, protection contracts, independently
protected brackets, exact slice allocation, add/profit-pocket transitions,
dynamic trailing, and protection reconciliation. Portfolio sizing now uses the
worst permitted entry price and weighted risk across every protection slice.
The old duplicated notional/capital checks in runtime risk were narrowed to an
OMS order-safety guard; portfolio is the only sizing/capital authority.

OMS was changed from one global command lane and a static quote-derived ladder
to per-account command lanes plus a global IBKR warning-reply lane. Quote,
partial-fill, and bounded timer events wake adaptive repricing. Every order has
role, slice, broker mapping, and cumulative fill state. Cancelling one
protective sibling no longer falsely terminates the semantic position.
Protection coverage is reconciled against the authoritative broker position,
and a missing catastrophic backstop is repaired.

The implementation also closed lifecycle gaps identified in the prior audit.
An `outcome_unknown` submission retains its portfolio reservation. OMS terminal
and ambiguity transitions notify portfolio management. Protective fills carry
exact action, role, slice, cumulative quantity, and incremental quantity.
Planner flags for cancelling/reconciling protection are executed. OMS state is
persisted and recovered across runtime run IDs using stable strategy identity,
then reconciled with broker state before resuming.

Continuous risk was added as a separate event-driven supervisor. It consumes
portfolio loss/drawdown, broker/synchronization state, protection deficits,
and internal reaction latency. States are normal, entries paused,
reconciling, reduce only, emergency exit, and fully blocked. Escalation can be
automatic, but recovery is latched: an operator resume is queued to the
authenticated runtime and succeeds only after a fresh normal evaluation.
Backend commands cannot briefly bypass this latch. Durable operator commands
are merged into the in-memory portfolio authority before broker refresh so
they cannot be overwritten. Kill-entry commands preserve exits/protection;
emergency flatten is per-account, policy-gated, confirmation-gated in Canvas,
and requires fresh broker/quote state.

Canvas portfolio management now exposes continuous-risk state/reasons,
loss/drawdown, protection coverage/deficits, active OMS groups, policy/profile
identities, working limits, reaction latency, and pending operator commands.
The architecture and full event flow are documented in
`docs/architecture/ADAPTIVE_EXECUTION_AND_RISK.md`. IBKR OMS and portfolio
documents were updated, and a Paper-only acceptance launcher/runbook were
added.

Implementation validation passed Python compilation, all 189 repository unit
tests, the frontend TypeScript/Vite production build, and launcher help.
Bounded light/dark captures at UI scales 0.8 and 1.25 reported no objective
horizontal-overflow findings. Direct authenticated portfolio content could
not be rendered because the local session gate/backend dependencies were
unavailable; this was not represented as successful Live/Paper validation.
Temporary Vite processes were stopped. No IBKR order was submitted.

## Durable decisions

- Strategy chooses a versioned execution policy and protection profile; OMS
  may consume current QMD/IBKR/simulated price to execute adaptively.
- Portfolio alone binds accounts, sizes, reserves capital/risk, and reconciles
  allocation. OMS cannot increase approved quantity or exceed its envelope.
- Broker cumulative fill and position state are authoritative.
- Partial fills modify/cancel only the broker-known remainder according to the
  selected policy; cancellation remains pending until broker terminal state.
- Live protection is broker-held. Multiple slices are independent brackets,
  and stop changes are tighten-only unless an explicit add rebase policy says
  otherwise.
- Per-account policy controls execution/profile allowlists, loss bands,
  protection options, reaction latency, and emergency authority.
- Continuous-risk recovery never auto-resumes. Resume requires a fresh
  runtime-side evaluation.
- Live automation stays disabled until authenticated Paper acceptance passes.

## Delivered outcomes

- `TASK-0145`: multi-account portfolio management.
- `TASK-0149`: adaptive OMS, protection profiles, continuous risk, lifecycle
  repair, persistence, operator controls, evidence, docs, and tests.
- Core files:
  `src/trading_runtime/execution_policies.py`,
  `order_management.py`, `portfolio.py`, `risk_supervisor.py`, `runtime.py`,
  `strategy_orders.py`, and `journal.py`.
- Operator/UI files:
  `src/backend/portfolio_management_service.py` and
  `frontend/src/pages/CanvasConfigurationPage.tsx`.
- Acceptance:
  `scripts/run_ibkr_paper_oms_acceptance.py` and
  `docs/runbooks/IBKR_PAPER_OMS_ACCEPTANCE.md`.
- Focused adaptive/risk/recovery tests:
  `tests/test_adaptive_execution_risk.py`; full repository result: 189 tests
  passed.

## Unfinished or hanging work

The implementation phases are complete. The remaining dependency is
environmental deployment acceptance, not deferred deterministic code.

- Current state: authenticated IBKR Paper behavior is unverified.
- Why: no safe authenticated Paper session, operator-selected account,
  instrument, or current quote was supplied; blindly placing an order would
  be unsafe.
- Next action: run the preflight and controlled matrix in
  `docs/runbooks/IBKR_PAPER_OMS_ACCEPTANCE.md`, retain transcripts and
  latency/protection evidence, and approve warning IDs explicitly.
- Dependency/owner: operator with the authenticated IBKR Paper gateway.
- Related task: deployment gate following `TASK-0149`.

## Handoff to the next chat

Read `TASK-0145`, `TASK-0149`,
`docs/architecture/ADAPTIVE_EXECUTION_AND_RISK.md`,
`docs/architecture/IBKR_ORDER_MANAGEMENT.md`, and the Paper acceptance
runbook. Do not move current-price authority back entirely into strategy; that
position was explicitly superseded. Do not duplicate portfolio risk limits in
OMS or release `outcome_unknown` reservations. The next action is the
authenticated Paper matrix. It requires the user's account/instrument choices
and explicit execution confirmation; Live must remain disabled until it
passes.
