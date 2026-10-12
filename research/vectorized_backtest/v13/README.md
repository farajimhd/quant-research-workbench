# V13: aggressive session specialists

Copied from V12; no feature, rule evaluator, GPU replay or assumed-fill changes.
Fresh generation-one campaign uses the same certified 30 training / 6 validation
sessions and 102 V7 seeds plus 922 random initial strategies.

Minimize each session cost: -net_profit + .05*drawdown + .005*risk_dollar_hours
+ .0004*capital_dollar_hours. Complexity, trimming, tail, concentration and
shortfall weights are zero. These remain diagnostics only. Aggregate score is
for reporting, never parent selection. Terminal-invalid candidates are excluded;
a candidate must have traded in a session to enter that session's parent pool.

For population 1024: 204 fresh immigrants; top 27 parents per training session;
30 unchanged champions, 150 nominal champion crossover children (five distinct
random partner sessions per first-parent champion), 640 general crossovers.
Ordered pairings produce one child each. General first-parent assignments rotate
extra seats across sessions; shuffled pool cycles avoid fixed-rank favoritism.
Second parents come from different sessions, balancing session usage with random
ties. Five parent-link ancestry restrictions remain mandatory for every pair.
Duplicate champions occupy one slot; missing champion children transfer to general
crossovers. General mate/dedup failures after bounded attempts become explicitly
counted extra immigrants. Empty pools have no eligible parent; their general
assignments also become immigrants. No mutation route or aggregate elite quota.
Exact strategy deduplication applies to the entire successor population.

Session scoring/sorting uses NumPy session x candidate arrays. Packed pedigree
membership uses vectorized operations and a bounded row cache. Structured child
rule construction and serialization retain bounded Python loops. Selection_seconds
is recorded separately in each full-generation wall receipt.

Gxxx is an immutable origin-generation prefix inherited from the first parent
through crossover/mutation. Fresh immigrants start their origin at creation.
The numeric `generation` field records actual child birth; `origin_generation`
records the prefix. Serial and operation counts distinguish new children. Retained
champions keep their entire original label. Second-parent ancestry is preserved.

Training terminal shows session then score, with existing aggregate diagnostics
and max 50 rows per page. Full aggregate ranking and session-ranking.json persist.
Validation shortlist is a unique round-robin of training session ranks (up to one
batch), frozen before display-only validation; no validation feedback into RNG,
selection, weights, stopping or checkpoint choice. W&B remains a separate observer
with scalar-only uploads. Its leader is the best aggregate-training-score member
of the frozen session-balanced shortlist, matched by identity across both splits.

Run using `python -B -m research.vectorized_backtest.v13.run_search --help`.
Use PYTHONDONTWRITEBYTECODE=1, runtime output under D:/TradingML/runtimes,
--maximum-gate-gib 8 for the certified validation panel. Never resume a V12
checkpoint as V13. Workstation deployment must be committed/pushed and verified.
