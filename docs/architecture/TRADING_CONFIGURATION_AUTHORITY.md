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
| Strategy Deployment | Usable unit combining a Strategy Profile, capability bindings, OMS profile, runtime modes, and account mandates |
| Runtime assignment | Operational binding of a published deployment to a ticker inside a run; never a configuration definition |
| Approved Release | Immutable application snapshot consumed by new runs |

System code may ship predefined Strategy Profiles and Capability defaults.
These are starting configurations, not locked secrets: the user can modify or
clone them, and the resulting draft is validated and published like any other
profile. Capability behavior remains implemented in registered code; the UI
configures only declared parameters and authority levels.

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

Entry logic has three explicit stages:

1. trigger groups select whether any or all configured opportunities must pass;
2. confirmation groups contribute declared weights to a configurable minimum
   score; and
3. veto groups block entry when their configured Boolean relationship passes.

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
Strategy Definition + Capability Definitions
    -> configured Strategy Profile
    -> Strategy Deployment + Strategy-account mandates + OMS Profile
    -> approved application release
    -> Replay / Backtest / Live runtime assignment
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

Portfolio translates the request into a final account quantity. A strategy
cannot exceed account policy or mandate limits. When an explicitly permitted
request lacks capacity, Portfolio may create an auditable rebalance proposal.
The proposal names the candidate position, evidence, improvement threshold,
and autonomy requirement. It does not silently release capital or bypass OMS.

## Configuration pages

| Page | Owns |
|---|---|
| Canvas | Canvases, layouts, groups, containers, link contexts, and presentation settings |
| Strategy Studio | Strategy Profiles, typed inputs, decision rules, and configurable Capability Bindings |
| Strategy Deployments | Profile-to-OMS-to-mode composition and deployment readiness |
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
- Strategy Studio presents frequently tuned entry, sizing, profit-taking, and
  re-entry controls first.
- Logical strategy behavior uses a guided rule builder with visible providers,
  source parameters, timeframes, comparisons, thresholds, Boolean grouping,
  and confirmation weights.
- Less frequently changed model, signal, and fixed technical parameters remain
  available under Advanced.
- Every parameter has a readable label, contextual help, units, appropriate
  control, and immediate range/choice constraints.
- The Strategy Profile -> Deployment -> Capital mandate -> Publication journey
  remains visible across configuration pages.
- Generated JSON is available on demand through a read-only advanced inspector.
- System profiles and capabilities identify their origin while remaining
  cloneable and configurable.

## Publication contract

Draft entities are mutable and non-executable. Publication:

1. validates profiles against registered strategy implementations;
2. validates capability settings against code-owned schemas;
3. validates deployment references and runtime-mode readiness;
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

The v2 model is authoritative. `resolve_runtime_configuration()` selects one
approved deployment for a runtime mode and projects it into the existing shared
strategy, Portfolio, OMS, account, and assignment contracts. This resolver is a
single migration boundary, not a duplicated configuration store.

Replay records both the complete approved model and its selected deployment.
Capability bindings are merged into the registered strategy parameters only at
this boundary. OMS remains a separate profile authority; the projection supplies
the existing runtime planner until it accepts profile references directly.

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
