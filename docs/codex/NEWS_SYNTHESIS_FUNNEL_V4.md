# Deterministic News Synthesis Funnel V4

## Outcome

`news_synthesis_funnel_v4` integrates the completed provider-path exception
audit into `news_synthesis_provider_context_router_v4` and
`news_synthesis_engine_v54`. It improves cheap deterministic routing without
turning aggregate metadata paths into unconditional semantic labels.

The final label authority used for development analysis is:

`D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_sentiment_authority_provider_path_exceptions_v2`

## New deterministic path handling

Two Benzinga channel-pair predicates are admitted as hard context filters:

- `analyst ratings` plus `hot`;
- `hot` plus `price target`.

They are subset predicates, so additional channels are allowed. Both have more
than 10,000 corrected development examples, support in 2025 discovery,
January-April 2026 validation, and May-August 2026 temporal confirmation, zero
eligible examples in every period, and no remaining unaudited path exception.
Mixed provider tags retain precedence and force semantic rescue.

Two other newly important paths are deliberately not hard filters:

- `long ideas` plus `markets` is noise-dominant but retains 35 reviewed
  eligible counterexamples, so it always receives semantic rescue;
- the exact Benzinga `M&A + news`, no-tag, two-ticker signature has 329/335
  corrected eligible examples but six reviewed ineligible counterexamples, so
  it is an event prior that still requires semantic confirmation.

Temporal novelty remains trace-only. Provider absence still fails open.

## Corrected development evaluation

The v4 router was evaluated against the final 346,103 decisive 2025-August
2026 labels before opening the held-out result.

| Metric | Funnel V3 | Funnel V4 | Change |
|---|---:|---:|---:|
| Hard context articles | 128,975 | 130,710 | +1,735 |
| Estimated compute reduction | 37.2645% | 37.7662% | +0.5017 pp |
| Eligible false rejections | 0 | 0 | 0 |
| Retained eligible recall | 100% | 100% | 0 pp |

The new explicit mixed family sends 1,601 `long ideas + markets` articles to
semantic rescue. The M&A event prior sends all 335 matching articles through
semantic synthesis rather than forcing eligibility.

## Frozen 1,000-article held-out evaluation

The v4 code was evaluated once on the same prediction-blind post-authority
holdout used for Funnel V3. The sample and gold SHA-256 values are unchanged:

- sample: `1fa256b8bedf86ae1922da912e04367dbd8ae9cc4290af91a0db656420a874b1`;
- gold: `f1d520b5dd34d75b5bd0f3ea61431c8c2615f7e7e7a4d361106d74776319d996`.

| Metric | Funnel V3 | Funnel V4 |
|---|---:|---:|
| Article accuracy | 79.20% | 79.20% |
| Article balanced accuracy | 73.53% | 73.53% |
| Eligible precision | 72.66% | 72.66% |
| Eligible recall | 57.41% | 57.41% |
| Ineligible recall | 89.64% | 89.64% |
| Fast-context articles | 282 | 288 |
| Fast-context share | 28.20% | 28.80% |
| Prefilter eligible false rejections | 0 | 0 |
| Ticker-unit accuracy | 84.71% | 84.71% |
| End-to-end eligible-ticker sentiment accuracy | 44.64% | 44.64% |

Ten records changed routing or ticker/context representation. Eight known-noise
records moved from full semantic synthesis to fast context, while two records
matching the reviewed mixed `long ideas + markets` family moved from fast
context to semantic rescue. No article label changed, and all ten remained
correctly ineligible. The net compute reduction increased by six articles, or
0.6 percentage points of the holdout.

The unchanged classification metrics are expected: V4 changes path-aware
routing and semantic safeguards, not the downstream issuer-event extraction
rules. The remaining 138 eligible false negatives and 70 ineligible false
positives are full-semantic errors. Provider-path expansion alone cannot repair
them without unsafe metadata-only label forcing.

## Runtime evidence

Corrected development evaluation:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_context_router_evaluation_v4`

Frozen held-out predictions and report:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\funnel_fresh_holdout_v1\final_evaluation_v2`

Generated predictions, errors, and metrics remain outside the repository.

## Interpretation boundary

- The hard prefilter remains safe on both corrected development and the frozen
  held-out population: zero eligible false rejections.
- The 79.20% held-out article accuracy is an end-to-end deterministic-funnel
  result, not the accuracy of metadata paths alone.
- The same held-out population has now been observed for V3 and V4 and should
  not be reused to select further semantic rules. A new independently reviewed
  population is required for the next generalization claim.
- Further accuracy work belongs in issuer identity, evidence extraction,
  current-event semantics, and forecast eligibility—not broader path forcing.
