# Range-bin regression localization

CPU-only audit of immutable `evaluation-0.json` and `evaluation-3000.json`
from `phase1-v21-temporal01-20261122/joint_basis_prefloor_slow_temporal`.
Receipt: `phase1-v21-temporal01-audit-20261122/range-confusion.json`.
The script records SHA256s and requires identical target-bin counts, metric
schemas and finite nonnegative confusion counts. No data is reread or fitted.
One focused test verifies accuracy differences and rejects mismatched targets;
the real saved-evaluation CLI also completed successfully.

Masked1m ask ranges0.1-0.25%:13,546 cells, correct31.382% to24.671% (-6.710pp).
Predictions below0.1% grow9,131 to10,070; correct4,251 to3,342. Bid in the same
group loses6.436pp across13,596 cells. These are cells, not independent samples.
Masked daily ask ranges>0.5% dominate9,583 of10,215 valid cells; their accuracy
falls78.723% to75.290%. Predictions in the adjacent0.25-0.5% bin grow1,682
to1,904. The small0.1-0.25% daily group improves34.896% to41.667% (192 cells).
Thus the aggregate loss is not uniform across bins; prominent errors move
toward narrower predicted ranges.

The fixed legacy decoder applied to standalone bar embeddings has a worst
per-bin range-accuracy decline of only0.20pp, versus6.71pp through the masked
window path. Its price MAE drift therefore does not establish a corresponding
loss of range information. This localizes the larger range regression to the
joint temporal/window-readout path, without separating encoder from decoder
causation or proving retention of every feature.

Next diagnostic: examine actual saved-update contributions to range shrinkage
through temporal and decoder parameters, including raw-price and range losses.
Do not jump from this observation to freezing the bar encoder or enlarging the
decoder. Preserve the matched temporal-LR control; any gradient-routing or
loss revision needs a new identity, parity and worst-group qualification.

Reproduce with `python -B -m research.bar_gpt.phase1_v21.range_confusion_audit`
and explicit `--before`, `--after`, and new runtime `--output` paths. Set
`PYTHONDONTWRITEBYTECODE=1`. This analysis does not qualify a full training run.

