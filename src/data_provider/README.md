# Data Provider

The data provider is the canonical market-data layer for local research, backtests, and chart inspection. It separates data preparation from strategy execution:

- Offline build mode reads raw Massive minute files, normalizes timestamps, rebuilds all supported timeframes, calculates feature columns and supervision labels, writes Parquet artifacts, and records a manifest.
- Online/read mode loads those prepared artifacts without recalculating indicators.
- Consumers decide their own trading/session filters. The provider stores all bars present in the raw source, including premarket and after-hours data.

Default paths:

- Raw source: `D:/TradingData/massive_flatfiles/us_stock_sip/minutes_agg_v1`
- Processed output: `D:/TradingData/quant-research-workbench/market_data`

## Files

- `config.py`: versions, default paths, supported timeframes, feature groups, supervision groups, and build request objects.
- `raw_loader.py`: scans and loads raw Massive CSV/GZIP minute files.
- `timeframes.py`: canonical timestamp conversion, `bar_id` creation, and timeframe aggregation.
- `features.py`: deterministic feature and indicator calculations.
- `supervision.py`: future-looking research labels for learning and diagnostics.
- `builder.py`: orchestrates offline builds and writes artifacts.
- `provider.py`: read API used by backtests and frontend charts.
- `manifest.py`: JSON manifest for artifact status and provenance.
- `store.py`: partition paths and Parquet writes.

## Storage Layout

Artifacts are partitioned by group, timeframe, year, month, and session date:

```text
market_data/
  manifest.json
  bars/{timeframe}/{yyyy}/{mm}/{yyyy-mm-dd}.parquet
  features_core/{timeframe}/{yyyy}/{mm}/{yyyy-mm-dd}.parquet
  features_session/{timeframe}/{yyyy}/{mm}/{yyyy-mm-dd}.parquet
  ...
  supervision_bar/{timeframe}/{yyyy}/{mm}/{yyyy-mm-dd}.parquet
  supervision_method/{timeframe}/{yyyy}/{mm}/{yyyy-mm-dd}.parquet
  supervision_scanner/{timeframe}/{yyyy}/{mm}/{yyyy-mm-dd}.parquet
```

The manifest key is `{group}|{timeframe}|{session_date}` and records rows, columns, build time, and source file metadata. Version constants in `config.py` are part of the provider contract:

- `SCHEMA_VERSION`: changes when base bar schema or artifact layout changes.
- `FEATURE_VERSION`: changes when feature definitions change.
- `SUPERVISION_VERSION`: changes when future-looking label definitions change.

Builds intentionally use a single `force_rebuild` mode. Every selected session artifact is regenerated from the raw source and overwritten so the processed store reflects the current schema, feature definitions, supervision definitions, and raw inputs. The Build Data page uses the XNYS market calendar to separate expected trading sessions from weekends and exchange holidays, and reports missing raw files only for expected sessions.

## Time Handling

Raw Massive minute bars use `window_start` in UTC nanoseconds. The provider creates:

- `bar_time_utc`: timezone-aware UTC datetime from `window_start`.
- `bar_time_market`: timezone-aware exchange datetime converted to `America/New_York`.
- `session_date`: New York calendar date as `YYYY-MM-DD`.
- `session_month`: New York month as `YYYY-MM`.
- `minute_of_day`: New York hour * 60 + minute.

All chart labels and strategy filtering should use exchange time. Raw timestamps stay available for joins and provenance.

## Timeframes

Supported timeframes:

- `1m`
- `5m`
- `15m`
- `30m`
- `1h`
- `2h`
- `4h`
- `1d`
- `1mo`

`1m` bars are canonicalized directly from raw rows. Intraday aggregations bucket by New York `minute_of_day`, grouped by ticker and session date. Daily bars aggregate every available raw bar for the New York session date, including extended hours. Monthly bars aggregate daily bars.

Base OHLCV aggregation:

- `open`: first open in bucket.
- `high`: max high in bucket.
- `low`: min low in bucket.
- `close`: last close in bucket.
- `volume`: sum volume in bucket.
- `transactions`: sum transactions in bucket.
- `window_start`: first source timestamp in bucket.
- `bar_time_utc`: first UTC timestamp in bucket.
- `bar_time_market`: first exchange timestamp in bucket.

## Base Bar Columns

These columns are stored in `bars/*` artifacts:

- `ticker`: stock symbol.
- `volume`: aggregate share volume.
- `open`, `high`, `low`, `close`: OHLC prices.
- `transactions`: aggregate trade count from source data when available.
- `window_start`: source UTC nanosecond timestamp for the bucket start.
- `bar_time_utc`: UTC bucket timestamp.
- `bar_time_market`: New York bucket timestamp.
- `session_date`: New York date.
- `session_month`: New York month.
- `minute_of_day`: New York minute of day.
- `bar_id`: stable row key, formatted as `{timeframe}|{ticker}|{bar_time_utc}`.
- `timeframe`: artifact timeframe.

`bar_id` is the foreign key for all feature and supervision tables.

## Feature Groups

Feature artifacts are split by group so consumers can load only what they need. `MarketDataProvider.load_bars(..., feature_groups=[...])` joins selected feature groups to base bars on `bar_id` and drops duplicate base columns before joining.

### Core

- `hlc3`: `(high + low + close) / 3`.
- `ohlc4`: `(open + high + low + close) / 4`.
- `dollar_volume`: `close * volume`.
- `return_1`: `close / prior_close - 1`, per ticker.
- `log_return_1`: `ln(close / prior_close)`, per ticker.
- `bar_range`: `high - low`.
- `body`: `close - open`.
- `body_abs`: `abs(close - open)`.
- `upper_wick`: `high - max(open, close)`.
- `lower_wick`: `min(open, close) - low`.
- `close_location`: `(close - low) / (high - low)`, or `0` when range is zero.
- `is_green`: `close > open`.
- `is_red`: `close < open`.
- `vwap`: cumulative session `sum(close * volume) / sum(volume)` by ticker and session date.

### Session

- `day_open`: first open for ticker and session date.
- `day_high_so_far`: cumulative max high for ticker and session date.
- `day_low_so_far`: cumulative min low for ticker and session date.
- `day_volume_so_far`: cumulative volume for ticker and session date.
- `prev_close`: previous close in ticker order. This is currently a prior-bar reference, not a cleaned official prior regular-session close.
- `gap_pct`: `day_open / prev_close - 1` when `prev_close > 0`.
- `premarket_high`: max high before 09:30 New York.
- `premarket_low`: min low before 09:30 New York.
- `premarket_volume`: summed volume before 09:30 New York.
- `premarket_range`: `premarket_high - premarket_low`.
- `or_5m_high`, `or_10m_high`, `or_15m_high`, `or_30m_high`: opening-range high from 09:30 through the window end.
- `or_5m_low`, `or_10m_low`, `or_15m_low`, `or_30m_low`: opening-range low from 09:30 through the window end.
- `or_5m_range`, `or_10m_range`, `or_15m_range`, `or_30m_range`: high minus low for the opening range.
- `distance_to_day_open_pct`: `close / day_open - 1`.
- `distance_to_day_high_pct`: `close / day_high_so_far - 1`.
- `distance_to_day_low_pct`: `close / day_low_so_far - 1`.

### Momentum

- `sma9`, `sma20`, `sma50`, `sma200`: simple moving averages of close.
- `ema9`, `ema20`, `ema50`, `ema200`: exponential moving averages of close.
- `tema9`, `tema20`: triple exponential moving averages.
- `macd_line`: EMA12 - EMA26.
- `macd_signal`: EMA9 of `macd_line`.
- `macd_hist`: `macd_line - macd_signal`.
- `rsi14`: RSI from 14-period average up/down body movement.
- `roc10`: 10-bar rolling sum of `return_1`.
- `cci20`: reserved in the feature contract; not currently emitted unless added to the calculation.
- `stoch_k14`, `stoch_d3`: reserved in the feature contract; not currently emitted unless added to the calculation.
- `indicator_bar_count`: cumulative count per ticker.
- `macd_ready`: `indicator_bar_count >= 35`.
- `tema_ready`: `indicator_bar_count >= 20`.

### Volatility

- `true_range`: max of current range, `abs(high - prior_close)`, and `abs(low - prior_close)`.
- `atr14`: 14-bar mean of `true_range`.
- `bb_mid20`: 20-bar SMA of close.
- `bb_upper20`: `bb_mid20 + 2 * rolling_std20(close)`.
- `bb_lower20`: `bb_mid20 - 2 * rolling_std20(close)`.
- `bb_width20`: `(bb_upper20 - bb_lower20) / bb_mid20`.
- `donchian_high20`: 20-bar rolling high.
- `donchian_low20`: 20-bar rolling low.
- `donchian_mid20`: `(donchian_high20 + donchian_low20) / 2`.
- `keltner_mid20`: EMA20.
- `keltner_upper20`: EMA20 + `2 * atr14`.
- `keltner_lower20`: EMA20 - `2 * atr14`.
- `return_z20`: 20-bar z-score of `return_1`.
- `range_z20`: 20-bar z-score of `bar_range`.

