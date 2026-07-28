# Chat Summaries

This is the concise index of durable narrative summaries for long chats related
to this repository. Detailed summaries are stored separately under
`docs/codex/chat-summaries/<YYYY>/`. Entries are ordered newest-first within
each year.

## 2026

### [2026-07-28 - Issuer-scoped News and SEC intelligence](docs/codex/chat-summaries/2026/CHAT-20260728-issuer-scoped-news-sec-intelligence.md)

- Related task: `TASK-0153`

Replaced the invalid document-wide News gate with one issuer-scoped V4
authority shared by certified historical labeling, normalized News/SEC
relationships, and live News Intelligence. Multi-issuer events preserve the
full publication while separating per-issuer evidence and semantics; direction
is now issuer-role aware and certified independently from reported reaction.

### [2026-07-27 time unavailable - Design and implement adaptive OMS, portfolio protection, and continuous risk](docs/codex/chat-summaries/2026/CHAT-20260727-UNKNOWN-adaptive-oms-portfolio-risk.md)

- Chat/task ID: `019fa4ce-2ab7-7490-a297-e38b4e496102`
- Related tasks: `TASK-0145`, `TASK-0149`
- Summary written: 2026-07-28 07:42 PDT

This chat established IBKR-authoritative multi-account portfolio management,
then evolved the OMS from static quote tactics into strategy-selectable
adaptive execution with broker-authoritative partial fills. It implemented
multi-slice protection, add and profit-pocket transitions, continuous
account-bound risk, durable recovery and operator controls, Replay/Backtest
parity, Canvas evidence, and a guarded Paper acceptance workflow. All
deterministic implementation phases are complete; authenticated IBKR Paper
acceptance remains the explicit gate before Live automation.

### [2026-07-27 08:56 PDT - Investigate ChatGPT UI freezes](docs/codex/chat-summaries/2026/CHAT-20260727-0856-investigate-chatgpt-ui-freezes.md)

- Chat/task ID: `019fa44b-0973-7c81-9672-939236736821`
- Related tasks: `TASK-0135`, `TASK-0136`, `TASK-0137`, `TASK-0138`
- Summary written: 2026-07-27 10:43:20 PDT

The chat began as a diagnosis of desktop UI freezing during concurrent tasks,
then traced repository artifact pressure, established laptop/workstation runtime
authority, quarantined 16.435 GiB of generated content, externalized frontend
and IBKR outputs, and ended by designing and activating bounded cross-chat
summary and task-continuity governance. The desktop concurrency defect itself
remains an app-level issue to verify after a full restart and fresh task.

### [2026-07-26 06:24 PDT - Rebuild Canvas strategy, signals, and low-latency order management](docs/codex/chat-summaries/2026/CHAT-20260726-0624-canvas-strategy-order-management.md)

- Chat/task ID: `019f9e98-eec0-73b3-a9ee-5742a1f5fc7a`
- Related tasks: `TASK-0122`, `TASK-0125`, `TASK-0126`, `TASK-0130`, `TASK-0134`, `TASK-0139`
- Summary written: 2026-07-27 10:52:37 PDT

This chat replaced the pre-refactor strategy catalog with typed indicator,
signal, clock, and strategy boundaries; repaired historical Scanner production;
implemented the configurable long-only campaign; and established exclusive
low-latency IBKR order management. It preserves the evolving requirements,
rejected approaches, validation evidence, and remaining authenticated
paper-order and continuous live-controller work.

### [2026-07-19 09:28 PDT - Diagnose ordinal gaps and add safe automatic flatfile event updates](docs/codex/chat-summaries/2026/CHAT-20260719-0928-safe-automatic-flatfile-event-updates.md)

- Chat/task ID: `019f7b35-01f1-7862-8e51-58dc8a80b002`
- Related tasks: `TASK-0072`, `TASK-0143`
- Summary written: 2026-07-27 11:14:07 PDT

This chat diagnosed how out-of-order source days could corrupt per-ticker event
ordinals, then added a minimally invasive automatic planner around the tested
ingest core. Bare invocation now discovers the next complete remote sessions,
accounts for cached files, requires approval, rejects manual gaps, and stops
later inserts after failure. The change passed unit, compile, CLI, and live
read-only checks and was hash-verified in the workstation code copy; no
production update or workstation-host execution occurred.

