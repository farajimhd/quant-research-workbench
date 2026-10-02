# Search the strategy representation

`run_optimization` now defaults to `--search-mode categorical`. The old numeric
optimizer is available only through explicit `--search-mode numeric`. The
strategy is a typed genotype, not a fixed policy with fourteen tunable numbers.

## Candidate contract

A population is `[B,P]`, with P derived from the registered contracts. Its
coordinates contain numeric values followed by discrete operation, input,
backward-reference and output class IDs. The manifest supplies the meaning,
allowed IDs, defaults and bounds of every coordinate. Categorical IDs are
sampled from their allowed sets; crossover preserves IDs and mutation selects
another legal ID. Numeric interpolation is never applied to class coordinates.

Each candidate decodes into:

- Entry and addition instruction arrays `[L,4]`: operation ID, operand a,
  operand b, parameter reference.
- Capital, initial stop and initial target arrays using the same scalar ABI.
- Resistance and protection ATen instruction arrays `[L,2]` plus operand
  trees. Every operation has an ID in the registered ATen vocabulary; inputs
  have individual names, dtypes, shapes and their own node IDs.
- Numeric parameter arrays, including protection thresholds, and explicit
  tensor-literal overrides for eligible policy thresholds.

Scalar references use nonnegative input IDs and negative earlier-node IDs.
ATen references use the ATen program's nonnegative node namespace. The two
namespaces are explicit in the catalog. There is no "Strategy 1" opcode.
The released research program supplies the initial candidate and instruction
capacity, while its operation/reference choices participate in search.

## Validity and fixed contracts

Scalar decoding validates arity, units, types, output units, bounds and acyclic
references. ATen alternatives have compatible operation signatures; references
retain inferred units, dtype and shape. CPU schema witnesses check the output
register layout before GPU compilation, including after rebinding to another
session's geometry dimensions. Expressions feeding those output registers are
searchable reference classes; register ownership and layout remain fixed.
Missing numeric evidence keeps the
scalar evaluator's existing unknown/fail-closed semantics.

Individually legal edits can form an invalid combined program. Deterministic
repair restores invalid edits and increments `rejected_class_edits`; unknown
IDs fail immediately. Repair and RNG state are reproducible. It is not a
promise that every legal typed program is profitable or meaningful.

The manifest enumerates fixed fields and their reasons. Shapes, axes, register
ownership/layout, indexing addresses, identity sentinels, price encoding and
source alignment remain execution contracts. Policy values are not hidden in
that category: for example, completed-30s-low **age** is searchable independently
of the fixed 30s candle-alignment divisor. It is currently represented as a
typed tensor-literal override and therefore belongs to the compile key.
Other numeric parameter variants reuse the same captured topology.

The input catalog remains the certified source/engine schema. A new input or
operation needs an explicit unit, availability, range/signature and preparation
contract; assigning an arbitrary ID cannot invent unavailable history. The
saved funnel population remains frozen. The current genotype keeps bounded
instruction capacity; output/reference choices can change effective depth,
but it does not search unbounded graph length or arbitrary Python classes.

## Backtest objective

The evaluator groups candidates by a deterministic topology hash. Numeric
variants within a group run together as independent accounts on `[B,N]` lanes.
Different topologies receive different compiled/captured runners on the same
resident market tape. A bounded LRU holds at most two runners per session;
evicted topologies can be reconstructed using normal compiler caches.

Groups are padded to the configured batch size with duplicates. Only original
candidate lanes enter fitness and results are scattered back to original
population order. Each replay resets accounts. Liquidity is conserved within
each counterfactual account, not shared between independent candidates.
Financial, causal and broker checks remain engine-owned.
In particular, a permissive searched entry rule cannot admit a ticker before
its causal source episode exists, replace a held position or supersede a
working entry. These checks are enforced outside the searchable rule graph.

Receipts separately report prepared replay time, new topology compile time,
group count and class-edit rejection count. A categorical generation can cost
much more than a numeric generation because new structures require setup.
No comparison should hide that cost inside an old numeric-only timing claim.

The fitness formula, one-session then two-session workflow, frozen evaluation
and restart boundaries are unchanged. Source/configuration changes require a
new run. Class catalogs and complete decoded winners are saved alongside source
hashes. No validation/evaluation score controls mutation or selection.

## Runnable paths

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -m research.vectorized_backtest.v1.torch_backtest.run_optimization --search-mode categorical --population 8 --generations 8 --clock-ms 1000 --validation-preobserved
```

The flag records that the default Aug 20 evaluation has already been examined.
Provide new certified later run IDs to obtain a fresh untouched holdout.
This command does not authorize data generation or writes to market tables.

Add `--initialization random` to randomize every numeric and categorical
coordinate in the first population without inserting a default lane. Coupled
type/shape constraints are repaired deterministically; the saved initial
population contains the complete executable tensors and rejection count.
Phase two continues from the first phase's training winner. The default is
evaluated as a frozen baseline after search. Initialization is part of the
restart identity, and per-topology compile/replay progress is printed.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -m research.vectorized_backtest.v1.torch_backtest.audit_categorical --cache D:/TradingML/runtimes/vectorized_backtest/strategy_one_audit/f0cadde7a53f4425ac110526fab97a82/tape --prefix-ms 600000 --clock-ms 1000
```

