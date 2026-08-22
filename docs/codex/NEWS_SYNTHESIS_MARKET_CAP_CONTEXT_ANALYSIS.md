# News Synthesis Market-Cap Context Analysis

## Outcome

Market capitalization materially changes several metadata, text-shape, ticker-count, timing, and ticker-history paths, but it is not a safe global noise rule. The completed causal V3 analysis evaluated all 346,103 decisive 2025-through-August-2026 articles and retained 168 statistically gated interactions where a cap-conditioned path became safer than its unconditioned context.

This work does not promote those interactions into Funnel V4. The existing 1,000-article holdout has already been observed, so candidate exceptions require blind review and any production successor requires a fresh held-out population.

## Authority and causality

For every article ticker, the analysis selects market cap strictly from information available before publication:

1. Prefer a provider-published `q_live.market_security_market_snapshot_v1` observation whose observation and insertion timestamps are both earlier than the article. Resolve by point-in-time `symbol_id` first and use ticker only as a recorded fallback.
2. When no provider snapshot is available, estimate capitalization from the latest SEC-reported common shares available before publication multiplied by the latest completed daily close.
3. Preserve missing identity, shares, or price as explicit missingness. Never substitute a current market cap for an historical article.

The provider snapshot authority begins on 2026-05-20, so most earlier covered rows use the explicitly marked estimate. Across 45,173 ticker-article observations where both methods were available, the estimate agreed with the provider snapshot in the same six-band bucket 89.92% of the time and within one adjacent bucket 98.43% of the time. The geometric mean absolute difference factor was 1.47, so the estimate is suitable for coarse research buckets but not interchangeable with an exact provider value.

## Coverage

| Split | Complete | Partial | Missing | No tickers | Complete or partial |
|---|---:|---:|---:|---:|---:|
| Discovery 2025 | 110,332 | 18,124 | 70,769 | 4,622 | 128,456 |
| Validation Jan-Apr 2026 | 40,180 | 6,677 | 23,062 | 1,726 | 46,857 |
| Final May-Aug 2026 | 49,799 | 6,107 | 12,763 | 1,942 | 55,906 |

The run resolved 11,974 distinct article tickers, 5,304 bridge tickers, 3,779 SEC share CIKs, and 5,346 provider snapshot symbols. Ticker-level selections comprised 287,885 derived values, 61,032 provider snapshots, and 231,275 explicit missing values.

## Feature space

- 95 market-cap paths
- 193,523 unconditioned metadata, text-shape, timing, and ticker-history paths
- 2,111,248 market-cap interactions
- 168 non-redundant candidates where market cap improved the underlying path
  - 57 high-precision candidates
  - 49 promising candidates
  - 62 audit candidates

The market-cap bands are nano below $50 million, micro from $50 million to $300 million, small from $300 million to $2 billion, mid from $2 billion to $10 billion, large from $10 billion to $200 billion, and mega at or above $200 billion.

## Main findings

Market cap alone is not a noise filter. Single-band paths for nano and micro issuers have higher eligible rates than large and mega issuers in this supervision authority. The useful signal appears in interactions and multi-ticker composition.

Representative stable paths include:

| Conditioned path | Support | Eligible | Discovery rate | Validation rate | Final rate |
|---|---:|---:|---:|---:|---:|
| Maximum cap is micro and ticker count is greater than 10 | 713 | 0 | 0% | 0% | 0% |
| Cap set spans nano, micro, and small and channel is `movers` | 1,193 | 0 | 0% | 0% | 0% |
| Same cap set and rendered text is 1,001-2,500 characters | 1,181 | 1 | 0.14% | 0% | 0% |
| Same cap set and article is after-hours | 407 | 0 | 0% | 0% | 0% |
| Same cap set and prior ticker news was 1-4 hours earlier | 412 | 0 | 0% | 0% | 0% |
| Contains nano/micro and channel is `reiteration` | 1,047 | 7 | 0.77% | 0% | 0.71% |
| Mid-cap-only and deterministic text indicates price target | 24,327 | 353 | 1.04% | 2.44% | 1.64% |

The strongest result is the interaction between cap composition and multi-ticker/list-style metadata. A news item spanning many nano, micro, and small issuers behaves very differently from a single-issuer nano-cap event. This explains why a global small-cap rejection would be incorrect while cap-conditioned mover, length, timing, and ticker-count paths are promising.

## Candidate union

Overlapping path supports were reconciled at the article level:

| Candidate scope | Unique articles | Currently eligible exceptions | Eligible rate |
|---|---:|---:|---:|
| Opened precision paths | 42,851 | 537 | 1.25% |
| Opened audit paths | 14,935 | 410 | 2.75% |
| All candidates | 47,353 | 791 | 1.67% |

The high-precision subset alone covers 6,015 unique articles and contains 26 currently eligible exceptions. Those exceptions are the smallest defensible first blind-review tranche. Aggregate rates do not authorize automatic correction or production rejection.

## Runtime authority

Bulk outputs are stored outside the repository at:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_market_cap_context_analysis_v3`

Important files are:

- `ARTICLE_MARKET_CAP_FEATURES.jsonl`: per-article and per-ticker cap values, provenance, ages, and aggregates.
- `MARKET_CAP_PATH_STRENGTH.csv`: unconditioned market-cap paths.
- `CONTEXT_PATH_STRENGTH.csv`: unconditioned comparison paths.
- `MARKET_CAP_INTERACTION_STRENGTH.csv`: every cap-conditioned interaction with its unconditioned baseline.
- `CANDIDATE_INTERACTIONS.csv`: the 168 incremental candidates.
- `CANDIDATE_ARTICLES.jsonl`: deduplicated candidate membership and current labels.
- `REPORT.md`, `REPORT.json`, `VALIDATION.json`, and `HASH_MANIFEST.json`: summary, validation, and provenance.

## Required next gate

Blindly review the 26 currently eligible exceptions in the high-precision candidate union using metadata, title, teaser, and first three sentences, escalating to full text only when compact evidence is insufficient. Collapse logically equivalent paths before routing changes. After review, freeze the accepted generic rules and evaluate them on a newly sampled, independently reviewed holdout rather than the previously observed 1,000 articles.

## Gate result

The blind audit completed with 52 compact votes and 10 full-text votes. Twenty-one exceptions were independently preserved from compact evidence; two full-text readers disagreed on all five escalations, so the fail-safe policy preserved their eligible labels. No supervision label changed.

Funnel V5 consequently promoted only the two pre-audit zero-exception rules: the exact nano+micro+small mover composition and the more-than-10-ticker list whose maximum observed cap is micro. Development hard-context routing increased by 1,889 articles with zero eligible conflicts. The observed 1,000-row holdout routed seven additional, correctly ineligible articles to fast context without changing accuracy. See `docs/codex/NEWS_SYNTHESIS_FUNNEL_V5.md`.
