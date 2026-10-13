# Campaign execution and root certification

[Design overview](../certified_research_dataset_design.md) · [Exact controller schemas](12d-schema-campaign.md)

[Checkpoint seed handlers and remaining migration work](13a-checkpoint-seeds.md)

[Short-data materialization and publication boundaries](13b-short-materialization.md)

[Whole-plan capacity admission and frozen-plan checks](13c-plan-capacity.md)

[Existing corporate/allocation/supply authority handoffs](13d-reference-handoffs.md)

[Frozen recipe/rule metadata and runtime reconstruction](13e-frozen-runtime.md)

[Read-only saved dataset verification command](13f-saved-verification.md)

[Case-sensitive canonical source symbol checks](13g-source-symbols.md)

[Current validation and remaining work](13h-validation-status.md)

[Requirement completion audit and next execution integrations](13i-completion-audit.md)

[Unified launcher execution, resume and capacity configuration](13j-launcher-execution.md)

[First-launch context preparation and graph assembly](13l-initial-preparation.md)

[Retained reference discovery and pre-publication short identity](13m-reference-discovery.md)

## Implemented controller core

`ExecutionLaunch` now combines the concrete market, reference, activity and universe registries into one `Campaign` executor. Acquisition, initial-seed and corporate/allocation stages must supply registered application handlers; missing handlers stop construction, and competing product authorities are rejected. Nothing in stored arguments is executed as code.

Before `run`, the entry point checks ownership/source guards and a fingerprinted capacity-admission certificate bound to the exact plan hash. Blocked, corrupted or wrong-plan admission prevents writes. `verify` performs authority verification without requesting future write headroom. A bounded live fixture exercised SEC→derived-fundamental publication and sealing through this assembly; its capacity certificate was synthetic test evidence, not a production-scale pilot.

`ExecutionLaunch.from_stored()` restores the bounded, hash-validated ClickHouse plan and gives that saved configuration/graph to the runtime factory. It checks the resulting controller against the stored contract hash before returning. Current CLI defaults cannot replace frozen dates, feature selections or resource budgets. Restoration performs no capacity admission or writes; subsequent `run` and `verify` retain their normal gates.

Source preparation remains before derived-plan freeze because canonical migrations require exclusive ownership. `source.certified_units()` exposes each prepared canonical/reporting day as a `canonical_source` root with its day-specific hash. The built-in `CanonicalSourceHandlers` independently rereads source statistics, successful import status, complete reporting evidence and mutation status through a private client. Missing, changed or mutating evidence blocks descendants. These roots verify the result of acquisition/repair; they never acquire data, launch repairs, reopen flatfiles or call Massive. No additional persistence schema is needed: their authority is the existing canonical/reporting certificates, with the normal campaign unit metadata and progress.

The built-in `PreparedShortSourceHandlers` supplies `short_volume_acquisition` and `short_interest_acquisition` verification for **already acquired** inputs. Its exact unit arguments are `entity_key`, `start`, `end`, `source_hash` and `coverage_hash`. It validates Gateway source/coverage SSD placement, completed or explicitly empty acquisition coverage over required exchange sessions, the frozen coverage fingerprint, and the independent per-security short-data audit. `bootstrap_trusted` alone is insufficient. Gateway coverage intervals use exclusive end dates. Missing original publication clocks/evidence, duplicate observations, changed values or uncovered sessions block execution. Empty observations require actual acquisition coverage; absence of rows does not establish coverage. No source download or new persistence table is introduced. Plans must freeze both hashes after dated membership is available; launch-time discovery remains unfinished.

A read-only January 2–9, 2026 inspection on October 13 found completed daily-short-volume acquisition coverage for all six exchange sessions, but no qualifying short-interest coverage for those six sessions. This is acquisition coverage only; short-volume original-clock/row certification remains a separate requirement.

