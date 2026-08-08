# Trading Configuration Authority

## Invariant

Configuration pages define and publish versioned application behavior.
Replay, Backtest, Backtest Debug, Live, and Paper consume approved releases
and must never contain copied configuration or mode-specific implementations
of the same rules.

A feature is incomplete if a strategy, capability, account mandate, portfolio
limit, OMS behavior, protection rule, Canvas layout, container setting, or
presentation choice must be changed separately in a run page.

## Domain vocabulary

The product deliberately separates concepts that were previously grouped under
`strategy` or `assignment`.

| Concept | Authority and lifecycle |
|---|---|
| Strategy Definition | Code-owned implementation, schema, defaults, compatibility, and factory identity |
| Strategy Profile | User-configured or system-predefined instance of a Strategy Definition; mutable as a draft and immutable when published |
| Strategy Input Definition | Code-owned provider, field, parameter, value type, supported timeframes, and runtime projection |
| Decision Rule | User-configured comparison between a typed source and a constant or another typed source |
| Decision Rule Set | Configurable ALL/ANY/required-score relationship between typed conditions; any score threshold is local to that rule set |
| Capability Definition | Code-owned function contract, defaults, validation, help, UI schema, runtime handler, and compatible autonomy |
| Capability Binding | System/user configuration of one capability on one Strategy Profile |
| OMS Profile | Reusable versioned execution and protection behavior |
| Account | Stable application identity mapped to broker or simulated sessions |
| Portfolio Policy | Reusable account safety, exposure, capital, and loss envelope |
| Strategy-account mandate | Rule assigning a Run Plan to an account, with allocation topology, risk limits, and a maximum action-authority cap |
| Watch Universe | Versioned source of symbols that Run Plans may evaluate; configured symbols, an approved Scanner view, or a Watchlist |
| Strategy Run Plan | Reusable launch contract combining a Strategy Profile, Watch Universe, environment eligibility, per-action authority, OMS profile, and account mandates |
| Strategy Run | One actual execution of a Run Plan, pinned to an Approved Release |
| Strategy Campaign | One durable runtime lifecycle that exclusively owns one ticker-side pair in one portfolio book across initial entry, exits, and zero or more reentries |
| Campaign account leg | Account-specific execution and position state belonging to one Strategy Campaign; several legs may share the same ticker lease |
| Strategy Orchestrator | Shared runtime authority that grants and releases ticker leases and prevents conflicting active campaigns |
| Approved Release | Immutable application snapshot consumed by new runs |

System code may ship one protected default Strategy Profile plus predefined
profile templates and Capability defaults. The default remains editable and
cloneable but cannot be removed, renamed out of its stable identity, or
weakened through generated JSON. Templates create ordinary user profiles.
Capability behavior remains implemented in registered code; the UI configures
only declared parameters and authority levels.

## Strategy inputs and decision logic

Logical behavior must not be represented as one ambiguous choice such as
`breakout_reference`, and the runtime must not hide additional QMD, VWAP,
MACD, news, or veto rules outside the published Strategy Profile.

Every condition records:

- the named input and provider, such as QMD indicator, QMD market signal,
  market reference, or News signal;
- the exact source parameter or field;
- its calculation timeframe or event/session clock;
- the comparison operator;
- either a typed threshold or another typed source; and
- its containing rule set and that rule set's ALL, ANY, or required-score
  condition logic.

Initial Entry contains three subordinate stages:

1. opportunity groups select whether any or all configured opportunities pass;
2. confirmation rule sets decide their own condition logic; when score logic is
   selected, the required score belongs to that rule set and nowhere else; and
3. entry blockers prevent a new position when their configured Boolean
   relationship passes.

These stages are not peers of Exit or Reentry. A Strategy Profile has the
following primary behavioral areas:

1. Trading Behavior: exactly one side (`long` or `short`), eligible sessions,
   and manual-position adoption. Evaluation is derived from the sources and
   timeframes referenced by the active lifecycle rules; it is not an
   independent Strategy Profile gate;
2. Initial Entry: opportunity, confirmation, and blocker rules, a relative
   capital request, an OMS execution policy, and zero or more ordered add steps;
3. Reentry: independent opportunity, confirmation, and blocker rules, a
   relative capital request, an OMS execution policy, cooldown, maximum
   attempts, and fresh-evidence requirements. Initial Entry rule sets can be
   copied into Reentry as editable copies; they are not linked aliases;
4. Exit: ordered named rule sets using the same source, condition, grouping,
   timing, position action, and execution-policy grammar as Entry and Reentry;
   and
5. Capabilities: reusable code-defined functions that extend a lifecycle
   without replacing Entry, Reentry, or Exit.

