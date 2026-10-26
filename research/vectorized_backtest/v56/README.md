# V56: V53 graphs with a 1024-node budget and selection risk caps removed

V56 retains V53's five independent recurrent action graphs, rich operators,
structural mutation, mask crossover, OR/AND/XOR/XNOR composition, species and
ancestry rules. The version-local config raises both MAX_NODES (per action) and
MAX_STRATEGY_NODES (total) to 1024. Maximum depth remains 512 and fan-in remains
16. Existing V53 payloads retain their compatible schema and exact identities.
No shared V42 config or existing V53 source is changed.

Selection no longer rejects a candidate because recorded maximum position risk
exceeds $300 or recorded maximum stop distance exceeds 20%. This applies to both
the global aggregate and local objective. Finite/nonnegative financial metrics,
valid terminal liquidation, and the active-parent requirement remain enforced.
Fitness remains exactly net P&L - gross losing-position dollars - 2 * equity DD.
Risk and stop-distance measurements remain available as diagnostics.

The user's change is to selection. Execution entry/add risk admission still uses
$300 and 20%, and sizing caps remain $1000/$200. Commissions, strict valid bid/ask
quotes at most one second old, stops and liquidation accounting remain V53.
Tiny reductions can accumulate minimum commissions past the entry risk envelope;
V56 does not remove such a candidate from selection solely for that reason.

The authorized laptop run is 2026-08-13 only, 50 total generations, seed 2021,
population 1024, and no validation. Generation 1 restores the exact original V53
6144 pre-selection founders and saved initialization RNG, and reuses their
sealed original full-session financial receipt. It imports no trained winner and
performs zero founder market replays. V56 recalculates eligibility under its new
selection rule. Generations 2 through 50 run normally with V56's larger budget.

Reuse requires explicit --comparison-source pointing to a completed V53 risk-cost
pilot. The binding verifies founder population, RNG, population and training
seals, receipt hashes, execution/objective/input contracts, byte-identical shared
dependencies, and normalized identical numerical replay sources. These are
founder reuse evidence, not a claim that V56 replayed those 6144 candidates.
Gen1 training receipt references retain producer version V53 and an explicit
user-authorized reuse marker. Source population and receipt files stay intact.

The population remains 1024, evaluated in batches of 512. At 453 listings, two
float32 recurrent buffers plus known masks for a maximum-size 512-candidate
batch require approximately 2.37 GB, leaving the existing 4 GiB neural workspace
admission intact. GPU/host admission remains fail closed. CUDA loops over packed
runtime node counts; CPU/CUDA qualification includes graphs above 512 nodes.
All five actions require at least one node, so a single action in a complete
1024-node individual can occupy at most 1020 nodes when the others use one each.

Generated deployments, profiles, compiler caches, source bindings, checkpoints
and results belong under D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56.
Use a committed immutable deployment and full_launch with --generations 50 and
--comparison-source. The scheduled runtime entry pins code and job hashes;
active-pilot.json identifies its actual paths. V55 is stopped at its next sealed
generation before the laptop GPU is assigned to V56.
