# Deterministic News Synthesis Funnel V5

## Outcome

Funnel V5 promotes two causal market-cap context rules into the cheap deterministic gate. On the 346,103-article corrected development population, hard-context routing increased from 130,710 to 132,599 articles while retaining every eligible label. The estimated share avoiding full issuer synthesis increased from 37.7662% to 38.3120%.

The prior 1,000-article holdout is now an observed regression population, not a fresh accuracy set. V5 routed seven additional articles through fast context; all seven were gold-ineligible. End-to-end article accuracy remained 79.20%, balanced accuracy remained 73.53%, and eligible prefilter false rejections remained zero.

## Blind exception audit

The audit froze the 26 currently eligible exceptions in the high-precision market-cap candidate union. Reviewers received only provider metadata, title, teaser, and the first three sentences; labels, paths, statistics, and model outputs were hidden.

- Two independent compact readers produced 52 decisions.
- Twenty-one articles received two eligible votes.
- Five articles were escalated because of disagreement or a proposed ineligible decision.
- Two independent full-text readers disagreed on all five escalations.
- The fail-safe adjudication preserved the parent eligible label for every disagreement.
- Final result: 26 eligible, zero confirmed corrections, and no successor label authority.

This outcome blocks the broader nano/micro reiteration rule: its exceptions remain plausible issuer events and cannot be converted into deterministic noise.

## Promoted rules

Only the two rules with zero eligible exceptions in discovery 2025, validation January-April 2026, and final May-August 2026 were promoted:

| Rule | Development support | Eligible conflicts | V5 context family |
|---|---:|---:|---|
| Exact observed cap set is nano + micro + small and channel contains `movers` | 1,193 | 0 | `small_issuer_multi_band_mover` |
| Maximum observed cap is micro and the article names more than 10 tickers | 713 | 0 | `micro_cap_many_ticker_list` |

Their incremental article union is 1,889 because 17 articles satisfy both rules.

The router accepts market-cap evidence only when every nonmissing value is positive, uses a known six-band bucket, and has an availability timestamp strictly before publication. Invalid, noncausal, or absent cap evidence fails open to the existing semantic route.

## Evaluation

| Metric | Funnel V4 | Funnel V5 | Change |
|---|---:|---:|---:|
| Development articles | 346,103 | 346,103 | 0 |
| Hard context | 130,710 | 132,599 | +1,889 |
| Estimated full-synthesis reduction | 37.7662% | 38.3120% | +0.5458 pp |
| Eligible false rejections | 0 | 0 | 0 |
| Retained eligible recall | 100% | 100% | 0 pp |
| Semantic rescue | 10,869 | 9,014 | -1,855 |

Observed holdout regression:

| Metric | Funnel V4 | Funnel V5 |
|---|---:|---:|
| Articles | 1,000 | 1,000 |
| Fast context | 288 | 295 |
| Article accuracy | 79.20% | 79.20% |
| Balanced accuracy | 73.53% | 73.53% |
| Eligible prefilter false rejections | 0 | 0 |

The holdout had strictly-prior provider cap context for 828 of 1,000 articles. The seven changed routes preserved the same correct ineligible article label; only analysis depth changed from full semantic to fast context.

## Production behavior

The historical backfill now attaches point-in-time provider snapshots before routing and includes that context in its source revision. Its query is bounded to one pre-day snapshot per ticker plus same-day changes. A dry run over 2026-08-20 processed 832 of 832 articles successfully.

Provider snapshots begin in May 2026. Older production backfills fail open when snapshots are absent; the development evaluation uses the separately certified SEC-shares-times-prior-close fallback. Therefore historical compute reduction before snapshot coverage can be lower than this research evaluation, but eligibility safety is preserved.

## Runtime authority

- Blind audit: `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\market_cap_high_precision_exception_blind_audit_v1`
- Development evaluation: `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_context_router_evaluation_v5_final`
- Observed holdout regression: `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\funnel_fresh_holdout_v1\final_evaluation_v5_market_cap_regression`

No fresh accuracy claim is made. A future accuracy release requires a newly frozen, independently reviewed population that has not informed rule selection.
