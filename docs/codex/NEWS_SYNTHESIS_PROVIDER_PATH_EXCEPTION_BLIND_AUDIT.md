# News Synthesis Provider-Path Exception Blind Audit

## Outcome

The merged stable provider-path exception population has been exhausted under
the current corrected supervision authority. The final refresh contains 2,102
exceptions, and every one is covered by a documented prior review or
certification source, or by this audit. There are zero unreviewed exceptions.

The audit corrected 189 labels in total:

- 136 eligible to ineligible;
- 53 ineligible to eligible.

The immutable final successor is:

`D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_sentiment_authority_provider_path_exceptions_v2`

It contains all 361,695 article-label rows. The inherited gold sentiment table
is byte-identical to the parent authority.

## Frozen population and prior-review reconciliation

The pre-audit merged path catalog nominated 2,257 unique exceptions:

- 2,115 currently eligible articles under stable-ineligible paths;
- 142 currently ineligible articles under stable-eligible paths.

The exact union of earlier review and certification authorities contained
19,739 unique source IDs. After excluding intersections, 960 exceptions had
never been reviewed:

- 840 eligible under stable-ineligible paths;
- 120 ineligible under stable-eligible paths.

The controller, packet assignments, source-text hashes, packet hashes, and
review instructions were frozen before review. Worker packets omitted current
labels, matched paths, path statistics, model outputs, and prior decisions.

## Review funnel

Three existing local Codex reviewers were reused; no new task was created.
Each handled 320 compact rows. The compact stage used provider metadata,
tickers, channels, tags, title, teaser, and the first three sentences.

| Compact result | Articles |
|---|---:|
| Eligible | 572 |
| Ineligible | 329 |
| Needs full text | 59 |
| **Total** | **960** |

Full text was required for every compact proposed change, every ambiguous
preview, and a deterministic 10% sample of compact-preserve decisions:

| Full-first selection reason | Articles |
|---|---:|
| Compact proposed change | 347 |
| Compact needs full text | 59 |
| Compact-preserve quality control | 62 |
| **Total** | **468** |

The first full-text decisions were 295 eligible, 167 ineligible, and 6
insufficient. A second independent full-text reader, who had seen neither the
compact decision nor the first full-text decision, reviewed 217 proposed
changes or unresolved records.

The two full-text readers agreed on 182 of 217 confirmation cases (83.87%).
The other 35 preserved the parent label fail-closed. No label changed from a
compact decision alone.

The 62-row compact-preserve quality-control sample produced 57 immediate
full-text preserves and five proposed changes that the independent confirmer
rejected. It produced zero two-reader-confirmed missed corrections. This is
supporting quality-control evidence, not proof that the unaudited compact
preserves have zero error.

## Primary audit corrections

The 960-row audit produced:

| Original exception family | Final eligible | Final ineligible | Corrections |
|---|---:|---:|---:|
| 840 eligible under stable-ineligible paths | 711 | 129 | 129 eligible to ineligible |
| 120 ineligible under stable-eligible paths | 53 | 67 | 53 ineligible to eligible |
| **Total** | **764** | **196** | **182** |

After these corrections, the full path refresh introduced one newly stable
ineligible path, `channel_pair=long ideas|markets`, leaving eight genuinely
unreviewed exceptions. Because this residual was small, all eight received two
independent full-text reviews. Seven changed from eligible to ineligible and
one was preserved. The v2 authority incorporates this refinement.

## Final refreshed statistics

The final decisive 2025-2026 analysis population contains 346,103 articles:

| Label | Articles |
|---|---:|
| Eligible | 124,331 |
| Ineligible | 221,772 |

The fixed 1,132-path catalog now has:

| Statistical class | Paths |
|---|---:|
| Stable ineligible | 104 |
| Stable eligible | 15 |
| Stable mixed | 84 |
| Temporal drift | 264 |
| Directional context | 39 |
| Insufficient forward support | 626 |

The final path catalog nominates 2,013 eligible exceptions under
stable-ineligible paths and 89 ineligible exceptions under stable-eligible
paths. All 2,102 are already reviewed; the unreviewed count is zero.

The principal statistical-class changes caused by the corrected labels were:

- `metadata_signature={"channels":["m&a","news"],"provider":"benzinga","provider_tags":[],"ticker_count_bucket":"2"}` became stable eligible with 329/335 eligible;
- `channel_pair=long ideas|markets` became stable ineligible with 42/1,617 eligible;
- five broad paths moved from temporal drift to stable mixed;
- `channel=broad u.s. equity etfs` moved from temporal drift to directional context.

These class changes are statistical evidence for rule refinement. They are not
authorization to flip every future article matching a broad path. Stable event
paths still contain ineligible exceptions, and stable noise paths still contain
eligible issuer events; deterministic routing must retain semantic overrides
and fail-open handling for ambiguous records.

## Runtime evidence

Primary audit:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_path_exception_blind_audit_v1`

Final feature refresh:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_filter_feature_audit_v6_provider_path_exceptions_final`

Final merged-path refresh:

`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_filter_merged_path_analysis_v5_provider_path_exceptions_final`

The runtime authorities contain correction ledgers, reviewer votes, exact
evidence excerpts, source hashes, packet hashes, reports, validations, and hash
manifests. Generated packets, reviews, labels, and statistics remain outside
the repository. Seventy-four temporary staging files were removed after
validated ingestion; durable runtime evidence was retained.

## Limitations

- Codex multi-reader adjudication is not human certification.
- The audit closes exceptions generated by the fixed 1,132-path catalog; new
  metadata representations or future temporal drift can create new candidates.
- Aggregate path stability supports deterministic routing but does not replace
  article-level semantic handling for mixed or conflicting evidence.
