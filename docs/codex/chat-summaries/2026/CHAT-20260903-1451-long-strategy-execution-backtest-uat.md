# Repair long-strategy execution, backtest review, and historical structure authority

- Chat started: 2026-09-03 14:51:52 PDT
- Chat ended or last activity: 2026-09-04 16:04:51 PDT
- Summary written: 2026-09-04 16:04:51 PDT
- Chat/task identifier: `01a06941-c1c8-7dd2-a1ad-3208abed64e8`
- Repository or scope: `D:\TradingCodes\quant-research-workbench`; long-momentum Strategy, Portfolio, OMS, QMD History, Backtest, and chart presentation
- Related task-history entries: `TASK-0014`, `TASK-0051`, `TASK-0206`
- Source completeness: Partial; the current task, compacted conversation context, repository commits, prior durable summaries, and final runtime evidence were accessible, but compacted verbatim turns were not.

### Narrative

This continuation began with the user reviewing a failed long-strategy backtest and rejecting several behaviors that did not match the intended structural breakout policy. The central requirement was that Strategy own its entry, support-based protection, and structural targets: OMS may execute and maintain those instructions but must not replace a strategy-selected stop with a generic hybrid risk stop. Resistance can never be used as a long stop. Entry and target level qualification was migrated away from legacy hold probability to the Unified Structural Level Book's ticker-normalized `ticker_relative_quality_score >= 20%`. The user clarified that entry candidates are the three highest qualified resistance records at or below the live high of day; a long wick above the book does not create a synthetic high-of-day level. Chart terminology must identify resistance as R1/R2/R3 and support as S1/S2/S3.

Sizing investigation found an unrequested one-third mandate rule that recalculated each tranche from declining internal available cash. The user explicitly removed that design. Commit `67b3c9e2` made Portfolio recalculate every order from the latest broker account snapshot, including newly realized cash, while retaining stop-distance risk, settled-cash restrictions for cash accounts, reservations, exposure limits, and mandate constraints. The long-squeeze request now asks for all currently available eligible cash, Portfolio applies an 8% planned/open-risk ceiling, and the hard quantity cap is 10,000 shares. This preserves Portfolio as the sizing authority and prevents Strategy from fabricating a fixed position budget.

The chart then received separate intent and execution evidence. Commit `d853fde4` renders an entry or exit intent on the candle where Strategy issued it and renders final or partial fills at their actual fill timestamps, with configurable labels containing quantity and price. Fill labels remain distinct from intent labels so execution latency is visible. The follow-up retained realized profit/loss text and added initial supports, the chosen stop, entry resistances, and the evolving target ladder for long lifecycles, with the reverse semantic roles for shorts. Presentation settings remain chart-instance configuration rather than execution authority.

Completed Backtest review was expanded in commits `36a3ccbc` and `fa397b24`. Completed-run selection now exposes the run time, fill count, P&L, ticker set, and tested start/end period. Replay, Backtest, Backtest Debug, and Live initialization surfaces use the shared application dropdown pattern instead of page-specific native-looking controls. The backend projects these values from completed run evidence rather than requiring frontend inference.

A later run stopped because profit pocketing attempted a partial protected exit while the CPAPI `isSingleGroup` contract could not guarantee proportional reduction of every protective child. The user confirmed that profit pocketing was not part of this strategy. Commit `69b97473` disabled it in the long-momentum definition, registry, defaults, and migration path while preserving ordinary structural target fills and full exits.

The user next identified a one-second entry delay: price crossed a structural level at 04:02:52, but the strategy waited for the next completed candle. The latest clarification was that the price crossing must be evaluated on each causal real-time trade event; MACD is read as available at that evaluation time and must not make the price trigger wait for a later candle. Commit `dc35e26f` implemented event-native top-three resistance crossing and updated the architecture documents. The strategy still requires its configured MACD, executable-VWAP, liquidity, volume, and freshness gates. It does not accept a crossing merely because a later event makes the other operands favorable.

