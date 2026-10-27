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

The rerun also disables the $300/20% execution entry/add admission limits via
risk_limits_enabled=false. Stops, quote validity, commissions, $1000/$200 sizing
and terminal accounting remain intact. The protected CUDA financial kernel still
records risk diagnostics; its admission envelope becomes 100% distance and an
unbounded dollar budget, so valid positive stops cannot trigger the old limits.

The authorized laptop run is 2026-08-13 only, 50 total generations, seed 2021,
population 1024, and no validation. It restores the exact original V53 6144
pre-selection genomes and saved initialization RNG. All founders must be replayed
under the new execution: prior financial receipts are forbidden as fitness.
Original populations, receipts and earlier V56 campaign remain untouched.

The next population keeps 102 global elites, 205 fresh candidates and 717
crossover slots. A balanced schedule assigns approximately 25% of crossover
parent draws to ticker ranks and 75% to global ranks. Tickers rotate; only
active, terminal-valid candidates can parent. Species, ancestry, duplicate and
budget checks remain. Siblings share their selected pair; failed mating slots
use existing mutation fallbacks, so realized fractions are reported separately.
Ticker scores use P&L - gross losses - 2 * equity drawdown, with per-listing
mark-to-market peaks/drawdowns tracked every replay clock. Offspring still trade
the whole session. Global elites use the unchanged global objective.

Replay batches stay at 512. Extra ticker peak/drawdown buffers are bounded by
batch size and listing width; CPU and captured CUDA paths use the same accounting.
All five actions require at least one node, so a single action in a complete
1024-node individual can occupy at most 1020 nodes when the others use one each.

Generated deployments, profiles, compiler caches, source bindings, checkpoints
and results belong under D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v56.
Use a committed immutable deployment and full_launch with --generations 50 and
--comparison-source. The scheduled runtime entry pins code and job hashes;
active-pilot.json identifies its actual paths. Earlier V56 output is preserved
and stopped before assigning the laptop GPU to the new run.
