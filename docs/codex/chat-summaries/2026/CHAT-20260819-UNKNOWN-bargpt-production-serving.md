# Productionize BarGPT v2/v3 serving across application modes

- Chat started: Exact time unavailable; 2026-08-19 PDT (America/Vancouver)
- Chat ended or last activity: 2026-08-19 16:54 PDT (America/Vancouver)
- Summary written: 2026-08-19 16:54 PDT (America/Vancouver)
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

The final UI review found a concrete navigation defect and an ownership decision. The Services dashboard already includes BarGPT, but `service-bar-gpt` is absent from the valid page and service-mode maps in `frontend/src/App.tsx`, so selecting its detail view can render no page. A dedicated BarGPT operational configuration page is justified because releases, device and memory capacity, batching, caches, scopes, and inference health exceed what a generic service card can communicate. It belongs under Services. Market Discovery must continue to own revisioned product intent—Watchlists, selected models, Auto/Manual behavior, ticker limits, and downstream fields. The operational page must select immutable promoted release records rather than expose editable arbitrary checkpoint paths.

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

### Unfinished or hanging work

- **Dedicated BarGPT Services page.** Current state: dashboard card exists, but its route is missing and there is no complete operational editor. Next action: add the route and a page for promoted releases, desired versus effective/restart-required settings, capacity, caches, scopes, queues, latency, and output inventory. Keep Market Discovery intent separate. Related task: `TASK-0197`.
- **Release promotion.** Current state: loader compatibility is proven for v2 and the immutable v3 3.5B checkpoint, but neither filename recency nor loading is sufficient evidence for promotion. Next action: finish v3 training, compare fixed-panel checkpoints against v2, and publish an immutable release manifest. Related tasks: `TASK-0170`, `TASK-0197`.
- **GPU capacity certification.** Current state: defaults are bounded but not measured for the production workstation. Next action: benchmark memory and latency across ticker count, batch size, model combination, and context geometry; derive safe capacity rather than editing N by guess. Related task: `TASK-0197`.
- **End-to-end workstation smoke.** Current state: loader, unit, frontend, QMD, and API checks passed independently. Next action: deploy the service on the workstation with promoted shadow releases, warm a bounded Watchlist from QMD History, consume live QMD, and verify publication, freshness, queue behavior, and chart/rule consumption. Related task: `TASK-0197`.

### Handoff to the next chat

Read `TASK-0170`, `TASK-0197`, this summary, `services/bar-gpt/README.md`, and commits `5b9e06e2` and `faa6366f`. Do not point production automatically at a changing latest checkpoint, merge operational service settings into Market Discovery, remove raw heads in favor of decoded fields, or add KV reuse without parity evidence. The next implementation should repair the `service-bar-gpt` route and build the dedicated operational page around promoted immutable release records, then run the measured workstation capacity and end-to-end smoke after a release is explicitly approved.
