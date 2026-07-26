# V16 Error Study Guide

The V16 error study diagnoses why held-out news decisions fail. It does not
train a new model, tune a confidence threshold, or convert 2026 back into an
untouched test set.

## Decision and realized-outcome contracts

For every article, each available authoritative horizon contributes the
argmax class exported by the official V16 evaluator:

- `no_meaningful_opportunity`
- `upside_dominant`
- `downside_dominant`

The consolidated prediction is hard plurality. An exact tie becomes no
position. Vote confidence is:

```text
winning votes / number of available authoritative horizons
```

Realized positive and negative excursions are made comparable across horizons:

```text
up_strength(h)   = max(high_return(h), 0) * 100 / minimum_span_pct(h)
down_strength(h) = max(-low_return(h), 0) * 100 / minimum_span_pct(h)
```

The article uses the maximum strength observed across available horizons. If
neither side exceeds one, the realized outcome is no opportunity. Otherwise,
the larger side determines direction. An exact tie abstains.

The current evaluator exports only horizons with authoritative realized labels
and anchors. Therefore this is a held-out diagnostic over evaluable horizons,
not a simulation in which all ten production heads are necessarily actionable.

## Automatic error taxonomy

| Code | Predicted | Realized |
| --- | --- | --- |
| `false_long` | Upside | Downside |
| `false_short` | Downside | Upside |
| `missed_upside` | No position | Upside |
| `missed_downside` | No position | Downside |
| `false_opportunity` | Long or short | No meaningful opportunity |
| `correct` | Same class | Same class |

These attributes remain separate from the primary taxonomy:

- `two_sided_actual`: both standardized positive and negative excursions
  exceed their meaningful thresholds;
- `horizon_prediction_conflict`: at least one head predicts upside and another
  predicts downside;
- `horizon_actual_conflict`: authoritative horizon labels contain both
  directions;
- `timing_mismatch`: the consolidated decision is wrong, but at least one
  authoritative horizon realizes the predicted direction.

This separation prevents a volatile two-sided path from being mislabeled as a
fourth trading action while preserving the evidence that direction alone may
be ill-posed.

## Human review protocol

`human_review_sample.csv` contains up to 100 deterministic cases from each:

1. confident false long;
2. confident false short;
3. missed upside;
4. missed downside;
5. correct high-confidence decision;
6. two-sided or horizon-conflict case.

Review the title, source metadata, nearest training neighbors, and eligible
trade path together. Assign:

### Primary reason

- `label_clean_model_error`
- `two_sided_path`
- `timing_or_horizon_mismatch`
- `concurrent_same_ticker_news`
- `movement_started_before_publication`
- `broad_market_or_sector_move`
- `illiquid_or_sparse_prints`
- `outlier_or_conditioned_trade`
- `halt_or_session_boundary`
- `corporate_action_or_split`
- `missing_company_or_supply_context`
- `ambiguous_or_insufficient_text`
- `source_or_timestamp_problem`
- `other`

### Label quality

- `clean`
- `directionally_ambiguous`
- `timing_ambiguous`
- `market_contaminated`
- `source_suspect`
- `invalid`

Use the secondary reason only when two independent mechanisms are materially
supported. Do not infer who bought or sold from price action alone.

## Statistical interpretation

`slice_metrics.csv` suppresses slices below the configured minimum support and
reports a 95% Wilson interval for accuracy. A slice is a diagnostic lead only
when:

- it has adequate support;
- its interval and effect size are material;
- the result is coherent in adjacent or related slices;
- a causal mechanism is visible in reviewed cases.

Do not select a threshold or architecture on 2026 and then report its 2026
metric as new out-of-sample performance. After this study, freeze changes using
2019-2025 or a development subset and evaluate on a later untouched period.

## Embedding neighbors

The neighbor stage applies a deterministic random projection to bound search
cost over the 2019-2025 corpus, retains a candidate set, and reranks candidates
using exact cosine similarity in the original 3,072-dimensional OpenAI
embedding space. Projection scores are never reported as final similarity.

Conflicting reactions among close textual neighbors indicate that text alone
does not identify the outcome. Inspect stock state, supply context, market
regime, prior news, and concurrent events before changing embeddings.

## Price-path evidence

`price_paths.jsonl.gz` uses the shared V16 condition-aware event query. Each
case contains eligible-trade one-minute OHLCV, trade and quote counts, exchange
session segment, distance from publication, and a deterministic large-jump
flag. This evidence can expose:

- pre-publication movement;
- spike-and-fade or dip-and-recovery paths;
- extended-hours discontinuities;
- sparse or implausible prints;
- reaction timing outside the winning vote horizon.

Minute bars support diagnosis but cannot prove participant identity or intent.
