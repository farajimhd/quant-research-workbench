# Structured Metadata Random Forest V1

## Outcome

`news_structured_metadata_rf_v1` is an independent eligibility challenger. It
does not call or encode the deterministic News Synthesis router and it does not
use TF-IDF, rendered text, exact ticker identity, source identifiers, label
provenance, or certification fields as model inputs.

The final model trained on all 203,847 decisive 2025 articles and was evaluated
once on the chronological 142,256-article 2026 population. At the threshold
selected on November-December 2025, it achieved 79.22% accuracy, 80.94%
balanced accuracy, 74.06% eligible F1, and 0.8475 ROC AUC. This is forward
temporal evidence, but not a pristine release holdout: the 2026 labels have
already informed earlier News Synthesis research and audits.

## Frozen feature contract

The historical catalog scans Benzinga categories from 2010-01-01 through
2025-12-31. It records 145,430 normalized category rows: 145,112 tags, 125
channels, 33 content-quality flags, one provider, and 159 derived 2025
categories. Only categories observed in labeled 2025 articles receive learned
dimensions. Categories known historically but absent from 2025 and categories
first seen after training map to separate unknown buckets.

The 11,434 model dimensions comprise:

- provider, tag, channel, and content-quality multi-hot indicators;
- Eastern-time hour, weekday, month, and session segment;
- ticker count, article character length, session ordinal, first-news flags,
  and time since the prior ticker article;
- point-in-time market-cap coverage, source, age, buckets, counts, fractions,
  and log-scaled aggregates; and
- twelve bounded lexical event indicators such as price target, analyst rating,
  halt, material event, earnings preview, and market recap. These are structured
  flags, not router decisions or synthesis outputs.

The matrices contain 9,086,121 nonzeros for training and 6,384,838 for test.
The observed windows are `2025-01-01T00:50:03Z` through
`2025-12-31T23:45:28Z` and `2026-01-01T00:50:04Z` through
`2026-08-13T21:04:05Z`.

## Model selection and metrics

Four bounded Random Forest configurations were compared using January-October
2025 for development training and November-December 2025 for threshold and
configuration selection. The winner used 400 trees for the final refit,
unlimited depth, one-sample leaves, `log2` feature sampling, 80% bootstrap
samples, balanced-subsample class weights, and seed `20260822`. Its selected
eligible threshold was 0.43.

| Metric | Threshold 0.43 | Threshold 0.50 |
|---|---:|---:|
| Accuracy | 79.22% | 79.56% |
| Balanced accuracy | 80.94% | 80.33% |
| Eligible precision | 64.78% | 66.14% |
| Eligible recall | 86.42% | 82.81% |
| Eligible F1 | 74.06% | 73.54% |
| Ineligible recall | 75.46% | 77.86% |
| ROC AUC | 0.8475 | 0.8475 |

At 0.43, the confusion matrix is 70,519 true ineligible, 22,929 false eligible,
6,627 false ineligible, and 42,181 true eligible. The model disagrees with
29,556 current labels (20.78%): 22,929 current-ineligible/model-eligible and
6,627 current-eligible/model-ineligible. There are 9,824 disagreements beyond
0.90 or below 0.10, but these are review candidates, not automatic label
corrections. The top probability bin is materially overconfident, with a 63.25%
observed eligible rate despite a 94.95% mean predicted probability.

Performance changes substantially over time: balanced accuracy is 84.65% in
January, 79.18% in February, 79.35% in March, 77.71% in April, 75.24% in May,
79.71% in June, 85.25% in July, and 91.38% in August. This drift is evidence
against treating one aggregate score as a stationary production guarantee.

## Feature strength findings

Grouped permutation importance uses a deterministic 4,000-article 2026 sample
and measures the balanced-accuracy loss after permuting a complete feature
family:

| Feature family | Dimensions | Balanced-accuracy drop |
|---|---:|---:|
| Provider channels | 119 | 6.258 pp |
| Source quality | 34 | 1.252 pp |
| Lexical flags | 12 | 0.922 pp |
| Publication time | 61 | 0.489 pp |
| Ticker history | 28 | 0.422 pp |
| Provider tags | 11,033 | 0.202 pp |
| Ticker structure | 10 | 0.067 pp |
| Market cap | 124 | 0.010 pp |
| Article shape | 10 | -0.037 pp |

Channels are the strongest portable metadata family. Price-target and analyst
rating signals, article length, time of publication, and time since prior ticker
news rank highly by impurity importance. The very large tag dictionary adds
only modest out-of-time incremental value as a group, consistent with sparse
and drifting entity-like tags. Market cap is not a strong global standalone
separator after correlated context is present, although cap-specific slices
show materially different error profiles and can still support interactions or
strict deterministic rules. Impurity importance is associative and biased
toward continuous or high-cardinality features; grouped permutation is the
preferred family-level reading.

## Authority and reproduction

Durable implementation:

- `research/text_intelligence/news_synthesis_v1/structured_metadata_rf.py`
- `research/text_intelligence/news_synthesis_v1/run_structured_metadata_rf.py`
- `research/text_intelligence/news_synthesis_v1/test_structured_metadata_rf.py`

Generated model, matrices, predictions, reports, validation, and hashes are
stored only beneath:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\structured_metadata_rf_v1`

Run the three immutable stages in order:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.news_synthesis_v1.run_structured_metadata_rf build
python -m research.text_intelligence.news_synthesis_v1.run_structured_metadata_rf train-evaluate
python -m research.text_intelligence.news_synthesis_v1.run_structured_metadata_rf validate
```

The previous within-day metadata-only RF result (86.57% accuracy and 87.44%
balanced accuracy) is an interpolation experiment with a different feature
contract and exact ticker identity, so it must not be compared as if it were the
same test. Likewise, Funnel V5's 1,000-article observed regression result is a
different deterministic system and an already-observed population.
