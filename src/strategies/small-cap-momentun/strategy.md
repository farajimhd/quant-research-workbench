The desired strategy is essentially a **persistent abnormal-expansion momentum continuation strategy** focused on catching rare explosive small- and mid-cap stock moves while minimizing time spent in weak or stalled setups.

At a high level, the strategy continuously scans the US equity market for stocks that suddenly exhibit abnormal price and volume expansion, then attempts to participate only if the move demonstrates signs of becoming a true momentum continuation rather than a short-lived spike.

The intended behavior is:

---

# Universe and Market Focus

The strategy focuses primarily on:

* US common stocks
* Small-cap and mid-cap names
* Lower float stocks (typically below ~50M float)
* Stocks priced below roughly $50–100
* Non-ETFs
* Stocks capable of fast intraday expansion

The strategy intentionally avoids:

* ETFs
* slow-moving large caps
* illiquid names with unusable spreads
* symbols with weak volume participation

The philosophy is that the best momentum opportunities occur in stocks that can rapidly become “leading gainers” and attract aggressive trader attention.

---

# Core Detection Philosophy

The strategy is based on the belief that:

```text
True momentum expansion is abnormal.
```

A stock that becomes a real momentum runner typically shows:

* rapid percentage expansion,
* unusually high relative volume of the startung day >5,
* strong candle closes,
* repeated high breaks,
* persistence after pullbacks.

The system continuously scans the market every few seconds or every new bar and looks for:

* large recent percentage moves (e.g. 5%+ within minutes),
* strong relative volume,
* expanding participation,
* breakout continuation behavior.

When such abnormal expansion is detected, the stock becomes a “leader candidate.”

---

# Leader Watch State

After detecting abnormal expansion, the strategy does NOT blindly chase immediately.

Instead, the stock enters a:

```text
leader-watch state
```

In this phase, the algorithm continuously monitors:

* highs,
* lows,
* consolidation ranges,
* pullbacks,
* breakout attempts,
* volume persistence,
* spread quality.

The strategy attempts to distinguish between:

* a temporary spike,
* and a genuine momentum leader.

The stock remains under watch as long as:

* it continues making meaningful highs,
* volume remains elevated,
* structure remains intact,
* and the move is not “dead.”

---

# Entry Logic

The strategy enters long only when continuation behavior appears likely.

Desired entries occur when:

* price breaks recent highs,
* breakout candles close strongly,
* relative volume remains elevated,
* pullback structure is respected,
* spreads remain tradable,
* the breakout appears explosive rather than weak.

The philosophy is:

```text
Do not buy random green candles.
Buy abnormal continuation.
```

The system is designed to:

* enter quickly when momentum confirms,
* avoid delayed entries after the move is already exhausted.

The strategy is especially interested in:

* breakout reclaims,
* continuation after consolidation,
* pullback breakouts,
* repeated high-of-day breaks.

---

# Stop and Risk Philosophy

Risk is based on chart structure rather than arbitrary fixed percentages.

Stops are intended to be:

* below recent pullback lows,
* below structural support,
* dynamically adjusted to volatility.

The desired logic is:

```text
Wide enough to survive noise,
tight enough to avoid dead trades.
```

Position size should shrink automatically when:

* volatility is high,
* stops are wider,
* spreads are larger.

The strategy should not reject all volatile setups simply because the stop is wide. Instead:

* position size should adapt.

---

# Trade Lifecycle Philosophy

This is the most important part of the strategy.

The intended philosophy is:

```text
If the move is real, it should move quickly.
```

After entry:

* the stock should demonstrate rapid continuation,
* maintain momentum,
* and keep attracting volume.

If price stalls for too long without progress:

* probability of continuation decreases,
* so the trade should be exited.

However:

```text
Normal pullbacks should not be confused with failure.
```

The strategy aims to:

* survive healthy consolidations,
* but quickly abandon weak breakouts.

---

# Desired Exit Behavior

The strategy does NOT aim to predict exact tops.

Instead, it wants to:

* stay in strong moves,
* trail winners,
* protect profits after meaningful expansion,
* exit when structure truly breaks.

Important distinction:

## Weakness ≠ failure

A temporary pullback should not necessarily cause exit.

True failure should involve:

* loss of structure,
* failed reclaim,
* breakdown below pullback support,
* heavy selling pressure,
* collapse of momentum.

The strategy should:

* allow strong stocks to breathe,
* but aggressively remove capital from dead moves.

---

# Re-entry Philosophy

Re-entry is a core part of the strategy.

The assumption is:

```text
Real leaders often make multiple legs.
```

So after exit:

* the stock should remain under watch,
* and re-entry should occur if momentum reappears.

Typical re-entry scenarios:

* reclaim of prior highs,
* breakout from pullback,
* renewed explosive continuation,
* second or third momentum leg.

The strategy intentionally prefers:

```text
many attempts on true leaders
```

over:

```text
holding weak names indefinitely.
```

---

# Premarket and After-Hours Philosophy

The strategy strongly values:

* premarket,
* postmarket,
* and extended-hours momentum.

Reason:

* many explosive small-cap moves begin outside regular hours,
* halts are less problematic,
* volatility can be higher,
* early leader detection becomes possible.

However, execution quality must still be respected:

* valid bid/ask,
* live marketability,
* acceptable spreads.

The goal is NOT:

```text
avoid extended hours
```

but:

```text
avoid non-marketable or fake liquidity conditions.
```

---

# Ultimate Goal

The ultimate goal is NOT:

* high win rate,
* smooth equity curve,
* frequent tiny profits.

The goal is:

```text
Catch rare explosive momentum leaders early,
stay in them as long as possible,
re-enter if they continue,
and cut weak trades quickly.
```

The strategy expects:

* many small losses,
* many failed attempts,
* but occasional outsized winners that dominate total returns.

Conceptually, it resembles:

* momentum ignition continuation trading,
* leading-gainer intraday momentum trading,
* abnormal expansion persistence trading,
* volatility expansion breakout trading.
