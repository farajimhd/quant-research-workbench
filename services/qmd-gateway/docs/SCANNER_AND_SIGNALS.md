# QMD Gateway Scanner And Signal Contracts

This file separates two ideas that should not be mixed:

- **Scanner primitive**: a fast candidate emitted by `qmd-gateway` from Massive quotes/trades only.
- **Signal method**: a documented trading setup contract. It may require broker state, reference data, or app-backend logic and is not automatically active in the gateway.

The gateway currently emits scanner primitives. The signal catalog defines future detector contracts and lets the app, replay runner, and backtest simulator use the same names and required fields.

## Current Scanner Primitive Output

Endpoint:

```text
GET /snapshot/scanner-primitives?limit=250
WS  /stream/scanner-primitives
```

Each emitted row has this contract:

| Field | Meaning |
|---|---|
| `schema_version` | Current scanner primitive contract version. |
| `detected_at` | UTC time when the gateway emitted the primitive. |
| `ticker` | Massive ticker. |
| `timeframe` | Bar timeframe that produced the primitive. |
| `primitive_key` | Primitive family, such as `tape_acceleration`. |
| `side_bias` | Current value is `long`. Short-side primitives are not implemented yet. |
| `score` | 0 to 1 rank score. Inputs are clamped to 0..1 and averaged. |
| `trigger_reason` | Short reason for the primitive. |
| `reject_reason` | Empty for current emitted primitives. Reserved for future rejected-candidate audit rows. |
| `close`, `vwap`, `price_change_pct` | Price evidence from the closed bar. |
| `volume`, `dollar_volume` | Activity evidence from the closed bar. |
| `trade_rate`, `quote_rate` | Event-rate evidence from the closed bar. |
| `tape_imbalance` | Buy-vs-sell volume proxy from trade classification. |
| `spread_bps` | Close spread in basis points. |
| `liquidity_score` | Dollar volume per unit of spread. Higher is better. |
| `estimated_luld_active`, `estimated_luld_state` | Local estimated LULD session flag and proximity state copied from the source bar. This is not official SIP LULD state. |
| `estimated_luld_distance_to_upper_pct`, `estimated_luld_distance_to_lower_pct` | Estimated percent distance to the locally calculated upper/lower LULD bands. Lower values mean closer to a band. |

## Active Primitive Rules

All rules use a closed bar. They do not use `conid`, float, short interest, account state, portfolio state, or IBKR.

| Primitive | Trigger | Score Inputs | Why It Exists |
|---|---|---|---|
| `tape_acceleration` | `trade_count_accel > 10`, `tape_imbalance > 0.15`, `spread_bps_close < 80` | trade acceleration, positive tape imbalance, positive price change, liquidity score | Finds early names where trade frequency and buyer pressure are increasing. |
| `volume_shock` | `dollar_volume_accel > 250000`, `price_change_pct > 0.25` | dollar-volume acceleration, percent gain, trade rate | Finds names where dollar activity appears suddenly. |
| `liquidity_recovery` | spread tightened, quote-rate acceleration is positive, liquidity score is positive | spread improvement, quote-rate acceleration, liquidity score | Finds symbols becoming routeable after poor spread conditions. |
| `vwap_reclaim` | trade close and quote midpoint are above VWAP, tape imbalance is positive | close-vs-VWAP, midpoint-vs-VWAP, tape imbalance | Finds reclaim behavior with tape confirmation. |
| `high_momentum_bar` | `price_change_pct > 1`, close is within 0.5 percent of bar high, `trade_rate > 0.5` | percent gain, trade rate, tape imbalance | Finds strong bars that close near the high instead of fading. |

These thresholds are starter values. Treat them as gateway defaults, not final trading rules.

## Signal Method Catalog

Endpoint:

```text
GET /signal-catalog
```

A catalog row tells a detector what it needs:

| Field | Meaning |
|---|---|
| `key` | Stable method id. |
| `label` | Human-readable name. |
| `category` | Setup family, such as tape acceleration or VWAP. |
| `priority` | `P0` default candidates, `P1` useful secondary methods, `P2` research or opt-in methods. |
| `compute_mode` | How it should run: tick, bar close, hybrid, or cross-timeframe. |
| `persistence_policy` | What should be written. Current stance is decision snapshots, not every intermediate value. |
| `status` | `cataloged` means contract exists. `implemented` should be used only after a detector writes decisions. |
| `working_timeframes` | Timeframes where the method is evaluated. |
| `confirmation_timeframes` | Timeframes used to confirm or reject the setup. |
| `required_bar_fields` | Bar fields needed by the detector. |
| `required_indicator_fields` | Indicator fields needed by the detector. |
| `required_reference_fields` | Non-Massive context needed by the detector. These belong in the app backend. |
| `trigger_rules` | Plain-English trigger conditions. |
| `confirmation_rules` | Conditions that strengthen the setup. |
| `reject_rules` | Conditions that block or weaken the setup. |
| `emits` | Output fields expected from a detector. |
| `snapshot_fields` | Evidence fields that should be saved when a signal is emitted or rejected. |

## Cataloged Signal Methods

| Method | Priority | Working Timeframes | Confirmation | Gateway/App Boundary |
|---|---|---|---|---|
| `tape_acceleration_breakout` | P0 | `1s`, `10s`, `30s` | `1m` | Gateway can provide tape fields. App adds float/short context and final route filters. |
| `volume_shock_momentum` | P0 | `10s`, `30s`, `1m` | `5m` | Gateway provides bar/tape activity. App adds market-cap/float context. |
| `opening_range_breakout` | P0 | `1m`, `5m` | `5m`, `1h` | Needs session/opening-range state, planned for gateway; app still applies context and route rules. |
| `vwap_reclaim_momentum` | P0 | `10s`, `30s`, `1m` | `5m` | Gateway provides VWAP and tape state. App applies strategy-specific risk. |
| `liquidity_pullback_reversal` | P0 | `30s`, `1m` | `5m` | Gateway provides spread/liquidity and trend fields. App decides if the pullback is tradable. |
| `gap_and_go_continuation` | P0 | `1m`, `5m` | `5m`, `1h` | Requires previous-close/session context and news/reference context. |
| `short_squeeze_pressure` | P0 | `10s`, `30s`, `1m` | `5m` | Gateway provides tape acceleration. App adds float, short labels, freshness checks, and route limits. |
| `high_of_day_break` | P1 | `10s`, `30s`, `1m` | `5m` | Gateway can supply day-high state once session indicators are implemented. |
| `trend_continuation` | P1 | `1m`, `5m` | `1h` | Needs cross-timeframe trend state. |
| `cross_timeframe_trend_alignment` | P1 | `1m`, `5m`, `1h` | none | Mainly a confirmation method for ranking other candidates. |
| `failed_breakout_exhaustion` | P1 | `30s`, `1m` | `5m` | Useful for rejecting weak breakouts and later reversal strategies. |
| `liquidity_recovery_after_spread_shock` | P1 | `1s`, `10s`, `30s` | `1m` | Gateway-native NBBO/spread method. |
| `premarket_leader_continuation` | P1 | `1m`, `5m` | `5m`, `1h` | Requires session phase and reference/news context. |
| `news_volume_breakout` | P1 | `10s`, `30s`, `1m` | `5m` | App backend owns news recency. Gateway owns live volume/tape evidence. |
| `mean_reversion_to_vwap` | P2 | `1m`, `5m` | `5m` | Disabled by default because momentum names can continue far past normal extension levels. |
| `range_compression_expansion` | P2 | `1m`, `5m` | `5m`, `1h` | Research candidate; useful after validation. |

## Persistence Rule For Signals

Raw quotes, raw trades, and bars are already persisted. For signals, persist the decision snapshot:

- method key and version
- ticker and timeframes
- emitted/rejected status
- score and side
- trigger, confirmation, and reject reasons
- exact evidence fields used at decision time
- route-blocking context from the app backend, if any

Do not persist every possible live indicator just because a signal might use it later. If a signal method becomes production-critical, promote the exact required fields through a versioned persistence contract.
