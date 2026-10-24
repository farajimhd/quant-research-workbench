# V47: whole-network specialist crossover with V46 bid/ask fills

August 13, 2026: population 1024, 50 generations, seed 2020, the exact original
6,144 V42 initial programs and post-initialization RNG. No scores, offspring or
financial evaluations from another version are imported. V46 is stopped cleanly;
V42 workstation is untouched.

## Reproduction

Keep 102 positive global-profit elites, 205 baseline fresh, 358 global crossover
slots and 359 positive ticker/session crossover slots. Assign 25% of case slots
(89 or 90 with deterministic integer allocation) to whole-network composition;
keep the remainder on the original mask crossover. Whole slots are 50% same-case
and 50% cross-case pairs; modes are 50% union/OR, 25% intersection/AND, 25% XOR.
Integer remainders rotate deterministically. Existing species compatibility and
three-generation ancestry exclusions still apply. No eligible mate, duplicate or
oversized child becomes an explicit fresh fallback in the same slot. Requested,
accepted and fallback counts are recorded; realized ratios can differ.

Copy all five complete parental graphs, including separate delayed-state nodes,
and combine their Boolean decisions after each parental threshold and age gate.
Both branches observe the child's shared position and fill feedback. Sizing uses
the sole firing branch, or the larger request when both fire, never their sum.
The child inherits the base parent's shared financial policy (stop, target,
cooldown, etc.); conflicting financial policies are not independently executed.
Original financial action precedence and protections remain unchanged. Nested
composition is allowed only within the unchanged 512-node total strategy budget,
512-node action budget and 4-GiB neural workspace admission.

Composition itself preserves parental numerical graphs. The existing separate
52.5% mutation stage remains active afterward, so descendants may edit inherited
graphs. Original mask crossover remains available for composite descendants.
Version-owned genome, preparation, CPU oracle and Triton network execution bind
these bridges explicitly; V42 and V46 modules/deployments are unchanged.

## Execution contract

Use the same certified prepared market data. At each existing execution second,
entry/add buys use the timestamped ask; reductions/exits use the bid. Quotes must
be finite, positive, uncrossed (locked allowed), as-of that second, and at most
one second old, matching the existing neural quote-feature validity rule.
No per-second database work or quote joins. No stale/missing quote falls back
to close. Existing observed-price gating remains for normal fills. Terminal
liquidation also requires a valid quote; unresolved positions make that candidate
ineligible and are counted in unliquidated_positions. Their realized open-episode
cashflows are retained separately and reconciled with completed episodes; no
completed trade or exit price is fabricated. Static quote coverage is
published separately under quote-coverage/.

Neural market inputs and trigger clocks remain unchanged. Position cost basis,
entry targets, prospective risk, paid fees and action-fill feedback reflect ask
purchases; realized sales and turnover reflect bids. Equity marks and stop/target
triggers retain the original last-price convention. Fees and original risk checks
remain mandatory. There is no L2 depth, market-impact, latency or partial-fill
simulation; this is an idealized full-fill top-of-book model, not execution proof.

Quote assembly happens once during bounded CPU session loading. Float64 bid/ask
and boolean validity tensors share across candidates and follow the existing
pinned upload path. The fused Triton transition consumes them inside the existing
CUDA graph. V47_MASK_QUOTES selects equivalent all-lane or potential-order-lane
quote loads. The measured faster form is unconditional resident loads (V47_MASK_QUOTES=0); financial/episode outputs
must match exactly. No lower precision or omitted risk checks are permitted.
V47 evaluator, data adapter, worker and graph are version-owned; immutable V42
and existing V45 deployments are not patched.


## Running and evidence

Deploy with the pinned original V42 dependency snapshot plus this version only.
Use `python -B -m research.vectorized_backtest.v47.launch --job <original-full-job>
--output <new-runtime-folder> --initial-pool <original-6144-pool> --authorize-run`.
Task Scheduler owns the optimizer outside the app process tree while logged in.
STOP under output/local/2026-08-13 requests a sealed generation boundary stop.
No reboot recovery or automatic retries. The progress table retains both global
profit and best-case score, plus financial eligibility E.

Focused tests cover Boolean truth tables, missing provenance, independent
recurrence, nested composition, parental age gates, max sizing, CPU/GPU parity,
original execution accounting and selection. A bounded real-data GPU profile
checks composed graphs through the complete replay wrapper. V46's historical
0.26% quote overhead measurement is not a V47 composition performance claim.
