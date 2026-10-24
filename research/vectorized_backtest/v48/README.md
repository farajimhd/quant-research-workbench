# V48: isolated ticker/session specialists, then unified session evolution

Authorized August 13, 2026 laptop run: phase0 has 30 generations; phase1 has 20.
Steady total population is 1,024 in each phase, seed 2020. Phase0 starts from the
same pinned 6,144 initial V42 programs/RNG as V47. V47 stops at its sealed boundary;
V42 workstation is unchanged. No prior-version financial evaluations are reused.

## Cost and selection

    Score = NetProfit - 0.05 * RealizedDrawdown - 0.5 * GrossLosingPositionDollars

Loss dollars sum max(-final net episode P&L,0), including bid/ask fills and fees.
A position is an entry-to-flat episode, including adds and reductions; there is
no trade-count penalty and no trimming of winners. Simultaneous closes aggregate
before computing realized equity drawdown. Preserve unresolved cashflows and
existing ineligibility; never manufacture terminal fills.

Phase0 uses this cost for the owned economic-security/session case across all
selection routes. Phase1 uses the same cost over all ticker positions combined,
with chronological session realized drawdown. It has ONE score-ranked parent pool,
not V47's raw-profit versus specialist split. Raw profit and best-case score remain
separate display diagnostics. No multi-session phase2 is launched in this test.

## Phase0 isolation and dynamic allocation

Each initial program is deterministically assigned one immutable case owner before
any financial replay. Ownership is not a neural input. Each candidate can open
positions only in its owned case; features/normalization still use the full causal
cross-section. All parent selection, mask/whole crossover, mutation, archives,
cache identities, payload/checkpoint restore, receipt ownership and position/P&L
ledgers enforce that owner. No reassignment from another ticker and no automatic
multi-case membership. Expansion of a case uses fresh programs or its own parents.

Reserve one of the 1,024 slots per certified case for exploration. Allocate the
remaining slots equally across cases with positive-score parents, rotating integer
remainders. If none is positive, distribute across all cases. More than 1,024 cases
fails closed; this run uses the pinned August13 inventory. Positive elites are
chosen in case-balanced rank rounds subject to these quotas. No raw-profit bypass.

Keep 102 elite slots, 205 baseline fresh and 717 crossover slots. Missing eligible
elites/mates, oversized graphs and rejected children become fresh fallback slots,
never extra population. Apply whole-network composition to 25% of all crossover
slots: 50% OR, 25% AND, 25% XOR. All phase0 parents must have the same owner.
In phase1 all parents come from the unified session-score pool; cross-origin
mating is permitted without case quotas. Preserve existing species compatibility,
three-generation ancestry exclusions, 1/2/3 sibling schedule, mask modes and separate
52.5% mutation stage. Whole composition preserves branch graphs at creation;
subsequent ordinary mutation may edit descendants. Keep the 512-node strategy
budget and original risk/memory/GPU protections.

## All-generation specialist promotion

Maintain a bounded case-balanced positive specialist archive throughout all 30
phase0 generations. A better program from any earlier generation remains eligible
for phase1 even if absent from generation30's live population. Retain multiple
canonical programs per case within the shared 1,024 archive capacity.

After phase0's final seal, independently verify all historical source seals and
receipts/scores/birth/archive hashes. Seed phase1 in case-balanced positive-rank
rounds from that all-generation archive; deduplicate after removing ownership.
Record each specialist's case, source generation, original seal, score and original/
promoted program identities. Record shortages explicitly and fill only those slots
with fresh exploration; never pass losing candidates off as positive specialists.

Phase1 restores pedigree, serial continuity and RNG, removes the financial ownership
restriction, clears financial caches/archives, and fully replays every promoted
program across all August13 tickers. Phase0 scores only select the initial pool;
they are never imported as phase1 fitness. Promotion is sealed and restart-safe.
Validation is never opened and cannot affect behavior, weights or training RNG.

## Operation and validation

Use the immutable V42 dependency snapshot plus committed V48 source, launched via
`python -B -m research.vectorized_backtest.v48.launch --job <full-job.json>
--output <new-runtime-root> --initial-pool <original-6144-pool> --authorize-run`.
Task Scheduler owns the job outside the app process tree while logged in.
STOP under the current phase's local/2026-08-13 directory requests a sealed stop.
Root STOP prevents the next phase from starting. No automatic retries/reboot resume.

Outputs have phase-0/ and phase-1/ roots, per-phase sealed checkpoints, root
phase1-promotion.json, active.json and final complete.json. The observer follows
phase changes and displays raw net profit, selection score, case score and E.
Tests cover execution and whole-network CPU/GPU parity, the actual compiled owned
financial replay, owner-scoped cache/crossover/archives, loss accounting, dynamic
budgets, deterministic reproduction restore, historical promotion/tamper rejection,
and a short real-controller phase0/resume/promotion/phase1 integration path.