The user also questioned five-to-six-second fills under the backtest's 25% execution-participation baseline. The durable decision was to preserve execution realism: a valid entry order remains working and continues filling under the participation model until its requested quantity fills or a genuine strategy exit condition occurs. It must not be silently cancelled only because one event filled a small portion. Premarket liquidation cannot use market orders; it must continue selling at the bid, while regular-session liquidation may use a market order when policy permits it.

Detailed chart comparison exposed additional problems. An early request had crossed resistance without valid MACD separation, liquidity/volume evidence was uncertain, the initial target did not use the third qualified resistance, later targets advanced without a meaningful close above the preceding frontier, and another entry used a lower level even though the high-of-day resistance set had changed. Commit `71b9fdba` enforced the structural and non-structural gates from live causal evidence, added multi-ticker Backtest UI and backend support, and removed misleading L1/L2/L3 naming. It did not hardcode the reported SUGP timestamps or prices.

Commit `584703d0` consolidated the causal repair. Strategy snapshots now preserve the exact support and resistance book used at entry and target decisions, select stops only from qualified support, retain working entry remainders until exit authority becomes true, and move targets through qualified resistance only after the configured meaningful crossing/close contract. OMS preserves a valid strategy protection selection instead of overwriting it. Chart labels again include lifecycle P&L and show the three structural levels at entry and exit. Focused strategy, OMS, configuration, replay, and frontend contracts were added or updated.

That commit also introduced a regression while attempting to make delayed historical reports execution-aware. Historical structural reconstruction was incorrectly changed to require execution-clock sidecar coverage across the complete 180-day structural horizon. A backtest whose prepared one-second window needed only six ticker-days consequently failed with `covered 6/126 ticker-days`, even though the certified structural campaign was intentionally built using the approved SIP-availability plus canonical-condition approximation. The user's latest clarification superseded the attempted historical execution-clock requirement: live data can place delayed trades by its native execution clock, but the current historical campaign remains the approved approximation.

Commit `d44d09a0` restored structure algorithm version 16, made the same no-execution-clock policy control both structural revision identity and structural event streaming, and retained execution-clock enforcement for ordinary execution-aware historical bar/indicator paths. It also removed obsolete frontend source-string assertions and the retired seven-signal expectation, repaired the scanner VWAP test with fresh prevailing NBBO quotes instead of weakening production VWAP eligibility, and registered `price_change_1_bar_pct` as a typed QMD-owned bar-timeframe percentage Data Field. Validation passed all 225 QMD Gateway library tests, all 99 QMD History library tests, and 42 focused registry/frontend tests. No strategy backtest was launched.

The first retry still produced the identical 502 because port 8801 was owned by an unregistered, stale release binary reporting structure algorithm 17. The scoped workspace stop correctly refused to kill an unowned process. After resolving the exact listener to PID 40428 at `D:\TradingML\runtimes\qmd_history_gateway\cargo-target\release\qmd-history-gateway.exe`, only that verified process was stopped and the canonical QMD History launcher rebuilt and restarted it. Final `/health` evidence reported `ready`, structure algorithm 16, calculation revision `qmd-derived-v57`, and checkpoint set `canonical-tradable-20250101-20260831-v18-sip-condition-v1`. A fresh strategy backtest remains intentionally unperformed so the user can conduct the acceptance run.

### Durable decisions

#### Confirmed requirements

- Long entries cross the live top three qualified resistance records at or below high of day on causal trade events; there is no one-second candle-close delay for the price trigger.
- Entry, stop, and target levels require `ticker_relative_quality_score >= 20%`; legacy hold-probability thresholds are not strategy authority.
- A long protective stop must come from qualified support. OMS must preserve a valid strategy protection selection.
- The initial profit target is the third qualified resistance in the entry snapshot. Target advancement requires the configured meaningful structural pass and must retain exact evidence.
- Liquidity, volume, MACD, VWAP, freshness, and structural gates all remain mandatory where configured. No reported timestamp, ticker, or price may be hardcoded.
- Portfolio sizes every order from the latest broker cash/account state and current stop risk, with an 8% ceiling and 10,000-share cap. Equal or frozen one-third tranches are rejected.
- Entry remainders keep working under execution-participation realism until filled or an actual exit becomes authoritative.
- Profit pocketing is disabled for this strategy. Premarket liquidation sells at bid; eligible regular-session liquidation may use market orders.
- Historical structure uses the certified SIP-availability plus condition approximation. Live delayed trades may use the more accurate native execution clock.

