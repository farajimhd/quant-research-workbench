# News Synthesis Provider-Filter Contradiction Review

## Outcome

All 2,767 articles that were currently labeled eligible but matched at least one blindly labeled `likely_ineligible` provider feature received a prediction-blind full-text review.

- Final eligible: 2,130
- Corrected eligible to ineligible: 636
- Corrected eligible to insufficient: 1
- Total corrected: 637, or 23.02%
- Current eligible label confirmed: 2,130, or 76.98%
- One three-distinct-label vote remained unresolved and stayed eligible

The corrections were materialized in a create-new scoped successor containing the exact original 361,695 article IDs:

`D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_sentiment_authority_provider_filter_v1`

The predecessor `forecast_eligibility_sentiment_authority_v1` was not modified. Gold issuer sentiment was copied byte-for-byte.

## Blind review protocol

The controller selected the population from provider-feature contradictions but did not expose that selection evidence to semantic reviewers. Worker packets contained only:

- opaque review ID;
- publication timestamp;
- complete rendered article text;
- verified rendered-text SHA-256.

Workers could not see source IDs, current labels, matched features, feature labels, tags, channels, other metadata, temporal split, statistical results, model output, or prior votes.

The first pass used 65 immutable packets covering 4,760,690 rendered characters. Packets contained at most 45 articles and 80,000 characters; no article was truncated and no oversized solo packet was required.

All 2,767 articles received a first blind read. Every proposed change, insufficient decision, and confidence below 0.80 received a second blind read by a different reviewer. Every first/second disagreement received a third blind read by the remaining reviewer.

- First reviews: 2,767
- Second reviews: 641
- Third reviews: 102
- Direct first/second ineligible agreement: 538
- Direct first/second insufficient agreement: 1
- Third-reader cases: 102

Three reusable reviewer tasks performed the campaign. No nested agents were used. Bulk decisions were written to temporary files, validated into the runtime ledger, and deleted after every packet. The final temporary directory was removed. All three reviewers completed and no reviewer remains live. Completed Codex task history is retained by the application and cannot be deleted by the controller.

## Correction results

The successor label population is:

| Label | Parent authority | Scoped successor | Change |
|---|---:|---:|---:|
| Eligible | 139,068 | 138,431 | -637 |
| Ineligible | 221,219 | 221,855 | +636 |
| Insufficient | 1,408 | 1,409 | +1 |
| Total | 361,695 | 361,695 | 0 |

Corrections by temporal analysis split:

| Split | Reviewed contradictions | Corrections | Correction rate |
|---|---:|---:|---:|
| 2025 discovery | 954 | 177 | 18.55% |
| Jan-Apr 2026 validation | 1,739 | 405 | 23.29% |
| May-Aug 2026 final | 74 | 55 | 74.32% |

The final-period rate is based on only 74 selected contradictions and should not be generalized to the full final-period population.

Majority-supported correction reasons were:

| Reason | Corrections |
|---|---:|
| Scheduled preview | 338 |
| Screener or list | 208 |
| Technical or valuation commentary | 26 |
| Other ineligible | 24 |
| Generic macro commentary | 13 |
| Routine halt or listing notice | 12 |
| Analyst-action-only | 10 |
| Price-movement-only | 2 |
| Already-reported recap | 2 |
| Background/reference | 1 |
| Insufficient evidence | 1 |

## Provider-feature findings

### `bzi-ep` is a strong earnings-preview identifier

All 325 reviewed contradictions carrying the `bzi-ep` template family were independently resolved as ineligible. The dominant reason was scheduled earnings preview rather than a new event. In this audited population, the tag cleanly separated known-ahead-of-time expectation articles from current issuer events.

### `bzi-recaps` does not mean forecast-ineligible recap

All 2,009 reviewed contradictions carrying `bzi-recaps` were confirmed eligible from their complete text. These articles reported current earnings results or other new issuer information despite the provider tag's apparent recap name.

This is the most important correction to the earlier feature-level semantic opinion: provider code names cannot be interpreted literally without full-text validation. `bzi-recaps` must not be used as a rejection rule.

### Mover and market-update tags require semantic rescue

The selected currently eligible contradictions showed mixed outcomes:

| Feature family | Reviewed contradictions | Corrected ineligible | Correction rate |
|---|---:|---:|---:|
| `big losers` | 55 | 41 | 74.55% |
| `mid morning market update` | 64 | 45 | 70.31% |
| `mid day market update` | 34 | 18 | 52.94% |
| `overbought stocks` | 4 | 4 | 100.00% |
| `oversold stocks` | 1 | 1 | 100.00% |

The mover and market-update families are useful candidate generators but unsafe hard rejects: 14 `big losers`, 19 `mid morning market update`, and 16 `mid day market update` contradictions remained eligible after full-text review. A material-event rescue is required.

The overbought/oversold results are semantically consistent with technical-screen noise but too small in this contradiction-only population to certify independently.

### Halt and previously zero-eligible template families were not tested here

The contradiction population began from currently eligible articles. Features such as `halts`, `bzi-pod`, `bzi-tfm`, and `bzi-auoa` had no currently eligible articles in the analyzed population, so they contributed no rows to this 2,767-article correction campaign. Their zero-error appearance still requires separate sampling of currently ineligible rows before production certification.

## Validation

- Exact 2,767-row review population and unique opaque IDs
- Complete 2,767 first-pass coverage
- Independent reviewer identity for all 641 second reads
- Independent third reviewer for all 102 disagreements
- Exact evidence-excerpt containment in complete rendered text
- Exact 361,695 successor rows and unique source IDs
- Source IDs and row order unchanged from the parent authority
- Exactly 637 changed article rows
- No changes outside the eight authorized forecast-authority fields
- Gold sentiment SHA-256 unchanged
- 287 audit artifacts independently match the audit hash manifest
- 6 successor artifacts independently match the successor hash manifest

## Scope limitation

This is a correction-grade scoped successor for provider-filter contradictions. It does not complete the separate 35,995 combined-model mismatch audit and is not a fresh generalization benchmark. Reviewer decisions are multi-reader Codex adjudications, not human certification.

## Runtime artifacts

Blind packets, validated reviews, reviewer lineage, the controller population, validation, reports, and hashes:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_filter_contradiction_review_v1`

Full successor article labels, byte-identical gold sentiment, correction ledger, validation, report, load manifest, and hashes:

`D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_sentiment_authority_provider_filter_v1`
