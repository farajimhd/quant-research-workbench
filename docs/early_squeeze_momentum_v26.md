# Full-session momentum v26

The new candidate enables `momentum_full_session`. Existing candidates without
that parameter retain their prior purchase and position-age behavior.

After Early Squeeze, every initial entry and addition checks a fresh quote with
spread at most 250 bps, at least 25,000 session shares, $100,000 session dollar
volume, 1 trade/second over the last 10 seconds and 0.5 over 60 seconds. These
configurable starting thresholds are engineering defaults, not profitability
findings. Missing or older-than-two-second activity values block purchases.

All tranches use the first actual entry fill as one fixed price basis. Their
targets advance together through the existing session multiplier sequence.
OMS publishes this fixed basis separately from its evolving fill average.

At five minutes from the first entry fill, additions stop. Green means positive
proceeds at the executable bid after final recorded entry commissions and an
estimated exit commission (default $0.005/share, minimum $1). The position's
fee-adjusted break-even includes all purchases. Unknown fees cannot prove green.

A green position gets a stop below the closest current resistance below the bid
that also permits a stop above break-even. Its target is the lower band of the
closest resistance above the bid. If either side cannot be represented, exit.
Existing tighter stops never loosen. The age target is fixed; session multiplier
advances no longer push it away. Repairs and subsequent fills preserve it.

A red position gets until six minutes from the first fill to recover. It exits
immediately when green, otherwise at the first executable observation at or after
six minutes. Additions do not reset this deadline. Existing stop and target orders
remain active while waiting. Session flatten and protective exits retain priority.

On a cash-size rejection of an otherwise eligible entry or addition, release one
other position at a time. Only positions with strictly smaller frozen average
resistance gaps qualify. Rank green positions by oldest first, then net profit;
red positions qualify only after six minutes and rank after all green positions.
Require fresh quotes, canonical fee evidence, the same account and exit permission.
Do not sell further positions while another exit is working. Non-cash portfolio
limits do not authorize displacement. No purchase is submitted against promised
proceeds: the next observation must obtain Portfolio approval using actual cash.
An unfunded confirmed addition can retry only while its geometry, bullish MACD
episode and above-midpoint price remain valid; all purchase gates are rechecked.

Exit evidence distinguishes age recovery, grace expiration, unavailable green
bracket, age resistance stop/target and larger-gap cash reallocation. Validation
uses synthetic Strategy/Portfolio/OMS tests; no historical backtest was run.

## Evaluation performance

The momentum executor caches up to eight distinct resolved configurations using
detached value keys, so nested settings changes invalidate cached settings.
Frozen activation-gap evidence and published resistance geometry use the shared
immutable-evidence containers. Mutable episode, acceptance and purchase state
still receives independent copies. JSON checkpoint restores are sealed again
at the next publication; checkpoint values and strategy decisions are unchanged.

A synthetic held-position fixture with 200 retained resistance levels took
0.927 seconds for 500 evaluations before this change and 0.113 seconds after
warmup with immutable evidence (about 8.2x for this workload). This is an evaluator
microbenchmark, not a measured full-session speedup. Journal durability,
event ordering, eligibility checks and broker execution remain unchanged.
