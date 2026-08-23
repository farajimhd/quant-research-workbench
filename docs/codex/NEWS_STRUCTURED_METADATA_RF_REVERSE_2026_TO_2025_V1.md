# Structured Metadata RF Reverse 2026-to-2025 V1

## Outcome

The reverse-role Random Forest trained on all 142,256 decisive articles from
the latest revised 2026 authority and evaluated all 203,847 decisive revised
2025 articles. It reused the exact frozen V1 sparse matrices and 11,434
structured dimensions; no TF-IDF, rendered text, exact ticker identity,
deterministic synthesis output, or label-provenance field entered the model.

The selected threshold was 0.40. Revised-2025 results were:

| Metric | Result |
|---|---:|
| Accuracy | 91.82% |
| Balanced accuracy | 92.57% |
| Eligible precision | 84.46% |
| Eligible recall | 95.48% |
| Eligible F1 | 89.63% |
| Ineligible recall | 89.66% |
| ROC AUC | 0.9782 |
| Average precision | 0.9559 |

The confusion matrix was 115,058 true ineligible, 13,266 false eligible,
3,414 false ineligible, and 72,109 true eligible. There were 16,680 label
disagreements, or 8.18% of the 2025 population.

At an unselected threshold of 0.50, raw accuracy was higher at 92.78%, but
balanced accuracy fell to 92.02% and eligible recall fell from 95.48% to
89.10%. The 0.40 result is primary because the threshold was selected inside
2026 by balanced accuracy and eligible F1 before evaluating 2025.

## Selection and training

The experiment mirrored the V1 bounded selection procedure in reverse:

1. Train each of the same four 120-tree candidates on 71,645 revised
   January-April 2026 articles.
2. Select configuration and threshold on 70,611 revised May-August 2026
   articles.
3. Refit the selected configuration with 400 trees on all 142,256 revised 2026
   articles.
4. Evaluate revised 2025 once.

Candidate 3 won with unlimited depth, one-sample leaves, `log2` feature
sampling, 80% bootstrap samples, balanced-subsample weights, and threshold
0.40. Its internal May-August 2026 balanced accuracy was 86.35%.

## Interpretation limits

This is not an unbiased held-out or forward-production accuracy estimate:

- The frozen feature dictionary was originally built using 2025 category
  support. Reusing it is necessary for exact feature parity, but it exposes the
  test year's category inventory to the representation contract.
- Revised 2026 labels were produced after RF-conditioned disagreement
  selection, even though semantic reviewers were prediction-blind.
- Training on a later year and evaluating an earlier year measures backward
  transfer, not future generalization.

The result shows that revised structured metadata relationships transfer
strongly into 2025 under the frozen representation. A new time-forward,
independently audited population is still required for a release-grade accuracy
claim.

## Runtime authority

Validated artifacts are stored at:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\structured_metadata_rf_reverse_2026_to_2025_v1`

The runtime contains the saved model, revised-2025 predictions and
disagreements, report, validation, and SHA-256 manifest. Validation confirmed
142,256 training rows, 203,847 unique prediction rows, exact disagreement
coverage, a nonempty saved model, and zero independent hash mismatches.
