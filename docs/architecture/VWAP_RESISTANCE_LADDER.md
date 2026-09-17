# VWAP midpoint resistance ladder v1

Contract: `vwap-midpoint-resistance-ladder-v1`. Backtest Candidate 313:
`ca1c4a43-5644-4d6c-a545-b70dd8d77f21`.

## Decisions

- Entry at or above the VWAP–HOD midpoint, above VWAP, with matching,
  fresh completed native MACD line/signal observations on 100ms, 1s, 5s,
  10s and 30s. Strict line > signal on all five. The repeated 30s operand
  in the original request is interpreted as line > signal.
- One initial position per bullish 10s episode. Line < signal ends it;
  equality does not start or end an episode. Closing a position does not
  unlock its episode. Rejected/unfilled acquisitions do not consume it.
- Initial stop one tick below a confirmed swing low inside a V7 support
  band that existed by the swing pivot. Actual bands are retained when
  the shared projection also carries point-price geometry.
- Swing history is independent of MACD episodes. A confirmed, still-valid
  post-04:05 swing may predate the current episode. Historical replay preserves
  it across same-session gaps of up to 30 seconds only when a complete, pinned
  native 1s stream proves no eligible intervening candles and contains both
  bordering candles. Unknown gaps and session/book changes still reset the
  detector. Old ladder replay checkpoints require a fresh run.
- Count distinct physical resistance IDs once per session, across positions;
  retain their pre-break bands across role flips. R1 and R2 do not move an
  early position's stop. R3 trails below R1, R4 below R2, always two behind.
- Target is three resistance levels ahead, two after four session breaks,
  and one after six. Existing targets never move down. Targets sit one tick
  above the selected upper band so a break can advance the target before a
  fill. A gap through the working target can fill first and takes precedence.
- A position entered after six session breaks starts with a one-level target,
  permits two upward target adjustments, then fixes it. Its stop begins
  trailing at its second break, still two levels behind in the session ladder.
  `late_entry_breaks=4` is supported, but the default is six pending clarification.
- Initial purchase requests one third of eligible account cash. Portfolio
  freezes that initial notional cap. The first two subsequent resistance
  breaks each request up to the same notional cap, clipped by available cash,
  fees, integer quantity, and existing risk limits. No future cash is reserved.
  No retry/top-up of a consumed addition opportunity; recrosses cannot add again.
  Adds require a still-bullish 10s episode and fresh executable quote.
- MACD episode closure prevents acquisition; it does not liquidate a position.
  Stops, targets, configured session flattening and operator exits manage exits.

## Derived-trade policy and activation

`exclude-trades-before-0405-et-v1` excludes trades before 04:05:00 America/New_York
from derived state while preserving canonical events. A completed 1s bar ending
at 04:05:00 is excluded; the first eligible complete second ends at 04:05:01.
The policy covers bars, indicators, HOD, V7, scanner state and volume projections.
The explicit historical-campaign-v16 build retains its old semantics.

Historical cache calculation revision is `qmd-derived-v59-0405-et`; changed
trade-condition revision material invalidates dependent structural authority.
Old streaming/replay checkpoints fail closed. Start new runs instead of resuming
pre-policy state. A source rebuild/restart is needed to activate changed services;
creating the candidate does not restart services or authorize live trading.

Old V7 predecessor books may contain early-trade contributions. Streaming filters
cannot undo them. Snapshots expose both current input policy and seed policy;
mixed seeds remain labelled mixed. This strategy refuses initial entries against
unfiltered seeds. Build a new immutable cumulative V7 campaign from canonical SIP
using the new policy and certify its lineage before historical acceptance.
Do not overwrite the old campaigns or merely relabel their manifests.

The catalog recognizes the independent `filtered-0405-v1` successor campaign.
Its plan records the input policy and must cover the original historical prefix;
each selected checkpoint must certify that policy. Campaign SQL excludes early
trades before aggregating OHLCV or estimating discovery noise. Single-symbol
and universe backtests validate the seed policy during preparation, so an
unrebuilt symbol fails explicitly instead of completing with only wait decisions.
The initial successor contains SUGP; other symbols require a filtered rebuild
before using this strategy.

## Validation

Focused tests exercise the actual strategy executor, Portfolio cash admission,
runtime funding/rejection callbacks, session counters, stop/target behavior,
checkpoint restoration and the New York cutoff in winter and summer. These are
implementation checks, not a profitability claim or a completed historical replay.
