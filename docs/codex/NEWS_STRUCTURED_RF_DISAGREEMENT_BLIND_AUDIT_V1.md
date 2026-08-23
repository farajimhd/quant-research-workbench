# Structured RF Disagreement Blind Audit V1

## Outcome

The audit confirms that the structured metadata Random Forest disagreements are
high-value supervision-label review candidates, but not uniformly so. Among
1,000 prediction-blind stratified articles, two independent reviewers resolved
970. They judged the current label wrong in 741 cases and the model wrong in
229; 30 remained unresolved.

After restoring the 335-stratum population weights, the estimated current-label
error rate among resolved disagreements is **82.81%**. The approximate 95%
Wilson interval using Kish effective sample size is **80.05%-85.26%**. Treating
every unresolved weighted article as current-label-correct or wrong bounds the
full-disagreement error rate at **80.74%-83.24%**.

This estimate applies only to the 29,556 RF/current-label disagreements. It is
not the error rate of the complete 2026 label population and it is not a new
model-accuracy claim.

## Frozen sampling and blindness

The controller selected 1,000 disagreements across direction, month, RF
confidence band, and dominant channel. Every nonempty composite stratum was
represented, and population weights were retained for analysis. Reviewers saw
metadata, title, teaser, and the first three sentences, but not source IDs,
current labels, RF labels/probabilities, source split, stratum, weights, other
reviews, or aggregate statistics.

Three fixed compact reviewers supplied two independent votes for every article:

- 1,000 sampled articles;
- 2,000 compact votes in 42 bounded packets;
- 894 exact compact agreements; and
- 106 compact disagreements or `needs_full_text` decisions.

The 106 escalations received two fresh, independent full-text reviews:

- 212 full-text votes in 34 bounded packets;
- 76 two-reader decisive agreements; and
- 30 disagreements or insufficient-information outcomes preserved as
  unresolved.

All 2,212 votes passed identity/order, schema, label, reason-code, confidence,
evidence-substring, rationale-length, and isolation-attestation validation.

## Main findings

| Disagreement slice | Resolved sample | Weighted current-label error |
|---|---:|---:|
| All disagreements | 970 | 82.81% |
| Current ineligible, RF eligible | 663 | 88.35% |
| Current eligible, RF ineligible | 307 | 63.36% |
| RF confidence at least 0.90 | 278 | 97.43% |
| Priority expansion policy | 331 | 98.11% |
| Earnings channel | 290 | 94.18% |
| Guidance channel | 87 | 98.42% |
| Earnings Beats channel | 141 | 98.96% |
| Earnings Misses channel | 75 | 98.41% |
| Analyst Ratings or Price Target | 59 | 35.88% |
| Trading Ideas | 153 | 56.53% |

The priority expansion policy is:

> Current label is ineligible, RF label is eligible, and either RF confidence
> is at least 0.90 or an Earnings/Guidance/Earnings Beats/Earnings Misses channel
> is present.

The exact disagreement population contains **12,099** articles matching this
policy. Its weighted audit error estimate is **98.11%** among resolved reviews,
with an approximate 95% interval of **95.86%-99.14%**. These articles should be
the next expanded blind-review tranche. They are not authorized for automatic
correction without review.

The opposite lesson applies to analyst and price-target material. Only 35.88%
of those audited current labels were judged wrong, and the Price Target channel
alone was lower still. The RF is commonly wrong in that family, consistent with
analyst opinion being ineligible for company-event forecasting. Trading Ideas
is nearly balanced and cannot support a blanket rule.

Other useful associations include:

- 501-1,000-character articles: 98.11% weighted current-label error;
- after-hours articles: 93.95%;
- premarket articles: 85.06%;
- single-ticker articles: 87.09%;
- articles within five minutes of prior ticker news: 93.44%; and
- February 2026 disagreements: 92.38%.

These dimensions overlap and must not be summed or promoted independently
without exception review. Market-cap bands changed the rate but did not isolate
a universally safe correction rule.

## Artifacts and next action

Generated audit authority:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\structured_rf_disagreement_blind_audit_v1`

Important files:

- `FINAL_DECISIONS.jsonl`: all 1,000 reviewed decisions and votes;
- `FINAL_REPORT.json`: unweighted and direction-level reconciliation;
- `ANALYSIS_REPORT.json`: weighted population estimate and limitations;
- `AUDIT_GROUP_STATS.csv`: 509 metadata/path statistics;
- `EXPANSION_CANDIDATE_PATHS.csv`: 37 statistically stronger paths;
- `POPULATION_EXPANSION_REPORT.json`: exact full-population candidate counts;
- `PRIORITY_EXPANSION_CONTROLLER.jsonl`: the 12,099 next-review candidates;
- `VALIDATION.json`: audit invariants; and
- `HASH_MANIFEST.json`: hashes for every frozen artifact.

The audit deliberately does not modify supervision labels. The correct next
step is to create prediction-blind compact packets for the 12,099 priority
articles, excluding the 334 already sampled priority rows, promote only
independently confirmed decisions into a successor label authority, retrain the
RF from that successor, and reserve a newly frozen untouched population for the
next accuracy claim.

## Agent lifecycle

The campaign reused three already-completed compact reviewer tasks and created
two fresh full-text reviewer tasks. All five reviewer tasks completed. There
were no failed, retried, or interrupted reviewer tasks, and no reviewer spawned
children. Packet outputs were stored only under the runtime root; reviewer
responses returned bounded counts and paths rather than decision datasets.