Strategic exit rule sets are evaluated in declared list order. Each may define
an activation delay and an expiry measured from the confirmed entry, so a
failed-entry thesis cannot silently remain valid forever. Protective stops are
not strategy exit rule sets: the deployed OMS Profile calculates, submits,
repairs, and reconciles protection independently. Neither a Strategy Profile,
Run Plan, Portfolio mandate, nor account permission can weaken protective
execution.

A profile can therefore define price breaking either a confirmed QMD swing
high or QMD VWAP by a configured buffer, require scored flow-structure,
VWAP-slope, and MACD confirmation, and reject a configured
liquidity-dislocation signal. These are published data, not React-only
presentation choices or hard-coded runtime branches.

Publication validates source identities, timeframe support, comparisons,
targets, unique rule identities, per-rule-set required scores, exit timing,
position actions, and order intents. Replay requests
every timeframe referenced by the selected profile and keeps a point-in-time
cache keyed by source and timeframe. Historical QMD market-signal lifecycle
events are fetched from the QMD History scanner engine with an explicit ticker
filter, ordered by their effective time, and activated or resolved into that
cache without reconstructing signal decisions in Python. Future Live and
Backtest adapters must populate the same typed observation contract.

## Authority flow

```text
Watch Universes + Strategy Definition + Capability Definitions
    -> configured Strategy Profile
    -> Strategy Run Plan + action authority + Strategy-account mandates + OMS Profile
    -> approved application release
    -> passive strategy evaluation
    -> Strategy Orchestrator grants one ticker lease
    -> Strategy Campaign with one or more account legs
    -> strategy semantic request
    -> Portfolio account resolution, sizing, arbitration, and reservation
    -> OMS execution, broker lifecycle, and protection
    -> broker or simulated broker
```

Strategy may express sizing relatively through a `CapitalRequest`:

- fixed quantity;
- fraction of available mandate capacity;
- fraction of account risk;
- all capacity available under the mandate.

Each Initial Entry, Add, or Reentry transition owns its request. Strategy does
not define a position share ceiling because it does not own account capacity or
final quantity. Portfolio translates every request into a final account
quantity and applies account policy, the strategy-account mandate, current
positions, reservations, and arbitration rules. When an explicitly permitted
request lacks capacity, Portfolio may create an auditable rebalance proposal.
The proposal names the candidate position, evidence, improvement threshold,
and required action authority. It does not silently release capital or bypass OMS.

## Configuration pages

| Page | Owns |
|---|---|
| Canvas | Canvases, layouts, groups, containers, link contexts, and presentation settings |
| Strategy Studio | Protected default and user Strategy Profiles, Trading Behavior, Initial Entry, Reentry, ordered Exit routes, typed inputs, and Capability Bindings |
| Strategy Run Plans | Watch Universes, profile-to-OMS composition, environment eligibility, per-action authority, Safety Supervisor policy, and readiness |
| Portfolio & Risk | Account policies and Strategy-account mandates |
| OMS & Protection | Versioned execution-policy and protection-profile catalogs, plus reusable OMS routing profiles |
| Accounts & Sessions | Stable application accounts and mode-specific session bindings |
| Approved Releases | Whole-model validation, Canvas capture, immutable publication, and current runtime authority |

Run pages own scenario and transport inputs only. Replay owns historical date,
entry clock, playback controls, and simulated funding. Backtest owns its window
and execution pace. Live owns session startup and operational controls.

## UX contract

Raw JSON is never the primary configuration interface.

Every non-Canvas configuration route uses the same Configuration Workbench
shell. The compact workbench header is the single owner of page identity,
runtime authority, start options, and the Guided/Expert mode switch. A shared
seven-step workflow rail provides cross-authority orientation without
duplicating page navigation or save state. Canvas remains a distinct visual
workspace and is not governed by this form-oriented shell.

Configuration begins in one hybrid Configuration Studio rather than forcing
every operator into the full field inventory:

- **Use recommended setup** applies only registered protected Strategy and
  system OMS starting points. It previews the affected references and preserves
  account-specific mandates, risk limits, and broker bindings for explicit
  review.
- **Guided setup** asks one question at a time in authority order: Strategy,
  Run Plan, Portfolio, Execution, Protection, Accounts, and Review. Strategy
  coverage is generated from the complete selected profile: trading behavior,
  initial-entry capital/order/rules, every position add, reentry, every
  strategic exit, registered capabilities, and published advanced parameters.
  Section counts make that coverage visible, while **Keep remaining values**
  lets an operator explicitly approve a section's current defaults. Previous
  and next navigation preserves the same mutable draft, and each Continue
  action validates and saves the current canonical section.
- **Clone approved release** previews the immutable source release and replaces
  the complete mutable draft atomically. The approved release and active runs
  remain unchanged.
- **Expert editor** retains every existing field, rule set, catalog, and
  advanced parameter. Guided and Expert are views over the same schema-v9
  model, not separate configuration systems.

