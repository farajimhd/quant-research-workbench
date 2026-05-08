# ORB 5-Minute Momentum Strategy

This is the local Phase 1 translation of the latest QuantConnect opening-range breakout strategy.

## Core Idea

The strategy builds a 5-minute opening box from regular-market minute bars, ranks the strongest opening setups, then watches the top candidates for a live continuation signal.

It is long-only.

## Setup Scan

At 09:35, each ticker is scored from its opening box:

- opening range high, low, mid, close, volume
- prior 14-day average volume
- prior 14-day ATR proxy
- gap from previous close
- opening relative volume
- close location inside the box
- body-to-range quality

Candidates must pass liquidity, ATR, relative volume, gap, range quality, shape, and setup-score filters. The highest scoring names form the watchlist for the rest of the day.

## Live Entry

Every minute after the opening box:

- candidates are reranked by live score
- price must still be in a valid breakout zone
- 5-minute MACD must be open and positive
- 5-minute TEMA 9 must be above TEMA 20 plus an ATR-based buffer
- a stop-buy order is placed at the box high plus a small buffer

The strategy can hold multiple positions at once up to `max_active_positions`.

## Exit Logic

Positions exit when:

- price breaks the protective box stop
- 5-minute TEMA closes, meaning TEMA 20 plus buffer rises above TEMA 9
- end-of-day liquidation is required
- a much stronger candidate replaces an older weak position after the minimum hold period

## Position Sizing

Sizing uses both risk and cash constraints:

- risk percentage scales with live score quality
- capital per trade is capped
- cash reserve is preserved
- quantity shrinks when the box stop is wider

## Notes

This implementation uses 1-minute bars for Phase 1 fill approximation. Phase 2 should reuse the same strategy rules while replacing the fill model with quote/trade based matching.
