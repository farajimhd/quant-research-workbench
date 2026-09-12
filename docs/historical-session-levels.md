# Whole-session historical levels, version 1

This is an independent retrospective experiment, not a replacement for V6 or
algorithm 18. It accepts one completed session and no past-level seed. Its
output is available at 20:00 America/New_York and must never be used to trade
the same session. No campaign, live strategy, or restart checkpoint consumes it.

## Source and extraction

The reader verifies the certified canonical SIP continuity and condition-rule
revision before and after aggregation. It reads only `market_sip_compact.events_YYYY`,
in eight bounded two-hour windows with two database threads. It uses the existing
historical SIP condition policy, SIP timestamps, canonical price encoding and
Float32 share sizes. Last-eligible trades define opens/closes, extrema-eligible
trades define highs/lows, and volume-eligible trades define volume. Exact trade
prices are retained in the volume profile. Seconds lacking an eligible price bar
are counted with their volume; that volume remains in the whole-session profile.
Profile totals must reconcile with price-bar volume plus unavailable-price volume.
No empty second is fabricated or filled forward.

The fixed input resolution is one second, independent of chart settings:

1. Find full-session high/low extrema with prominence at least the maximum of
   three ticks, six times the median nonzero second range, and 5% of session range.
   Include segment extremes. Segments break on gaps longer than 60 seconds.
2. Add volume-profile modes after three-bin smoothing; mode prominence must be
   at least 8% of the maximum smoothed bin volume. This proposes zones; volume
   alone does not qualify a support or resistance.
3. Cluster proposals with a bounded price span, rather than transitive chaining.
   Use the prominence-weighted median and a tick-rounded half-width of at least
   one tick or one quarter of the reaction prominence.
4. Study independent encounters. A zone rearms only after price departs by the
   reaction prominence. After a touch, evaluate up to 180 seconds, stopping at
   data gaps. A favorable close beyond the prominence is a rejection. Two
   consecutive one-second closes through the opposite boundary plus half-width
   are acceptance. The first resolution wins; other encounters are unresolved.
5. Keep zones with at least two rejections and a rejection fraction of at least
   60% among resolved encounters in either role. Record both roles, crossings,
   unresolved encounters, exact band volume, and encounter volume relative to
   the preceding 60-second rate. Later crossings do not erase earlier evidence.

These are explicit engineering defaults for a first research version, not fitted
or validated predictive thresholds. Geometry uses the completed session and thus
has lookahead by design. One-second OHLC cannot resolve within-second event order.
Volume contributes candidate proposals and review evidence, not a learned score.
More than 512 candidates fails explicitly; there is no hidden top-N truncation.
All rejected candidates are preserved with reasons.

## Run and inspect

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:\Users\g835l\miniconda3\envs\ml4t\python.exe scripts/extract_historical_session_levels.py --ticker AAPL --session 2026-08-21
```

Dependencies are in `requirements.txt`. Runtime output defaults to
`D:\TradingML\runtimes\historical-session-levels\AAPL-2026-08-21`:

- `inputs.json`: exact aggregated inputs and source audit.
- `levels.json`: version, parameters, input hash, all zones and encounters.
- `chart.html`: self-contained interactive candles, bands and volume profile.

The chart supports candle timeframes, pan/zoom, level visibility, evidence
inspection and light/dark themes. Changing candle timeframe never recalculates
the level book. Bands span the whole chart because this is a finalized historical
view, not their causal availability during that day.

Before production adoption, compare against V6, test diversified subsequent
sessions, and design a causal streaming estimator and a separate finalized-book
consumer contract. A visually persuasive historical fit does not establish
next-session trading value or campaign-wide performance.