Guided mode is a focused decision document: it presents one question, its
consequence and provenance, the current choices, and Previous/Continue actions.
Each question is limited to one coherent subject, behavior, or lifecycle
action. A complete action may group only its own trigger, capital request,
execution response, and protection into clearly named subsections; unrelated
settings must be separate questions. Every editable field shows a concise
plain-language explanation inline, while the help control supplies secondary
detail. Control boundaries use the active theme's authority accent and a
distinct focus ring instead of a harsh dark stroke.

Every catalog and enumerated field uses the shared inventory lookup interaction
rather than a browser-native select. The trigger exposes the current human-readable
selection, the menu is portalled above dense editors, keyboard navigation is
preserved, and catalogs with more than seven values become searchable. This is
the same interaction authority used by News and SEC filters, adapted to the
configuration field hierarchy rather than reimplemented per page.

Guidance is structured, not a loose paragraph. Guided questions identify the
decision, why it matters or what changes, when it becomes effective, and how
keeping the displayed value approves the current default. Field-level guidance
names what each value controls; extended parameter/value notes remain available
through the help dialog.

The Configuration Workbench uses an editorial visual hierarchy derived from
the application's white operations theme: neutral page and card surfaces,
hairline structural rules, prominent decision headings, readable supporting
copy, and restrained controls. Domain accents identify the current authority
through navigation, icons, and focus; success, warning, and danger colors are
reserved for semantic state. Ordinary sections, editable fields, and action
subsections must not introduce independent tinted backgrounds or decorative
phase colors. Dark themes preserve the same hierarchy with tone-appropriate
neutral surfaces rather than becoming a separate visual language.

Page and decision titles are short operational labels, not explanatory
sentences. At wide workbench viewports, the page description and Configuration
Studio introduction remain single-line orientation copy; narrower layouts may
wrap them naturally without reducing type size. Runtime publication authority
is a prominent semantic status card that distinguishes a draft-only state from
an approved release and tells the operator whether publication is required.
Expert mode is a full-width subject editor, not the legacy field inventory under
different colors. Its leading contract map names the outcome, authority boundary,
subjects, and release behavior for the active route. Editors are then organized
as full-width subject sections with a readable two-column control grid; shared
lookups and field guidance remain identical to Guided mode. The save action stays
available in a sticky footer, profile libraries remain supporting navigation,
and canonical JSON remains an API and release artifact rather than page copy.

Recommended, inherited, customized, incomplete, invalid, and approved states
are shown as explicit provenance/status text rather than inferred from color.
The final review matrix names the effective selection for every authority and
links back to the relevant guided decision before publication.

- Every page starts with a concise summary of its responsibility and authority.
- Configuration navigation, page headers, journey state, and section headers
  use icon-backed domain accents resolved from the registered application
  theme. Strategy and OMS use blue/cyan orientation cues, Portfolio uses green,
  and deployment, account, and publication pages retain their own accents;
  semantic success, warning, and danger colors remain reserved for state.
- The visual hierarchy remains usable at every supported application scale and
  compact viewport. Color supplements icons, labels, borders, and structure; it
  is never the only distinction between configuration authorities.
- Configuration pages use the full main-workspace width with only the shared
  content inset; they do not impose a desktop max-width that narrows forms or
  leaves an unused right rail. The application sidebar begins directly below
  the top bar at every supported scale.
- Strategy Studio presents Trading Behavior, Initial Entry, Reentry, Exit, and
  Capabilities as collapsible containers with descriptive headers and
  at-a-glance configured summaries.
- Existing rule sets are collapsed by default, newly added rule sets appear
  first and open immediately, and Initial Entry rules can be copied into
  Reentry without creating hidden shared state.
- Logical strategy behavior uses a guided rule builder with visible providers,
  source parameters, timeframes, comparisons, thresholds, Boolean grouping,
  and per-rule-set required scores.
- Less frequently changed model, signal, and fixed technical parameters remain
  available under Advanced.
- Every parameter has a readable label, contextual help, units, appropriate
  control, and immediate range/choice constraints. Help for compound controls
  describes every nested parameter and how each selectable value changes
  runtime behavior.
- Editable controls use an accent treatment distinct from protected,
  inherited, and computed values. Section color and typography encode meaning
  and hierarchy; bold text is reserved for high-value identifiers and states.
- Context help opens only on click in a centered, viewport-level modal with its
  own scroll area and explicit close action. It never disappears on pointer
  movement or gets clipped by panels. It explains the parameter's role, the
  meaning of each available value, and any authority or safety consequence.
- Every Initial Entry, Add, Reentry, and Exit editor presents the
  Portfolio + OMS handoff before its decision rules. Capital request and
  execution policy use separate guided cards so relative sizing authority,
  execution trade-offs, and the resulting downstream output remain explicit.
- The Strategy Profile -> Deployment -> Capital mandate -> Publication journey
  remains visible across configuration pages.
