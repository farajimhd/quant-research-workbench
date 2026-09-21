# Early Squeeze momentum v24

Separate research candidate: `early-squeeze-momentum-v24`. The published
strategy is unchanged. This is implementation validation, not profitability
or live-release acceptance.

## Executable strategy contract

- Early Squeeze activates the strategy for the New York session. Buy only
  during 04:00 <= time < 20:00; cancel acquisitions and request full liquidation
  at 20:00. Replay emits the boundary even without an exact-time trade. A sell
  still needs actual executable market data; the engine never invents a fill.
- Structural decisions require causal V7 identity, matching seed/input policy,
  no future inputs, and a snapshot at most one second old. Swing pivots come
  only from the shared detector after their confirmation time.
- Initial-entry BOS crosses the latest confirmed swing high. The BOS gate
  stays open while awaiting VWAP and the other entry filters. An arbitrary
  accepted V7 resistance is not a BOS.
- At every purchase, price must exceed completed 100ms VWAP; completed 1s,
  forming 1s, and completed 100ms MACD must all have line > signal. Completed
  MACD/VWAP evidence retains its original clock. Quotes must also be fresh.
- Early mode ends permanently for the session when the session high reaches
  120% of the first eligible trade at/after 04:00. Thereafter, purchases also
  require a trade crossing the nearest resistance midpoint strictly below the
  prior causal HOD. That gate clears when its geometry changes/disappears or
  price returns to/below its midpoint. Early entries are exempt.
- Additions require a completed green 1s candle with open <= midpoint < close,
  followed by the first actual trade of the immediately following second above
  the midpoint, with identical level ID/lower/upper boundaries. Empty seconds
  expire confirmation; a later green reclaim can create a new confirmation.
- Each purchase requests one third of currently unreserved available mandate
  cash, shared across tickers. Existing Portfolio limits, fees and executable
  share rounding still apply. Maximum three filled purchases per position;
  pending purchases reserve a slot, rejected/unfilled terminal requests release
  it. One accepted resistance funds at most one filled addition per completed
  1s bullish MACD episode. Partial fills count once.
- Initial stop: one tick below the latest confirmed swing low formed within
  ten seconds and inside a current V7 support band. Otherwise use one tick
  below the first support entirely below VWAP if its lower band is within 1%
  below entry. Otherwise use 5% below entry, rounded down to a tick and rebased
  to actual cumulative entry fill cost.
- Every three distinct accepted resistances after entry earn one upward stop
  step, to one tick below the next resistance lower band above the current
  stop anchor. No between-band trailing or downward steps.
- Freeze average consecutive overhead resistance midpoint gaps at Early
  Squeeze, through 4x activation price (+300%). Exclude activation-to-first
  distance. Fewer than two eligible levels means no available target and no
  purchase; never use a later snapshot to invent the frozen average.
- Each purchase has its own actual average fill price + 5x frozen gap target,
  rounded to the tick. Three distinct accepted resistances in strictly less
  than three seconds upgrade all open targets to 8x; three new such resistances
  upgrade to 10x, then no further upgrades. Slow crossings do not upgrade.
  Triples do not overlap. Later additions inherit the current multiplier.
- The first target fill, including a partial fill, immediately commits the
  entire remaining position to urgent liquidation and cancels pending buys.
  Completion uses available executable liquidity, retaining the triggering
  target's recorded exit reason. Stops and the session boundary also liquidate
  the remainder. CHOCH, chop, bearish MACD and
  loss of VWAP are not strategy exits.
- After a target fill, re-entry is blocked through the remainder of that
  1-second candle. The next second restores eligibility, subject to every
  normal entry gate. The restriction persists across position closure.

## Execution and presentation

The normal StrategyEngine, Portfolio, OMS and replay adapters execute v24.
Actual fill reconciliation and target amendments retain each tranche's own
cost basis, including partial fills and repaired protection. Purchase slots
and resistance entitlements are owned by fill callbacks.

Every sell carries its recorded cause: supported swing low, below-VWAP support,
5% entry stop, three-resistance stop step, 5x/8x/10x target, session flatten,
or an explicit operational/manual cause. The chart uses that execution's
reason, not the final position reason. Missing reasons remain visibly unknown.

`src/backend/early_squeeze_momentum_candidate.py` builds/saves a separate test
candidate only after the running backend advertises its executor. It does not
publish or enable live trading.

## Validation

The 5% fallback is an explicit candidate parameter. Older saved candidates
without that parameter retain their original 1% fallback. Protection repair
validates percentage stops against actual cumulative fill cost, preserving the
original signal price separately; repriced partial fills cannot trigger a
false stop/reference error.

2026-09-21: 150 focused tests and two subtests passed. A broader runtime/replay
run passed 274 tests and ten subtests, with the three baseline failures below.

Focused tests cover causal pivots and snapshot age, BOS/VWAP ordering, MACD
rechecks, immediate-second addition expiry and moved geometry, the 20% latch,
three-purchase accounting, shared cash reservations, stop steps, frozen targets,
partial fills, tranche-specific amendments, repaired exit reasons, target
remainder handling, candidate compilation, and the replay cutoff timer.
A synthetic real Strategy/Portfolio/OMS/simulated-broker round trip verifies
entry, fill-owned accounting, broker-effective protection and the recorded stop
execution reason.

The broader replay suite has three baseline failures reproduced without v24
changes: the old debug round-trip fixture lacks current structural evidence,
a saved-review error text expectation is stale, and a V6 fixture violates the
existing V7-only requirement. These are not v24 validation passes.

Earlier production lifecycle/ChartPanel browser checks, managed frontend build,
and journal visual review passed for the exit presentation. Artifacts are under
`D:/TradingML/runtimes/quant-research-workbench/ui-reviews/momentum-exits-20260921`
and `momentum-journal-20260921`. No profitability acceptance is claimed.
