# IBKR Paper OMS Acceptance

This is the final environment-dependent gate before Live automation. Passing
deterministic tests is necessary but does not prove Client Portal gateway
latency, broker warnings, borrow, exchange fills, or reconnection behavior.

## Safety

- Use a dedicated authenticated IBKR Paper session and a `DU...` account.
- Use one-share quantities and a liquid stock during regular hours.
- Verify the supplied conid and quote independently.
- The harness defaults to account discovery and IBKR what-if preview only.
- Real Paper submission requires both `--execute` and the literal
  `I_UNDERSTAND_THIS_PLACES_PAPER_ORDERS`.
- Runtime evidence is written under
  `D:\TradingML\runtimes\trading\ibkr-paper-acceptance`; nothing generated is
  stored in the repository.
- Inspect TWS/Client Portal after every scenario and confirm no working order
  remains.

## Preflight

Before starting the harness, publish an Approved Release that contains the
exact `DU...` account id, Paper mode on that account binding, an enabled Paper
deployment, the intended Portfolio policy revision, and the selected OMS,
execution-policy, and protection-profile revisions. The application Paper/Live
preflight blocks when the release is missing, the broker discovery id differs,
or no enabled deployment covers the selected mode.

With the Client Portal gateway authenticated:

```powershell
C:\Users\g835l\miniconda3\python.exe scripts\run_ibkr_paper_oms_acceptance.py `
  --account-id DU1234567 `
  --conid 265598 `
  --ticker AAPL `
  --bid 200.00 `
  --ask 200.02 `
  --tick-size 0.01 `
  --quantity 1 `
  --maximum-buy-price 200.05 `
  --stop-price 198.00
```

The preflight must show the exact account, fresh summary/ledger, positions,
open-order count, and an acceptable what-if response. It does not place an
order.

## Entry/cancel acceptance

Repeat with current prices and:

```text
--execute --confirmation I_UNDERSTAND_THIS_PLACES_PAPER_ORDERS
```

The scenario submits the versioned adaptive entry plus broker-held stop through
the real OMS, requests cancellation of the entry root, then reconciles. The
manifest and SQLite journal are retained in the run directory.

Required observations:

1. bracket shape and exact account are correct;
2. no unbounded market order is sent;
3. price remains inside the maximum-buy envelope;
4. only reviewed warning IDs are confirmed;
5. cancellation is recorded as pending until broker-terminal state;
6. the entry and its children leave no unexplained working order.

## Full authenticated matrix

The following scenarios require operator-controlled prices or gateway
interruption and therefore are performed deliberately rather than automated
blindly:

| Scenario | Required evidence |
|---|---|
| Entry full fill | Parent fill, correct allocation, stop coverage equals position |
| Entry partial fill | Exact cumulative/incremental quantity, remainder replacement within 100 ms internally, no duplicate quantity |
| Hard-stop fill | Role/slice reported as exit, position and remaining protection reconcile |
| Profit-target fill | OCA sibling lifecycle and exit attribution |
| Broker trailing stop | Correct amount/percent payload and fill attribution |
| Partial pocket | Reduction only after fill; every remaining stop resized to IBKR position |
| Modify race | Fill during replace produces no over-order |
| Cancel race | Fill during cancel remains allocated; reservation releases only terminal remainder |
| Warning chain | Complete transcript; known IDs only; unknown ID declined |
| Disconnect | Entries freeze; broker protection remains; reconnect reconciles before resume |
| Restart | OMS group/mappings/fills restore; no blind resubmission |
| Outcome unknown | Reservation retained and matched by client/broker ID |
| Short denial | Fields 7636/7644 recorded; no order sent |
| Multi-account | Independent account lanes/configs; no account rerouting |
| Kill entries | Roots cancelled; exits/protection remain |
| Emergency flatten | Explicitly enabled account only; fresh quote and bounded protected exit |

For every row, retain:

- acceptance run directory;
- account and policy/profile identities, without secrets;
- journal warning/command/state transcript;
- Canvas screenshot at default and compact scale;
- measured `internal_reaction_ms` and `decision_to_submit_ms`;
- broker position/open-order snapshot after reconciliation;
- operator verdict and any warning-ID approval.

Live automation remains disabled until every applicable row passes and warning
IDs are explicitly approved. Paper success does not authorize broadening a
cash/registered account, enabling shorting, or enabling emergency
auto-liquidation on another account.
