# V62 joint entry-stop construction

`authority.feasible_policy` owns joint policy projection; `authority.construct`
applies it to every routed leaf. Sampling, mutation, mask/whole/module crossover,
templates, calibration, and the final population builder use this authority.
Existing members supplied as `initial` to a population builder are evidence,
not new proposals, and are not silently converted.

New candidates persist `policy.entry_stop_rule = bounded-swing-v1`. The rule is
not a randomly selectable gene. Its prices use only current quotes and causal
history; neither position state nor fill feedback enters inference.

- The fixed stop is `ask * (1 - stop_fraction)`.
- Construction projects `buy_limit_offset >= -stop_fraction + 0.000001`, for
  both fixed and swing policies, so the fixed fallback also fits the limit.
- A finite positive confirmed swing is the preferred swing stop. If none
  exists, the candidate's fixed stop is the explicit fallback. This does not
  fabricate a swing or relax the causal confirmation requirement.
- The swing stop is capped at
  `min(ask, ask * (1 + buy_limit_offset)) * (1 - 0.000001)`.
  A valid lower swing is preserved. This is numerical separation, not a risk cap.
- Submitted orders retain their initial stop. Existing cancellation, liquidity,
  limit-price, partial-fill, cooldown, and accounting behavior is preserved.
  Invalid/missing quotes still cannot produce fills.

The tensor reference is `authority.entry_stop`; its fused CUDA implementation is
`entry_boundary_cuda.entry_stop`. Routed arbitration, calibration, the CPU order
oracle, and fused order execution use the same rule. Packed slot 8 distinguishes
legacy swing windows (1..60) from bounded swing windows (101..160), retaining the
11-field packet ABI and adding no graph nodes or feature channels.

Saved policies without `entry_stop_rule` remain `strict-v1`; restore never repairs
them and their serialized payload remains unchanged. Explicit construction makes
a new genome identity. Old financial receipts cannot be attributed to converted
candidates. The stopped campaign and its saved candidates are not migrated by
this source update. Score calculation is unchanged.

## Root cause and verification

Generation-24 leader 27011 used a 3/30 swing. The old authority checked supported
window sizes, but left dynamic feasibility to execution. Initial calibration
required only one feasible probe point; reproduction did not preserve alignment
between entry signals and swing availability. Causal history requires a fully
observed 34-clock window to confirm this pivot and only exposes it after
confirmation. Missing pivots and previously confirmed pivots above today's ask
therefore caused stop abstentions despite active entry networks.

The bounded diagnostic trace is stored outside source under
`D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v62/joint-stop-audit-20261101`
on the workstation. It re-evaluates only saved candidate 27011, reconciles signal
counts to sealed receipts, and records current ask, buy limit, swing, and exact
failure category per signal. It does not run evolution or alter receipts.

All 36 sessions matched the sealed leader's signal counts exactly:

| Split | Signals | Missing swing | Swing at/above ask | Valid stop |
| --- | ---: | ---: | ---: | ---: |
| Training | 271 | 259 | 10 | 2 |
| Validation | 127 | 82 | 45 | 0 |

There were no additional below-ask/above-limit cases for this leader. One of the
two valid training signals arrived while an order was pending; the other became
the August 13 position. Those are distinct from swing feasibility failures.

An isolated full execution check of July 31 and August 13 preserved the legacy
candidate's saved financial/admission results exactly. Explicitly constructed
and routed copies had zero stop abstentions and matching results. The July 31
copy made two trades totaling -105.9705; the August 13 copy made two totaling
1628.6376. These are bounded correctness results, not profitability acceptance.

Focused tests cover all construction routes, full population replacement,
legacy restore, missing/nonpositive/above-ask/above-limit swings, preserved valid
swings, and CPU/CUDA order parity. This fixes entry feasibility; it does not
establish improved profitability or out-of-sample quality.