`PreparedSecSourceHandlers` supplies `sec_acquisition` verification for retained SEC inputs. Exact arguments are `entity_key`, `cik`, `start`, `end` and `source_hash`. `reference_sql.sec_source_evidence()` hashes the full existing SEC projection, including normalized facts, original source records and filing-clock proofs, in one ClickHouse projection scan. Six aggregate numbers leave SQL. Fixed namespace UUIDs remove campaign/attempt dependence; issuer, normalized CIK, date range, full metric catalog and evidence are bound into the hash. Empty/ambiguous input blocks; this fingerprint is not proof that every required SEC filing has been acquired. Completeness admission remains a separate dependency. The handler checks source storage and the frozen hash using a private client, with no acquisition or persistence. A fundamental consumer must match the verified receipt's issuer/CIK/range/hash. Before publishing new facts, its producer checks the materialized private stage against the same hash; this uses the existing stage rather than rescanning SEC sources. A mismatch prevents fact/provenance publication and certification. Custom SEC metric subsets are not yet supported by this prepared-input handler.

Final CLI `run/resume/verify` routing, prerequisite acquisition/admission and representative pilot measurement remain unfinished. The assembled controller does not make an incomplete source/universe/seed graph executable.

`planning.program_units()` now compiles the complete supplied acquisition/repair, market and reference dependency graph for this controller. A single market aggregate supplies indicators, V7, activity and regular-close prices; float then waits for those prices, issuer fundamentals, corporate actions and allocation evidence. Certified security-to-listing relationships connect renamed historical listings to one security. Competing same-day listings, divergent price inputs, missing dependencies, duplicate keys, cycles and excessive memory reservations block compilation. The compiler does not resolve missing source evidence or create initial seeds; their certified prerequisite units and handlers must be supplied. A bounded executor test verifies one aggregate invocation, acquisition→repair ordering and failure isolation across the joined graph.

`Campaign` freezes the launch configuration, source epoch and bounded unit arguments. The ClickHouse plan stores normalized numeric unit/dependency IDs, product names and argument hashes. Dataset definitions and unit rows are immutable: changed, extra, missing-on-verification or duplicated metadata fails closed.

Campaign identity includes dates and resource budgets. `CampaignConfig.calculation_hash` separately identifies semantic feature selections, so extending dates or changing workers does not by itself change that calculation component. Compatibility still requires algorithm/rule/calendar versions, source lineage and exact predecessor evidence. The calculation hash alone is not a checkpoint certificate; automatic compatibility discovery and the complete V7 recipe binding remain unfinished.

The scheduler uses topological dependency accounting and a heap of ready memory reservations. It does not scan the entire graph after each completion. Incremental progress counters avoid repeated full-state recounts. Independent consumers run concurrently within worker and memory budgets; failed parents block descendants. Long chains are processed iteratively, without recursion.

Each registered producer has an independent authority verifier. A successful function return cannot certify a unit. Resume reads actual persisted product receipts and output fingerprints. Progress history is diagnostic, not proof of data readiness.

## Dated universe prerequisite

`universe.execution_membership()` converts certified dated intervals into full 04:00–20:00 New York ticker-days and normalized security/listing/issuer relationships. Binary searches restrict each interval to intersecting exchange sessions. Renames remain dated; delistings stop future admission; ineligible intervals retain reason counts. Partial-day eligibility is reported as a blocker because current market consumers require a full day. Competing security/session listings and metadata overflow fail rather than select or truncate. `planning.program_from_universe()` joins this result to market/reference planning, verifies the universe prerequisite's frozen hash/range, and checks exact security/issuer coverage. Its returned metadata contains a deterministic membership hash; it performs no queries or writes. Security inputs may leave `market_parents=()` for this combined compiler to fill from shared aggregates; standalone reference planning requires explicit price parents. Historical evidence repair and automatic seed/corporate admission remain separate unresolved dependencies.

`UniverseHandlers` loads the Reference Gateway identity/lifecycle graph against the frozen source hash. Unresolved historical mappings block publication. `UniversePublisher` writes normalized identities and half-open membership intervals, with Gateway proofs copied server-side into compressed provenance. Publication requires both dataset ownership and a separate global dictionary claim so concurrent datasets cannot race shared identity/provenance inserts.

