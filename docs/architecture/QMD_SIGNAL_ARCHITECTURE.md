# QMD market-signal architecture

## Authority boundary

QMD owns reusable, causal observations derived only from canonical market data.
A strategy may consume those observations together with portfolio state,
reference data, news, SEC evidence, or its own models. The strategy owns the
final decision and any resulting order request.

| Layer | Authority | Output | May place an order? |
| --- | --- | --- | --- |
| Indicator | QMD | Measurement such as VWAP, flow imbalance, spread, or trade rate | No |
| Market signal | QMD | Versioned lifecycle event describing a reusable market observation | No |
| Strategy signal | Strategy runtime | Enter, exit, hold, or wait interpretation with source evidence | No |
| Order intent | Strategy runtime | IBKR-compatible `OrderRequest`, subject to risk validation | Yes |
| Broker state | Broker adapter | Acknowledgement, status, execution, position, and portfolio state | Executes only accepted intents |

This separation is deliberate. A bullish QMD signal can be useful to several
strategies without QMD knowing account exposure, risk, or the strategy's entry
rules.

## Canonical market-signal contract

`qmd-market-signal-v1` is emitted by the shared Rust
`MarketSignalEngine`. Every lifecycle row contains:

- `event_id`: unique identity of this lifecycle update.
- `signal_id`: stable identity shared by the trigger, updates, and resolution.
- `signal_key`, `schema_version`, `engine_version`, and `producer`.
- `ticker` and `working_timeframe`.
- `confirmation_timeframe` only when the emitted lifecycle actually consumed
  that completed confirmation interval; cataloged future confirmations are not
  projected into emitted events.
- `observed_at` and `effective_at`. Both are causal event/bar clocks, never the
  frontend wall clock.
- `state`: `triggered`, `updated`, or `resolved`.
- `direction`, signed `score`, and `confidence`.
- trigger/resolution reason, reference and invalidation prices, expiry, and
  bounded evidence.

The live and historical gateways run the same engine over the same finalized
QMD working-timeframe bar contract. A signal becomes effective when its declared
working-timeframe input is complete; displaying it on a larger chart must not
delay it until that display candle closes. The chart maps the exact
`effective_at` into the containing display candle. Methods that require
subsecond reaction must declare and compute on the 100 ms working timeframe
rather than relying on a frontend projection.

## Implemented QMD market signals

The following catalog methods currently have executable lifecycle logic:

- Tape acceleration breakout.
- Volume-shock momentum.
- Liquidity recovery after a spread shock.
- VWAP reclaim momentum.
- High-of-day break.

Opening-range breakout and liquidity pullback remain cataloged research
methods. Catalog presence is not treated as implementation.

The obsolete 25/100/500-event forecast contract is not part of the canonical
chart-bar or Python client schema. Its multiple horizons were difficult to
compose and could disagree while appearing strategy-ready. The lifecycle
contract above replaces it with versioned, reusable observations.

## Live and historical delivery

Live QMD exposes:

- `GET /snapshot/signals`: active signal lifecycles.
- `GET /snapshot/signal-events`: bounded newest-first lifecycle history.
- `WS /stream/signals`: newly emitted lifecycle events.

Application clients use the backend boundary rather than connecting directly
to QMD:

- `GET /api/trading/canvas-market-signals/{symbol}` returns active state or
  bounded lifecycle history for one ticker.
- `WS /api/trading/canvas-market-signals/stream/{symbol}` proxies only that
  ticker's canonical QMD lifecycle events.

QMD History computes the same signal events while causally rebuilding chart
bars and returns them as `market_signal_events` from the chart snapshot. The
history cache engine version changes whenever signal derivation changes so an
old cached interpretation cannot masquerade as current output.

Deterministic QMD events do not need a second durable copy solely for Canvas:
they can be rebuilt from canonical market events. If a strategy consumes a QMD
signal and makes a decision, that strategy decision is durable and is never
reconstructed from UI state.

## Scanner, Signal Stream, and chart

The scanner market universe and the signal event stream are separate
collections:

- scanner rows remain one row per security;
- the strongest active QMD signal and active-signal count may be joined onto a
  scanner row for sorting and filtering;
- `signal_rows` contains canonical lifecycle events for Signal Stream;
- the frontend must not invent signals from scanner percentages or activity
  fields.

The chart displays triggered QMD events as directional markers with confidence
and working timeframe. Updates and resolutions remain available to tooltips,
guides, and consumers but do not create duplicate entry arrows.

## Strategy integration and persistence

A strategy can implement `on_market_signal(signal, account_id)` and return a
`StrategyEvaluation`. The evaluation atomically separates:

- `signals`: explained strategy decisions, including source QMD `signal_id`
  values;
- `orders`: optional IBKR-compatible requests.

The runtime journals strategy signals before routing orders. Typed analytics
are written to `q_live.tr_signal_v2`, including strategy revision, action,
direction, score, confidence, timeframe, source signal IDs, invalidation price,
and reason. The legacy `tr_signal_v1` projection remains available during
migration.

## Causality and operational rules

- A signal cannot use events after `effective_at`.
- Historical and live output must share schema and formulas.
- `signal_id` is stable through a lifecycle; `event_id` is unique per update.
- A frontend refresh, chart timeframe change, or scanner sort cannot generate a
  new market signal.
- Missing evidence remains missing; it is not converted to neutral evidence.
- QMD signals never bypass strategy risk, account, or broker authorities.
- Full-universe historical Signal Stream requires a causal materialized
  cross-sectional signal artifact. It must not be approximated with per-ticker
  frontend rules.
