# Trading Journal and filtered V7 preparation

- Chat started: September 17, 2026; exact time unavailable
- Last activity / summary written: September 18, 2026, America/Vancouver
- Chat/task identifier: 01a0b19f-533f-72d0-aed3-e2b1de599695
- Scope: quant-research-workbench; TASK-0211, TASK-0212, TASK-0206
- Source completeness: Partial; earlier work recovered from compacted context, acceleration directly validated

## Narrative and decisions

The user first requested compact open positions at the left of the Journal chart,
showing open time, quantity, purchase price and forming P&L. Earlier work delivered that
layout and addressed Replay readiness. The Windows SelectorEventLoop cannot use asyncio
subprocess transports; filtered preparation uses a shielded threaded Popen startup with
terminate/wait/kill cleanup. ACI exposed a numerical optimizer failure; the prior fix
added a bounded SLSQP retry with the same objective and validity checks, published under
a new numerical identity. It does not reuse stale bands.

The user asked about persistent reuse and slow filtered V7 preparation, then authorized
all acceleration proposals. Preparation was sequential across tickers and rebuilt full
history even for earlier Backtest sessions. Work moved to an isolated laptop worktree to
avoid altering a running job's pinned files. That run was later observed stopped at AFJK;
this task issued no stop or resume command.

Backtest preparation now uses four bounded ticker processes by default, configurable
from one to four with a shared per-API-loop process budget. Within a ticker, sessions
remain sequential. Children retain one SQL/BLAS thread, per-successor OS locks, and
cancellation cleanup. Failed runs reap sibling children. Progress exposes fixed worker
slots, session counts, retries, resumed checkpoints, update age, counts, throughput and
approximate ETA. Replaceable status writes are limited to two per second; receipts and
checkpoints remain immediately durable.

A separate prefix runner reuses canonical SQL, decoding, fitting and checkpoint contracts.
It builds only source sessions preceding the latest requested Backtest session, retains
the frozen complete source manifest, and publishes an immutable cutoff marker. It never
marks a partial history fully ready. Later sessions extend verified receipts. Catalog
readers verify the publication identity, source manifest, cutoff, terminal receipt and
requested checkpoint. Missing or corrupt published filtered artifacts fail closed;
all-empty filtered history is unavailable rather than a legacy-book fallback. The kernel
source hashes remain unchanged, preserving existing full histories and partial receipts.

The user then requested one script to prepare all V7 histories using workstation compute.
`prepare_filtered_v7.py` exports a laptop-consumer-pinned campaign and runs it on the
workstation with a persistent process pool, automatic CPU/RAM admission (default cap 16),
explicit higher concurrency up to the resource ceiling, checkpoint-safe stop/resume,
fixed paged worker display, durable full failure reasons and clear exit status. Workers
share the same filtered successor artifacts as Backtest and access their local workstation
path rather than SMB. The new campaign covers 6,441 eligible tickers, 2,484,145 sessions,
and 239 separately reported deferred identities. It does not invent unresolved authority.
The user, not this task, will start the full workstation campaign.

## Evidence and limitations

The four-ticker canonical-data benchmark used AEG, AEHL, AEON and AESI, seeded through
August 13 and extended through August 18. One/two/four workers took 51.0/27.4/15.8 seconds;
after progress throttling the repeat measured 38.0/24.3/14.1 seconds. Every terminal hash
matched its existing full history. These local-checkpoint samples are not a full-universe
runtime forecast. AEON/AESI profiling showed checkpoint deep copies dominating those
sampled sessions; the numerical kernel was preserved to retain artifact identities.

The focused preparation suite passed 31 tests before workstation additions. The broader
QMD/Replay suite passed 174 tests and six subtests, with three failures reproduced on
unchanged main: debug round-trip fixture, saved-review error-message expectation, and
V6 structural fixture rejected by the current V7 contract. Build and 12 light/dark,
normal/compact, min/default/max-scale visual scenarios passed, plus final stopped-state
validation. Workstation tests cover manifest identity, deferred scope, safe stop and
compact/wide paged rendering. Final focused validation passed 52 tests. The actual persistent
pool completed four full 424-session histories in 33.44 seconds (seeded prefixes reused),
recovered two transient query failures, and matched all four authoritative hashes.
See research/level_book/v7/README.md for commands/contracts.

Evidence roots: D:/TradingML/runtimes/codex_tests/v7-benchmark-20260918-081637,
v7-benchmark-20260918-081940, v7-ui-review and v7-ui-final. Unrelated hindsight files in
the main checkout belong to another task. Preserve original campaigns, frozen metadata,
causal predecessors and raw-SIP prohibition. Full campaign completion, sustained workstation
throughput and strategy/P&L acceptance remain open under TASK-0211.
