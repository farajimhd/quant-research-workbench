# Productionize BarGPT v2/v3 serving across application modes

- Chat started: Exact time unavailable; 2026-08-19 PDT (America/Vancouver)
- Chat ended or last activity: 2026-08-19 17:18 PDT (America/Vancouver)
- Summary written: 2026-08-19 17:18 PDT (America/Vancouver)
- Chat/task identifier: unavailable
- Repository or scope: `quant-research-workbench`, BarGPT v2/v3 serving, QMD, backend, Market Discovery, Canvas, and service operations
- Related task-history entries: `TASK-0170`, `TASK-0197`
- Source completeness: Partial. The current conversation, repository state, commits, tests, and bounded workstation evidence were accessible; some early conversational detail was compacted and no inaccessible content was reconstructed.

### Narrative

The work began with a review of BarGPT v2, BarGPT v3, and the drafted `market-ai` service. The user wanted one production design that respected Live, Paper, Replay, Backtest, and Backtest Debug; warmed a configurable liquid Watchlist from QMD History; admitted and warmed tickers that entered during the day; updated bounded context continuously; supported Auto and Manual inference; and performed vectorized, concurrent inference as frequently as completed one-second origins allowed. The outputs needed to become ordinary application Data Fields so users could compose Rule Sets, Signal Streams, Market Discovery, and chart presentations without creating a second model-specific decision system.

Review showed that the generic `market-ai` code was stale relative to the trained BarGPT contracts. Its active contextual-news responsibility was not stale, so that responsibility was preserved as `services/news-hypothesis` rather than deleted. A new `services/bar-gpt` became the only BarGPT inference boundary. It loads immutable existing v2/v3 checkpoints through each research version's inference loader; no architecture update or retraining is required. Causal attention is safe for serving, but the current checkpoints do not expose a certified incremental KV-cache contract. Full-prefix inference therefore remains authoritative, with dynamic cross-ticker batching as the first optimization. KV reuse is explicitly deferred until numerical parity covers attention-window eviction, masks and warm-up padding, irregular views, sessions, split normalization, late corrections, and multiview as-of fusion.

The service implements fixed-length per-ticker ring caches, QMD History bootstrap, live QMD compact-event consumption, bounded warm concurrency, bounded inference queues, and mode/run isolation. Live and Paper share the live data authority; each Replay or Backtest run owns a separate historical cache and clock. Historical future bars are held outside the causal cache and promoted only as the run clock advances. New Watchlist members warm before inference. Manual mode keeps caches current but infers only when requested. Auto mode performs one non-recursive forward pass per eligible completed origin and publishes all heads; it never recursively predicts its own future inputs.

The first historical integration was asynchronous for performance. Final tracing exposed that a fast Backtest could outrun GPU publication when a Rule Set actually referenced BarGPT fields. The implementation was corrected: historical configurations with BarGPT dependencies use a synchronous fail-closed inference barrier at the causal clock, while runs without model dependencies retain asynchronous scope updates. Live/Paper prediction availability is the actual post-inference wall-clock time; historical availability is the logical bar-close time enforced by the barrier.

QMD Live gained a ticker-filtered compact-event batch stream with bounded event counts, delay, sequence metadata, and the existing lag/resnapshot semantics. The backend gained BarGPT health, prediction, scope, and feature-publication APIs plus a mode/scope/ticker/model store that prevents historical outputs from leaking into Live. Market Discovery configuration now carries BarGPT serving intent: enablement, Watchlist references, Auto or Manual trigger mode, maximum ticker count, and model IDs. The application registry exposes 2,984 v2/v3 fields, including every original raw checkpoint head and separate decoded values/probabilities. These fields are filterable and sortable through the existing Data Field to Rule Set to Signal Stream pipeline rather than a parallel signal authority.

Canvas gained independent forecast-open, high, low, and close lines plus translucent future candles. The lines use decoded q50 price values. Candles are rendered only when predicted OHLC geometry is valid; invalid geometry is not silently repaired, and the individual head lines remain visible. Original raw heads remain available as Data Fields. The UI work reused the existing indicator and chart configuration language rather than adding a model-specific chart system.

The stale `market-ai` launcher and implementation were removed. Service lifecycle, documentation, health telemetry, and the Services dashboard were updated for BarGPT and News Hypothesis. Commits `5b9e06e2` and `faa6366f` were pushed to `main`. Focused BarGPT, backend, registry, configuration, QMD Rust, and frontend builds/tests passed. The managed frontend visual matrix passed for the exercised application scenarios, although the BarGPT service detail route itself was not exercised.

Workstation verification corrected the earlier belief that a v3 artifact was unavailable. The v2 production run has a stable `checkpoint_latest.pt` dated August 17. The v3 production run was active on August 19, with bounded metrics showing 3,837,067,192 origins at 16:52 PDT and immutable global-validation checkpoints through 3,500,046,492 origins. The immutable 3.5B-origin v3 checkpoint loaded read-only through the actual service loader on laptop CPU. It reports 38,667,089 parameters, checkpoint SHA-256 `06885fdf8281e6e04366afcd690b59ddf54e9a1f3d41f09e799d0e7df154b180`, contract hash `61c8b8fca977403971ada4dd94202586e46a4567a0b3ee10f08ec3ba0e5196b0`, the expected six physical horizons, and full-prefix KV-disabled authority. This proves loader compatibility, not release approval.

