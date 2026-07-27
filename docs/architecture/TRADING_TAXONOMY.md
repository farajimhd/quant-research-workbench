# Trading taxonomy and clock contract

## Authority and boundary

The trading runtime owns the shared taxonomy in
`src/trading_runtime/taxonomy.py`. QMD publishes catalog and event metadata in
that vocabulary; it does not own strategy decisions or orders.

The taxonomy separates three concerns that must not be collapsed:

1. **Indicator type** describes reusable measured state: `technical`, `qmd`,
   `fundamental`, `reference`, or `model`.
2. **Signal domain** describes the evidence domain of a scored observation:
   `market`, `news`, `sec`, or `model`.
3. **Producer** identifies the service or model that created the value. `qmd`
   is a producer and an event-native indicator type, never a signal domain.

Every signal definition requires a normalized score so scanner consumers can
rank active observations without knowing the producer-specific formula.

## Clock contract

Calculation and delivery use an explicit clock contract:

| Field | Responsibility |
| --- | --- |
| `input_basis` | State that advances calculation: market event, bar, document event, reference snapshot, or model output |
| `calculation_window` | Window represented by one emitted value |
| `evaluation_mode` | Developing, closed-only, or point-in-time semantics |
| `update_trigger` | Event that causes evaluation |
| `publication_cadence` | When a changed value is exposed to consumers |
| `publication_interval_ms` | Required only for fixed-interval publication |

An event-native QMD indicator may update internally for every quote or trade,
while a scanner projection publishes the current state on a bar close. Those
are not contradictory: its input basis remains `event_native` and its
publication cadence is `bar_close`. A fixed 100 ms evaluation/publication clock
must be declared as `interval` with `publication_interval_ms: 100`; it is not an
implicit universal default.

Market signals produced by the current QMD signal engine are derived from
closed bars. Their event contract therefore declares `bar_derived`,
`closed_only`, and `bar_close`. Future event-native signal methods must declare
their own clock rather than inherit this one.

## Strategy contract

A strategy definition declares:

- indicator input references;
- signal input references;
- each input's timeframe, role, freshness, weight, and optional score or
  confidence threshold;
- whether developing inputs are permitted;
- its evaluation triggers; and
- a presentation policy.

Automatic strategies must declare at least one indicator or signal input.
Strategy decisions remain separate durable events with actions such as
`enter_long`, `add_long`, `reduce_long`, `take_profit`, `exit`, `hold`, and
`wait`. An order instruction is a downstream runtime decision and is not
implied by a signal. The first executable post-refactor revision is long only;
a future short strategy requires its own reviewed semantic-action extension.

## General chart presentation

Strategies do not own chart pixels. The Canvas chart uses one general
presentation adapter that accepts any normalized strategy decision stream and
the strategy's presentation policy. It can render:

- entry and exit markers;
- optional hold and wait markers;
- confidence in marker labels; and
- invalidation levels.

The renderer ignores the legacy Canvas strategy fixture. Only non-fixture
runtime decision events can appear as strategy annotations.

## API and QMD projections

- `GET /api/trading/taxonomy` exposes the shared vocabulary.
- Strategy definition reads expose normalized `taxonomy` beside `config`.
- QMD indicator and signal catalogs expose type/domain, producer, clock
  metadata, and calculation windows.
- QMD market-signal events use schema version 2 and include `domain`, `clock`,
  and the mandatory score.
- Scanner rows preserve taxonomy and clock fields so ranking and presentation
  do not have to infer them from labels.

Live and historical QMD paths share the same Rust event contract. Historical
replay must consume the stored/reconstructed event contract without changing
score, timing, lifecycle, or clock semantics.
