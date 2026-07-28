# Text candidate inventory v1

## Purpose

`text_candidate_inventory_v1` is the read-only empirical vocabulary prerequisite
for the reviewed news-role taxonomy and a future deterministic SEC labeler. It
does not classify live news, summarize filings, or supply model prompts.

## Sources

- Certified `q_live.benzinga_news_event_v2` and
  `q_live.benzinga_news_rendered_v2`.
- Current `q_live.sec_filing_document_v3` and
  `q_live.sec_filing_text_rendered_v3`.

Source text remains in its canonical database authority. Generated candidate
products are runtime artifacts and never belong in the source repository.

## Identity and counting

News counts one rendered article once. SEC counts one current rendered document
once. Phrase support is document presence; multiple repetitions remain
available as diagnostic occurrence counts but do not create additional
document support.

The inventory normalizes typed financial values before phrase discovery:

| Value | Placeholder |
|---|---|
| Currency amount | `<money>` |
| Shares | `<share_count>` |
| Price per share | `<price_per_share>` |
| Percentage | `<percentage>` |
| Ratio | `<ratio>` |
| Basis points | `<basis_points>` |
| Valuation or operating multiple | `<multiple>` |
| Other numeric value | `<number>` |

Each value example retains its raw spelling, normalized numeric value, bounded
context, source identity, and timestamp.

## Bounded discovery

The miner uses a bounded Space-Saving candidate inventory per independent
source unit and a second bounded merge. Every row stores an estimated document
frequency and its replacement error. The conservative lower bound is:

```text
document_frequency_lower_bound =
    estimated_document_frequency - error_bound
```

Minimum-support filtering uses the lower bound. Curated discovery seeds are
retained even when support is low, but remain `proposed`; they are not approved
labels.

The job records any per-document candidate-bound hit. A run with such a hit,
source failures, an explicit document limit, or incomplete source coverage is
`partial` and cannot certify the corpus inventory.

## Review boundary

The runtime inventory supports:

1. keyword, phrase, source, time, and typed-value distributions;
2. representative evidence review;
3. consolidation of grammatical/provider variants;
4. proposed concept mappings;
5. stratified validation-set construction.

Production taxonomy changes happen only after review and certification in a
separate versioned task.
