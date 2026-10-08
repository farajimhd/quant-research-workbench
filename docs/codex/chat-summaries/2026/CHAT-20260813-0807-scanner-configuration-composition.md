# Scanner authority, reusable configuration, and guided trading setup

- Chat started: 2026-08-13 08:07:40 PDT (America/Vancouver; source creation timestamp 1786633660).
- Chat ended or last activity: 2026-10-08; archive documentation requested. Last implementation commits recorded here: 2026-08-16.
- Summary written: 2026-10-08 PDT.
- Chat/task identifier: 019ffbaa-28d1-7dc2-9279-1eea890623df; title: Align scanner item columns.
- Repository or scope: D:\TradingCodes\quant-research-workbench; QMD, backend registries, Canvas, configuration UI.
- Related task-history entries: TASK-0188, TASK-0192; successor authority context: TASK-0194.
- Source completeness: Partial. User requirements and later implementation evidence are available; several earlier assistant replies and tool receipts are absent.

### Narrative

The chat began with inconsistent Market Discovery and Canvas Scanner presentation. Core Scan displayed nine active items, but downstream Scanner tabs and columns did not match those definitions. The user wanted every relevant item to have a presentation column name and changes to propagate through QMD, backend, Scanner, and Watchlists rather than remain isolated UI adjustments. Subsequent requests covered missing float, liquidity, and reference values, consulting the reference gateway and database, using configured presentation labels, and removing table-header second lines while making headings uppercase.

The discussion widened to execution authority. An earlier review, quoted by the user, reported that Replay, Backtest, and Debug preflight lacked an approved trading configuration; historical endpoints resolved Replay configuration rather than their own mode; runtime selection was hard-wired to long-momentum-campaign revision 5; and exhausting the historical stream did not necessarily close a strategy's positions. The user authorized fundamental fixes and dynamic strategies aligned with Strategy Studio. Those quoted diagnoses explain the intended scope, but this retained source is insufficient to independently certify every corresponding implementation or current runtime status. An approved graph and actual strategy selection must be checked in a successor execution review; historical stream completion must not be presented as forced liquidation.

Repeated UI inconsistencies exposed a deeper problem: the same item appeared as a column, calculation, signal, capability, or source in different editors. The user rejected the overly broad word Capability and asked that larger objects be composed from existing smaller registered objects, not recreated. The architecture inventory at docs/2026-08-10-application-architecture/17-information-ontology-and-registry-inventory.md became the durable design reference. Fields are values; derivations produce values; signals can originate in news or other external services as well as calculations. A column presents a registered value or rule result; it is not a competing computation authority. Sources, records, indicators, oscillators, strategy inputs, accounts, Portfolio, and OMS all needed explicit roles and ownership.

The user clarified the configuration model into three practical layers: persisted Canvas UI/UX configuration; Data configuration, including fields, columns, and Rule Sets; and System configuration, including Strategy, accounts, Portfolio, OMS, and Run Plans. General fields do not belong exclusively to Market Discovery or Strategy Studio. The agreed Data Catalog is a read-only, searchable documentation/detail surface for registered atomic fields and preregistered derivatives, indicators, and signals. Users select definitions upstream rather than create low-level computation switches in the UI. Only dependencies required by a selected graph should activate. Parameterized volume windows were an example, not a mandate to enumerate an infinite Cartesian product.

Catalog groups and subgroups must be collapsible and semantic, based on relevance and data type rather than producer. Each detail page documents one registered field. Labels must preserve meaning: IPO Date is not merely Date, and technical acronyms require proper names. The user rejected generic provenance grids, obvious descriptions, and duplicated metadata. Documentation should instead state the real source/table or service, inputs, formula or operation, timeframe, classification thresholds, and missing-value behavior where established. Do not invent formulas or coverage to fill blanks.