### Volume And Liquidity

- `volume_sma20`: 20-bar average volume.
- `relative_volume20`: `volume / volume_sma20`.
- `dollar_volume_sma20`: 20-bar average dollar volume.
- `relative_dollar_volume20`: `dollar_volume / dollar_volume_sma20`.
- `obv`: cumulative on-balance volume using close direction.
- `mfi14`: money flow index using 14-bar positive/negative typical money flow.
- `cmf20`: Chaikin money flow over 20 bars.
- `volume_z20`: 20-bar z-score of volume.
- `transactions_sma20`: 20-bar average transaction count.
- `transactions_z20`: 20-bar z-score of transaction count.
- `liquidity_band_25bp_volume`: 20-bar rolling volume proxy for near-price liquidity.
- `liquidity_band_50bp_volume`: 50-bar rolling volume proxy.
- `liquidity_band_100bp_volume`: 100-bar rolling volume proxy.
- `hvn_price_proxy20`: 20-bar close mean, used as a simple high-volume-node price proxy.
- `lvn_price_proxy20`: 20-bar close median, used as a simple low-volume-node price proxy.

### Price Action

- `inside_bar`: high below prior high and low above prior low.
- `outside_bar`: high above prior high and low below prior low.
- `bullish_engulfing`: green bar whose body overlaps above the previous body.
- `bearish_engulfing`: red bar whose body overlaps below the previous body.
- `nr4`: current range is the narrowest of the last 4 bars.
- `nr7`: current range is the narrowest of the last 7 bars.
- `consecutive_green`: cumulative green count per ticker. This name is retained for compatibility but should be treated as a cumulative count until a reset-based streak feature is added.
- `consecutive_red`: cumulative red count per ticker. This name is retained for compatibility but should be treated as a cumulative count until a reset-based streak feature is added.
- `breaks_high20`: high equals or exceeds the 20-bar rolling high.
- `breaks_low20`: low equals or breaks the 20-bar rolling low.
- `pullback_from_high20_pct`: `close / donchian_high20 - 1`.
- `reclaim_vwap`: close crosses from below/equal VWAP to above VWAP.
- `breakdown_vwap`: close crosses from above/equal VWAP to below VWAP.

### Shock

The shock group separates price abnormality from participation abnormality, then records their recent sequence. It is designed for events where price wakes up first and volume may confirm immediately or several minutes later.

- `return_shock`: true when `return_z20 >= 2.5` and the current return is positive.
- `range_shock`: true when `range_z20 >= 2.5` and the candle body is positive.
- `structure_break_shock`: true when price breaks an important local structure level: 20-bar high, prior day high so far, premarket high, 5-minute opening-range high, or VWAP reclaim.
- `price_shock`: true when return/range/displacement/structure shock occurs and close location is at least 0.55.
- `price_shock_score`: bounded 0-1 score from return z-score, range z-score, close location, structure break, and bullish displacement.
- `relative_volume_shock`: true when `relative_volume20 >= 3.0`.
- `dollar_volume_shock`: true when `relative_dollar_volume20 >= 3.0`.
- `transactions_shock`: true when `transactions_z20 >= 2.5`.
- `volume_shock`: true when relative volume, relative dollar volume, transaction shock, or `volume_z20 >= 2.5` is true.
- `volume_shock_score`: bounded 0-1 score from volume z-score, relative volume, relative dollar volume, and transaction z-score.
- `bars_since_price_shock`: bars since the latest price shock for the ticker.
- `bars_since_volume_shock`: bars since the latest volume shock for the ticker.
- `minutes_since_price_shock`: approximate minutes since latest price shock.
- `minutes_since_volume_shock`: approximate minutes since latest volume shock.
- `price_shock_recent`: true when a price shock occurred within the last 15 bars.
- `volume_shock_recent`: true when a volume shock occurred within the last 15 bars.
- `price_shock_before_volume_shock`: true when the current volume shock confirms a recent earlier price shock.
- `confirmed_price_volume_shock`: true when a current volume shock occurs while a price shock is recent.
- `shock_confirmation_delay_minutes`: minutes from recent price shock to confirming volume shock when confirmed.
- `shock_confirmation_type`: one of `SAME_BAR`, `PRICE_FIRST_IMMEDIATE_VOLUME`, `PRICE_FIRST_DELAYED_VOLUME`, `VOLUME_FIRST_BREAKOUT`, `PRICE_ONLY_UNCONFIRMED`, `VOLUME_ONLY`, or `NONE`.
- `price_volume_shock_score`: bounded 0-1 combined score from price score, volume score, and confirmation speed.