Partial membership resumes by inserting only missing expected intervals. Changed, duplicated or unplanned intervals fail closed. Authority verification rechecks dictionaries and source-proof hashes; a complete resume performs no inserts. Live ClickHouse coverage includes partial restart, dated removal, no-insert resume and duplicate/unresolved rejection. Historical mapping repair and full launch-time prerequisite discovery remain to be integrated.

## Final seal and restart

`campaign.load_plan` reconstructs the exact stored configuration and dependency graph from ClickHouse. It validates current schema/version, dataset dates/database, the complete unit census and content hash, deterministic IDs, argument hashes, parent references and acyclicity. Explicit unit/metadata bounds reject oversized plans without truncating them. The restored controller requires registered producers/verifiers and the original source guard; loading metadata alone never proves data readiness.

The single entry point now exposes `stored-plan --dataset-id <UUID> --database arte` (optionally `--format json`) as a server-enforced read-only inspection of this validated graph. A live fixture restored and resumed an existing sealed controller and rejected changed stored arguments. Full production `run/resume/verify` assembly still requires the unfinished acquisition/admission and initial-seed paths.

The final root hashes the immutable plan, frozen source and all independently verified unit outputs. It is inserted only after every unit verifies, then read back exactly. A previously sealed campaign resumes without inserts and rechecks the complete authority chain. Changed outputs or duplicate seals are rejected.

Keeper suspension/loss invalidates the writer permanently, including a reconnect retaining the session ID. An invalidated claim cannot publish or release uncertain ownership. Source guards and physical SSD placement checks remain required before writers.

Reference campaign producers require global dictionary ownership for shared provenance inserts in production. The lock covers only that insert, leaving SEC/derived/float calculations and output publication concurrent. Acquisition waits are bounded (10 seconds by default for dictionary claims); a failed or uncertain insert releases the valid claim and propagates failure. Constructor acquisition failures remove their listener. Read-only Keeper states are rejected whether represented as strings or named states. Verified no-insert reference resume acquires no new dictionary claim.

## Operator inspection

Short-pressure snapshots expose `window_end` separately from the requested cutoff and reject cutoffs outside the parent's certified public-time range. This makes the latest published measurement date explicit; consumers must not interpret an older report as current-day evidence. Source publication freshness/coverage and persistent output registration remain required for the complete campaign.

`short_pressure.snapshot_sql` computes configurable CNMS rolling volume ratios inside ClickHouse from independently certified short-volume facts. Windows count exchange sessions, not available observations; missing/invalid dates withhold the ratio. Latest revisions are selected only when their public clocks are at or before the requested cutoff. Units and measurement contexts are checked. Live coverage demonstrates 2/3/20-session results, unavailable initial context and a correction that changes only later as-of values. Persistent short-pressure publication and campaign registration remain unfinished.

`plan` now includes the Gateway historical-universe admission audit, with an explicit `--universe-anchor-date`. It summarizes admitted intervals, rejection counts and frozen evidence hash. Unresolved source/reporting, universe, short-publication or corporate-vintage inputs produce `status=blocked`, actionable blockers and exit code 2. Unfinished full execution also blocks admission. A live January 2 plan reported 4,295 intervals, 2,634 unresolved intervals and 20,024 short-volume rows without public-time evidence; it made no writes. These counts are a date/anchor-specific audit, not completed historical coverage.

`ActivityHandlers` registers configurable elapsed-time volume, notional, trade-count and execution-activity windows as a concurrent consumer of shared certified liquidity. The SQL uses one input scan/shared ordering, materializes compressed staging once and publishes normalized rows keyed by window and bucket. Incomplete prefix context produces null values with an explicit readiness flag; missing exchange-session parents block the unit. `market_units` plans this consumer against current/context aggregate units with a bounded elapsed-time context selection. Live coverage checks sparse 10/60/120-second windows, publication, root sealing and no-insert resume. Complete launch-time session admission/context discovery and short-pressure rolling windows are still outstanding.

