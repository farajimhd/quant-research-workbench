# V34: balanced template/random initialization experiment

Laptop-only Aug 4, 2026 Phase 1 experiment: population 1,024, seed 2020,
50 generations, 6,144 fresh initial candidates. No global phase or validation.
V32's objective, execution, top-10 admission, reproduction, speciation and
search bounds are preserved. Source and runtime are separate from V32/V33.

Initial seeds: 3,072 causal templates across 13 families and 3,072 candidates
from the existing calibrated random sampler. See `templates.ALLOCATIONS` for
exact counts. Template thresholds are translated into the existing absolute
normalized channels; templates are not activation-calibrated. Every template
is an ordinary mutable graph. No special template evaluator is introduced.

Breakouts have 32 core rules and eight management configurations each.
Other families use deterministic mixed-radix management variations. Initial
management variants include disabled heads, profitable-position additions,
trailing activation, and certified confirmed-swing stops. Signal-driven
reductions start disabled; native bracket target reductions remain active.
Evolution retains all existing operators and the full 0..20 additions range.
Crossovers use strict opposite-side observations with explicit current and
previous validity. Equality is not classified as an opposite-side crossing.
Higher-timeframe MACD is not invented: continuous indicators use the certified
completed nonempty 1-second observation bank. Candle features use their actual
5/15/60-second horizons. Known missing features never become valid signals.

IDs add T/R ancestry contributions to the existing origin, serial, mutation,
crossover and identity suffix. Each template seed contributes T1/R0; each
random seed or later random immigrant contributes T0/R1. Crossovers sum both
parents; mutation preserves counts. Elites retain identity. Counts describe
ancestry paths, not distinct founders or the actual percentage of inherited
neurons. A family contribution vector is checkpointed with each pedigree.

The detailed Phase 1 observer retains rankings and network/policy views.
Press `h` to cycle initial-family statistics, current-family statistics, and
rankings. Initial statistics assign each candidate to exactly one family.
Current mixed descendants distribute one candidate-equivalent and its profit
by normalized ancestry contributions. These are descriptive statistics, never
selection inputs. Profit sums describe independent hypothetical backtests,
not an executable combined portfolio. `families.json` records aggregation time.

`prepare_initial` seals fresh candidate payloads and exact RNG before/after
states for reuse by short diagnostics and the actual run. This imports no
financial results. `V34_INITIAL_POOL` requires matching source, probe, starting
RNG, count and payload hash. All initial 6,144 candidates are replayed normally.

`bounded_profile` checks actual template/random candidates on 64 replay clocks,
exact V32/V34 captured and cold/warm financial parity, and 512/1024 batch parity.
Eager-versus-captured differences are retained separately; eager arithmetic
ordering is not substituted for the captured production authority. This
is not a measured full-session time. Runtime logs retain the actual full
initialization and later-generation costs. Laptop memory uses an explicit
12 GiB reserve plus 8 GiB admission; workstation processes are undisturbed.

`compare_v32` verifies matching contracts and compares generation 1 and 50 with
the preserved V32 Aug 4 local run. The runtime launcher invokes it only after
V34 completes. This is an in-sample comparison, not a generalization test.
