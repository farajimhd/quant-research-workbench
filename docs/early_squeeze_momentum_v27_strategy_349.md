# Strategy 349: adaptive multi-timeframe momentum v27

Strategy 349 is a separate `test_candidate` successor to Strategy 348. Strategy
348 and its saved configuration remain unchanged. Implementation validation is
not profitability, holdout, or live-release acceptance.

## Session admission and purchases

- The completed prior regular-session close must be strictly below `$20`.
  Today's price is never subjected to that ceiling. This gate runs fail-closed
  in the strategy executor using the causal replay/live observation. It is not
  a Watchlist condition because the historical Watchlist interval provider
  does not own prior-session-close materialization.
- Early Squeeze and the live `$1` threshold are independent. Early Squeeze is
  retained for the session even if it occurs below `$1`; the ticker remains
  watched, but no entry or addition is allowed while the current trade is below
  `$1`. Falling below `$1` never creates an exit.
- Every purchase requires fresh executable quotes, existing liquidity gates,
  price above VWAP for an initial entry, and bullish forming MACD (`line >
  signal`) on 1s, 5s, 10s and 30s. Forming values are calculated causally from
  completed QMD bars and the current trade; completed values are not relabeled.
- At a 15% advance from the first eligible session trade, late mode latches.
  Initial entry then requires price in `[70% of prior HOD, prior HOD)`, the
  existing resistance-below-HOD crossing, and BOS of a confirmed local high.
- The BOS base must be a confirmed earlier local low inside a current support
  band, or the closest current resistance reclaimed below the broken high.
- Purchases remain one third of currently unreserved mandate cash. A position
  has at most three filled purchases. One resistance identity may fund only one
  filled addition during that open position; a new position resets usage.

## Reentry

The existing target-fill restriction remains: no reentry during the remainder
of the target's one-second candle. On complete closure, preserve the entry-base
resistance identity, close time and highest causal trade observed while the
position was open. A reentry within ten seconds, or a reentry using that same
resistance at any later time, requires an event-native crossing of the saved
high: `previous trade <= saved high < current trade`. More than ten seconds
later on a different resistance, normal entry rules apply.

## Initial and trailing protection

The structural initial stop remains the supported swing, below-VWAP support, or
5% fallback selected by Strategy 348. Strategy 349 additionally builds rolling
2-second and 5-second price ranges from completed one-second bars:

```text
noise distance = max(
  $0.10,
  1.5 * latest completed rolling 2-second range,
  1.25 * session-to-date p90 completed rolling 5-second range
)
maximum distance = max($0.10, 5% of actual entry reference)
required distance = max(structural distance, noise distance)
```

The percentile participates after six completed 5-second samples. If required
distance exceeds the maximum, entry is deferred; the strategy never submits a
knowingly too-tight substitute. Additions cannot widen protection.

Five seconds after the first fill, freeze `current trade - effective stop` and
trail that distance behind subsequent eligible-trade highs. A three-resistance
step remains independently active and unchanged; when it wins, it resets the
trail anchor to the new structural stop. Beginning at ten seconds, VWAP is a
hard stop floor. If VWAP is already at or above executable price, liquidate;
otherwise the effective stop is the highest valid candidate. Stops never move
down.

## Two-minute age policy

At two minutes from first fill, additions end. Net green/red status uses actual
position executions, final entry commissions, executable bid, and estimated
exit commission, as in Strategy 348.

- Green position and nearest resistance-below-price stop above fee-adjusted
  break-even: install that stop and the nearest valid resistance target above.
- Green position but that stop is not green, or either side of the bracket is
  unavailable: liquidate immediately.
- Red position: retain existing protection for one additional minute. If it
  becomes green, apply the same bracket test; if the stop is not green,
  liquidate. If still red at three minutes, liquidate.

Session flatten, an existing stop, and an existing target retain priority.

## Evidence and acceptance

Entry evidence records prior close, forming MACD values, supported-BOS base,
adaptive range inputs, required/capped distance and reentry-high decision.
Protection changes retain their exact source and previous effective stop.
Strategy 349 is not accepted for profitability or live use without separate
cost-aware full-session and holdout evidence.

## Historical initialization correction

Saved candidate revision 349 incorrectly also placed `market.previous_close`
in its Watchlist rule. Historical initialization rejects that unsupported
external interval source before strategy execution. Candidate revision 350
removes only the duplicate Watchlist condition; the executor's fail-closed
prior-close gate and every trading rule above are unchanged. Revision 349 is
retained as failed evidence and should not be run.
