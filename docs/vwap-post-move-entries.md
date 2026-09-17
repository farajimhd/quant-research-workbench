# Separate entries after the first upward move

Candidate 316 (`3823f64d-26d2-44ad-a35d-a0af731d48ff`) uses the backtest-only
`vwap-grouped-pullback-breakout-v3` profile cloned from Candidate 314.
Candidate 315 preserves the intermediate midpoint-gated pullback trial.
New settings default off for older candidates. The initial five-MACD,
VWAP-to-HOD midpoint entry remains. After six session grouped-zone breaks:

| Entry | Trigger | MACDs above signal | Initial stop |
| --- | --- | --- | --- |
| Pullback | A newly confirmed swing and recovered retest of a previously broken resistance/transition band | 1s | Below the swing price by the configured tick offset |
| Breakout | Price crosses the highest known resistance zone below current HOD, plus the configured offset | 100ms, 1s, 5s, 10s | Below the breakout zone's lower band by the configured tick offset |

The breakout offset defaults to one tick beyond the first tradable price at or
above the upper band. Stop offsets remain one tick, with downward rounding.
A breakout requires an observed crossing, not merely a price already above its
trigger. Pullback entries require price above VWAP; the initial and breakout
entries retain the VWAP–HOD midpoint requirement. Existing executable-quote,
filtered-V7 provenance, cash, and position guards still apply. No overlapping
new positions in the same assignment.

Each distinct pullback pivot permits one entry, including when the current 10s
episode was already used. Its entry window is the first one-second interval
after both swing confirmation and completed-candle recovery are available.
Older pivots may provide structural context but cannot authorize a delayed
pullback entry. If the order receives no fill, its setup can be retried only
within that same window; an earlier episode's consumed status is preserved.
Breakout entries retain the original unused-bullish-10s-episode requirement.
When both qualify, the fresh pullback takes priority. A pullback acquisition
uses the 1s MACD, so a bearish 10s MACD does not immediately cancel it.

For a breakout target, compute the arithmetic mean of positive edge-to-edge
gaps between previously broken zones. Add this distance to the breakout band's
upper edge. Find the neighboring overhead resistance zones on either side of
that threshold and target the midpoint of the free gap between their bands.
The breakout band itself can be the lower neighbor when no intervening zone
exists. Missing or overlapping bounding bands fail closed. Round the midpoint
to the nearest executable tick, then require it to remain above the ask.
For point levels at $4.00, $4.03, $4.08, $4.15 and a $0.10 average gap, the
raw target is $4.115, or $4.12 at a one-cent tick.

Pullback targets retain the existing late-entry next-zone target. Both paths
retain unreserved thirds, at most two resistance additions, two-zones-behind
stop progression, and the late-entry limit of two break-driven target moves.
Breakout target advances use the same midpoint rule from the highest broken
zone. Geometry repairs from regrouping do not consume that move allowance.

## Target corrections shared by ladder candidates

- Count unbroken resistance bands by their upper boundary when price/ask is
  inside the band; do not skip them because the lower edge is below the ask.
- Reconcile targets after grouped membership or bounds change, even without a
  new break. This creates no extra break count or add opportunity.
- Preserve pending replacement prices and move accounting through rejection
  and regrouping. Never lower an existing target.
- Add intents and journal metadata use the target updated during the same
  evaluation, instead of the earlier target snapshot.

The requested SUGP times are diagnostic cases, not forced entries or acceptance
labels. Validate native completed MACD samples, the actual setup and decision
times, broker-effective protection, fills, and final reconciliation. This
candidate is not live acceptance or a profitability claim.
