# News Synthesis Merged Provider-Path Analysis

## Outcome

The 709 blind semantic feature paths and 423 later residual candidate paths
were merged into one fixed, provenance-preserving catalog of 1,132 unique
paths. There was no overlap between the two inputs.

Every statistic was computed over both the 346,107-row baseline full population
and refreshed article features generated against the immutable
trading-ideas-corrected authority. The old-to-new deltas therefore use the same
fixed path catalog and full-population scope:

`D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_sentiment_authority_trading_ideas_v1`

The refreshed decisive population contains 346,103 articles:

| Split | Articles | Eligible | Ineligible |
|---|---:|---:|---:|
| 2025 discovery | 203,847 | 75,517 | 128,330 |
| Jan-Apr 2026 validation | 71,645 | 21,259 | 50,386 |
| May-Aug 2026 final | 70,611 | 27,638 | 42,973 |
| **Total** | **346,103** | **124,414** | **221,689** |

Four previously decisive articles are absent because the trading-ideas audit
corrected them to `insufficient_short_text`. The other 1,609 changed rows moved
from eligible to ineligible.

## Updated path statistics

| Updated statistical class | Paths |
|---|---:|
| Stable ineligible | 103 |
| Stable eligible | 14 |
| Stable mixed | 79 |
| Temporal drift | 270 |
| Directional context | 40 |
| Insufficient forward support | 626 |

Of the 103 stable-ineligible paths, 83 came from the original 709-path semantic
catalog and 20 came from the later 423-path residual catalog. All 14
stable-eligible paths came from the residual catalog.

The 423 residual candidates changed as follows after label refresh:

- all 14 stable-eligible paths remained stable eligible;
- 20 paths became stable ineligible: seven were temporal-drift paths and 13
  were directional-context paths on the comparable baseline full population;
- 270 remain temporal drift, 79 are stable mixed, and 40 are directional
  context.

The 20 newly stable-ineligible representations are highly overlapping. They
primarily describe two reviewed trading-idea families: the Benzinga `bzi-ia`
template and technical/signals/opinion/expert-idea combinations. They nominate
only 46 unique currently eligible exceptions, so their path count must not be
mistaken for 20 independent rules or 20 independent article populations.

The 14 stable-eligible event paths nominate 142 unique currently ineligible
exceptions: 81 from 2025, 25 from January-April 2026, and 36 from May-August
2026. The previous residual analysis found 135; all 135 remain and seven new
exceptions were added after recomputation. These paths cover earnings/news,
M&A/news, buybacks/news, dividends/news, and FDA/biotech-news families.

Across both catalogs, 2,115 unique currently eligible articles fall under at
least one updated stable-ineligible path, while 142 unique currently
ineligible articles fall under at least one updated stable-eligible path.
These are audit candidates, not automatic label corrections.

## Coverage and interpretation

At least one merged path matches 345,736 of 346,103 decisive articles; 367
match none. This high coverage is expected because the catalog contains broad
paths such as individual channels and tags. It does not mean that deterministic
rules can label 99.9% of the population.

The original path provenance remains explicit:

- 536 of the 709 semantic paths were labeled `likely_eligible`;
- 173 were labeled `likely_ineligible`;
- the 423 residual paths remain statistically classified and semantically
  unreviewed.

Updated empirical rates do not replace semantic authority. In particular, 54
original `likely_eligible` paths are now statistically stable ineligible. Many
were deliberately labeled fail-open because the broad path can contain a real
issuer event even when its aggregate rate is low. Their article exceptions
must be read rather than flipped from path prevalence.

## Runtime evidence

Refreshed feature authority:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_filter_feature_audit_v4_trading_ideas_corrected`

Final merged-path analysis:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_filter_merged_path_analysis_v3_trading_ideas_corrected`

The latter contains `MERGED_PATH_STATS.csv`, a deduplicated
`UPDATED_PATH_EXCEPTION_CANDIDATES.jsonl`, JSON and Markdown reports,
validation, and an input/output hash manifest. Validation passed exact source
catalog counts, catalog disjointness, all-path observation in both full
populations, population and label reconciliation, and baseline and refreshed
article-feature SHA-256 verification.
