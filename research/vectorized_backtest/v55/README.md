# V55: evolving shared recurrent core with six heads

V55 starts with V54's 64-unit sparse recurrent founders, then supports 16 to
506 recurrent units subject to a 512-node total ceiling. Count the core once,
plus every head readout and Boolean join. Six plain heads cost 6 nodes;
all five Boolean heads together cost 16, leaving 496 core units. Fan-in stays
bounded at 8. Entry, exit, add, reduce, trail and continuous stop control retain
the V54 numerical and financial contracts.

Growth mutation adds 1, 2 or 4 cloned units with new persistent IDs and redirects
existing head taps to those clones. Incoming edges, including delayed reads of
the original unit, are preserved. From session reset all preexisting head outputs
are unchanged. Subsequent parameter/connection edits can specialize the clones.
Pruning removes 1 or 2 units, removes incident edges and remaps surviving delayed
and head references. A bias-only remainder uses a zero-weight placeholder with
no known-market flag. The minimum core size is 16. Pruning can change behavior.

Ordinary crossover aligns cells by persistent identity and takes the full union
of parent units. Cell parameters and all six heads are inherited; delayed and
readout indices are remapped by ID. Each inherited head carries its parent's
age gate. Over-budget unions are rejected, recorded, and use the existing ranked
parent-mutation fallback. No required dependencies are silently truncated.
Boolean OR/AND/XOR/XNOR head composition retains the base core and imports missing
donor readout dependencies transitively; it still evaluates common IDs on the
child's latent state, so full parent behavior is not guaranteed to be retained.
Nested Boolean heads remain explicitly limited to two branches. Stop stays
continuous. Accepted crossover children receive no immediate mutation.

The V54 reproduction proportions, active financially eligible parent rule,
ancestry, species, independent 32-child mutation diagnostic and checkpoint seals
remain. Structural grow/remove schedule entries now change actual neuron count;
other structural entries rewire as in V54. There is no complexity penalty added
to fitness. Neutral growth must earn its place through later edits and selection.
Evolution resets recurrent memory for each replay/session; no live resizing occurs.

Storage is ragged: recurrent buffers allocate the sum of candidate sizes, not
population times the largest size. CPU packing builds core-tile launch lists;
CUDA evaluates only actual candidate tiles, followed by six head readouts.
No candidate accesses another candidate's memory. Checkpointed replay state binds
both neuron IDs and program identities. Profiling compares neuron/listing tile
choices and cohort-maximum padded launches at the actual 453-listing Aug13 width.
Synthetic neural timings exclude feature projection, finance and serialization.
The explicit neural workspace admission remains 4 GiB; GPU/host headroom checks
and native batch tails remain enforced.

Pilot: laptop only, 2026-08-13, seed 2021, 6144 fresh founders (3072 feature-family
and 3072 random), population 1024, ten generations, no validation. A comparison
binding to the completed V54 pilot verifies freshly regenerated founders and
post-initialization RNG after normalizing only the payload version. It does not
import trained candidates. Generation 1 also must reproduce every sealed V54
financial panel field within 1e-8 before evolution may proceed.

The authorized 50-generation extension uses `continuation`, not a fresh launch.
It verifies the completed generation-10 checkpoint, all historical evidence and
financial receipts, then produces the omitted generation-11 successor using the
parent deployment and saved RNG. This boundary operation performs no market
replay. A separate runtime preserves the original seals and binds the boundary
state, numerical-engine hashes, financial contract, and new generation budget.
Only the versioned v2 job can extend to 50; fresh v1 pilots remain restricted to
ten generations. Population, neuron identities, species and ancestry continue.

Fitness remains net P&L - gross losing-position dollars - 2 * mark-to-market
maximum drawdown. Execution retains $1000/$200 entry/add caps, $300 prospective
position-risk admission, 20% maximum stop distance, commissions and valid bid/ask
quotes at most one second old. Learned stops can only tighten held stops.
The inherited V53/V54 limitation remains: tiny reductions can pay minimum fees
that push paid-cost risk above $300. Post-replay eligibility rejects those
candidates; this is not a hard per-action risk ceiling. V55 does not change that
execution rule during an architecture comparison.

All runtime data, profiles, compiler caches, ledgers and checkpoints belong under
D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v55. Run the committed,
hash-verified deployment using full_launch and pilot. Its active-pilot.json names
the actual code and job paths; the scheduled entry pins their hashes and opens
an independent progress terminal.

Bounded neural profile from the laptop repository (no backtest data access):

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TRITON_CACHE_DIR='D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v55/qualification/triton'
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -m research.vectorized_backtest.v55.profile_shared --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v55/qualification/profile --batch 128 --width 453
```
