# News Forecast Funnel and Manual LLM Review

## Shipped operating contract

Text Intelligence is the single routing authority for each canonical News
revision. News Synthesis produces a structured, evidence-preserving view for
every revision, but its eligibility interpretation is informational only: it
cannot reject a document, suppress DeepFM, or authorize an LLM call. It cannot
generate a signal independently; the sole protected exception combines positive
issuer direction with a DeepFM-eligible decision for the same canonical event.
Every revision is scored by the hash-pinned DeepFM serving release.
DeepFM is the sole live forecast-eligibility authority at the configured
operating threshold, currently `0.5`; only DeepFM-eligible candidates may reach
an issuer LLM review.

The default trigger mode is `manual`. The frontend requests review for a
specific canonical News identity and publication timestamp. Text Intelligence
loads the canonical source, calls Model Gateway through
`news.issuer_review.v1`, validates the versioned structured response, persists
the issuer labels, and requests contextual forecasts from News Hypothesis for
eligible issuers. Setting both Text Intelligence and News Hypothesis trigger
modes to `automatic` activates the same tested path without changing its
contracts; the production launcher leaves both manual.

## Authority and persistence

| Concern | Authority | Durable tables |
| --- | --- | --- |
| Informational synthesis | Text Intelligence / News Synthesis V1 | `q_live.news_synthesis_v1` |
| Funnel decision | Text Intelligence / promoted DeepFM release | `q_live.news_forecast_funnel_v1` |
| Issuer labels and article-language implication | Text Intelligence via Model Gateway | `q_live.news_llm_issuer_review_v1`, `q_live.news_llm_issuer_review_history_v1` |
| Causal market-reaction hypotheses | News Hypothesis via Model Gateway | `q_live.news_market_hypothesis_v1`, `q_live.news_market_hypothesis_history_v1` |
| Provider audit, idempotency, budget, and cost | Model Gateway | existing Model Gateway audit authority |
| Signal evaluation | backend News Signal runtime and configured Rule Set | existing Signal Stream authority |

Latest-state tables support fast UI reads. Append-only history tables retain
every completed LLM result. Provider, model, prompt/schema/contract versions,
source hash, tokens, cost, latency, and trigger mode are persisted. Failed work
remains visible in current state and is never presented as a completed label.

## Causal context

News Hypothesis freezes context at the source publication time. It includes
bounded price-action summaries for recent sessions, the publication-time market
session, point-in-time fundamentals, recent SEC filing titles/forms and any
available SEC labels, and recent prior News. Missing inputs remain marked
unavailable. The existing SEC Canvas/filing-label authority is reused; a broader
future SEC synthesis product is intentionally outside this implementation.

## Signal integration

Funnel and LLM outputs are registered Data Fields. The live News event loader
merges deterministic and LLM events, projects configured fields, and evaluates
every enabled `source_type=news_events` Signal Stream through its Rule Set.
Manual labels therefore generate signals when—and only when—an enabled stream's
configured rules match. Automatic labels use the identical event contract.
News Synthesis fields remain available for presentation and comparison and are
excluded from forecast-eligibility authority. Signal decisions reject synthesis-
only rules; the protected Bullish Synthesis + DeepFM stream requires both positive
issuer direction and DeepFM eligibility on the same canonical event.

## Operations

Promote the serving artifacts with
`scripts/promote_news_forecast_funnel.py`. Validate the exact release against
canonical data with `scripts/validate_news_forecast_funnel.py`; validate durable
state with `scripts/validate_news_review_persistence.py`. The standard live
gateway lifecycle starts Model Gateway, News Hypothesis, and Text Intelligence.
Automatic mode remains disabled unless an operator explicitly changes both
trigger-mode environment variables.
