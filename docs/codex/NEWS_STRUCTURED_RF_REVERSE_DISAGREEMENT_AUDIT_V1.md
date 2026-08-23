# Structured RF Reverse-Disagreement Blind Audit V1

## Outcome

The complete 16,680-row disagreement population from the structured-metadata random forest trained on revised 2026 labels and evaluated on revised 2025 labels has been audited.

- 915 rows reused exact, rendered-text-hash-matched correction-grade reviews.
- 15,765 rows received fresh blind compact review.
- The initial fast lane failed certification at 52.31% agreement with a 46.88% Wilson 95% lower bound, so all 15,765 fresh rows received a second independent compact vote.
- The two compact reviewers agreed on 10,738 rows and disagreed on 5,027.
- Full-text adjudication covered 6,316 rows: every compact contradiction or insufficiency plus the conservatively quarantined batching-risk rows.
- 6,273 full-text decisions were decisive and 43 remained insufficient.

Across all 16,680 disagreements, the final audit found:

- 4,237 current labels wrong and changed to the model-predicted label.
- 12,400 model predictions wrong and the current label retained.
- 43 unresolved rows whose current labels and authority metadata remain unchanged.
- 6,364 final eligible decisions, 10,273 final ineligible decisions, and 43 unresolved decisions.

## Blindness and fail-closed controls

Compact reviewers received provider metadata, tags, channels, ticker/session context, title, teaser, and the first two sentences. They did not receive source IDs, current labels, model predictions, probabilities, split membership, prior reviews, other reviewers' votes, repository data, or internet context.

When a multi-packet turn produced measurable calibration drift, all affected rows were forced into independent QA or full-text adjudication. When the sampled fast lane failed its certification thresholds, the remaining 2,124 candidate fast-lane rows were expanded to independent QA rather than promoted.

Full-text reviewers were fresh agents that received only their assigned complete rendered source text and metadata. All 548 packet files were validated for exact identity, order, schema, label vocabulary, confidence range, rationale length, isolation attestation, and evidence-substring membership before reconciliation.

## Successor authority

The promoted authority is:

`D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_sentiment_authority_structured_rf_reverse_audit_v1`

It contains 361,695 article rows:

- 151,632 eligible
- 208,650 ineligible
- 1,413 `insufficient_short_text`

The successor applies 4,237 label changes, preserves 43 unresolved parent rows, and keeps all sentiment data byte-identical. This is correction-grade local Codex adjudication for model development, not human-certified ground truth.

## Model diagnostic after correction

The same frozen 11,434-feature structured random-forest contract was retrained on revised 2026 labels and evaluated chronologically on revised 2025 labels at the selected threshold of 0.40.

- Accuracy: 93.896%
- Balanced accuracy: 94.376%
- Eligible F1: 92.406%
- ROC AUC: 98.612%
- Disagreements: 12,443, or 6.104%

Before this audit, the corresponding figures were 91.817% accuracy and 16,680 disagreements (8.18%). The exact 4,237-disagreement reduction equals the promoted audit corrections. These post-audit figures measure agreement with labels targeted using this model's disagreements; they are not an unbiased estimate of generalization accuracy.

## Runtime evidence

Frozen audit root:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\structured_rf_reverse_disagreement_blind_audit_v1`

Post-audit model root:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\structured_metadata_rf_reverse_2026_to_2025_post_audit_v1`

The audit root contains the frozen controller, blinded packets, validated review outputs, assignment manifests, final decisions, validation report, and hash manifest. Controller validation passed for all 16,680 rows with unique source membership and complete two-vote coverage of every fresh row.

Repository validation: 523 tests passed with 175 subtests.
