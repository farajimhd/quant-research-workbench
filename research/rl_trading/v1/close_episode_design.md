# Completed-close episode labels and dynamic teacher

This is a separately versioned experimental path. It does not reinterpret
existing Phase 1 V4, Phase 2 V6, or Phase 3 V3 certificates.

## Phase 1 V5

- MACD changes on completed one-second indicators delimit an episode.
- Within a two-second pre-cross window, the lowest completed one-second close
  is the long entry reference (highest for short). This can precede the MACD
  crossover; it is a hindsight label, not a causal crossover signal.
- The highest later completed one-second close within a long episode is its
  target (lowest for short). Ties use the earliest close. The entry and target
  must be at least two completed seconds apart.
- Accepted episodes are numbered consecutively within a listing and session.
  Phase 2 carries `episode_key` as `TICKER_<id>` and `episode_uid` as the
  session-start microsecond, listing ID, and episode ID. Every decision second
  linked to one episode keeps that identity.
- `*_setup` identifies the pre-entry window; `*_entry` its selected close;
  `*_candidate` later eligible decision seconds. When fewer than two full
  seconds remain to target, the stage is `too_late_to_open`. Setup and late
  rows cannot open new positions.

## Phase 2 V7

The full holding/closing grid and sparse opening table retain V6's physical
layout. Opening values use a completed one-second close and a per-leg fixed
commission proxy of $0.005/share by default. Ranking subtracts both buy and
sell per-share proxies and divides by price plus the buy proxy. It does not
pretend to know the order size, so the $1 order minimum and 1% trade-value cap
are evaluated in Phase 3 after sizing. A minimum two-second remaining target
duration is enforced even when the discount-adjusted score is positive.

## Phase 3 V7 allocation diagnostic

At each second, eligible long rows with after-proxy score at least 1% and at
least three seconds from entry decision to target compete
for available cash. A candidate is one episode, never one independent trade per
label second. In a bounded 15-second lookahead, each future episode contributes
its first eligible score once. Current and future scores are normalized together:
future episodes' share of cash is reserved, while the remaining cash is
allocated among current episodes in proportion to their scores. The quantity-aware Fixed fee is
rechecked after sizing; candidates below 1% after actual modeled order fees
are rejected. Open positions leave at the first closeable completed second at
or after their episode target clock, or the segment terminal clock; terminal
liquidation fails closed if no close is available. Sale proceeds become
available for later seconds.
There is no fixed open-lot limit or arbitrary participation cap. This is a
desired allocation diagnostic, not executable share sizing: score-normalized
budgets can still imply more shares than the market can fill. The output records cash, marked
equity, drawdown, order counts, position ledger, and cost assumptions.

The 15-second normalized allocation is an **approximate heuristic**, not a
globally optimal portfolio. The sequential account transition is required by
cash reuse; per-second candidate filtering, scores, fee inversion, and sizing
are vectorized; future episode scores use a cumulative sum rather than a
per-second lookahead loop. A full-day build checkpoints every 60 seconds and resumes at
the last committed second. The full model campaign requires a separately
certified V5/V7 dataset and diagnostic replay before training.
The streaming replay reads sparse eligible opening rows for allocation and a
narrow complete-grid price projection for marking held positions. It verifies
the certified holding grid at each second without joining sparse opening
fields onto every market row.

The completion report splits marked-equity profit into America/New_York
premarket [04:00, 09:30), regular [09:30, 16:00), and after-hours
[16:00, 19:58]. The final interval ends at the configured 19:58 liquidation
clock. Each period starts from the prior period's ending equity, so a holding
that crosses a boundary contributes its change in marked value to both periods
and the three profits sum to full-session net profit. Entry fees are assigned
to the entry period; exit fees and realized closed-position P&L to the exit
period. Closed-position P&L should not be summed with marked-equity profit.

The completed-close price is a historical oracle reference, **not** a verified
post-decision fill. Spread, routing, slippage, partial fills, and a live
execution delay remain unobserved. Do not use hindsight stages or scores as
student observations, and do not promote this teacher on simulated profit
alone. Compare the one-second close candidate policy against next-second-fill
replay, fees, holding times, and sealed forward sessions before training or
deployment.

Full-session Phase 3 V6 keeps the same dynamic cash algorithm but restricts its
candidate and market snapshots to the nonempty prior-V7 population certified
by the corresponding immutable V3 teacher. This is a feature-availability
contract, not a max-position limit. The old teacher's Phase 2 listing identity
must exactly match the new Phase 2 V7 day. Bounded canaries may omit this proof;
they cannot be promoted to training or test supervision.

## Dynamic supervision export

`build_dynamic_supervision.py` converts each certified full-session Phase 3
position ledger to sparse, quantity-bearing buy and sell targets. Buy targets
carry the fraction of cash available after same-second sales and before buys;
the generated order labels reconcile to the teacher's per-second account and
terminal net profit. The export references the certified Phase 2 market tensor
and Phase 3 trajectory by hash rather than copying or recomputing them. The
fixed-lot V4 model shard format is not reused: its action tokens and top-100
slots cannot represent the uncapped fractional teacher. A new model input
format and causal feature binding are required before model training.

The pinned chronological split is July 30 through August 21 for training,
August 24–25 for development, and August 26 as a sealed test session. Generating
test supervision is allowed; its profits and order details must stay out of
model and rule selection until the model is frozen.
