# Repair SUGP strategy UAT, historical review, and structural checkpoint authority

- Chat started: 2026-09-01 19:59:09 PDT (America/Vancouver)
- Chat ended or last activity: 2026-09-04 09:09 PDT (America/Vancouver)
- Summary written: 2026-09-04 09:09 PDT (America/Vancouver)
- Chat/task identifier: `01a0600e-5ddf-78f3-a6fc-0984902f8208`
- Repository or scope: `D:\TradingCodes\quant-research-workbench`; long-momentum strategy, Replay/Backtest/Debug UX, QMD History, Unified Structural Level Book, and workstation checkpoint campaign
- Related task-history entries: `TASK-0014`, `TASK-0051`, `TASK-0206`
- Source completeness: Complete through the last activity stated above

### Narrative

The chat began as the remaining acceptance audit for the August 21, 2026 SUGP long-momentum replay. The inherited baseline was deliberately not a strategy approval: the corrected engine had processed 342,618 events in 25m25.7s, reproduced the separate 30-minute run's 2,730-fill prefix exactly, ended flat, and eliminated the stale structural-frontier latch, but produced 61 flat-to-flat positions, nine wins, 52 losses, gross P&L of -$7,814.346, fees of $7,015.630, and net P&L of -$14,829.976. The user requested a lifecycle-by-lifecycle causal audit, exact request/fill/exit times, liquidity and volume evidence, real one-second visual review, and a fundamental fix only if the churn represented a genuine causal defect.

The investigation quickly exposed that the operator surfaces were themselves insufficient for that audit. Completed Backtest Debug could freeze or become unresponsive, a completed strategy revision could fail to open because no matching executor was installed, ticker rows did not consistently open Charts & Quotes, Canvas layouts did not persist, Strategy Activity truncated or hid older evidence, and time filters were inconsistent with ET. The implementation made completed-run state durable and responsive, added secure new-tab ticker routing, persisted Replay and Backtest Canvas arrangements, exposed bounded ticker/time launch fields and accelerated-run progress, restored complete causal Strategy Activity rows, removed the fixed 2,000-row visibility ceiling through durable pagination, hydrated filters against the authoritative history, and made non-decision rows disclose their available evidence rather than boilerplate. The backtest page was simplified toward explicit ticker and time-period inputs. These changes support Debug, Replay, and Backtest through shared contracts rather than page-specific replicas.

The visual comparison also revealed bad early one-second bars. The underlying issue was delayed trade reports being treated as if they were current market events. The first correction separated execution time from availability/SIP time: a delayed report remains auditable when received but cannot retroactively update current bars, MACD, structure, or entry gates. Historical charting can still place a trade at execution time when a trustworthy execution clock exists. Later, after inspecting the actual historical authority, the user clarified a critical distinction: imported `market_sip_compact.events_YYYY` does not generally contain participant/execution timestamps and must never be altered to add them. Historical structural reference is intentionally approximate and should process SIP order while excluding ineligible trade conditions; only live websocket data may use participant or execution time for finer late-trade placement. The final implementation follows that clarification. It removes historical level construction's execution-clock preflight, uses canonical condition eligibility, and preserves execution clocks as an optional live/chart refinement. Repository policy now explicitly prohibits QMD, charts, indicators, strategies, backfills, or research from reopening SIP flatfiles after import, and prohibits downstream mutation of the canonical historical event schema.

The strategy rules also changed based on chart inspection. The user rejected an unrequested maximum extension above executable VWAP, so that gate was removed while executable VWAP remained the only VWAP authority. The user clarified that completed one-second MACD line must be above zero, but its signal line does not need to be positive. Early Squeeze Move remains a one-time campaign admission, forming one-second MACD remains available for exits, and existing protection authorities remain intact. No arbitrary maximum re-entry count and no SUGP-specific suppression were introduced.

Structural entry selection was made dynamic. At every decision time, the strategy takes the highest three qualified resistance prices at or below the live high of day. Only those three can admit entries, each level requires its own real-time price cross, the high of day and eligible set update continuously, and one third of the capital mandate is reserved for each independently crossed level. Position quantities remain integers and are hard-capped at 5,000 shares, preventing the earlier 20,000-25,000-share outcomes even when other sizing inputs are permissive. Support selection and strategy-owned protection were revised to use explicit structural evidence; an immediate native trail accompanies the fixed stop, and the obsolete downside bearish-CHoCH exit was removed where it contradicted the intended MACD/protection lifecycle. Extended-hours incomplete-target liquidation follows executable bid-aware behavior.