`context.plan()` now derives rolling market context from the earliest target PM timestamp minus the longest configured window in **UTC seconds**. It includes a prior exchange-session origin before that cutoff, including weekend/holiday closure and DST transitions. A seven-day window starting March 9, 2026 needs February 27 through March 6 context: spring DST puts the cutoff before March 2 PM. Windows exceeding the canonical source floor or 1,024 activity parents are explicitly blocked. Indicator/V7 requirements expose independent `calculation_start` dates; the combined planner accepts `state_start_dates={listing_key: (indicator_start, v7_start)}`. Older context can produce shared bars/liquidity without replaying state from before a recent compatible checkpoint. State calculation starts must cover each listing's target sessions; future/unplanned starts block compilation. Launch-time discovery must pass these derived dates rather than choose a common arbitrary lookback.

V7 rollover permits only the next exchange session. Weekend/holiday gaps are allowed; omitted trading sessions require context replay. It cannot silently jump past missing sessions.

The `plan --format json` output includes live active-part row/byte counts, compression ratios, physical bytes per stored row and designated-disk free/unreserved capacity. These are physical storage observations, not logical final-row counts or future campaign estimates. Named disks can share a physical volume; their free capacities must not be summed without volume evidence. Projection remains explicitly unqualified until representative normalized product/session pilot rates and staging/merge headroom are measured.

`python -B scripts/prepare_certified_research.py status --dataset-id <UUID> --format json` reads plan/progress/seal metadata without changing it. It explicitly reports `inspection_only`; a stored seal alone is not a fresh data-integrity verification.

## Verification and remaining integration

Concrete reference registration now connects SEC facts, derived fundamentals, short families, regular-session prices and float supply to the controller's producer/verifier contracts. Each call owns its ClickHouse client. A real AAPL SEC→derived campaign successfully published its root seal and resumed through those concrete handlers. Allocation and corporate producers still require full planning/registration, together with source acquisition and repair orchestration.

Regular-price units consume bounded shared-market parents and verify their listing/date/bar receipts. They recheck dated security membership during production and authority verification, including sealed resume. Float parent scopes come from actual certified reference receipts, so calendar/bar-derived price lineage is preserved rather than being recomputed with empty dependencies. An integrated market→regular-price ClickHouse fixture publishes the expected price and root seal, resumes without inserts, and rejects ambiguous membership and mismatched parent identity. Global universe evidence still belongs in the frozen campaign source guard.

### Shared market execution

`MarketHandlers` registers aggregation, technical indicators and V7 as campaign units in the same global worker/memory pool. It does not start a nested executor. One canonical aggregation builds shared staging; bars and liquidity are independently published/read back before the aggregate unit can certify. Its output hash binds both product receipts. Indicators and V7 then run independently and require the exact verified aggregate parent. V7 alone fetches its bounded bar input; SQL consumers remain in ClickHouse.

The standalone market pilot uses the same stage implementations. An isolated live ClickHouse campaign verifies one aggregate insert, a root seal, no-insert sealed resume, immutable request-scope checks and rejection of missing or altered shared parent evidence. This qualifies the execution wiring, not all-universe scan throughput. Request/engine factories still need full launch-time calendar, identity, context and certified predecessor discovery.

`v7.load_checkpoint` restores one certified PM/regular/AH state directly from ClickHouse. It verifies the product receipt, bounded payload size, calendar boundary, exact expected source-prefix/predecessor binding and payload/header hash, then rereads authority. It returns both full engine state and the rollover-compatible checkpoint record; same-day restoration performs no prior-day rollover. Live round trips cover all three boundaries and reject changed input-prefix evidence. Automatic compatible-predecessor discovery remains a launch-time integration requirement.