Rule Sets received their own library. Built-in defaults are atomic and immutable; user definitions are editable, named, described, and composed from registered fields. Penny/Small/Mid/Large Cap lists are Watchlists, not Rule Sets. The user identified malformed condition presentation, notably Bullish change of character shown as greater than an empty threshold, and requested a full semantic review. Another browser contained atomic definitions incorrectly copied into Custom drafts. The authorized fix filters stored custom rows by atomic=false and origin=user, rejects IDs colliding with backend atomic definitions, deduplicates by rule_set_id, versions/migrates session payloads, and stores Strategy references instead of copying the entire catalog. Earlier implementation receipts for these requests are incomplete here, so successors should verify current code/tests rather than infer completion from approval.

A shared lookup pattern was refined through screenshots: small-value selectors remain compact; large catalogs have a fixed search region and a separately scrolling grouped list. Items have black headings and useful subordinate descriptions, selected values are black, empty placeholders gray, and excessively bold copy is avoided. Public Sans was selected for ordinary UI text and numbers, JetBrains Mono for technical identifiers. Comparison pages were to be removed except the permanent typography-system reference, placed last in navigation. This is a durable user preference, not permission to repeatedly introduce new fonts or selector styles.

Market Discovery was then reduced, at that design checkpoint, to Core Scan and Watchlists. It composes registered Rule Sets, ranking definitions, and table columns, including rule-result columns. History and duplicate capability/enrichment catalogs were removed from configuration ownership. Computation demand should follow references, not duplicated inline calculations or frontend guesses based on IDs. The user required all existing Watchlists to be rebuilt from these building blocks. TASK-0192 records schemas v24/v26 and migration of 19 Watchlists; its later Signal Stream extension supersedes the earlier two-group restriction and must not be accidentally removed using this older conversation.

Market Discovery polish included a 50-percent wider library, a single gray selected-card outline without a black left shadow, detectable inline-editable name/description, scrolling the library to newly created Watchlists, and deletion for custom Watchlists. These are interaction requirements; the selected object and displayed detail must remain synchronized.

Strategy Studio became lifecycle composition from Rule Sets, Trading Actions, and reusable Action Policies rather than a competing parameter catalog. Atomic behaviors such as profit-pocket or pullback-add handling prompted discussion of reuse in automatic and semi-manual trading. The intended authority split keeps observation in QMD, decision composition in Strategy, admission in Portfolio, and execution/protection in OMS. The user requested removal of Parameter Catalog and inline Rule Set creation/editing pages. Add references in Strategy; route definition creation or custom editing to the Rule Set Library; atomic rules may be added/removed but not modified there. Lifecycle headings and field help must explain their specific meaning rather than reuse a generic description. Early implementation cannot be exhaustively certified from the retained excerpts.

The user liked Strategy Studio's guided parameter UX and asked for major Portfolio, OMS, and Accounts revisions inspired by it. Advanced structure was explicitly rejected: there must be one guided configuration path, with all parameters available. Accounts need guided creation, deletion, and editing. Run Plans are necessary final compositions, not stale Strategy-owned deployment wiring; they need their own visible navigation entry and guided configuration.

The latest delivered changes are supported by existing commits. cca9652da exposed guided Run Plans and updated sidebar navigation. 5fcf7f2ca completed guided account configuration. 436eef55a neutralized Watchlist selection cards, including the shared abstraction component and registry accent, replacing the green styling. 617978014 tightened OMS field spacing. The user then reported that labels still had a left gutter and adjacent description/label pairs crowded together. 3c5765348 corrected the shared guided-field CSS across Run Plans, Portfolio, OMS, and Accounts rather than limiting the change to one screenshot.

Historical validation in the retained later implementation context includes managed TypeScript/Vite builds, 13 focused registry tests for neutral cards, live browser interaction, and a 48-capture guided UI review with zero objective issues. An Accounts HTTP 500/loading capture was retried before usable screenshots were obtained; it was not evidence of successful authenticated broker execution. The existing large-chunk Vite advisory remained non-blocking. These are August implementation receipts, not new October runtime acceptance tests.

At archival time the repository has advanced substantially and other chats are concurrently updating documentation. This request changes documentation only. Existing research notebooks, tests, and unrelated summary changes must be preserved; this summary neither restarts services nor publishes a configuration or runs trades.

### Durable decisions