### Fair Value Gaps

These are deterministic three-bar gap approximations:

- `bullish_fvg`: current low is above the high from two bars ago.
- `bearish_fvg`: current high is below the low from two bars ago.
- `fvg_high`: upper boundary of the gap.
- `fvg_low`: lower boundary of the gap.
- `fvg_mid`: `(fvg_high + fvg_low) / 2`.
- `fvg_size`: `abs(fvg_high - fvg_low)`.
- `fvg_size_pct`: `fvg_size / close`.

### Market Structure

- `swing_high_3`: high equals or exceeds centered 3-bar rolling high.
- `swing_low_3`: low equals or breaks centered 3-bar rolling low.
- `swing_high_5`: high equals or exceeds centered 5-bar rolling high.
- `swing_low_5`: low equals or breaks centered 5-bar rolling low.
- `higher_high`: high above prior high.
- `lower_low`: low below prior low.
- `bos_up`: close breaks above the prior 20-bar high.
- `bos_down`: close breaks below the prior 20-bar low.
- `trend_regime`: `up` when EMA20 > EMA50, `down` when EMA20 < EMA50, otherwise `range`.
- `bars_since_high20`: reserved column for future exact distance-to-high implementation.
- `bars_since_low20`: reserved column for future exact distance-to-low implementation.

### Order Blocks

These are deterministic displacement approximations:

- `bullish_displacement`: range > `1.5 * atr14` and close > open.
- `bearish_displacement`: range > `1.5 * atr14` and close < open.
- `bullish_order_block_high`: prior high when bullish displacement occurs.
- `bullish_order_block_low`: prior low when bullish displacement occurs.
- `bearish_order_block_high`: prior high when bearish displacement occurs.
- `bearish_order_block_low`: prior low when bearish displacement occurs.
- `distance_to_demand_pct`: `close / bullish_order_block_high - 1`.
- `distance_to_supply_pct`: `close / bearish_order_block_low - 1`.

## Supervision Tables

Supervision artifacts are future-looking labels for research and model training. They must not be used directly by a live strategy. Every supervision row references the source bar through `bar_id`.

### Bar Supervision

`supervision_bar` creates one row for each `(bar_id, horizon)` pair. A single bar therefore repeats across all fixed horizon rows, while the future-looking values change for each horizon. Fixed horizons are:

- 1 minute
- 2 minutes
- 3 minutes
- 4 minutes
- 5 minutes
- 6 minutes
- 7 minutes
- 8 minutes
- 9 minutes
- 10 minutes
- 11 minutes
- 12 minutes
- 13 minutes
- 14 minutes
- 15 minutes
- 20 minutes
- 25 minutes
- 30 minutes
- 45 minutes
- 60 minutes
- 90 minutes
- 120 minutes
- 150 minutes
- 180 minutes
- 360 minutes
- 480 minutes

Columns:

- `horizon`: string label such as `30m`.
- `horizon_minutes`: numeric horizon.
- `future_bar_count`: available future bars in the horizon.
- `valid_future_window`: true when at least one future bar exists.
- `fwd_close_return`: final future close return over the horizon.
- `fwd_high_return`: max future high return.
- `fwd_low_return`: min future low return.
- `fwd_mfe`: same as `fwd_high_return` for long-side maximum favorable excursion.
- `fwd_mae`: same as `fwd_low_return` for long-side maximum adverse excursion.
- `fwd_mfe_to_mae_ratio`: `fwd_mfe / abs(fwd_mae)` when possible.
- `time_to_mfe_bars`: bars until the best high.
- `time_to_mae_bars`: bars until the worst low.
- `time_to_mfe_minutes`: `time_to_mfe_bars * timeframe_step`.
- `time_to_mae_minutes`: `time_to_mae_bars * timeframe_step`.
- `mfe_before_mae`: true when the best high occurs before or at the worst low.
- `oracle_best_exit_bar_id`: future bar with the best long exit high.
- `oracle_best_exit_time_utc`: timestamp for that future bar.
- `oracle_best_exit_price`: best future high.
- `oracle_best_exit_return`: best future high return.
- `oracle_long_entry_signal`: true when the future path has at least 1 percent MFE, no more than 0.5 percent MAE, and MFE occurs first.
- `oracle_long_entry_confidence`: bounded score from return, path efficiency, and adverse movement.
- `oracle_long_exit_signal`: true when favorable movement is no better than adverse movement.
- `oracle_long_exit_confidence`: bounded inverse-path score.
- `path_efficiency`: direct entry-to-best distance divided by total path traveled.
- `green_bar_ratio`: fraction of future bars closing green.
- `fwd_volume_sum`: total share volume inside the future horizon.
- `fwd_dollar_volume_sum`: total dollar volume inside the future horizon.
- `fwd_transactions_sum`: total transactions inside the future horizon.
- `fwd_max_volume`: largest single-bar future volume.
- `fwd_max_dollar_volume`: largest single-bar future dollar volume.
- `fwd_max_relative_volume20`: largest future `relative_volume20`.
- `fwd_max_relative_dollar_volume20`: largest future `relative_dollar_volume20`.
- `fwd_max_volume_z20`: largest future `volume_z20`.
- `fwd_volume_expansion_ratio`: `fwd_max_volume / current_volume`.
- `fwd_dollar_volume_expansion_ratio`: `fwd_max_dollar_volume / current_dollar_volume`.
- `fwd_liquidity_confirmed`: true when a future volume shock appears in the horizon.
- `fwd_first_volume_shock_bar_id`: first future bar where volume shock is detected.
- `fwd_first_volume_shock_time_utc`: UTC time of the first future volume shock.
- `fwd_first_volume_shock_time_market`: New York time of the first future volume shock.
- `fwd_minutes_to_volume_shock`: minutes from the source bar to the first future volume shock.
- `fwd_volume_shock_before_mfe`: true when the first volume shock occurs before or at the best future high.
- `fwd_return_at_volume_shock`: close return at the first volume shock bar.
- `fwd_drawdown_before_volume_shock`: worst low return before the first volume shock.
- `fwd_estimated_capacity_dollars`: rough capacity estimate using 1 percent of max future dollar volume.
- `fwd_capacity_score`: bounded capacity score, scaled against 25,000 dollars.
- `fwd_price_outcome_quality`: same bounded price-path quality score used for long-entry confidence.
- `fwd_liquidity_quality_score`: bounded score from future relative volume, volume z-score, and capacity.
- `fwd_outcome_bucket`: combined label: `good_price_good_volume`, `good_price_bad_volume`, `bad_price_good_volume`, or `bad_price_bad_volume`.

Volume shock is currently detected when any of these future conditions is true:

- `volume_z20 >= 2.5`
- `relative_volume20 >= 3.0`
- `relative_dollar_volume20 >= 3.0`
- future volume is at least 3 times current volume
- future dollar volume is at least 3 times current dollar volume

### Method Supervision

`supervision_method` creates one row for each `(bar_id, trade_method)`. Methods define different horizon windows:

- `SCALP`: 1 to 10 minutes.
- `PRICE_VOLUME_SHOCK`: 1 to 45 minutes.
- `MOMENTUM_SCALP`: 5 to 30 minutes.
- `DAY_TRADE`: 30 minutes to end of available session data.
- `SWING_TECHNICAL`: 1 to 20 trading days by bar count approximation.
- `MEAN_REVERSION_LONG`: 1 to 60 trading days by bar count approximation.

Columns:

- `trade_method`: method family.
- `method_min_horizon_minutes`: earliest allowed future exit.
- `method_max_horizon_minutes`: latest allowed future exit, null for open-ended.
- `valid_future_window`: true when the future window has data.
- `method_best_exit_bar_id`: future bar with best long exit.
- `method_best_exit_time_utc`: timestamp for best long exit.
- `method_best_horizon_bars`: bars from entry to best exit.
- `method_best_horizon_minutes`: minutes from entry to best exit.
- `method_best_price`: best future high in the method window.
- `method_best_return`: best future high return.
- `method_mae_before_best`: worst low return before the best high.
- `method_mfe_mae_ratio`: `method_best_return / abs(method_mae_before_best)` when possible.
- `method_path_efficiency`: direct-to-best path efficiency.
- `method_entry_signal`: true when `oracle_action` is `ENTER_NOW`.
- `method_exit_signal`: true when `oracle_action` is `IGNORE`.
- `method_confidence`: bounded score from best return, drawdown before best, and path efficiency.
- `oracle_action`: `ENTER_NOW`, `WATCH`, or `IGNORE`.
- `current_price_shock`: copied current-bar price shock flag.
- `current_volume_shock`: copied current-bar volume shock flag.
- `current_confirmed_price_volume_shock`: copied current-bar confirmed price-volume shock flag.
- `shock_confirmation_type`: current or inferred confirmation type for the shock method.
- `shock_confirmation_delay_minutes`: current or inferred minutes from price shock to volume confirmation.
- `shock_price_score`: copied current-bar price shock score.
- `shock_volume_score`: copied current-bar volume shock score.
- `shock_score`: copied current-bar combined price-volume shock score.
- `shock_drawdown_before_confirmation`: worst drawdown before the confirming shock bar when confirmation occurs in the future window.
- `shock_return_after_confirmation`: best high return after confirmation, measured from the confirmation close.
- `shock_best_exit_after_confirmation_bar_id`: future bar id of the best post-confirmation high.
- `shock_best_exit_after_confirmation_time_utc`: UTC timestamp of the best post-confirmation high.

`PRICE_VOLUME_SHOCK` uses the same method-supervision row shape but overrides confidence with shock context, confirmation speed, and post-signal price path. The method is intentionally long-only and is meant to capture price-first events where volume arrives on the same bar, within the next 1-2 bars, or later within the 45-minute method window.

### Scanner Supervision

`supervision_scanner` ranks all tickers at the same timestamp for each trade method:

- `universe_size`: number of symbols ranked for that timestamp and method.
- `oracle_rank`: dense rank by `method_confidence`, descending.
- `oracle_percentile`: normalized rank where 1.0 is best.
- `method_best_return`: copied from method supervision.
- `method_mae_before_best`: copied from method supervision.
- `method_best_horizon_minutes`: copied from method supervision.
- `method_confidence`: copied from method supervision.
- `oracle_action`: copied from method supervision.
- Shock columns such as `current_price_shock`, `current_volume_shock`, `current_confirmed_price_volume_shock`, `shock_confirmation_type`, `shock_confirmation_delay_minutes`, `shock_score`, `shock_return_after_confirmation`, and `shock_drawdown_before_confirmation` are copied from method supervision when available.
- `is_top_1`, `is_top_3`, `is_top_5`, `is_top_10`: rank cutoffs.
- `is_top_1pct`, `is_top_5pct`: percentile cutoffs.

This table is intended to answer: at this timestamp, which tickers were the best opportunities for each trading method?

## Backtest Integration

Phase 1 backtests now use the provider for prepared minute data:

1. The frontend checks whether requested `1m` provider artifacts exist before starting a run.
2. The strategy loader reads provider `1m` bars and required features.
3. Strategy-specific session filtering is applied inside the backtest adapter, not inside the provider.
4. Five-minute strategy context is read from provider `5m` bars when available and joined to minute bars through an as-of join.
5. Prior daily stats prefer provider `1d` bars and fall back to raw file aggregation if processed daily artifacts are missing.

## Frontend Integration

The Streamlit sidebar has a `Data Provider` workspace:

- Choose raw source root and processed output root.
- Choose date range.
- Choose timeframes.
- Choose feature groups.
- Choose supervision groups.
- Optionally restrict to a comma-separated ticker list.
- Choose rebuild mode:
  - `skip_existing`: do not rewrite existing artifacts.
  - `build_missing`: write only missing artifacts.
  - `force_rebuild`: rewrite selected artifacts.
- Scan raw files before building.
- Build data and monitor per-date progress.

Charts and run dashboards use `MarketDataProvider` first. If provider artifacts are missing, the chart loader can fall back to older run artifacts/raw paths where that fallback is still supported.

## Performance Notes

- Raw files are scanned lazily where practical.
- Build output is partitioned by day and timeframe so the UI can load only the requested dates.
- Feature groups are separate Parquet files to avoid loading every indicator for every use case.
- Consumers can request specific tickers and columns.
- Avoid rebuilding supervision tables for very large universes unless needed; supervision is future-looking and can be much larger than base bars.

## Research Discipline

Provider features are deterministic and available at or before the bar they belong to. Supervision labels deliberately look into the future and are only for:

- strategy research
- scanner diagnostics
- model target generation
- post-run error analysis

Do not feed supervision columns into a live strategy, QuantConnect translation, or IBKR execution path except as offline labels during training/evaluation.