#### Architectural decisions

- Intent time, fill time, and exit time are separate immutable chart events.
- Structural snapshots and selection evidence travel through shared Strategy, OMS, journal, replay, and chart contracts.
- Multiple requested tickers are one Backtest run scope, not frontend-only text parsing.
- QMD remains the producer authority for `price_change_1_bar_pct`; the application registry owns its typed consumer contract so validation does not lose the field during a catalog outage.

#### Rejected approaches

- OMS default-stop replacement, resistance-based long stops, fixed one-third cash tranches, candle-delayed price crossings, unconditional target ratcheting, premature cancellation of partially filled entries, profit-pocket partial exits, and full-horizon historical execution-clock coverage were rejected.
- The scanner test was not fixed by fabricating VWAP from quote-less trades.

#### Assumptions and unresolved uncertainty

- Strategy correctness and profitability remain unproven until the user's fresh post-repair backtest and lifecycle audit.
- The backtest engine's 25% participation model is retained; whether its resulting fill speed is acceptable for the intended live policy remains an acceptance question.
- No short-strategy replay or short-performance conclusion was produced in this continuation.

### Delivered outcomes

- Commits `67b3c9e2`, `84eed459`, `d853fde4`, `36a3ccbc`, `fa397b24`, `69b97473`, `dc35e26f`, `71b9fdba`, `584703d0`, and `d44d09a0` are on `origin/main`.
- Broker-cash sizing, 8% risk, 10,000-share cap, profit-pocket disablement, event-native structural entry, multi-ticker Backtest launch, completed-run metadata, structural/intent/fill/P&L chart evidence, OMS protection preservation, and target/fill lifecycle corrections are implemented.
- QMD History is running `ready` on port 8801 with structure algorithm 16 and the v18 SIP-condition checkpoint set.
- Final validation for the historical correction: 225 QMD tests, 99 QMD History tests, and 42 focused Python/frontend tests passed. No backtest was run after the correction.

### Unfinished or hanging work

- **Fresh long-strategy acceptance (`TASK-0014`)** — Current state: code and service are ready, but profitability and exact behavioral acceptance are open. Next action: user runs the intended bounded multi-ticker or SUGP backtest; inspect every entry, partial fill, gate, support stop, target snapshot/reconciliation, exit, and P&L against the one-second chart.
- **Execution-realism acceptance (`TASK-0051`)** — Current state: orders retain working remainders under the 25% participation model. Next action: compare simulated fills with the configured participation and quote evidence; change the model only with explicit user approval.
- **Short strategy (`TASK-0014`)** — Current state: not evaluated. Next action: separately review and test the reverse structural policy after long acceptance; do not infer short profitability from long code symmetry.
- **Full structural campaign (`TASK-0206`)** — Current state: SUGP and JUNS were certified and the v18 set is active locally, but no new evidence in this chat proves the full 6,360-ticker campaign complete. Next action: retain the running/restart-safe campaign and certify final universe completion separately.
- **QMD-dependent configuration suite (`TASK-0051`)** — Current state: the repaired price-change contract passes during a QMD catalog outage; broader default-configuration tests still require other QMD runtime outputs. Next action: run the full integration suite with QMD Live available and distinguish service availability from registry defects.

### Handoff to the next chat

Read `TASK-0014`, `TASK-0051`, `TASK-0206`, this summary, and `CHAT-20260901-1959-structural-checkpoint-campaign-strategy-debug-uat` first. Preserve event-native resistance crossing, support-only stops, Strategy protection authority, ticker-relative quality at 20%, latest-broker-cash Portfolio sizing, working partial entries, disabled profit pocketing, and the historical SIP-condition approximation. Do not bump the structural algorithm or demand archive execution-clock coverage without a separately approved historical migration. The next important action is to inspect the user's fresh backtest, not to broaden the universe or claim profitability.
