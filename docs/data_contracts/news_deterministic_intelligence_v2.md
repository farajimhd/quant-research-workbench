# Deterministic News Intelligence v2

This contract separates what the article says at publication time from what the
market does afterward. It replaces neither normalized source news nor exact
event-relative reaction labels. It builds new versioned intelligence tables on
top of those authorities and leaves all v1 products intact during validation.

## Objectives

The pipeline answers two different questions:

1. **Publication-time meaning** — Is this article company-specific,
   ticker-related, or not relevant to the requested ticker? What positive,
   negative, neutral, or mixed issuer evidence is present in the language?
2. **Expected market reaction** — Given historical articles with comparable
   semantic events, what are the calibrated probabilities of negative, neutral,
   or positive abnormal returns at each causal horizon?

Language classification is invariant across horizons. Expected reaction is
horizon- and publication-session-specific. Realized reaction is an evaluation
label, never a synonym for sentiment.

## Sources and temporal split

- Normalized metadata: `q_live.benzinga_news_normalized_v1`.
- Full normalized text: `q_live.benzinga_news_text_v1`.
- Provider ticker links: `q_live.benzinga_news_ticker_v1`.
- Issuer identity: `id_symbol_v1 -> id_listing_v1 -> id_security_v1 ->
  id_issuer_v1`, constrained by listing dates where those dates exist.
- Exact causal labels: `q_live.news_reaction_labels_v2`.
- Model training: `[2019-01-01, 2026-01-01)`.
- Locked holdout: `[2026-01-01, 2027-01-01)`.
- Blind language review: the locked 750-row Codex expert-adjudicated sample.
  This is provisional expert review, not independent human ground truth.

The reaction source already contains event-relative 1m, 5m, 10m, 30m, 1h,
2h, 3h, end-of-premarket, end-of-market-hours, and end-of-after-hours labels.
This v2 job does not query fixed-clock intraday bars and does not rebuild prices.

## Output products

| Table | Grain | Responsibility |
| --- | --- | --- |
| `news_ticker_identity_alias_v2` | ticker + issuer/listing | Reusable ticker-to-issuer names and listing validity. |
| `news_ticker_relevance_v2` | news + requested ticker | `company_specific`, `ticker_related`, or `not_relevant`, with observable evidence. |
| `news_semantic_event_dictionary_v2` | semantic event | Canonical event family, direction, strength, materiality, certainty, time orientation, and phrase variants. |
| `news_semantic_event_features_v2` | news + ticker + event | One event-presence fact with source provenance and ticker-scoping state. Repeated occurrences are not retained. |
| `news_language_assessment_v2` | news + ticker | Horizon-invariant language class, continuous score, separate evidence masses, mixedness, and evidence IDs. |
| `news_reaction_scale_v2` | ticker/global + horizon + session | Training-only robust abnormal-return center and MAD scale with ticker-to-global shrinkage. |
| `news_reaction_baseline_v2` | horizon + session | Smoothed class priors and normalized return expectations. |
| `news_reaction_event_effects_v2` | event + horizon + session | Empirical-Bayes posterior probabilities, log effects, reliability, and normalized target/high/low effects. |
| `news_reaction_predictions_v2` | holdout news + ticker + horizon | Calibrated class probabilities and expected target/high/low abnormal returns. |
| `news_language_review_v1` | locked review item | Review provenance and labels, imported only from the immutable reviewed CSV. |
| `news_reaction_model_status_v2` | stage + bounded chunk | Durable running/completed/failed state and row counts. |

## Relevance authority

Provider ticker links define candidates, not relevance truth. For every
news/ticker pair, the classifier checks exact ticker and issuer-name mentions,
direct issuer-event language, ticker count, and roundup/analyst/mover metadata.

- `company_specific`: the issuer or ticker is identified, direct issuer-event
  language is present, and the item is not a roundup, analyst summary, mover
  story, or multi-ticker synthesis.
- `ticker_related`: the ticker is genuinely discussed but the article is
  analysis, comparison, roundup, market reaction, or multi-company coverage.
