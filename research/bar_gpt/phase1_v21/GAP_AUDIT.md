# Raw train/development gap audit (2026-11-22)

The dominant regressions also occur on training examples. Validation-only
overfitting is not the leading explanation, although some group-specific
generalization gaps remain. This is evidence about outcomes, not proof of a
particular gradient conflict or architectural defect.

Source`8d03bf92523b2c9cf815d178d56b536c1b0ab249`; runtime
`phase1-v21-gap-audit-20261122`; W&B`mfhpi9t0` in
`mehdifaraji/bargpt-phase01-v21-repair`. Runtime result records all checkpoint,
split, data and supplement hashes. Evaluated frozen parent, pre-floor control and
combined basis/pre-floor candidate on114,599training and11,146development
windows, with identical16-visible masks per model and separate all-visible
evaluation. No statistics were fitted and no checkpoints changed. Source-valid
cells, all18raw features, candle/range/activity directions, embedding variance
and close-price bands are included. GPU evaluation took27.90seconds after
provenance checks. Local boundary/denominator test passed.

Candidate versus retained parent across216timeframe/channel/masking raw groups:
146training groups improve and150development groups improve. Of66development
regressions,54also worsen on training; only12 improve on training while worsening
on development. The pre-floor-only control has58both-worse and23train-better/dev-
worse groups. Unequal price/activity distributions preclude interpreting a raw
train/dev error ratio alone as overfit; the within-split parent comparisons are
the more useful diagnostic.

| Combined candidate change versus parent | Training | Development |
|---|---:|---:|
| Masked daily-volume MAE | +23.72% | +20.72% |
| All-visible daily-volume MAE | +8.12% | +8.25% |
| All-visible1s close MAE | $0.3503 to$0.3308 | $0.2277 to$0.2118 |
| All-visible1s candle direction | 87.13% to85.90% | 91.14% to90.30% |
| All-visible daily ask-range bin accuracy | 81.82% to80.48% | 78.85% to77.54% |

No broad embedding collapse: all512dimensions have variance above1e-10. Combined
candidate mean variance is0.642training/0.983development versus parent
0.643/0.985. Variance alone is not a complete information-retention test.

## Close-price bands

Exact mean, median andP90absolute percentage errors are computed for source-valid
trade/bid/ask closes in six bands: below$0.10, $0.10–1, $1–5, $5–20, $20–100,
and$100+. Boundaries belong to the upper band. Counts of zero, tiny-positive
(<=1e-8) and negative target denominators are explicit. Such cells remain in raw
metrics and are excluded only from relative metrics. This audit found no zero
or tiny-positive close denominators. These bands are diagnostics only and never
enter model inputs or inverse reconstruction.

For all-visible development1s trade close:

| Band / metric | Parent | Combined candidate |
|---|---:|---:|
| Below$0.10 mean percentage error | 2.809% | 3.231% |
| Below$0.10 median percentage error | 2.465% | 3.010% |
| Below$0.10 P90 percentage error | 5.241% | 5.760% |
| $0.10–1 mean percentage error | 2.520% | 2.395% |
| $0.10–1 median percentage error | 1.578% | 1.510% |
| $0.10–1 P90 percentage error | 5.917% | 5.619% |

Below$0.10 has2,024training cells from34windows and4,096development cells from
64windows. These are contributing windows, not established independent
symbol/sessions. Training target median is$0.044, range$0.0101–0.0999;
development median$0.0309, range$0.0175–0.0457. Training mean percentage error
improves24.49% to23.60%, unlike development. This small, differently distributed
group merits separate scrutiny; do not call its thousands of cells independent
samples. CPU coverage receipt is`cheap-band-coverage.json`.

Decision: do not select from aggregate dollar MAE or label the problem generic
overfit. Preserve the partial masked-price gain, but full training remains
unqualified. Before another loss-weight/LR/capacity change, inspect actual
optimizer-step tradeoffs between reconstruction components and the coverage of
the weakest groups. Existing component-gradient norms and fixed-preconditioner
audits do not establish the effect of an actual Adam step with saved momentum.
