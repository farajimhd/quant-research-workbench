# News Synthesis Trading-Ideas Blind Audit

## Outcome

All 6,896 previously eligible articles carrying the `trading ideas` channel or
tag received a blind compact review and a complete-text review. Compact
agreement quality control was too weak to stop safely, so complete text was
reviewed for every candidate. Correction-grade confirmation was then required
for proposed ineligible labels lacking compact agreement and for every
insufficient full-text result.

The immutable successor authority is:

`D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_sentiment_authority_trading_ideas_v1`

It changes 1,613 of the 6,896 reviewed rows:

| Final candidate label | Articles | Share |
|---|---:|---:|
| Eligible | 5,283 | 76.61% |
| Ineligible | 1,609 | 23.33% |
| Insufficient short text | 4 | 0.06% |

The successor contains 361,695 unique article rows: 136,818 eligible, 223,464
ineligible, and 1,413 insufficient. Gold issuer sentiment is byte-identical to
the parent authority.

## Review funnel

| Stage | Coverage or result |
|---|---:|
| Compact first pass | 6,896 articles |
| Compact second pass | 2,252 high-risk or QC articles |
| Complete-text first pass | 6,896 articles |
| Complete-text first result: eligible | 5,158 |
| Complete-text first result: ineligible | 1,701 |
| Complete-text first result: insufficient | 37 |
| Independent full-text confirmation | 454 articles |
| Final ineligible from compact plus full agreement | 1,284 |
| Final ineligible from two full-text agreements | 325 |
| Final insufficient from two full-text agreements | 4 |
| Fail-closed disagreements preserved as eligible | 125 |

The initial compact-agreement QC showed that preview agreement was not a safe
terminal label: 20 of 116 sampled compact-ineligible agreements became eligible
on complete text, while 8 of 53 compact-eligible agreements became ineligible.
That evidence triggered complete-text review of the remaining 5,036 articles.

The confirmation stage independently checked 417 proposed ineligible labels
without compact agreement and 37 first-pass insufficient labels. Of the 417,
325 were confirmed ineligible, 87 were rescued as eligible, and 5 remained
insufficient. Only 4 of the 37 initial insufficient results were independently
confirmed insufficient.

## Findings

The most common final noise reasons were:

| Reason | Corrections |
|---|---:|
| Analyst or investment idea | 460 |
| Screener or list | 327 |
| Scheduled preview | 266 |
| Technical or valuation setup | 206 |
| Generic macro context | 135 |
| Price movement only | 111 |
| Recap or background | 57 |
| Other ineligible | 28 |
| Routine notice | 19 |

Correction rates differ sharply by period:

| Split | Reviewed | Corrected or made insufficient | Rate |
|---|---:|---:|---:|
| 2025 discovery | 2,262 | 263 | 11.63% |
| Jan-Apr 2026 validation | 1,483 | 434 | 29.27% |
| May-Aug 2026 final | 3,151 | 916 | 29.07% |

This confirms label-authority drift in the 2026 provisional population; it is
not evidence that the provider's content mix alone changed.

`trading ideas` is not a safe standalone rejection rule. For example,
`markets|news|trading ideas` retained 968 of 1,062 articles as eligible, while
`movers|trading ideas` rejected 122 of 196. Metadata should identify the
provider template family, and source language must rescue genuinely new issuer
events.

Both individually oversized articles were reconstructed losslessly, reviewed
through complete chunk coverage, and remained eligible: one reported a new
issuer-specific short-seller report and the other a new issuer-specific CMS
reimbursement proposal. Neither was truncated.

## Validation and limitations

- All 346 ordinary expansion packets and 47 confirmation packets passed exact
  identity, order, schema, evidence-containment, and isolation validation.
- Complete-text coverage is exactly 6,896 unique candidate IDs.
- The correction ledger has exactly 6,896 unique source IDs and 1,613 changed
  rows.
- The successor has exactly 361,695 unique article IDs and all declared file
  hashes were independently recomputed.
- Gold issuer sentiment is byte-identical to the parent.
- These are local Codex multi-reader adjudications, not human-certified labels.
- The 23.39% correction rate is specific to previously eligible trading-idea
  candidates and is not a general model-accuracy estimate.
- This scoped successor does not complete the separate 35,995 model-mismatch
  audit.
