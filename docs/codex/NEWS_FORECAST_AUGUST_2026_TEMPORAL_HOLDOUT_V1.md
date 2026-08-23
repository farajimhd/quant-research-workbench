# August 2026 Forecast-Eligibility Temporal Holdout V1

## Outcome

The 5,044 Benzinga articles published after the previously exposed August 13
boundary were frozen before either structured Random Forest was exposed to the
reviewers. Every article received prediction-blind full-text review. Independent
second and, when necessary, third readers resolved 4,913 labels; 131 ambiguous
articles remain explicitly unresolved and are excluded from metrics.

| Frozen model | Accuracy | Balanced accuracy | Eligible precision | Eligible recall | Eligible F1 | ROC AUC |
|---|---:|---:|---:|---:|---:|---:|
| Train 2025, threshold 0.45 | 83.02% | 83.83% | 72.72% | 86.97% | 79.21% | 91.45% |
| Train 2026, threshold 0.40 | 82.56% | 84.55% | 70.17% | 92.34% | 79.74% | 93.22% |
| Train 2025-Aug. 13, 2026, threshold 0.44 | **84.14%** | **85.25%** | 73.56% | 89.55% | **80.77%** | 93.19% |
| Structured + TF-IDF RF, threshold 0.48 | **84.88%** | 85.08% | **76.39%** | 85.88% | **80.86%** | **93.27%** |
| Structured + TF-IDF MLP, threshold 0.45 | 83.84% | **85.35%** | 72.45% | **91.24%** | 80.77% | 93.15% |

The 2025-trained model produced TN/FP/FN/TP of 2,490/596/238/1,589. The
2026-trained model produced 2,369/717/140/1,687. Therefore the 2025 model is
more selective, while the 2026 model is the better high-recall first filter.
Neither threshold was tuned on this holdout.

## Combined pre-boundary training result

A successor experiment stacked the frozen 203,847-row 2025 matrix and the
142,256-row January-August 13, 2026 matrix, replacing their targets from the
finalized correction-grade label authority. Candidate selection and threshold
selection used only the training population: 311,432 articles before July 1
for development and 34,671 July 1-August 13 articles for validation. The final
400-tree model was then refit on all 346,103 pre-boundary articles.

At its internally selected 0.44 threshold, holdout TN/FP/FN/TP was
2,498/588/191/1,636: **84.14% accuracy**, 85.25% balanced accuracy, and 89.55%
eligible recall. At the conventional 0.50 cutoff, accuracy was 85.24%, balanced
accuracy 85.23%, and eligible recall 85.17% (TN/FP/FN/TP
2,632/454/271/1,556). The 0.50 result is reported as a fixed-cutoff diagnostic;
it was not selected from the holdout.

The internally selected operating point is the primary result because its
candidate and threshold policy was fixed without holdout feedback. Compared
with either single-year model, combining all pre-boundary supervision improves
accuracy and eligible F1, but it still misses 191 of 1,827 eligible articles.

### Training-set resubstitution diagnostic

Scoring the final fitted forest on the same 346,103 articles used for fitting
produced 210 mismatches at threshold 0.44: 210 false eligible and zero false
ineligible, or 99.9393% training accuracy. At threshold 0.50 it produced only
three mismatches—two false eligible and one false ineligible—or 99.9991%
training accuracy.

These are deliberately labeled resubstitution metrics, not validation results.
The gap from 210 training mismatches to 779 holdout mismatches at threshold
0.44 (and from three to 725 at threshold 0.50) demonstrates that the forest
fits the training labels almost perfectly while being materially less accurate
on new articles. The sealed holdout remains the generalization authority.

## TF-IDF and neural challengers

The TF-IDF challenger adds a 75,000-column word unigram/bigram vocabulary to
the frozen 11,434 structured dimensions. The vocabulary is learned only from
the 346,103 pre-boundary articles. The forest configuration is held fixed from
the structured-only experiment, while its 0.48 threshold is selected on the
same 34,671-row July-August 13 internal validation slice. This isolates the
lexical feature contribution without a post-holdout parameter search.

The resulting 86,434-feature Random Forest achieved 84.88% accuracy and 743
mismatches: TN/FP/FN/TP 2,601/485/258/1,569. Compared with structured-only, it
corrected a net 36 holdout decisions and improved accuracy by 0.73 percentage
points, eligible precision by 2.83 points, and eligible F1 by 0.09 points, but
eligible recall declined by 3.67 points.

A single precommitted sparse-input neural challenger consumed the identical
86,434 features after training-derived max-absolute scaling. Its architecture
was 256 and 128 hidden units with GELU, layer normalization, 15% dropout,
AdamW, and positive-class-weighted binary cross-entropy. Epoch count and
threshold were selected only on the internal validation slice; epoch one and
threshold 0.45 won before the final model was refit for one epoch on all
346,103 training articles.

The MLP achieved 83.84% accuracy and 794 mismatches: TN/FP/FN/TP
2,452/634/160/1,667. It delivered the best eligible recall among the combined
models at 91.24%, but the extra 149 false eligible decisions relative to the
TF-IDF forest reduced accuracy and precision. The TF-IDF Random Forest is the
best overall classifier from this controlled comparison; the MLP is useful
only if the operating objective values eligible recall more than false-positive
compute cost.

## Population and blindness

- Frozen population: 5,044 articles, 2026-08-13 21:04:39 through
  2026-08-23 20:28:07 UTC.
- Resolved truth: 1,827 eligible and 3,086 ineligible.
- Unresolved: 131, retained without a forced label.
- Review packets hid existing supervision labels, model predictions,
  probabilities, split membership, and source IDs.
- Review evidence included provider metadata, tags, channels, timing, ticker
  context, title, teaser, opening sentences, and full rendered text at the
  escalation stage.

## Computational funnel

Two compact readers reviewed each article. Because the 364-row compact
fast-lane QA sample achieved only 89.01% agreement with full text, below the
precommitted 99% agreement and 98% Wilson-lower-bound gates, the fast lane was
rejected. All 5,044 articles therefore received full-text primary review.

Low-confidence decisions and compact/full contradictions sent 2,361 articles
to an independent second full-text reader. The first two readers resolved
4,005 articles. A third independent reader reviewed the remaining 1,039 and
resolved 908 by majority, leaving 131 without a decisive majority.

Final decision paths were:

- 2,683 high-confidence primary full-text decisions;
- 1,322 two-reader full-text agreements;
- 908 three-reader full-text majorities;
- 131 unresolved three-reader cases.

## Interpretation

This is time-forward release evidence for the two immutable structured-feature
models, unlike the earlier bidirectional diagnostics whose evaluation labels
were corrected after model-disagreement selection. It measures agreement with
the blind Codex review authority, not market-return forecasting accuracy.

For a cost-saving top-of-funnel filter, the 2026-trained model misses 140 of
1,827 eligible articles (7.66%) but incorrectly advances 717 of 3,086
ineligible articles. The 2025-trained model misses 238 eligible articles
(13.03%) and advances 596 ineligible articles. The higher-recall 2026 model is
the safer candidate if false rejection is the dominant cost; production use
still requires a policy for the 7.66% eligible miss rate.

## Authority and validation

Bulk packets, votes, labels, predictions, reports, and hashes remain outside
the repository at:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\forecast_eligibility_august_2026_temporal_holdout_v1`

`VALIDATION.json` passes all population, uniqueness, packet-coverage,
selection, unresolved-exclusion, and frozen-source hash checks. The final-label
SHA-256 is
`c7ad888fb620c78e48d08e2dbdad97b2ddbda0a31f53010f6e12c40c80f0cbcc`.