New market V7 publication uses the versioned `research-market-v7-prefix-v2` parent recipe. Its prefix origin binds calculation recipe, listing/ticker, session and trade/reporting policy, and contains no full-day bar digest. Previously the full-day bar hash entered earlier payload hashes despite causal engine calculations. The old parent recipe is not silently reused; existing campaigns need explicit compatibility treatment or a new dataset identity.

`v7.load_from_bars` derives expected boundary prefixes from independently certified 1s bars and verifies the PM→regular→AH chain before restoration. It reads only bars available by the requested boundary and performs hashing, not structural recalculation. This verification path supports predecessor discovery; a previously independently certified binding can use direct `load_checkpoint` for faster restoration. Tests prove an added AH bar leaves PM/regular payloads unchanged and exercise a live producer→certified-bars→AH restoration chain.

Production market V7 factories return `StartAuthority`, tying their initial engine fingerprint to the requested predecessor hash. Rollover construction verifies the AH payload and encoded boundary; genesis construction requires an untouched empty engine and a context-origin certificate independently verified by the launcher. The producer rejects mismatched/mutated start state before reading bars. An arbitrary raw engine is permitted only in isolated test namespaces. Tests reject wrong predecessors, mutated starts and PM payloads relabeled as AH. Full launch-time origin certification and predecessor discovery remain required.

`planning.market_units` makes the execution edges explicit: each admitted listing/session waits for certified source/reporting repair and the universe; indicators and V7 both wait for its shared aggregate. Indicator sessions form their own predecessor chain, and V7 sessions form a separate chain. Later aggregation can run ahead within resource limits. The first session requires certified seed/genesis units for both stateful products; missing seeds or duplicated listing/session membership stop planning. The caller must still derive the complete admitted session set and prerequisite units from actual authority.

Historical reporting repair no longer hydrates or reconstructs imported flatfiles. It requires source-bound staged sidecars, checks the stored mapping against canonical authority and retains sidecars after success. Live tests cover classification parity, low-bit/quote preservation, monthly bounds, idempotent mutation and mapping-tamper rejection. Missing sidecars block the repair; they require an explicit ingestion-owned versioned path.

Original-nanosecond reconciliation supports an explicit `repair_reporting_flags=True` mode. It compares the low six metadata bits and every supplied canonical comparison field while excluding only the two reporting bits that the native migration repairs. New-day ingestion keeps exact metadata comparison. Mismatched prices, clocks, eligibility bits, missing trades or ambiguous matches still block reconciliation; the native repair independently recomputes reporting reasons and verifies the final flags. This projection does not itself acquire historical clock evidence or publish a certificate.

Historical market recovery must not use REST. The user confirmed that Massive access is intentionally disabled while unsubscribed. Keep the existing flatfile downloader integration available for resubscription; do not repeat downloader or live Massive tests. Its launcher activation remains the explicit `prepare-sources --acquire-missing` option. Derivation and repair use certified ClickHouse events and retained certified sidecars. Missing historical evidence remains an explicit migration dependency; no REST or retained-flatfile fallback is authorized.

Canonical source certificates can span UTC midnight, and source tickers include case-sensitive symbols such as `ABRpD`. Any future ingestion-owned sidecar migration must respect the certified first/last SIP bounds and preserve exact ticker spelling.

`ExecutionLaunch` now verifies the full 28-table derived installation before capacity admission or execution, and before read-only authority verification. Missing tables, incompatible codecs/keys or incorrect part placement prevent controller writes. The bounded live reference-controller fixture uses these exact installed schemas; it remains a correctness fixture rather than production capacity evidence.

Tests cover a 10,000-unit reuse/failure chain, real ClickHouse plan/authority/seal publication, read-only sealed resume and rejection after output tampering. Keeper reconnect/session-loss fencing has deterministic coverage.

The local Keeper probe currently lacks the required secure LAN endpoint configuration. No plaintext fallback was used and no production campaign was started. The full launcher must still register all concrete acquisition/repair/reference/market handlers, build their dependency-derived plans and qualify campaign-scale performance. This controller core is not yet the complete launch path.
