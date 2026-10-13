# Current columnar programme app integration

This is the shared current path for new programme strategies. Legacy releases
and their recorded results retain their original executable contracts. The
frozen V7 reference is a strategy specification and comparison authority, never
an app market-data source or filling broker.

## Data and execution

Certified database inputs pass through QMD History, the shared volume scanner,
typed Signal Streams, market-only vectorized rule evaluation, and chronological
selection using actual filled position state and age. Orders use the existing
Portfolio, OMS and simulated filling broker. Evaluation and liquidity clocks
are separate pinned contracts.
The normal Backtest request accepts `broker_liquidity_resolution_ms` as 100 or
1000 for programme configurations. Omitting it retains the certified prepared
clock. Preflight reports that clock and rejects any requested mismatch; it never
silently changes a prepared filling contract. The Backtest page exposes this
choice separately from one-second programme evaluation and uses the full
population without attaching a legacy structural-book option.

The frozen winner uses elapsed 30-second completed share volume, prior RTH
split-adjusted close eligibility from $0.80 through $50 before ranking, top 10,
and lexical listing ties. Its initial entry allowance is $1,000 and each add is
$200; changing cash parameters creates a new immutable release. Every fill's
commission and applicable execution cost contributes to app P&L.
Opening-reference publication requires the entire sealed identity population.
Cold history preparation can load explicit `HistoryPinRequest` packets through
`certify_history_pins_async`. Its fixed worker pool owns SELECT-only connections
and retains only verified pins, releasing complete certification frames before
returning to the event loop. `certify_population_history_async` checks the full
price-eligible population and opening lineage before opening those readers.
These consumers reuse native child-hash/storage verification and never invoke
a missing-data producer.
The app's native `prepare_app_controller` also accepts a
`ProgramPublishedInputRequest` in place of preassembled cohort plans. While
holding its existing handoff reservation, it validates the release fields,
complete eligible population, opening lineage, evaluation origin and explicit
cohort windows; cold-loads the volume and feature seals with its approved
reader; then passes ordinary `ProgramCohortInputPlan` objects into the existing
typed journal/Signal Stream/launch preparation funnel. Missing inputs fail
before any journal writer is attached. This preparation does not start a run.
Programme preflight returns the shared page's configuration identity, run-plan
identity, cash and execution-window fields from that reviewed definition. The
page requires those fields before enabling launch. A browser-only fixture review
is available through `python scripts/run_frontend.py ui:review --
--programme-backtest-offline --output-dir <runtime-directory>` after a frontend
build. It intercepts all network requests, tests clock mismatch rejection and
the launch payload, and starts no HTTP service or actual backtest. This rendered
review is separate from native financial and source-data qualification.
A ticker projection cannot publish an alternative scanner population under the
same programme. Price eligibility is applied only after complete population
coverage, before volume ranking; legacy identity readers retain their existing
projection behavior.

QMD history and opening references use the central `q_live` contracts. Broker
liquidity, configuration, programmes, releases, lifecycle and journals use
their central `arte` contracts. No programme creates tables, persists JSON/blob
trading rows, or substitutes SQLite or a disk journal for ClickHouse authority.
Coverage, source hashes, exact clock bounds, native price bits and Keeper
receipts remain mandatory. A populated table alone is not certification.

## Credentials and readers

`program_app_credentials.py` defines the shared credential profile names.
`managed_backtest_credentials.py` loads provisioned optional profiles on the
workstation through the normal backend launcher. Missing optional files leave
existing launches unchanged; malformed or conflicting files fail atomically.
Private values stay in workstation secrets and process memory.

The lazy `program_app_reader_factory` accepts the market-input, release,
selection and journal reader roles. The journal role uses the already-approved
programme runner principal with server-enforced `readonly=1` and its native
programme cold-read profile; it does not create another account or writer lane.
Native asynchronous readers invoke each factory
in an owned worker, receive an independent SELECT-only connection, and close
it before returning. Factories never install releases, start producers, or
authorize backtests. The existing app startup gate still requires explicit
approved selection/executor pins and a native portfolio-policy attestation.

The QMD History columnar endpoint uses the dedicated
`program_market_input_reader` profile, loaded using the existing `DOTENV_PATHS`
mechanism from its private file. It does not fall back to the gateway's general
historical credentials. An absent profile returns HTTP 503 with
`columnar_history_reader_not_configured`; a partial or wrong profile fails
configuration validation. Health exposes `columnar_history_reader_configured`,
which reports configuration, not input coverage or successful authentication.
Every input request still checks grants, physical placement and its cold seal.
The gateway's other historical routes retain their existing credentials.

## Normal app startup

The normal `scripts/run_backend.py` launcher supports the paired explicit
`--approved-program-startup-plan` and
`--approved-program-startup-plan-sha256` arguments. Without them its original
app target remains unchanged. With them, the Uvicorn factory returns the same
app, routes and lifespan and defers file, database and Keeper I/O to owned
workers during startup. Reload starts a new worker with the same pinned plan.

The bounded runtime approval file has exactly these fields:

| Field | Meaning |
|---|---|
| `contract` | `approved-program-app-startup-v1` |
| `approved_selections` | Map of central configuration UUIDs to approved content hashes |
| `executors` | Approved `strategy_id`, `revision`, `expected_content_hash`, `expected_code_hash`, `expected_winner_hash` references |
| `concurrency` | Explicit admitted cold-read bound from 1 through 6 |

