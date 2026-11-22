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

## Actual-update counterfactual (2026-11-22)

Source791a68bb7, runtime `phase1-v21-range-steps-20261122`, W&B `l3gg3o1b`.
Restored the retained slow-temporal checkpoint, all three optimizer groups,
moments, RNG and traversal; applied eight disposable updates3001-3008. Each
module counterfactual applies that module's actual finite update to the
pre-step model; all parameters are restored afterward. These effects need not
add because of interactions and finite precision. No checkpoint is written.

Decoder-only updates reduce the mean predicted range across measured
timeframe/family/visibility groups in8/8 steps (about0.00010-0.00020 basis
points per step), and reduce the weighted range objective in8/8. They narrow
24-29 of36 groups on masked steps and13-15 of18 on full-visible steps.
Temporal-only updates widen the group mean in5/8 steps and narrow it in3/8;
bar effects are smaller. The actual combined update decreases total loss in
5/8 steps and the range objective in7/8, yet raw range MAE worsens in5-20
groups per step. Neither decreasing the objective nor narrowing a prediction
automatically means that its range-bin classification improves.

This is local evidence of an objective/metric tradeoff, not proof that the
decoder caused the accumulated validation regression. The magnitude is small,
eight batches are not a full trajectory, and some predictions are too wide.
Next test should quantify range objective gradients by target bin (including
training prevalence) before choosing a targeted correction; do not infer that
global range weighting, decoder expansion or freezing bar embeddings is needed.

The diagnostic took7.92seconds. Seven focused tests passed. An independent
eight-step replay without counterfactual evaluations produced exactly equal
sampling hashes, before/after metrics, gradient norms and alignment values;
receipt `parity.json`, run `phase1-v21-range-steps-parity-20261122`, W&B
`1y6iuvmf`. Both remote runs finished; primary final loss12.063206599 read back.
Original checkpoint SHAefd773910e06d53fbfd6f1b6280cad0be83886c75b9d6f652b0392718cec0433
is unchanged. No V21 process remains active. Full training is still unqualified.

## Training-bin pressure audit (2026-11-22)

Source46e262694, runtime `phase1-v21-range-gradients-20261122`, W&B `i3xtph6q`.
Frozen slow-temporal checkpoint;114599 training-pool windows and11146 dev
windows,16/64-visible passes. Analytical derivative of the actual weighted
range Huber and ordinal BCE terms matches autograd in a focused test.
Execution8.85seconds;1542 source files hash-verified. No fitting or parameter
updates. Checkpoint hash remains unchanged and no V21 process remains.
Remote W&B history readback confirms training1m ask bin3 count197864; run finished.

Of21,988,824 source-valid training range cells (three families),51.380% are
zero,24.981% in0-0.05%,8.663% in0.05-0.1%,7.365% in0.1-0.25%,2.742% in
0.25-0.5%,4.870% above0.5%. These describe the frozen experiment pool, NOT
the186M-window full training dataset or independent sessions.

Under16-visible evaluation, including the actual visible1/hidden0.1 weights,
zero ranges contribute67.49% of total positive output-gradient magnitude
(pressure to narrow);0-0.05% adds22.61%. Combined positive magnitude3.196e9
exceeds negative2.197e9 before family/batch averaging. Larger target bins
have negative average gradients (pressure to widen), so the loss does respond
to their errors; its response is bounded by Huber saturation and ordinal
logits. These are derivatives at predicted log range, not parameter gradients:
other losses, model Jacobians, Adam and clipping are excluded. Do not equate
this imbalance with proven causation or neglect necessary zero-range accuracy.

Next paired hypothesis: training-derived inverse-square-root range-bin weights,
normalized to preserve expected range-loss mass, can improve large-range
retention without blanket amplification of the entire range objective. Apply
only to the range Huber/ordinal terms; keep raw-price/activity objectives,
architecture, initialization, masks and temporalLR control unchanged. Fit weights
from training only, preserve all examples, and audit zero-range accuracy plus
cheap-stock and activity regressions. This specific bin-conditioning test is
different from prior global loss-weight and activity-timeframe sweeps. It is
not yet implemented or qualified; no full20 run has been launched.