- Generated JSON is available on demand through a read-only advanced inspector.
- The protected default profile explains why deletion is unavailable; user
  profiles remain removable only when no Deployment references them.
- Strategy Campaign controls distinguish Exit and keep watching from Exit and
  stop, so closing a position never silently changes ticker ownership.

## Publication contract

Draft entities are mutable and non-executable. Publication:

1. validates profiles against registered strategy implementations;
2. validates capability settings against code-owned schemas;
3. validates Watch Universes, deployment references, deterministic selection
   priority, and campaign authority;
4. validates account policies and Strategy-account mandates;
5. validates execution-policy and protection-profile identities, revisions,
   envelopes, partial-fill behavior, one-to-four protection slices, stop and
   trailing contracts, transition rules, and account-policy allowlists;
6. captures the complete configured Canvas registry;
7. serializes the complete model canonically;
8. records a SHA-256 content hash and immutable journal revision; and
9. makes the newest approved release authoritative for new runs.

Section edits use `PUT /api/trading/configuration/draft/{section}`. Operations
that intentionally replace several interdependent authorities, such as cloning
an approved release, use `PUT /api/trading/configuration/draft`; the backend
migrates, validates, and commits that complete draft atomically so intermediate
cross-reference states can never become the saved authority.

An active run pins the release identity, hash, approval timestamp, selected
deployment, full configuration model, and deterministic runtime projection.
Later draft edits or publications cannot mutate it.

## Runtime compatibility boundary

The schema-v8 model is authoritative. `resolve_runtime_configurations()` orders
every approved deployment eligible for a runtime mode by configured selection
priority and projects each profile, Watch Universe, campaign policy, Portfolio,
OMS, account, and campaign leg through one shared boundary. Individual
deployment resolution remains available for explicit operational selection; it
is not a second configuration store.

Replay records the complete approved model and every eligible Deployment.
Configured-universe symbols join Canvas and active-campaign symbols in the
historical stream. Every account leg carries its resolved profile parameters,
campaign identity, deployment, universe, book, and phase authority. The shared
Strategy Orchestrator permits several account legs in one campaign, rejects a
competing campaign for the same ticker, book, and side, and permits one long
plus one short campaign on the same ticker when they have distinct campaign
identities. One brokerage account cannot host both legs simultaneously because
ordinary broker positions net; opposing campaigns must use separate
non-netting account boundaries.

Strategy phase order settings select a broker-neutral registered execution
policy, partial-fill behavior, and deadline. Eligible sessions are configured
once in Trading Behavior. A strategy phase and an OMS Profile cannot override
time in force or outside-hours permission. The shared OMS derives those
broker-facing fields from eligible sessions and validates the account, venue,
broker, and order type before submission. Portfolio must first approve and size
the semantic request; OMS then chooses and manages broker-native order types,
session routing, replacements, partial fills, protection, and reconciliation.
Schema-v8 migration removes legacy phase and OMS session flags, creates the
versioned execution/protection catalogs, and binds every lifecycle order
intent to configured catalog identities so an older draft cannot reintroduce
conflicting authority.

The configuration UI exposes the complete `PortfolioPolicy` contract,
cross-account group limits, OMS quote source and bounded repricing envelope,
partial-fill behavior, and one-to-four protection slices. Every entry, add,
reentry, and strategic exit chooses an execution-policy identity; entry-like
actions also choose a protection-profile identity. Swing-based protection is
resolved only from causal structural observations already held by the
strategy, including first through fourth recent swings. The runtime projection
copies the selected immutable catalog revisions into each assignment before
evaluation.

`GET /api/trading/configuration/effective` provides a mode-selectable operator
preview. It does not make drafts executable: with `approved=true` it resolves
only the newest Approved Release and reports eligible deployments, exact
account/session bindings, policy identities, and OMS identities.

An exit makes a position flat; it does not necessarily finish a campaign.
`Exit and keep watching` retains the lease and enters Reentry wait. `Exit and
stop` completes every safe account leg and releases the lease. Paused campaigns
retain ownership by default. A release or handoff while exposed requires an
explicit safe transition rather than silently abandoning the position.

`initial_cash` configures `SimulatedBrokerAdapter` funding only. It is not a
Portfolio policy and cannot bypass allocation, exposure, risk, capability,
execution, or protection controls.

## Completion gate for another mode

Configuration eligibility for Live, Paper, Backtest, and Backtest Debug is
migrated through the shared resolver. A mode is operationally complete only
when its controller:

- loads one approved release through the shared configuration service;
- selects a valid Strategy Run Plan;
- uses the shared Strategy, Portfolio, and OMS contracts;
- renders the approved Canvas profile with `CanvasWorkspaceSurface`;
- records the pinned release and deployment identities; and
- proves configuration changes require publication in one place only.