The final implementation repaired the missing `service-bar-gpt` route and added a dedicated BarGPT operational editor inside the established Services detail page. The service owns a revisioned external configuration journal for immutable promoted-release selection, champion/shadow roles, device and precision, capacity and batching bounds, cache warm-up, prediction retention, and QMD connection intent. The UI distinguishes desired from currently effective settings and requires restart for safe activation. It receives release IDs, artifact basenames, hashes, parameters, and roles but never checkpoint paths. Market Discovery continues to own revisioned product intent—Watchlists, selected models, Auto/Manual behavior, ticker limits, Data Fields, Rule Sets, and Signal Streams. The service also adopted the shared `/snapshot/status` and `/metrics` contracts so the Services dashboard reports healthy BarGPT evidence without false HTTP 404 degradation.

### Durable decisions

- Existing v2/v3 checkpoints load directly; serving does not require retraining.
- One completed origin produces one non-recursive forward pass and publishes all heads.
- Full-prefix inference is authoritative. KV caching requires a separate parity-certified model/runtime interface.
- QMD and QMD History remain data authorities; BarGPT owns only causal model context and predictions.
- Live/Paper and every historical run must remain scope-isolated.
- Original raw heads and decoded values are separate field identities. Decoding must never replace raw output.
- Model predictions participate in the existing Data Field, Rule Set, Signal Stream, Market Discovery, and Canvas contracts.
- Production release selection must use an immutable checkpoint hash, contract hash, and fixed-panel evidence. `checkpoint_latest.pt` is not automatically promoted.
- BarGPT operational settings belong under Services; Market Discovery retains app-revision serving intent.

### Delivered outcomes

- Implemented `services/bar-gpt` and removed the stale generic Market AI implementation.
- Preserved contextual news inference as `services/news-hypothesis`.
- Added causal cache warm-up, live updates, batching, manual/auto modes, historical isolation, and fail-closed backtest synchronization.
- Registered all raw and decoded v2/v3 outputs and connected Live scanning, historical serving, rules, signal streams, and chart forecasts.
- Added QMD, backend, lifecycle, service telemetry, and documentation support.
- Pushed commits `5b9e06e2` and `faa6366f`.
- Loaded the immutable workstation v3 3.5B-origin checkpoint through the real service loader without touching active training.
- Added the Services → BarGPT configuration page, immutable release selection, desired/effective restart semantics, backend proxy, shared Services telemetry endpoints, and targeted UI review coverage.
- Captured a 12-scenario light/dark, 0.8/1.0/1.25 scale, normal/compact visual matrix with no objective issues, then visually verified the corrected healthy service state.

### Unfinished or hanging work

- **Release promotion.** Current state: loader compatibility is proven for v2 and the immutable v3 3.5B checkpoint, but neither filename recency nor loading is sufficient evidence for promotion. Next action: finish v3 training, compare fixed-panel checkpoints against v2, and publish an immutable release manifest. Related tasks: `TASK-0170`, `TASK-0197`.
- **GPU capacity certification.** Current state: defaults are bounded but not measured for the production workstation. Next action: benchmark memory and latency across ticker count, batch size, model combination, and context geometry; derive safe capacity rather than editing N by guess. Related task: `TASK-0197`.
- **End-to-end workstation smoke.** Current state: loader, unit, frontend, QMD, and API checks passed independently. Next action: deploy the service on the workstation with promoted shadow releases, warm a bounded Watchlist from QMD History, consume live QMD, and verify publication, freshness, queue behavior, and chart/rule consumption. Related task: `TASK-0197`.

### 2026-08-19 service lifecycle and QMD follow-up

Services and Market Discovery were reported unavailable. Runtime evidence showed
that QMD Live, QMD History, Backend, and Frontend were down. After using the
ownership-aware launchers, Services rendered normally after its roughly
two-second aggregate request, and Market Discovery recovered when QMD Live
restored the fail-closed definitions authority. QMD Live's earlier process had
shut down gracefully; it had not crashed.

The QMD log did expose a separate real defect: the inactive PLAG Generic
Structure checkpoint repeatedly received a non-retryable HTTP 409 because its
exact live cursor cannot be reconciled with archive ordinal identity. QMD now
persists that specific conflict as a blocked registry record with its error
code and required canonical-history rebuild action. It no longer retries every
five minutes, while all exact cursor checks remain unchanged. The restarted
gateway produced one blocked transition and no repeated conflict or deferred
lines. PLAG remains fail-closed until canonical reconstruction exists.

Application lifecycle is now composed by
`scripts/manage_application_services.py start|stop|restart|status`. It
preserves an already-healthy QMD Live stream, starts QMD Live when absent,
opens QMD History, Backend, Frontend, and BarGPT, waits for HTTP readiness, and
rolls back only services started by a failed attempt. The existing support
gateway bundle is unchanged. BarGPT production startup now accepts an external
promoted-release manifest and verifies its checkpoint SHA-256 and model
contract hash before loading. No workstation checkpoint was promoted: the
default external manifest is absent and fixed-panel release approval remains
the explicit blocker.

### Handoff to the next chat

Read `TASK-0170`, `TASK-0197`, this summary, `services/bar-gpt/README.md`, and the BarGPT serving commits. Do not point production automatically at a changing latest checkpoint, merge operational service settings into Market Discovery, remove raw heads in favor of decoded fields, or add KV reuse without parity evidence. Next, publish approved immutable release manifests from fixed-panel evidence, measure workstation GPU capacity and latency, and run the end-to-end QMD History warm-up plus live-publication smoke.