- Confirmed: one Public Sans/JetBrains Mono typography system; shared grouped searchable lookups; no excessive boldness or per-page selector redesigns.
- Confirmed: guided-only Accounts, Portfolio, OMS, and Run Plans; no Advanced structure escape hatch hiding required parameters.
- Architectural: larger definitions reference smaller registered objects by stable ID/revision. Strategy owns behavior; Run Plan owns deployment composition and bindings.
- Architectural: Data Catalog documents common registered values; Rule Set Library owns rule definitions. Atomic definitions are immutable; custom definitions are editable.
- Confirmed: source-specific formulas and classification tables are useful documentation; generic provenance filler is not.
- Rejected: duplicating full catalogs into profiles, guessing abstraction types in frontend IDs, inline editing of atomic Rule Sets, and Strategy-owned account/OMS/Portfolio deployment.
- Superseded: Core Scan plus Watchlists only was the checkpoint design; TASK-0192 subsequently added durable Signal Streams.
- Uncertain: early runtime and catalog fixes require current verification where original implementation evidence is missing.

### Delivered outcomes

| Outcome | Durable evidence |
|---|---|
| Guided Run Plans and navigation | cca9652da; frontend/src/pages/TradingConfigurationPage.tsx; frontend/src/app/components/Layout.tsx |
| Guided account configuration follow-up | 5fcf7f2ca; TradingConfigurationPage.tsx |
| Neutral Watchlist reference cards | 436eef55a; AbstractionCard.tsx, configurationVisuals.css, application_registry.py |
| OMS field spacing, then cross-domain alignment | 617978014 and 3c5765348; frontend/src/app/configurationVisuals.css |
| Prior authority/composition implementation context | TASK-0188 and its CHAT-20260810-0529-qmd-application-architecture-canvas-recovery summary |
| Market Discovery reference migration and later Signal Streams | TASK-0192; commits 5eda4079 and 9bf8da73, recorded in the task ledger |

The last UI fixes were committed and pushed in the original implementation turn. This archive update records them without claiming new backend, QMD, broker, or full strategy-run validation.

### Unfinished or hanging work

| Current state / limitation | Exact next action and owner | Related task |
|---|---|---|
| Several early assistant/tool receipts are missing | Successor agent inspect current tests and referenced implementation before certifying dynamic strategy dispatch, mode-specific selection, draft migration, or catalog-wide correctness | TASK-0188 |
| August browser review is historical; October source has evolved | On a requested UI follow-up, validate shared label/control/help alignment and guided parameter completeness in the current runnable app | TASK-0188 |
| Quoted preflight lacked approved configuration; current approval/service state not checked | User-authorized execution review must check selected published Run Plan, correct mode, service readiness, actual strategy implementation, and terminal position policy | TASK-0188 |
| Broker and external-data acceptance remain separate release gates | Follow TASK-0188's explicit coverage/storage/broker dependencies; obtain required authorization rather than silently deploying or weakening checks | TASK-0188 |
| Earlier two-group discovery design is stale after Signal Streams | Read v26/task history before subsequent Market Discovery changes; retain immutable occurrence evidence and causation | TASK-0192 |

### Unavailable or incomplete source chats

Only this chat was summarized. Its exact creation timestamp and title were retrieved; early user requests and response annotations are visible, but several original assistant implementation replies and receipts are missing. Other repository chats were inventoried by title/status, including Fix watchlist columns in Canvas, Design BarGPT production serving, Design SEC XBRL synthesis, and Diagnose backtest preflight errors. Their contents were not reviewed for this summary, and no outcomes are attributed to them. They remain separate summaries, not continuations reconstructed here.

### Handoff to the next chat

Read TASK-0188, TASK-0192, TASK-0194, the linked architecture summary, and 17-information-ontology-and-registry-inventory.md first. Preserve atomic/custom ownership, reference composition, guided-only setup, typography, and shared lookup patterns. Treat newer registry schemas and Signal Stream work as authoritative over older design screenshots. The next substantive action is verification of the specific current defect or runtime path the user requests—not an unsolicited architecture rewrite. Publishing, broker deployment, data migration, and trade execution require their own explicit scope and authority.

