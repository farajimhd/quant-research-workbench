# News Synthesis Random-Forest Challenger V1

## Outcome

The challenger combines provider metadata, causal publication/history features, structural text flags, and word TF-IDF features in a random forest. It was trained on the same 346,107 corrected 2025-2026 decisive labels used to develop the deterministic funnel and evaluated once on the same 1,000-article post-cutoff holdout.

At its frozen, validation-selected threshold, the random forest is a high-recall admission model, not a better noise filter: it found 323 of 324 eligible articles, but admitted 241 of 676 ineligible articles. The deterministic funnel made 34 more correct article decisions overall and rejected ineligible articles substantially better.

## Leakage-controlled protocol

- Fit the feature vocabulary and model specification on 203,849 discovery-2025 articles.
- Select only the probability threshold on 71,647 January-April 2026 validation articles, maximizing balanced accuracy. The selected threshold was 0.42.
- Confirm the unchanged model and threshold on 70,611 May-August 2026 articles.
- Refit the frozen specification on all 346,107 pre-cutoff articles.
- Score the existing 1,000-article post-cutoff holdout once, using its original source text and causal ticker-history state.
- Exclude label-provenance fields such as authority class, certification level, source dataset, and human-certified status.

The final sparse matrix had 139,171 columns: metadata categories and numeric values plus at most 75,000 word unigrams/bigrams. Random-forest parameters were fixed before holdout evaluation: 200 trees, depth 32, minimum leaf size 2, balanced bootstrap samples, and seed 20260821.

## Held-out comparison

Gold prevalence was 324 eligible and 676 ineligible articles.

| Metric | Deterministic synthesis | RF, frozen threshold 0.42 | RF, default threshold 0.50 |
|---|---:|---:|---:|
| Accuracy | **79.20%** | 75.80% | **80.40%** |
| Balanced accuracy | 73.53% | 82.02% | **85.10%** |
| Eligible precision | **72.66%** | 57.27% | 62.55% |
| Eligible recall | 57.41% | **99.69%** | 98.46% |
| Ineligible recall | **89.64%** | 64.35% | 71.75% |
| False eligible admissions | **70** | 241 | 191 |
| Missed eligible articles | 138 | **1** | 5 |

At the frozen 0.42 threshold, both systems were correct on 594 articles, only the RF was correct on 164, only deterministic synthesis was correct on 198, and both were wrong on 44. At 0.50, the corresponding counts were 628, 176, 164, and 32.

The 0.50 result is a secondary diagnostic, not a replacement primary result: changing the operating threshold after observing holdout outcomes would leak the holdout into model selection.

## Interpretation

The systems solve different operational problems:

- The deterministic prefilter remains the certified safe cost-saving layer. On this holdout it routed 282 articles to context-only with zero eligible false rejections.
- The RF at 0.42 is useful as a permissive eligibility detector. It nearly eliminates missed eligible news, but its false-positive load makes it unsuitable as the sole noise-removal gate.
- The disagreement is complementary enough to justify a hybrid candidate: retain deterministic hard context rules, then use the RF to rank or rescue the remaining semantic lane. That hybrid must be designed without further tuning on this released holdout and evaluated on a new later-time holdout.

This shared holdout was sealed before the RF predictions, but its aggregate deterministic result and class prevalence were already known from the preceding study. The RF hyperparameters and temporal threshold were fixed without using its item labels; nevertheless, a future hybrid needs a new holdout for an unbiased final claim.

## Runtime evidence

- Model, vectorizers, frozen manifest, and temporal report: `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\forecast_eligibility_rf_challenger_v1`
- Held-out predictions and report: `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\forecast_eligibility_rf_challenger_v1\heldout_evaluation_v1`
- Shared sealed holdout and deterministic evaluation: `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\funnel_fresh_holdout_v1`

All generated datasets and trained artifacts remain outside the repository.
