# News Synthesis Provider-Filter Residual Analysis

## Outcome

The residual analysis examined the exact 160,360 corrected decisive 2025-August 2026 articles that match none of the 709 previously labeled candidate paths. It found 80,020 observed feature paths and 423 new exact metadata paths with at least 300 residual articles and at least 30 articles in every temporal split.

It found no new forward-supported path whose eligible rate stayed at or below 5% in every period. Under the existing provider-tag/channel/signature vocabulary, the original 709-path screen appears to have exhausted the obvious prevalent deterministic noise paths. The residual should not be forced into additional hard-rejection rules from metadata alone.

## Residual population

| Split | Articles | Eligible | Ineligible | Eligible rate |
|---|---:|---:|---:|---:|
| 2025 discovery | 83,368 | 64,380 | 18,988 | 77.22% |
| Jan-Apr 2026 validation | 35,775 | 18,546 | 17,229 | 51.84% |
| May-Aug 2026 final | 41,217 | 26,303 | 14,914 | 63.81% |
| **Total** | **160,360** | **109,229** | **51,131** | **68.11%** |

The large period-to-period change is a label-authority warning. It cannot be attributed to provider-template drift without blind source review because most rows use provisional single-pass labels and the 2025 and 2026 source datasets have different labeling histories.

## New path classes

The 423 forward-supported exact metadata paths are highly overlapping:

| Class | Paths | Meaning |
|---|---:|---|
| Stable ineligible | 0 | No new prevalent hard-noise rule was found |
| Stable eligible | 14 | Strong candidate paths for finding incorrectly ineligible labels |
| Stable mixed | 42 | Both labels persist; requires semantic routing or stratified review |
| Temporal drift | 339 | Label rate changes by at least 15 percentage points |
| Directional context | 28 | Directional but below the stable 95% threshold |

The 14 stable-eligible representations collapse into a few substantive families: earnings/news, M&A/news, buybacks/news, dividends/news, and FDA/biotech news. They cover between 376 and 5,975 rows per representation and retain at least 95% eligible labels in every temporal split.

These paths nominate 135 unique currently ineligible articles for prediction-blind review:

- 77 from 2025 discovery;
- 23 from January-April 2026 validation;
- 35 from May-August 2026 final;
- 113 also appear in the independent combined-model mismatch inventory with an eligible prediction;
- 112 of those 113 remain completely unreviewed, while one has only a single blind read.

This 135-article queue is controller evidence only. Reviewers must receive complete source text and an opaque ID, without current labels, path names, metadata, model output, or earlier votes.

## Mixed and drifting families

Prevalent stable-mixed paths include general news, technology, cryptocurrency, biotech, management, global, health care, legal, and commodities. Their rates are too mixed to define deterministic labels.

The strongest drift is concentrated in overlapping earnings and guidance representations. For example:

| Path | Support | 2025 eligible | Jan-Apr 2026 eligible | May-Aug 2026 eligible |
|---|---:|---:|---:|---:|
| `channel=earnings` | 34,065 | 97.27% | 34.02% | 72.93% |
| `channel=earnings beats` | 18,302 | 99.83% | 14.15% | 67.40% |
| `channel=guidance` | 17,788 | 99.06% | 45.07% | 79.65% |
| `channel=trading ideas` | 11,527 | 19.23% | 33.96% | 50.30% |

These are audit strata, not candidate hard rules. The runtime provenance table records source dataset, authority class, certification level, split, and label for the 25 highest-support drift paths so label-process changes can be separated from provider changes.

## Runtime evidence

The validated V2 runtime is:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_filter_residual_analysis_v2`

It contains the exact residual source-ID manifest, all 80,020 feature statistics, 423 new path candidates, the 135-article blind-review exception queue, drift-provenance diagnostics, JSON and Markdown reports, validation, and a complete input/output hash manifest. Generated data remains outside the repository.