Chart presentation became part of the audit contract. Strategy-generated evidence moved out of the generic Indicators modal into a separate Strategy Presentation control. It can show entries, exits, initial and revised stops, adds, reconciliations, and up to three frozen entry and target guides over the selected lifecycle. Labels and guides are anchored to chart time and price, leave the viewport with their bars, remain scoped to the selected symbol, and use semantic entry, gain, loss, and exit colors. Styling is persisted per chart and includes visibility, line style, width, color, opacity, text size, box style, and event controls. Several follow-ups fixed missing guides, autoscale exclusion, open-position rendering, lifecycle/symbol leakage, white-color preservation, short guide legibility, session-shading z-order, and viewport jumps during setting changes.

The user then questioned whether the structural reference itself was trustworthy. The existing session-oriented population approach was slow, repeatedly reloaded state, poorly resumed sparse days, and generated damaging workstation pressure. The replacement campaign assigns whole tickers to worker processes. Each worker fetches compact historical events by ticker and ordinal chunks using the ordinal summary authority, loads the ticker's splits once, keeps one cumulative level book in memory, applies splits at session boundaries, writes an idempotent checkpoint at each completed day, then releases the ticker state before taking another. Active/liquid current names are prioritized, with SUGP and JUNS first for strategy development. Worker count became configurable up to 80, but concurrency remains bounded by ClickHouse, transport, and memory capacity rather than assuming every additional core is free throughput.

The campaign was made restart-safe through immutable successor runtimes. An existing source runtime is never rewritten during migration. A successor reads its completed ticker/session status and certified checkpoints, repairs derivable fields from retained raw counts, continues from the exact `(ticker, ordinal)` cursor, and writes under a new checkpoint-set identity. Empty or no-event sessions advance explicitly rather than causing reconstruction from the original start date. Writes are retry-safe and certification rejects schema drift, invalid serialized forms, cursor discontinuity, split-lineage mismatch, or executable/source identity mismatch. The launcher became host-portable, loads the workstation environment authority, supports a prebuilt signed/approved executable path, records its SHA-256, and binds source commit identity even when the deployed directory has no Git metadata.

Operational failures drove several refinements. Early implementations issued date/time scans instead of ordinal-bounded reads, reloaded splits, estimated ETA per worker rather than across all workers, failed noisily on Ctrl+C, opened many terminal windows, retained prior-run failures in red on a fresh launch, and allowed a monitor to detach or crash. Those paths were replaced with ordinal streaming, one ticker-owned process per worker, event-weighted aggregate ETA, bounded retry/failure reporting, graceful stop, stale-worker cleanup, and one non-flickering Rich supervisor. The monitor is observational and certification is sampled/bounded so it does not double the campaign cost. Application-control blocks on workstation-built executables were handled through reproducible binary identity rather than running a stale binary.

The level evidence contract was revised after the user observed that old hold-probability thresholds no longer matched the distribution. The durable book preserves raw hold/break counts, observation depth, lifecycle geometry, roles, source lineage, and cursors. Absolute scores are derived from those sufficient statistics: `hold_quality_score` is the conservative Wilson-based hold measure, accompanied by `hold_observation_count`, `hold_evidence_reliability`, `break_probability`, and `pressure_bias`. A second score, `ticker_relative_quality_score`, is the same-role midrank ECDF percentile against that ticker's frozen prior-session distribution. Current-session provisional levels do not alter the normalization baseline and fail open for the relative filter, preventing a level from changing the distribution used to judge itself. Reused checkpoints can migrate these derived fields from raw counts and rebuild frozen baselines without replaying the entire event archive, while the underlying source checkpoint remains immutable.

QMD History, the unified-level indicator, strategy consumption, and chart presentation were updated to require compatible certified checkpoint sets and the same score names. The final configuration redesign widened the structural settings form, removed overlapping headings, applied the approved Public Sans typography, reserved monospace for exact contract fields, and exposed `ticker_relative_quality_score` with an explicit Ticker-normalized badge and explanation. A production frontend build, focused source-contract test, and live browser render of the SUGP one-second chart confirmed the form no longer overlaps and the normalized control is visible. Commit `12f295bf` was pushed.

The chat did not complete the originally requested fresh post-fix strategy acceptance run. The older 61-position run remains useful defect evidence but cannot certify the revised strategy or repaired structural authority. The workstation's long campaign is also still an operator-run computation rather than a completed certified universe. The final priority is therefore to finish and certify the SUGP and JUNS successor checkpoints first, activate them through QMD History, publish the intended immutable strategy candidate, then run one independently bounded SUGP prefix and the authorized August 21 session for exact determinism and lifecycle/visual acceptance.

### Durable decisions

Confirmed requirements:

- Early Squeeze Move is one-time admission, not a recurring entry gate.
- Executable VWAP is the sole VWAP authority; there is no maximum VWAP-extension entry gate.
- Entry requires completed one-second MACD line above zero; MACD signal need not be positive. Forming MACD and existing protective authorities remain exit inputs.
- Structural entry candidates are the live top three qualified resistances at or below the continuously updated high of day; each requires an independent real-time cross.
- Quantities are integers with a 5,000-share hard cap. There is no arbitrary re-entry-count cap and no SUGP-specific behavior.
- Strategy and structural evidence must be visible and configurable on the one-second chart, but execution authority must never depend on browser presentation.
- Historical SIP tables are immutable after canonical import. Downstream systems must read `market_sip_compact.events_YYYY`, never flatfiles.

Architectural decisions:

- Historical structure uses SIP ordering plus canonical condition eligibility. Optional participant/execution clocks refine live data and retrospective charting but are not prerequisites for historical checkpoints.
- The checkpoint campaign is ticker-sharded and ordinal-streamed. A worker owns one ticker across the full date range, carries one cumulative book, loads splits once, and persists every completed daily checkpoint.
- Resume and migration use immutable successor checkpoint sets. Raw counts and lineage are durable; absolute and ticker-relative projections are derivable.
- `ticker_relative_quality_score` uses the frozen prior-session same-role distribution; provisional current-session levels cannot change their own reference distribution.
- Debug, Replay, Backtest, Strategy Activity, chart presentation, and Canvas persistence use shared contracts across application modes.

Rejected or superseded approaches:

- Date-window rescans, repeated split loads, flatfile recovery, historical execution-clock preflight, mutation of `market_sip_compact`, session-only workers, per-worker terminal windows, and in-place checkpoint migration are rejected.
- The earlier rule requiring both MACD line and signal above zero is superseded.
- Old completed runs and checkpoint sets remain immutable evidence; they are not silently recertified under new semantics.

Unresolved uncertainty:

- The revised strategy's actual lifecycle count, profitability, fees, and missed-entry behavior are unknown until a fresh certified replay.
- The complete successor checkpoint universe is not yet certified; workstation completion and final capacity remain operational evidence to collect.

### Delivered outcomes

- Repaired completed-run review, ticker navigation, Canvas persistence, Strategy Activity evidence/pagination/time filtering, and bounded accelerated Backtest launching under `TASK-0051`.
- Revised long-momentum structural entry, MACD, VWAP, sizing, protection, extended-hours exit, and chart-presentation contracts under `TASK-0014`.
- Implemented the resumable ticker/ordinal workstation campaign, Rich monitor, immutable successor migration, certification, score derivation, ticker-relative normalization, and QMD History consumption under `TASK-0206`.
- Documented the immutable historical-event and no-flatfile authority.
- Pushed the final structural settings redesign as `12f295bf`; the broader commit sequence is preserved in Git history and summarized in the linked task rows.

### Unfinished or hanging work

- Current state: the original 61-lifecycle audit was overtaken by causal data, strategy, and presentation repairs. Why unfinished: the old run predates the final semantics. Next action: preserve it as evidence, but audit the newly generated lifecycles after the fresh run. Owner/dependency: strategy UAT; `TASK-0014`.
- Current state: SUGP/JUNS successor checkpoints are prioritized but the long workstation campaign is not recorded as fully certified. Why unfinished: it is an external operator-run computation. Next action: resume the exact successor runtime, certify both tickers first, then sample counts, splits, cursors, score projections, and QMD History parity before broader activation. Owner/dependency: user/workstation plus campaign code; `TASK-0206`.
- Current state: no revised-strategy acceptance result exists. Why unfinished: the structural authority must be certified first. Next action: publish the immutable candidate, run a bounded SUGP prefix and August 21 full session, compare determinism, inspect liquidity/volume gates and every lifecycle, and perform real one-second chart plus Position Manager review. Owner/dependency: `TASK-0014` after `TASK-0206`.
- Current state: authenticated Paper/Live reconciliation remains outside this chat's validation. Next action: validate broker websocket, order, fill, fee, and protection parity only after historical acceptance. Owner/dependency: `TASK-0051`.

### Handoff to the next chat

Read `TASK-0014`, `TASK-0051`, `TASK-0206`, the August 28 SUGP UAT summary, and this file first. Do not reopen flatfiles, alter `market_sip_compact.events_YYYY`, reuse historical execution-clock preflight for level construction, mutate an existing checkpoint set, or infer strategy approval from the old loss-making run. The immediate action is to verify the workstation successor campaign state and certify SUGP and JUNS. A new backtest should begin only after QMD History resolves that certified authority. Task-history changes after future substantive work still require user approval.
