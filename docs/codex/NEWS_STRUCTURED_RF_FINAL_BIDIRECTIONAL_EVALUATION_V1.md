# Finalized-label structured RF bidirectional evaluation V1

## Final dataset authority

The finalized correction-grade development authority is:

`D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_sentiment_authority_structured_rf_reverse_audit_v1`

Its hash-manifest SHA-256 is
`33029c972f5514fc4d09b6b2646666849c48c319bd110ffe073b9e1d83bb90c6`.
The validated immutable authority contains 361,695 articles: 151,632 eligible,
208,650 ineligible, and 1,413 insufficient-short-text rows. It incorporates
4,237 corrections from the complete 16,680-row reverse-disagreement audit,
preserves 43 unresolved rows, and leaves issuer sentiment byte-identical.

This is finalized correction-grade local Codex supervision for model
development. It is not human-certified ground truth.

## Comparable training contract

Both directions use the same frozen 11,434-dimension structured feature
contract, deterministic seed, four-candidate Random Forest search, and final
400-tree fit. Neither model uses TF-IDF, rendered article text, exact ticker
identity, deterministic synthesis output, model predictions, or label
provenance as features. Each direction replaces the old frozen target arrays
with labels read by source ID from the finalized authority.

| Direction | Train rows | Test rows | Threshold | Accuracy | Balanced accuracy | Eligible F1 | Eligible precision | Eligible recall | ROC AUC | Disagreements |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2025 to 2026 | 203,847 | 142,256 | 0.45 | 88.060% | 88.345% | 86.594% | 83.180% | 90.299% | 95.014% | 16,985 (11.940%) |
| 2026 to 2025 | 142,256 | 203,847 | 0.40 | 93.896% | 94.376% | 92.406% | 88.675% | 96.465% | 98.612% | 12,443 (6.104%) |

For 2025 to 2026, configuration and threshold selection use only 2025: rows
before November form the development subset and November-December selects the
candidate and threshold. The final model then fits all decisive 2025 rows and
evaluates January-August 2026.

For 2026 to 2025, January-April 2026 selects the candidate, May-August selects
the threshold, and the final model fits all decisive 2026 rows before
evaluating 2025.

## Interpretation limit

These figures measure agreement with the finalized corrected labels. They are
not unbiased held-out release accuracy: the 2026 labels were revised after
forward-model disagreement selection, and the 2025 labels were revised after
reverse-model disagreement selection. The frozen category dictionary was also
constructed with 2010-2025 support. A new time-forward population outside the
audited January-August 2026 set must be independently labeled without seeing
either model before release accuracy can be estimated.

## Runtime evidence

- Forward: `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\structured_metadata_rf_2025_to_2026_final_labels_v1`
- Reverse: `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\structured_metadata_rf_2026_to_2025_final_labels_v1`
- Forward hash-manifest SHA-256: `472735b1c266f4bb97ed4f181d64ed854f0d2e8cb24b8e82583e14f16fc29cb2`
- Reverse hash-manifest SHA-256: `dff392266d263f530589e8f41f753c7a7842ccdffd2b18646bef329a37e1f8e6`

Both artifact validators passed exact row counts, unique source membership,
disagreement reconciliation, model presence, and report completeness.