This file holds approval references only. It cannot override cash, fees,
programs, market inputs, OMS or broker settings. The supplied SHA256 pins its
exact bytes; it is an integrity check, not a signature or automatic permission.
Read-only central release verification completes before code registration.
Central selection and portfolio-policy/Keeper receipts are then verified before
background services start. Startup owns and closes only its policy read
session; externally supplied services retain their existing ownership contract.
No publisher, journal writer, input producer, run or live admission is started
by programme selection setup. Service startup still needs operator approval.

## Operator boundary

`scripts/clickhouse/plan_program_app_principals.py` is review-only. Its eight
profiles separate three readers from five product publishers. Publisher writes
are confined to 28 shared tables: four history, three opening-reference, three
liquidity, five release and thirteen configuration/Signal Stream definition
tables. The source reader inherits the existing app's reference/market read
catalogue plus the required new product SELECTs. Exact SQL grants are available
with `--show-grants`.

`scripts/clickhouse/provision_program_app_principals.py` is plan-only by default.
Applying requires workstation ownership, an exact bounded runtime approval
plan, `--apply`, and `--confirm-program-principals`. It verifies SSD storage
before credentials, audits existing accounts before new changes, refuses
unexpected authority, preserves partial account/grant progress for exact-plan
resume, and verifies final effective grants. It creates no tables or rows and
starts no producer, release publication, service or backtest. Accounts and
grants require operator approval; this command does not confer permission for
subsequent operational steps.

Full app qualification requires certified source days including causal prior
RTH sessions, producer publication, centrally sealed release/configuration,
normal app activation, real fills, cold recovery and all30 optimizer comparison.
Synthetic rule equivalence does not establish filling or P&L equivalence. The
legacy SQLite-ledger market-day builder is not an approved new producer path.
The current producer checkpoint interface is
`pipelines.market_sip.events.market_day_checkpoint`. It stores completed stage
and seed receipts in the existing shared normalized ARTE certificate tables,
uses bounded off-loop reads and native Keeper ownership for writes, and
reconciles uncertain acknowledgements without blind reinsertion. Its immutable
completed-receipt view satisfies the existing certificate assembler's ledger
interface. These unfinished producer receipts never admit consumers: the full
native inventory and final Keeper fence are still required. No legacy builder
cutover, schema publication or market production is enabled by this source.
`pipelines.market_sip.events.current_market_day_bars` executes the current
canonical broker/base-bar/rollup stages using the shared numeric SQL and source
verification functions. It verifies outputs and unchanged canonical input
before committing the completed receipts through the shared checkpoint writer.
Verified completed work is skipped; uncheckpointed output is an explicit
recovery blocker, never blindly reinserted or overwritten. Technical/seed
stages, final certificate publication and consumer admission remain required.
`market_day_predecessor.MarketDayPredecessorLedger` replaces producer-side
SQLite candidate discovery with explicit immutable current checkpoints and
fully audited parent receipts. Parent headers and receipt families must match
their final fences and calculation/rule profile. The target-bound numerical
reader rejects same-day and future parent access. Missing or ambiguous parent
declarations block preparation; they never silently select a newer mutable
build timestamp or infer bootstrap state.
`current_market_day_technical.build_current_market_day_technical` reuses the
existing numerical SQL and seed-hash rules through that target-bound parent
reader. Split data comes from the immutable source plan. Reverified completed
technical output can finish its missing seed checkpoint without reinsertion;
changed parent state or orphaned uncheckpointed output blocks publication.
This stage alone does not issue a final fence or admit any app consumer.
`current_market_day_publication.publish_current_market_day_build` is the
operational completion boundary. It loads the complete shared checkpoint
population, assembles the existing normalized inventory off-loop, and retains
the native canonical source replay, physical storage checks, exact readback and
final Keeper CAS. Missing stage/seed receipts fail before a source publication
client opens. Calling this writer still requires explicit operational approval;
source implementation and synthetic qualification do not authorize publication.
`current_market_day_producer.run_current_market_day_producer` composes the stage
workers and completion gate with an explicit fixed worker count. Dates execute
chronologically; each day's bar phase completes before its technical phase.
Progress reports count ticker/session/phase units and expose queued, active,
completed, skipped and failed counts. Cancellation or peer failure stops new
units and drains active workers/checkpoint writes. An interrupted stage phase
cannot reach final publication. Declared predecessor checks run before new bar
work; absent predecessor/bootstrap certification remains a separate required
gate, and the coordinator does not silently infer it.
`market_day_predecessor_absence.certify_empty_market_day_predecessors` provides
the explicit absence gate through bounded SELECT-only catalogue reads for the
immutable calculation/rule profile. Any compatible published technical receipt
blocks an absence claim, including a receipt that requires further native
audit. The producer rechecks supplied absence evidence before day work and
again before final publication; a newly published parent revokes bootstrap
eligibility. Existing typed seed records still express the original bootstrap
policy. No table, storage fallback or alternative numerical seed rule is added.

Managed app shutdown disposes an unused staged programme handoff before closing
its policy reader. It drains runtime background-task and typed journal cleanup,
revokes the unused launch plan, and attempts policy-reader cleanup even when a
handoff resource fails. A run already transferred to the run service remains
run-owned. Preparation or launch in progress must settle before disposal;
shutdown reports an ownership error rather than disposing that active handoff.
