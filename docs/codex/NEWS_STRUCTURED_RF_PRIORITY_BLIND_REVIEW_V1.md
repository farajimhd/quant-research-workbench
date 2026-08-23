# Structured RF Priority Blind Review V1

## Outcome

The 12,099 current-ineligible/model-eligible priority disagreements are now covered by a completed blind-review campaign. The campaign reused 334 rows already adjudicated in the preceding 1,000-row calibration audit and newly reviewed the remaining 11,765 rows.

Across the complete 12,099-row priority population:

- 11,868 were adjudicated eligible, contradicting the prior ineligible label.
- 223 were adjudicated ineligible, confirming the prior label.
- 8 remain unresolved and retain their parent label.

The newly reviewed tranche produced 11,547 label changes, 213 confirmed ineligible labels, and 5 unresolved rows.

## Blind adaptive funnel

Reviewers never received source IDs, current labels, RF predictions, RF probabilities, calibration results, population statistics, or other reviewers' work.

1. One compact blind pass reviewed all 11,765 new rows using provider metadata, channels, tags, ticker/session features, title, teaser, and the first two sentences.
2. A second independent compact pass reviewed 1,763 rows: all 629 non-eligible, ambiguous, or low-confidence primary decisions plus a deterministic stratified sample of 1,134 high-confidence eligible decisions.
3. The fast lane certified at 99.47% agreement, with a 98.85% Wilson 95% lower bound and zero `needs_full_text` votes in the QA sample.
4. Only 432 compact contradictions or explicit insufficiency cases received fresh full-text review. Five remained insufficient after full text.

The new tranche used 13,960 validated votes instead of applying two or three reviews to every row:

- 11,765 primary compact votes
- 1,763 independent compact QA votes
- 432 fresh full-text votes

## Correction-grade authority

The successor authority is:

`D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_sentiment_authority_structured_rf_priority_v1`

It contains 361,695 article labels:

- 148,675 eligible
- 211,607 ineligible
- 1,413 `insufficient_short_text`

The authority inherits the previously promoted 1,000-row audit, adds 11,547 new label changes, preserves five unresolved parent labels, and leaves all 16,983 sentiment rows byte-identical. It is correction-grade Codex adjudication for model development, not human-certified ground truth.

## Runtime evidence

The frozen audit root is:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\structured_rf_priority_blind_review_v1`

Key evidence includes `FINAL_REPORT.json`, `FINAL_DECISIONS.jsonl`, `VALIDATION.json`, `HASH_MANIFEST.json`, all blinded packets, and all validated review outputs. Independent successor verification found 361,695 article rows, 11,765 new ledger rows, zero hash mismatches, and validation status `passed`.

Repository validation: 518 tests passed with 175 subtests.
