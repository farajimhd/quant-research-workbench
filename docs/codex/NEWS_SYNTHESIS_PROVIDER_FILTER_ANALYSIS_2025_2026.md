# News Synthesis Provider-Filter Analysis, 2025-August 2026

## Outcome

The rated 2025-August 2026 authority contains strong, temporally persistent
Benzinga metadata paths that can route a material share of forecast noise
without invoking the full semantic compiler. The strongest forward-tested
candidate prefix routed 24,033 of 70,611 final-period articles (34.04%) while
retaining 28,588 of 28,609 currently labeled eligible articles (99.9266%).

This is discovery evidence, not production certification. The current labels
contain known provisional/error risk, and the 117 labeled-eligible exceptions
across all three periods require blind adjudication before any hard-rejection
policy is approved.

## Immutable runtime authority

The causally corrected analysis is:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_filter_feature_audit_v2`

The directory contains:

- `ARTICLE_FEATURES.jsonl`: 346,108 source-bound article feature rows;
- `FEATURE_STRENGTH.csv`: 193,524 observed features and interactions;
- `CANDIDATE_RULES.csv`: 709 forward-screened candidates;
- `RULE_WATERFALL.csv`: marginal and cumulative routing behavior;
- `REPORT.json` and `REPORT.md`;
- `VALIDATION.json`; and
- `HASH_MANIFEST.json`.

All seven hashed analysis outputs independently reconciled with zero SHA-256
mismatches. Validation passed unique source identity, exact temporal splits,
rendered-text hashes, nonnegative ticker-history intervals, and output
completeness.

The earlier `provider_filter_feature_audit_v1` is superseded. V2 prevents
articles with identical publication timestamps from seeing one another as
prior news; simultaneous rows now share the same strictly-prior causal state.
This correction changed ticker-history counts slightly but did not change the
candidate count or metadata-rule waterfall.

## Population and temporal contract

The analysis loaded the current frozen 361,695-article authority, exact Benzinga
metadata, and the manifest-bound 1.03 GB rendered-text authority. It analyzed
only decisive 2025-August 2026 labels:

| Split | Articles | Eligible | Ineligible |
|---|---:|---:|---:|
| 2025 discovery | 203,849 | 75,957 | 127,892 |
| Jan-Apr 2026 validation | 71,648 | 22,098 | 49,550 |
| May-Aug 2026 final | 70,611 | 28,609 | 42,002 |
| **Total** | **346,108** | **126,664** | **219,444** |

The 1,407 insufficient 2025-2026 rows remain explicit in the source authority
and were excluded from binary feature-strength estimates. Another 14,180
authority rows predate 2025 and were outside the requested period.

Ticker-history features used only earlier publication timestamps. For each
article-ticker pair, the analysis calculated session ordinal, first-news state,
time since prior ticker news, and recent-news indicators, then conservatively
aggregated them to the article.

## Main findings

### 1. Exact provider paths are much stronger than broad semantic keywords

The exact channel set `analyst ratings | news | price target` covered 61,888
articles and had zero currently labeled eligible rows in every period:

| Period | Support | Eligible | Wilson 95% upper bound |
|---|---:|---:|---:|
| 2025 discovery | 35,792 | 0 | 0.0107% |
| Jan-Apr 2026 validation | 11,797 | 0 | 0.0326% |
| May-Aug 2026 final | 14,299 | 0 | 0.0269% |
| All | 61,888 | 0 | 0.0062% |

This one exact provider path alone covered 20.25% of the final population with
no label loss. Related exact channel sets for reiterations, initiations,
upgrades, and downgrades also showed high precision, though many overlap and
must be consolidated into a small nonredundant policy.

By comparison, the broad text-derived
`analyst_rating_without_material_override` path covered 39.25% but had a 16.61%
eligible rate. Metadata combination and content context therefore matter more
than the presence of analyst language alone.

### 2. The `halts` tag is strong; halt language is not

| Feature | Support | Eligible rate | Final support | Final eligible rate |
|---|---:|---:|---:|---:|
| Exact Benzinga tag `halts` | 2,165 | 0.00% | 153 | 0.00% |
| Halt language in rendered text | 8,240 | 16.77% | 1,744 | 22.48% |
| Halt language without the bounded material-event override | 6,331 | 8.96% | 1,247 | 10.34% |

The provider tag is a credible rule candidate, but the final period contains
only 153 observations, so its zero-error Wilson upper bound is still 2.45%.
It needs additional blind review and future accumulation. A generic `halt` text
rule is unsafe because material clinical, regulatory, financing, and other
issuer events can mention a halt.

### 3. Several coded Benzinga families are statistically clean

The following tags had zero currently labeled eligible rows across the complete
analyzed population:

- `bzi-pod`: 7,852 rows, including 1,251 final-period rows;
- `bzi-tfm`: 3,820 rows, including 777 final-period rows;
- `halts`: 2,165 rows, including 153 final-period rows; and
- `bzi-auoa`: 1,053 rows, including 195 final-period rows.

Other zero-error tags such as `bzi-aar`, `bzi-shorthist`, `bzi-uoa`, and
`bzi-pe` had no final-period support. They are historically strong but cannot
be called forward-certified without current observations or a confirmed
provider taxonomy lifecycle.

### 4. A high-precision metadata prefix has substantial routing value

The first 74 high-precision candidate entries collapse to a much smaller number
of marginally active, overlapping provider families. Their cumulative results
were:

| Period | Routed articles | Compute reduction | Retained eligible recall |
|---|---:|---:|---:|
| 2025 discovery | 66,680 | 32.71% | 99.8920% |
| Jan-Apr 2026 validation | 21,992 | 30.69% | 99.9366% |
| May-Aug 2026 final | 24,033 | 34.04% | 99.9266% |

The final period lost 21 currently labeled eligible rows. Inspection showed two
different causes:

- probable label-policy inconsistencies, including analyst initiation and
  analyst-list articles labeled eligible despite the stated policy; and
- genuine material events embedded in mover/trading-ideas channels, including
  earnings, acquisitions, clinical failures, and commercial deals.

This proves that broad channel rejection is unsafe and that the eventual
provider gate needs explicit semantic rescue conditions. It also shows why the
117 exceptions must be re-adjudicated blindly before calculating a certified
false-rejection rate.

Adding broader promising rules beyond this prefix increased final routing to
36.91% but reduced retained eligible recall to 97.42%. That extension is not
appropriate for a fail-open forecast filter.

### 5. Ticker timing is contextual, not a standalone rejection rule

First-news state was not a strong or stable noise discriminator:

- `any_ticker_first_session=true`: 57.56% coverage, 36.16% eligible overall,
  41.58% in the final period;
- `any_ticker_first_session=false`: 42.44% coverage, 37.19% eligible overall,
  39.04% in the final period; and
- news within five minutes of earlier ticker news was 51.58% eligible overall
  and 54.08% eligible in the final period.

The last result is especially important: rapid repeated coverage often follows
a genuine active event. Time since previous news should help distinguish
first report, follow-up, recap, and event cluster, but should not independently
reject an article.

### 6. Ticker count and publication time drift materially

Articles with more than ten tickers were only 6.20% eligible overall, but their
eligible rate rose from 3.14% in 2025 to 14.48% in the final period. Likewise,
several macro, technical, short-idea, intraday, and time-of-day features changed
sharply between 2025 and 2026.

Ticker count, clock time, and session position should therefore be contextual
features or rule conditions, never sole rejection authorities.

## Consequences for News Synthesis

The evidence supports a staged deterministic design:

1. Compile a versioned provider context from exact tag/channel sets, provider
   family, ticker shape, author/source, publication/update times, and causal
   ticker history.
2. Apply only blind-certified exact provider rules at the cheap routing stage.
3. Treat novel, missing, conflicting, or drifted metadata as `uncertain` and
   pass it through.
4. Apply bounded headline/material-event rescue before any hard rejection.
5. Run the existing full evidence-preserving semantic compiler for important
   and uncertain articles.
6. Preserve forecast noise for a later general-market, analyst-ideas, and
   market-context synthesis lane.

The statistical Random Forest remains useful for candidate discovery and
ranking, but the production route should be a small explicit rule manifest with
rule IDs, evidence, effective dates, provider-taxonomy versions, and fail-open
behavior.

## Required certification before production changes

1. Consolidate the 74 overlapping candidates into a minimal set of independent
   provider families.
2. Blindly adjudicate all 117 currently labeled eligible exceptions caught by
   the high-precision prefix.
3. Blindly sample labeled-ineligible captures from every proposed family,
   stratified by month and seen/unseen template.
4. Confirm the meanings and lifecycle of opaque Benzinga codes such as
   `bzi-pod`, `bzi-tfm`, and `bzi-auoa` from source evidence rather than naming
   assumptions.
5. Recompute Wilson bounds from adjudicated outcomes.
6. Only then implement a new provider-context/policy version and bump the News
   Synthesis engine version so one version identifies one behavior.

No News Synthesis production behavior or label authority was changed by this
analysis.

## Reproduction

From the laptop repository:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\g835l\miniconda3\python.exe -m `
  research.text_intelligence.news_synthesis_v1.run_provider_filter_analysis
```

The runner uses create-new output semantics and refuses to overwrite an
existing runtime analysis.
