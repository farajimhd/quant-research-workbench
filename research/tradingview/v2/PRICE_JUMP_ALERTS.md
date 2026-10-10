# Price jump screener and alerts v2.3

Copy `price_jump_alerts.pine` into TradingView's Pine Editor, replace the prior indicator, reset inputs, and recreate its alerts. This standalone indicator detects fresh candle displacements, not a statistical jump-process model or certified workbench signal.

## Detection

Defaults: upward jump of at least 2%, immediate live alerts, and **Previous close or bar open** reference. Upward movement uses the lower of a fresh previous close and the current open; downward movement uses the higher. This catches both a fresh opening gap and a move developing inside the candle. Select Previous close or Bar open explicitly to use just that reference. A candle that is flat near a price reached several bars earlier cannot qualify merely because an old low rolls through a five-bar window: there is no multi-bar return trigger.

The previous close is eligible only within the same exchange daily interval and with at most 120 seconds of missing time between its candle end and the current candle start. Contiguous 1-minute or 5-minute candles have zero missing time. The age limit is configurable. The default combined mode uses the current open alone when the previous close is stale; Previous close mode instead reports unavailable data. This is deliberate: stale overnight gaps are excluded, but a large move inside the first available candle is still detectable. There is no five-bar warmup/contiguity requirement. Price-only mode can evaluate from the first available candle using its open.

Realtime alerts evaluate the latest received price on every update. They do not wait for bar close. Feed/notification latency still applies. Optional volume/session gates must also pass at that update. Disable **Alert immediately during live bar** for close-only qualification. A threshold touch that reverses can alert in immediate mode.

After emission, that direction stays latched until a completed **settled candle**: its high/low range and its closing displacement against both selected directional references must each be at most 0.5% by default. Missing references and failed volume/session gates do not rearm. The settled threshold must be smaller than the jump threshold; change both if testing a jump threshold below 0.5%. Consecutive large candles in one burst therefore do not generate repeated same-direction signals. After a settled candle, a new fresh jump can trigger. Up/down latches and cooldowns are independent. Cooldown defaults to zero; when enabled, it is bar-index distance between emissions and can delay or suppress a fresh qualifying event.

## Markers and Pine Screener

In immediate mode, historical/confirmed screening qualification uses the current candle high for upward touches and low for downward touches against the same references. Close-only mode uses the closing price. Historical markers belong to the candle that touched the threshold, including a reversal candle. OHLC does not establish the exact first-touch time, order of upward/downward touches, or whether volume gates passed at the moment of touch. Optional historical volume/session gates use completed candle data. Historical markers are touch evidence, not proof of a delivered realtime alert. A live marker latches on its emission candle; a reload recalculates historical touches and may differ from the live tick sequence.

1. Save the script and add it to your indicator favorites.
2. In Pine Screener, choose your watchlist and **Research: price jump screener / alerts v2.3**.
3. Start with 1 minute and matching chart inputs/session/feed settings.
4. Filter **Recent up signal = 1** for an emitted confirmed candle-touch event within the last five completed bars by default. **New up signal = 1** restricts this to the latest completed bar. **Up qualified = 1** means that candle touched the threshold and passed gates, whether or not its burst latch permitted a new event. Downward equivalents require Detect = Downward or Both.
5. Scan/rescan after changes. Use chart alerts for continuous notifications; screening is a separate operation.

The first ten numeric columns retain the latest completed candle values while a live candle forms. **Jump %** is closing displacement against the selected directional references (the displacement with the larger absolute magnitude), not the maximum excursion: a touched/reversed candle can have Up qualified = 1 and Jump % below 2. **Relative volume** uses a prior-bars-only average, **Bar dollar volume** is `volume * close`, and **Data ready** means valid references and any required volume data exist. Readiness does not imply threshold/gate passage or certified feed coverage. Unavailable numeric values remain `na`. The additional **Live jump %** plot follows the live displacement and supplies alert messages. Alert-condition filters are live pulses, while numeric New/Recent outputs are confirmed candle-touch events.

TradingView Pine Screener evaluates only the last 500 bars and supports 1m, 5m, 15m, 30m, 1h, 2h, 4h, 1D, 1W and 1M. This indicator supports the intraday subset; the 10-second chart is for chart alerts, not Pine Screener. Volume baseline is bounded to 200 bars and recent retention to 100. Persistent burst state can differ between a long-running chart and a fresh 500-bar scan if no settled candle occurs within the scanned history. See [Pine Screener requirements](https://www.tradingview.com/support/solutions/43000742436-tradingview-pine-screener-key-features-and-requirements/).

## Alert setup

- Recommended: choose this indicator and **Any alert() function call**. The script controls immediate timing and suppresses repeated ticks using intrabar-persistent state.
- Alternatively choose **Upward price jump**, **Downward price jump**, or **Any price jump**, with **Once Per Bar**. Once Per Bar Close can miss a transient immediate pulse. For close-only alerts, disable the immediate input and choose Once Per Bar Close.
- If Both directions trigger in one candle, script-controlled alerts can deliver both; a combined alertcondition with Once Per Bar can only deliver its first event that bar.
- Recreate alerts after code/input/symbol/timeframe/session changes. Existing alerts retain their saved configuration. Select one mechanism to avoid duplicate notifications. See [Pine alerts](https://www.tradingview.com/pine-script-docs/concepts/alerts/).

Volume/session gates are off by default. Volume is assumed to be equity shares; dollar volume is an approximation, not executed notional. Missing volume does not block price-only mode. A required relative-volume gate rejects missing/zero baseline. The optional session uses New York time and gates eligibility only; its volume baseline can include bars outside that session. Enable extended-hours feed data if needed; absent bars cannot be manufactured.

## Validation

Local source and deterministic scenario checks do not prove Pine compilation, rendered behavior, screener compatibility, or notification delivery. Verify these in TradingView:

- Replay the provided sparse 10-second case: the first large candle should mark if its open-to-high move reaches 2%; later flat candles must not qualify from old low prices.
- Check a fresh opening gap, a stale previous close, previous-day rejection, open-only mode and Previous close mode without valid prior data.
- Test a live threshold touch/reversal, repeated ticks, a sustained burst, a completed settled candle, and a subsequent fresh jump. Test both directions and cooldown.
- Verify close-only mode does not mark a high/low touch that reverses before close. Test optional volume warmup, missing volume and session gates.
- Confirm the first ten numeric columns wait for close, historical touch markers stay on their source candle, and running alerts use the new v2.3 snapshot.