- `not_relevant`: neither the requested ticker nor its issuer is evidenced in
  the supplied text/metadata. This catches provider-link contamination and
  ticker-word collisions.

For `ticker_related` articles, body and external text are split into clauses.
Only clauses containing the exact ticker or issuer alias may generate semantic
events. Company-specific articles may use the complete article. This prevents a
positive event for company A from being projected onto company B merely because
both tickers share one article.

## Structured language composition

The dictionary models event families rather than a flat word list: earnings,
guidance, capital allocation, financing, mergers/acquisitions, contracts,
products, regulatory/clinical, legal, management, operations, credit/solvency,
analyst actions, and market reaction. Each event records:

- direction: negative, neutral, or positive;
- strength, materiality, and certainty;
- historical, current, forward, or structural orientation;
- title, teaser, scoped-body, and metadata provenance.

Source and orientation multipliers produce an event mass. Within one article,
only the strongest event for each `(family, direction)` contributes. This avoids
double counting correlated phrases such as “raises outlook” and “raises
guidance.” Positive and negative masses are retained independently:

```text
score     = (positive_mass - negative_mass) /
            max(positive_mass + negative_mass, epsilon)
mixedness = min(positive_mass, negative_mass) /
            max(positive_mass, negative_mass, epsilon)
```

Strong evidence on both sides yields `mixed`; it is not forced into neutral.
Explicit negation is handled before composition. For example, “no safety
concern” cannot contribute the ordinary negative safety-event direction.

## Reaction labels and model

A fixed return threshold is not comparable across 1 minute, 3 hours, tickers,
or sessions. The v2 class target normalizes the existing abnormal return using a
training-only robust scale:

```text
global_scale = 1.4826 * MAD(abnormal_return)
ticker_weight = n / (n + scale_shrinkage)
scale = ticker_weight * ticker_scale + (1 - ticker_weight) * global_scale
z = abnormal_return / scale
```

Sparse ticker scales fall back toward the horizon/session global scale. The
default class boundary is `z = +/-0.5`.

The model is an interpretable empirical-Bayes additive log-odds classifier.
For an event with class counts `c_k`, baseline probability `p_k`, and prior
strength `a`:

```text
posterior_k = (c_k + a * p_k) / (n + a)
reliability = n / (n + a)
log_effect_k = reliability * log(posterior_k / p_k)
```

At inference, the strongest learned event per family is selected, family log
effects are added to the horizon/session baseline, and a softmax produces three
probabilities. Expected terminal/high/low returns use the same shrunk normalized
effects and are converted back using the requested ticker scale. This is
deterministic, fast, auditable, and does not require embedding or LLM inference.

## Evaluation

The 2026 holdout reports:

- reaction multiclass log loss and Brier score;
- accuracy, balanced accuracy, macro F1, class-level precision/recall/F1;
- horizon/session/predicted-class calibration bins;
- relevance accuracy and macro F1 against the locked review;
- language accuracy and macro F1 against relevant locked-review rows.

The language evaluation never reads future returns. Reaction evaluation never
claims to measure language-sentiment accuracy.

## Execution and recovery

The launcher defaults to a read-only plan:

```powershell
python pipelines\news\benzinga\run_news_reaction_deterministic_v2.py
```

Execute after reviewing the plan:

```powershell
python pipelines\news\benzinga\run_news_reaction_deterministic_v2.py --execute `
  --review-labels-csv D:\market-data\prepared\news_reaction_labels\finalize_20260721_124909\codex_review_20260721\human_review_sample_codex_reviewed.csv
```

Two bounded workers are the safe default. Each worker owns one calendar month:
it replaces that incomplete v2 slice, writes relevance, writes scoped events,
writes language assessments, verifies row equality, and only then records the
month complete. A failed month is retained as failed and is safely rebuilt on
restart. Scale, training, prediction, and evaluation are global versioned
stages that run only after extraction. `--rebuild` explicitly replaces already
completed v2 units. Memory, query threads, workers, and terminal refresh are all
bounded.

No command in this workflow modifies a v1 table.
