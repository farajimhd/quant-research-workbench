# V48 objective design record

Recorded user decision, 2026-10-24. This is a design record only: V48 code has
not been duplicated, implemented or launched. Existing V47 and V42 runs are
unchanged. Task history remains unchanged as previously requested.

## Approved ticker/session cost

For candidate i and training case c = (economic security, session):

    L[i,c] = sum(max(-net_position_profit[p], 0) for completed positions p in c)
    S[i,c] = NetProfit[i,c] - 0.05 * RealizedCaseDrawdown[i,c] - 0.5 * L[i,c]

Lambda is fixed at 0.5. A position is the complete entry-to-flat episode,
including additions and reductions; use its final net P&L after bid/ask fills
and all fees, not the number of losing trades or negative intermediate marks.
Keep V47's chronological realized case-equity drawdown convention (aggregate
simultaneous closes). No winning trades are trimmed or capped. Net profit already
includes losses, so the extra term deliberately charges another 50% of completed
losing-position dollars. With fully closed positions this is equivalently gross
winning net-position profits minus 1.5 times gross losing net-position dollars,
minus the drawdown term. Unresolved positions retain their actual cashflows and
existing ineligibility; never invent a closing fill to calculate the penalty.

Apply S to specialist rankings, positive case eligibility, specialist archives,
case-parent selection and the displayed best-case score. Under this approved
score-only scope, the 102 global elites and 358 global crossover slots continue
using total net profit. Preserve the 205 baseline fresh and 359 case offspring
slots, existing whole-network/mask crossover distributions, mutation, ancestry,
species, execution, causal inputs, risk protection and validation independence.
Display raw net profit, gross loss dollars and the penalty separately.

## Phase 0 discussion: proposal, not an approved launch

The proposed hierarchy is:

1. Phase 0: independent ticker/session specialist populations.
2. Phase 1: mix specialists from the same session and evolve over all its tickers.
3. Phase 2: mix session populations and evolve over all training sessions.

Phase 0 would select all its routes using the single-case S above; retaining a
raw-profit elite route there would bypass the intended loss penalty. That is a
separate selection change from the approved V48 score-only definition above and
must be specified before implementation. Carry bounded diverse positive archives
and provenance into the next stage, not just one final champion. Re-evaluate
merged children over their complete next-stage training universe; parental
financial scores cannot be added or reused as child performance.

Preserve the full causal feature cross-section and original membership rules
when isolating a case financially. Do not normalize features against just that
ticker or feed a future winning ticker/session label into a merged strategy.
Whole-network branches share the child's position and inherited financial policy,
so combining graphs does not guarantee combined parental profits.

Per-case population sizes, generation counts, archive capacities, promotion
allocation, shared evaluation layout and total compute budget remain undefined.
Do not automatically multiply the full 1,024 population and 6,144 initialization
by every ticker/session. Validation remains display-only and cannot affect case
selection, promotion, reproduction, behavior, weights or training RNG.
