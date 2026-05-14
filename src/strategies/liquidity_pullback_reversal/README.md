# Liquidity Pullback Reversal

Liquidity Pullback Reversal is a low-turnover intraday long strategy built from
1-minute provider data. It looks for liquid stocks that have pulled back toward
or below VWAP, then waits for evidence that selling pressure is starting to
fade.

The strategy is intentionally different from breakout momentum. It does not buy
the hottest extended names. It prefers liquid pullbacks with a fresh reversal
bar, improving MACD pressure, controlled intraday damage, and enough expected
room to justify trading costs.

Implementation details are versioned under the version folders.