### [2026-07-17 09:25 PDT - Explain one-second news-reaction bars and correct the label authority](docs/codex/chat-summaries/2026/CHAT-20260717-0925-explain-one-second-news-reaction-bars.md)

- Chat/task ID: `019f70e5-87d3-7940-ba28-dd101ceef5aa`
- Related tasks: `TASK-0059`, `TASK-0142`
- Summary written: 2026-07-27 11:12:24 PDT

This chat traced why the first reaction extractor used one-second bars, found
that quote-forward-fill and reconstructed extrema violated the intended
trade-only label contract, and added an operational progress terminal. It also
records the later correction that a full-market 2019-2026 intraday-bar build
was the wrong prerequisite. Current `TASK-0059` work supersedes that
intermediate design with exact canonical compact events bounded to
news-relative windows.

### [2026-07-17 08:57 PDT - Design Tape, Quotes, and Canvas market intelligence](docs/codex/chat-summaries/2026/CHAT-20260717-0857-design-tape-quote-and-canvas-market-intelligence.md)

- Chat/task ID: `019f70cb-ac2e-7952-b33c-f86a3312b077`
- Related tasks: `TASK-0052`, `TASK-0060`-`TASK-0071`, `TASK-0073`-`TASK-0077`, `TASK-0079`, `TASK-0081`-`TASK-0085`, `TASK-0088`, `TASK-0090`, `TASK-0096`, `TASK-0097`, `TASK-0102`-`TASK-0105`, `TASK-0110`, `TASK-0111`, `TASK-0115`-`TASK-0117`, `TASK-0119`, `TASK-0122`, `TASK-0125`, `TASK-0126`, `TASK-0130`, `TASK-0134`, `TASK-0141`
- Summary written: 2026-07-27 10:55:53 PDT

This chat grew from separate Tape and NBBO Quotes containers into the Canvas
market-intelligence system: streaming QMD signals, event-derived structure and
level footprints, stable chart panes, Stock Facts and SEC/XBRL evidence,
trading management, scanners, and the multi-horizon Charts & Quotes workspace.
Its latest correction made historical footprints session-causal; the existing
QMD History service must be restarted to load that binary.

### [2026-07-13 09:25 PDT - Establish the foundational Canvas and historical trading workspace](docs/codex/chat-summaries/2026/CHAT-20260713-0925-foundational-canvas-historical-trading-workspace.md)

- Chat/task ID: `019f5c4c-5aa0-7552-abbf-78aaa21d6d4c`
- Related tasks: `TASK-0050`, `TASK-0051`, `TASK-0052`, `TASK-0144`
- Summary written: 2026-07-27 11:15:32 PDT

This chat corrected the product from page-oriented navigation to one
Canvas-container workspace, extracted shared Live/Replay primitives, created
the IBKR-shaped runtime and simulated broker foundation, and replaced the
transitional Python historical gateway with a Rust binary sharing live QMD's
core. It then established global Canvas configuration, one-day Replay,
single-symbol neon linking, focus canvases, compact telemetry and chart panes,
strict overflow ownership, and latest-covered-session initialization. Runtime
cutover, complete run/debug flows, and final Live migration remain under
`TASK-0051` and `TASK-0052`.

### [2026-07-08 09:21 PDT - Review SEC gateway errors and rebuild SEC v3](docs/codex/chat-summaries/2026/CHAT-20260708-0921-review-sec-gateway-and-rebuild-v3.md)

- Chat/task ID: `019f4288-d619-7731-965e-a3bbfb55cbd8`
- Related tasks: `TASK-0037`, `TASK-0046`, `TASK-0056`, `TASK-0087`, `TASK-0089`, `TASK-0091`, `TASK-0140`
- Summary written: 2026-07-27 10:51:37 PDT

An SEC submissions 404 review became the complete v3 source, timestamp,
identity, revision, renderer, taxonomy, bridge, and gateway lifecycle rebuild.
The remaining work is final embedding policy and a fresh data-frontier audit.