The audit compares default, logical-operation mutation and source/protection
class mutations with eager CPU on real data, including full ledgers and resets.
It is an implementation qualification, not an optimization/profitability run.

## Measured implementation qualification

The 2026-10-02 qualification used the cached Aug 18 source, a 1s main clock,
600 slots (ten minutes), and four independent account lanes per topology.
Its derived population shape was `[B,912]`: 15 numeric coordinates and 897
operation/reference/output class coordinates. This count is schema-dependent,
not a new hard-coded optimizer dimension.

Default, logical-operation mutation and input/protection mutation all matched
eager CPU financial summaries and complete fill ledgers within `1e-7`, with
identical results after reset. They produced 13, 13 and 2 fills respectively.
Prepared GPU replay took 0.895, 0.897 and 0.902 seconds; topology compilation
and capture took 38.19, 33.66 and 34.35 seconds with existing compiler caches.
These are prefix qualification timings, not full-session optimization results.

The immutable report is
`D:/TradingML/runtimes/vectorized_backtest/categorical_qualification/7013cf12554a44d39d6346f72e3fa60a/report.json`.
Unit tests additionally exercise class/output behavior, invalid IDs, causal
admission fences, grouping/padding/scatter, and interrupted solver recovery.


## Completed random categorical experiment — 2026-10-02

Campaign `febeafd36a644911ae5e5c1326521261` used a repaired random `[8,912]`
population, seed 20261002 and eight generations in each phase. Default was not
injected into phase one. Training used Aug18, then Aug18+Aug19; each replay
started an independent $10,000 account. Both winners were frozen before Aug20
**preobserved evaluation**, which never influenced selection or mutation.

| Session | Candidate | Net P&L | Max drawdown | Entries | Fills | Open positions |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-08-18 | Default | $3,160.51 | $1,351.13 | 26 | 182 | 0 |
| 2026-08-18 | One-session winner | $2,509.66 | $1,162.84 | 14 | 108 | 0 |
| 2026-08-18 | Two-session winner | $0.00 | $0.00 | 0 | 0 | 0 |
| 2026-08-19 | Default | $-2,368.05 | $3,117.15 | 32 | 217 | 0 |
| 2026-08-19 | One-session winner | $-865.69 | $1,627.63 | 14 | 121 | 0 |
| 2026-08-19 | Two-session winner | $562.75 | $485.41 | 1 | 15 | 0 |
| 2026-08-20 | Default | $859.82 | $1,602.66 | 25 | 194 | 0 |
| 2026-08-20 | One-session winner | $-400.55 | $2,119.06 | 11 | 77 | 0 |
| 2026-08-20 | Two-session winner | $0.00 | $0.00 | 0 | 0 | 0 |

The one-session winner's Aug18 fitness was 0.19282386025. Over both training
sessions the frozen default/one-session/two-session scores were
-0.14119163925 / -0.0297552116875 / 0.0089679282897095. Aug20 scores were
0.00584921975 / -0.14600794675 / 0. Both searched finalists failed to beat default
on this evaluation. The two-session winner's inactivity on Aug18 and Aug20 is
permitted by this experiment's unchanged objective, not proof of generalization.

The read-only first-entry audit found a weak semantic operand substitution:
`boundary_ms - episode_start_ms <= closed_boundary_ms`. Before the first
position, the RHS is zero. Eager evaluation reproduced zero eligible Aug18
entries and one Aug19 entry. This evidence does not demonstrate a GPU reset or
scatter bug, nor does it certify every arbitrary program. V3 separately adds
semantic operand restrictions and optional activity constraints; this v1
campaign was not changed or retuned midway.

### Timing and verification

All sessions contain 19,800 one-second slots (04:00–09:30 New York). Finalist
results reuse an eight-lane batch with duplicate padding and three distinct
compiled topologies. The following times cover the whole three-topology batch,
not an individual finalist:

| Session | Compile/capture | Prepared replay |
| --- | ---: | ---: |
| 2026-08-18 | 121.62s | 92.55s |
| 2026-08-19 | 146.70s | 117.56s |
| 2026-08-20 | 168.06s | 92.09s |

Summed training compile/capture and prepared replay times were 2,491.56s /
1,962.55s in phase one and 5,682.03s / 4,498.42s in phase two. These exclude
certification, source preparation, schema construction, mutation/repair and
checkpoint overhead and must not be presented as total campaign wall time.

Completion checks verified all 16 generation receipts against the objective,
all 35 pinned Python source hashes, both frozen decodes against the original
training-source program schema, winner fitness/membership in evaluated
populations, finalist identities, and final training/evaluation score arrays.
The worker has exited; no retry or additional evaluation was needed. Existing
Kazoo background warnings were preserved and did not prevent completion.

Immutable runtime evidence:
`D:/TradingML/runtimes/vectorized_backtest/strategy_search/febeafd36a644911ae5e5c1326521261/report.json`
and `completion_verification.json`. No fresh-holdout performance or global
optimality is claimed. No market-data or live-trading writes occurred.
