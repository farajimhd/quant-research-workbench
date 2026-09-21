# Early Squeeze momentum v24 — implementation in progress

This is a separate, incomplete candidate. It is **not registered with the
executor, saved as a test candidate, published, or activated**. The existing
published strategy is unchanged. No replay or profitability acceptance has
been performed for v24.

## Confirmed requirements

- Early Squeeze activates observation for the full New York session; purchases
  are restricted to 04:00–20:00.
- Use fresh causal V7 geometry and event-time confirmed local pivots. Never
  consume retrospective chart annotations.
- Initial-entry BOS breaks the last confirmed swing high. Its gate can stay
  open awaiting price above VWAP. Completed bullish 1s MACD, forming bullish
  1s MACD, and completed bullish 100ms MACD must also pass at purchase time.
- An addition requires a completed green 1s candle opening at/below and closing
  above a resistance midpoint, followed by the first actual trade in the
  immediately following second above that midpoint. Identity and boundaries
  must remain unchanged. Empty following seconds expire confirmation.
- At most one filled addition per accepted resistance per completed 1s MACD
  episode. A rejected order must not consume a filled-add entitlement.
- Initial stop: most recent confirmed low formed within 10 seconds and inside
  a causal V7 support band; otherwise a qualifying support below VWAP; otherwise
  1% below entry. The fallback support distance reference remains unresolved.
- Stop advances one resistance after every three broken resistances, below
  that level's lower boundary, and never moves down.
- Freeze the average resistance gap at Early Squeeze. Each tranche uses its
  actual fill price plus five times that frozen gap. Two rapid triples can
  upgrade the multiplier to eight and then ten; slow crossings do not upgrade.
- CHOCH is not an exit rule. Neither MACD nor VWAP becoming bearish/broken has
  been approved as a position exit in this description.
- Early entries are exempt from the resistance-immediately-below-HOD gate.
  The boundary between early entries and later entries is not yet defined.
- Presentation must use the reason recorded for each actual sell, rather than
  substituting the final episode reason or inferring a reason from chart prices.

## Decisions still required before runnable integration

1. What event ends the early-entry exemption and activates the below-HOD gate?
   Also settle this gate's confirmation and reset behavior.
2. Is each purchase one-third of currently available mandate cash, or a fixed
   initial allowance divided by three, capped by available cash? Is three the
   maximum total count of initial entry plus additions?
3. At 20:00, flatten or stop new purchases only?
4. Is the fallback support within 1% of price below VWAP or below entry?

The draft mechanics use these explicitly disclosed interpretations, which
are not a substitute for full strategy approval: one-second maximum V7 age;
the existing structural detector's event-time pivot confirmation; average
consecutive resistance midpoint gaps above activation price through four
times activation price, excluding activation-to-first-resistance distance;
disjoint rapid triples of distinct accepted levels. Fewer than two eligible
resistances yields an unavailable average, not an invented target.

## Implemented and reviewed so far

`src/trading_runtime/early_squeeze_momentum.py` contains isolated mechanics:
freshness, frozen gap calculation, supported-low selection, initial-stop
selection, candle/next-second resistance acceptance, confirmed-high BOS,
distinct fast triples, resistance-step stops, and fill-relative target math.
It has no evaluator and cannot submit an order.

`chartPresentation.tsx` now includes the execution's own recorded reason on
filled-exit labels. Unknown reasons stay visibly unavailable. Separate labels
exist for supported-low, below-VWAP-support, 1% entry, resistance-step stop,
and 5x/8x/10x frozen-gap targets. These new reason codes are presentation-ready;
v24 broker orders do not yet produce them.

The existing OMS replaces every position target with a common price and the
existing strategy callback may liquidate the remaining position after a target
fill. Runnable v24 still needs opt-in per-tranche fill reconciliation, target
amendment, remainder handling, fill-owned additions, candidate construction,
causal runtime wiring, and end-to-end validation. Do not route it through v23:
v23 also has a chop exit and different stop/target rules.

## Validation evidence (2026-09-21)

- 16 focused mechanics tests and 14 existing v22/v23 regression tests passed.
- Production lifecycle projection/ChartPanel browser test passed, including
  different target and stop reasons in one lifecycle and missing-reason cases.
- Managed frontend TypeScript/Vite build passed.
- Managed journal UI review captured 1/1 scenarios with zero objective issues.
- Screenshots and manifests are under
  `D:/TradingML/runtimes/quant-research-workbench/ui-reviews/momentum-exits-20260921`
  and `momentum-journal-20260921`.

These checks validate helpers and presentation, not an executable strategy.
