# News Synthesis Trading-Ideas Review Candidates

## Outcome

The corrected 2025-August 2026 authority contains 42,567 articles carrying the `trading ideas` channel or tag. Of 6,999 currently eligible articles, 103 already received correction-grade blind review and remained eligible after correction reconciliation. The other 6,896 are now frozen as controller-only review candidates.

No labels were changed. The audit hypothesis is stricter than the metadata label: an investment idea, recommendation, valuation or technical setup, or price-movement narrative is forecast-ineligible unless the complete article independently reports a new or current material issuer event.

## Population

| Split | Trading-idea articles | Eligible | Ineligible | Eligible rate |
|---|---:|---:|---:|---:|
| 2025 discovery | 25,608 | 2,336 | 23,272 | 9.12% |
| Jan-Apr 2026 validation | 8,165 | 1,495 | 6,670 | 18.31% |
| May-Aug 2026 final | 8,794 | 3,168 | 5,626 | 36.03% |
| **Total** | **42,567** | **6,999** | **35,568** | **16.44%** |

The rising eligible rate is suspicious but does not prove provider drift. The source populations have different provisional labeling histories.

## Smart review designation

Every unreviewed eligible article is retained, with deterministic controller-only priority:

| Priority | Articles | Selection evidence |
|---|---:|---|
| P0 | 126 | Combined model predicts ineligible and no event evidence is detected |
| P1 | 472 | Combined model predicts ineligible despite event overlap |
| P1 | 912 | No event evidence and explicit analyst, mover, technical, valuation, list, preview, or other idea/noise evidence |
| P2 | 68 | No detected event or explicit noise evidence |
| P2 | 5,113 | Event evidence and explicit idea/noise evidence coexist |
| P3 | 205 | Event evidence without explicit noise evidence |

Event evidence includes bounded material-event text evidence or an event channel such as earnings, guidance, M&A, FDA, clinical trials, contracts, offerings, buybacks, dividends, IPOs, or management. It is a rescue signal for review, not automatic eligibility.

## Exact-path first tranche

Two exact channel sets have at least 300 articles, at least 30 in every temporal split, and no more than 5% eligible labels in any split:

| Exact channel set | Support | Eligible | Eligible rate | Review status |
|---|---:|---:|---:|---|
| `news|trading ideas` | 8,021 | 22 | 0.27% | All 22 already received correction-grade review and remained eligible |
| `trading ideas` | 2,614 | 20 | 0.77% | All 20 remain unreviewed; frozen as the strict first tranche |

The reviewed `news|trading ideas` exceptions demonstrate why even a statistically clean metadata path cannot automatically overwrite complete-text semantics. The 20 pure-channel exceptions are the highest-value new blind-review packet.

## Evidence profiles

| Detected event | Explicit idea/noise evidence | Articles | Eligible | Eligible rate |
|---|---|---:|---:|---:|
| No | No | 11,231 | 114 | 1.02% |
| No | Yes | 12,683 | 1,014 | 7.99% |
| Yes | Yes | 18,247 | 5,655 | 30.99% |
| Yes | No | 406 | 216 | 53.20% |

This separation is more useful than a flat `trading ideas` rejection. The first two rows are primarily label-audit territory; the latter two require complete-text adjudication of whether the article reports an event or merely uses event language to support an investment thesis.

## Blindness contract

Semantic reviewers must receive only opaque review ID, publication timestamp, complete rendered source text, and verified source hash. Current labels, tags, channels, event/noise evidence, model prediction, probability, priority, provenance, and previous votes remain controller-only.

## Runtime evidence

The validated runtime is:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\trading_ideas_review_candidates_v2`

It contains all 6,896 controller candidates, the 103 previously reviewed eligible controls, 13,597 family-statistic rows, the strict 20-article first tranche, reports, validation, and a complete hash manifest. No bulk data is stored in the repository.
