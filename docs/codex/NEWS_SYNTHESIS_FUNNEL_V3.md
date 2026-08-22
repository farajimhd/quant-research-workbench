# Deterministic News Synthesis Funnel V3

> Superseded by `NEWS_SYNTHESIS_FUNNEL_V4.md`, which incorporates the completed
> provider-path exception audit and reevaluates the frozen fresh holdout.

## Outcome

`news_synthesis_funnel_v3` is the production orchestration contract around `news_synthesis_provider_context_router_v3` and `news_synthesis_engine_v53`. It provides an explicit final decision for every source:

| Final lane | Forecast label | Work performed | Preservation |
|---|---|---|---|
| `forecast_event` | `eligible` | Full issuer-semantic synthesis | Forecast synthesis document and ticker labels |
| `context_only` | `ineligible` | Metadata fast path or full semantic rejection | Context family, reason codes, ticker/context sentiment labels |
| `insufficient_information` | `insufficient_information` | No synthesis | Explicit failure label; source is not silently dropped |

The durable `q_live.news_synthesis_funnel_v1` table records the versioned prefilter decision, final lane, eligibility, analysis depth, context-preservation flag, reason codes, and ticker labels. Full semantic documents continue in `q_live.news_synthesis_v1`.

Historical backfill and canonical live processing now pass the provider field into the router, hash every routing/semantic input used by live reconciliation, persist every funnel result, and avoid downstream live market inference for forecast-ineligible rows.

## Certified deterministic filter

Rules are Benzinga-scoped and fail open when provider authority is absent. Hard context routing includes the previously certified provider tags plus V3's corrected-authority zero-exception families: automated analyst action, short-interest history, unusual options, scheduled preview, RSI/overbought/oversold screens, hypothetical dividend screens, options-only channels, and six exact high-support analyst channel signatures.

Mover-only, earnings-recap, market-update, broad analyst, and `most accurate analysts` families remain `semantic_rescue_required`; blind exception review confirmed real issuer events inside them.

The admission/stopping rule for new hard filters was:

- provider-authoritative exact metadata semantics;
- at least 1,000 corrected examples overall;
- support in 2025 discovery, January-April 2026 validation, and May-August 2026 temporal confirmation;
- zero eligible examples in every period;
- no retained eligible exception after prediction-blind review.

No remaining nonredundant candidate met all criteria. Lower-support zero-count patterns were not promoted because their uncertainty is too large; high-coverage patterns with exceptions were kept in semantic rescue.

## Corrected 2025-2026 evaluation

Authority: 346,107 corrected decisive articles (126,027 eligible; 220,080 ineligible).

- Hard context route: 128,975 articles (37.2645% estimated expensive-compute reduction).
- Corrected eligible false rejections: 0.
- Retained eligible recall at the prefilter: 100%.
- Semantic rescue: 9,268 articles (2.6778%).
- Wilson 95% interval for hard-route ineligible precision: 99.9970%-100%.

This is temporal development/validation evidence, not the final held-out estimate.

## Fresh held-out evaluation

The holdout was frozen before labels or predictions from 4,972 articles published after the corrected-authority cutoff. A deterministic SHA-256 sample selected 1,000 articles with zero development-authority overlap. Gold was produced prediction-blind by two independent readers; a third reader adjudicated every disagreement in the complete article/ticker/context/sentiment vector. Article eligibility agreement before adjudication was 95.3%. Gold contained 324 eligible and 676 ineligible articles.

The one-time frozen evaluation produced:

| Metric | Result |
|---|---:|
| Article accuracy | 79.20% |
| Article balanced accuracy | 73.53% |
| Eligible precision | 72.66% |
| Eligible recall | 57.41% |
| Ineligible recall | 89.64% |
| Fast-context share | 28.20% |
| Prefilter eligible false rejections | 0 |
| Full-semantic lane accuracy | 71.03% |
| Ticker-unit accuracy (1,884 units) | 84.71% |
| Sentiment accuracy when an eligible ticker was detected | 82.35% |
| End-to-end eligible-ticker sentiment accuracy | 44.64% |

The prefilter achieved its primary safety objective on fresh data: it skipped full synthesis for 282 articles without rejecting any of the 324 held-out eligible articles. Remaining error is concentrated downstream in deterministic semantic eligibility, especially recall; the system is complete as a versioned deterministic funnel, but it is not a perfect classifier.

## Runtime evidence

- Corrected feature audit: `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_filter_feature_audit_v3_corrected`
- Blind rule-exception audit: `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_context_rule_audit_v1`
- V3 corrected-authority evaluation: `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_context_router_evaluation_v3`
- Sealed holdout, blind gold, predictions, and final report: `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\funnel_fresh_holdout_v1`

The final sample, gold, and prediction hashes are recorded in the runtime manifests. Generated datasets and reports remain outside the repository.
