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
| Decision Rule Group | Configurable ALL/ANY relationship between conditions; confirmation groups also carry explicit weights |
| Capability Definition | Code-owned function contract, defaults, validation, help, UI schema, runtime handler, and compatible autonomy |
| Capability Binding | System/user configuration of one capability on one Strategy Profile |
| OMS Profile | Reusable versioned execution and protection behavior |
| Account | Stable application identity mapped to broker or simulated sessions |
| Portfolio Policy | Reusable account safety, exposure, capital, and loss envelope |
| Strategy-account mandate | Many-to-many rule governing how one deployment may use one account |
| Watch Universe | Versioned source of symbols that deployments may evaluate; configured symbols, an approved Scanner view, or a Watchlist |
| Strategy Deployment | Usable unit combining a Strategy Profile, Watch Universe, selection priority, campaign authority, OMS profile, runtime modes, and account mandates |
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
- its containing ALL/ANY rule group.

Initial Entry contains three subordinate stages:

1. opportunity groups select whether any or all configured opportunities pass;
2. confirmation groups contribute declared weights to a configurable minimum
   score; and
3. entry blockers prevent a new position when their configured Boolean
   relationship passes.

These stages are not peers of Exit or Reentry. A Strategy Profile has the
following primary behavioral areas:

1. Trading Behavior: exactly one side (`long` or `short`), eligible sessions,
   evaluation trigger, and manual-position adoption;
2. Initial Entry: opportunity, confirmation, and blocker rules, a relative
   capital request, an OMS execution policy, and zero or more ordered add steps;
3. Reentry: independent opportunity, confirmation, and blocker rules, a
   relative capital request, an OMS execution policy, cooldown, maximum
   attempts, and fresh-evidence requirements. Initial Entry rule sets can be
   copied into Reentry as editable copies; they are not linked aliases;
4. Exit: ordered named routes with their own rule sets, position fraction,
   execution policy, and protected, strategic, profit, or emergency semantics;
   and
5. Capabilities: reusable code-defined functions that extend a lifecycle
   without replacing Entry, Reentry, or Exit.

Exit routes are evaluated in declared priority order. The protective-stop route
is always enabled, first, automatic, and close-only. Strategic routes may
require operator authority, but neither a Strategy Profile, Deployment, nor
account permission can weaken protective execution.

A profile can therefore define price breaking either a confirmed QMD swing
high or QMD VWAP by a configured buffer, require weighted flow-structure,
VWAP-slope, and MACD confirmation, and reject a configured
liquidity-dislocation signal. These are published data, not React-only
presentation choices or hard-coded runtime branches.

Publication validates source identities, timeframe support, comparisons,
targets, unique rule identities, and confirmation weights. Replay requests
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
    -> Strategy Deployment + campaign authority + Strategy-account mandates + OMS Profile
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
and autonomy requirement. It does not silently release capital or bypass OMS.

## Configuration pages

| Page | Owns |
|---|---|
| Canvas | Canvases, layouts, groups, containers, link contexts, and presentation settings |
| Strategy Studio | Protected default and user Strategy Profiles, Trading Behavior, Initial Entry, Reentry, ordered Exit routes, typed inputs, and Capability Bindings |
| Strategy Deployments | Watch Universes, profile-to-OMS composition, deterministic selection priority, campaign lifecycle authority, runtime modes, and readiness |
| Portfolio & Risk | Account policies and Strategy-account mandates |
| OMS & Protection | Reusable named execution and protection profiles |
| Accounts & Sessions | Stable application accounts and mode-specific session bindings |
| Approved Releases | Whole-model validation, Canvas capture, immutable publication, and current runtime authority |

Run pages own scenario and transport inputs only. Replay owns historical date,
entry clock, playback controls, and simulated funding. Backtest owns its window
and execution pace. Live owns session startup and operational controls.

## UX contract

Raw JSON is never the primary configuration interface.

- Every page starts with a concise summary of its responsibility and authority.
- Strategy Studio presents Trading Behavior, Initial Entry, Reentry, Exit, and
  Capabilities as collapsible containers with descriptive headers and
  at-a-glance configured summaries.
- Existing rule sets are collapsed by default, newly added rule sets appear
  first and open immediately, and Initial Entry rules can be copied into
  Reentry without creating hidden shared state.
- Logical strategy behavior uses a guided rule builder with visible providers,
  source parameters, timeframes, comparisons, thresholds, Boolean grouping,
  and confirmation weights.
- Less frequently changed model, signal, and fixed technical parameters remain
  available under Advanced.
- Every parameter has a readable label, contextual help, units, appropriate
  control, and immediate range/choice constraints.
- Editable controls use an accent treatment distinct from protected,
  inherited, and computed values. Section color and typography encode meaning
  and hierarchy; bold text is reserved for high-value identifiers and states.
- Context help is rendered in a viewport-level overlay so it is not clipped by
  panels. It explains the parameter's role, the meaning of each available
  value, and any authority or safety consequence.
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
5. validates OMS/protection profiles;
6. captures the complete configured Canvas registry;
7. serializes the complete model canonically;
8. records a SHA-256 content hash and immutable journal revision; and
9. makes the newest approved release authoritative for new runs.

An active run pins the release identity, hash, approval timestamp, selected
deployment, full configuration model, and deterministic runtime projection.
Later draft edits or publications cannot mutate it.

## Runtime compatibility boundary

The schema-v5 model is authoritative. `resolve_runtime_configurations()` orders
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
policy, time in force, outside-hours permission, partial-fill behavior, and
deadline. They do not create broker orders. Portfolio must approve and size the
semantic request before the shared OMS chooses and manages broker-native order
types, replacements, partial fills, protection, and reconciliation.

An exit makes a position flat; it does not necessarily finish a campaign.
`Exit and keep watching` retains the lease and enters Reentry wait. `Exit and
stop` completes every safe account leg and releases the lease. Paused campaigns
retain ownership by default. A release or handoff while exposed requires an
explicit safe transition rather than silently abandoning the position.

`initial_cash` configures `SimulatedBrokerAdapter` funding only. It is not a
Portfolio policy and cannot bypass allocation, exposure, risk, capability,
execution, or protection controls.

## Completion gate for another mode

Live or Backtest Debug is not migrated until it:

- loads one approved release through the shared configuration service;
- selects a valid Strategy Deployment;
- uses the shared Strategy, Portfolio, and OMS contracts;
- renders the approved Canvas profile with `CanvasWorkspaceSurface`;
- records the pinned release and deployment identities; and
- proves configuration changes require publication in one place only.
