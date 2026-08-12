# Minimal Issuer News Labeling Prompt V2

This version replaces the article-level V1 output with one clear label set for
every issuer confirmed to be in the news.

## Why market prices are restricted

Market data known **before** publication may be supplied as context. Market
prices after publication must not be used because they reveal the market's
reaction to the news. That is a different prediction target and would leak the
answer into semantic news labels.

## Input

Use one compact `normalized_text` containing the normalized title and article
body. Do not also send the original body or enriched text.

```json
{
  "published_at_utc": "2026-01-15T14:30:00Z",
  "normalized_text": "Normalized title and article text",
  "metadata": {},
  "issuers_in_news": [
    {
      "issuer_id": "stable point-in-time issuer ID",
      "issuer_name": "Example Corporation",
      "ticker": "EXM"
    }
  ],
  "market_context_before_publication": [
    {
      "issuer_id": "stable point-in-time issuer ID",
      "observed_at_utc": "2026-01-15T14:29:00Z",
      "field": "last_price",
      "value": 25.4
    }
  ]
}
```

`market_context_before_publication` may be empty. Every observation must have an
`observed_at_utc` less than or equal to `published_at_utc`.

## Output schema

Return exactly one object in `issuer_labels` for every object in
`issuers_in_news`. Do not add or omit issuers.

```json
{
  "schema_version": "issuer_news_labels_v2",
  "issuer_labels": [
    {
      "issuer_id": "exact issuer_id from the input",
      "forecast_relevance_probability": 0.0,
      "positive_implication_probability": 0.0,
      "negative_implication_probability": 0.0,
      "concepts": [
        {
          "name": "generic_lower_snake_case_name",
          "value": null,
          "probability": 0.0,
          "evidence_quote": "short verbatim quote from normalized_text"
        }
      ],
      "evidence_quotes": [
        "short verbatim quote from normalized_text"
      ]
    }
  ]
}
```

## Fields and eligible values

| Field | Required value |
|---|---|
| `schema_version` | Exactly `issuer_news_labels_v2` |
| `issuer_id` | Exact string from `issuers_in_news`; each input ID exactly once |
| `forecast_relevance_probability` | JSON number from `0.0` to `1.0` |
| `positive_implication_probability` | JSON number from `0.0` to `1.0` |
| `negative_implication_probability` | JSON number from `0.0` to `1.0` |
| `concepts` | JSON array, possibly empty; maximum 12 items |
| `concepts[].name` | Generic `lower_snake_case` string; no issuer names or tickers |
| `concepts[].value` | Any valid JSON value: null, boolean, number, string, array, or object |
| `concepts[].probability` | JSON number from `0.0` to `1.0` |
| `concepts[].evidence_quote` | Short verbatim substring of `normalized_text` |
| `evidence_quotes` | Zero to three short verbatim substrings of `normalized_text` |

Positive and negative probabilities are independent and do not sum to one:

- both low: neutral or no directional implication;
- positive high and negative low: positive;
- positive low and negative high: negative;
- both high: mixed or a material tradeoff.

## Ready-to-use system prompt

```text
You label financial news separately for every supplied issuer.

Return only JSON matching schema_version issuer_news_labels_v2. Return exactly
one issuer_labels object for every issuers_in_news item, using the exact input
issuer_id. Never invent or omit an issuer. Sort output by issuer_id.

Use normalized_text as the news evidence. Metadata may clarify publication or
source facts. You may use market_context_before_publication only when its
observed_at_utc is no later than published_at_utc. Never use or infer a price,
event, or outcome from after publication.

forecast_relevance_probability means:
How likely is it that this article gives new, issuer-specific information that
could reasonably change an investor's expectation of the issuer's future
earnings, cash flow, assets, liabilities, financing, operations, regulatory or
legal position, or survival?

High forecast relevance includes material results, guidance, financing,
acquisitions, regulatory or legal decisions, clinical or product milestones,
capital returns, major contracts, management changes, and material operational
changes. A passing mention, old background, repeated known facts, price movement
alone, generic description, or unrelated content should receive low relevance.

positive_implication_probability is the chance that the new issuer-specific
information is materially favorable for that issuer.
negative_implication_probability is the chance that it is materially adverse.
They are independent probabilities: both may be high for mixed news and both
may be low for neutral news. Do not borrow evidence from another issuer.

Concepts are open vocabulary. Use generic lower_snake_case names and any valid
JSON value that faithfully represents the fact. Do not put issuer names,
tickers, source IDs, or headline text in concept names. Include only material,
evidence-backed concepts, at most 12 per issuer.

All probabilities must be finite JSON numbers in [0, 1]. Use uncertainty rather
than forcing 0 or 1. Evidence quotes must be short verbatim substrings of
normalized_text. Do not output reasoning, Markdown, comments, or extra keys.
```

## Ready-to-use user prompt

```text
Label every issuer in this input using issuer_news_labels_v2:

{{INPUT_JSON}}
```

## Examples

### Example 1: one positive issuer event

Input text: `Acme raised full-year revenue guidance from $500 million to $560 million.`

```json
{
  "schema_version": "issuer_news_labels_v2",
  "issuer_labels": [
    {
      "issuer_id": "issuer-acme",
      "forecast_relevance_probability": 0.99,
      "positive_implication_probability": 0.97,
      "negative_implication_probability": 0.02,
      "concepts": [
        {
          "name": "guidance_change",
          "value": {"metric": "revenue", "from": 500000000, "to": 560000000},
          "probability": 0.99,
          "evidence_quote": "raised full-year revenue guidance from $500 million to $560 million"
        }
      ],
      "evidence_quotes": [
        "raised full-year revenue guidance from $500 million to $560 million"
      ]
    }
  ]
}
```

### Example 2: material mixed news

Input text: `Beta beat quarterly revenue estimates but cut its full-year margin outlook.`

```json
{
  "schema_version": "issuer_news_labels_v2",
  "issuer_labels": [
    {
      "issuer_id": "issuer-beta",
      "forecast_relevance_probability": 0.99,
      "positive_implication_probability": 0.82,
      "negative_implication_probability": 0.88,
      "concepts": [
        {
          "name": "earnings_comparison",
          "value": {"metric": "revenue", "result": "above_estimate"},
          "probability": 0.96,
          "evidence_quote": "beat quarterly revenue estimates"
        },
        {
          "name": "guidance_change",
          "value": {"metric": "margin", "direction": "decreased"},
          "probability": 0.98,
          "evidence_quote": "cut its full-year margin outlook"
        }
      ],
      "evidence_quotes": [
        "beat quarterly revenue estimates",
        "cut its full-year margin outlook"
      ]
    }
  ]
}
```

### Example 3: issuer mentioned without new material information

Input text: `The report compared sector valuations and briefly mentioned Gamma.`

```json
{
  "schema_version": "issuer_news_labels_v2",
  "issuer_labels": [
    {
      "issuer_id": "issuer-gamma",
      "forecast_relevance_probability": 0.03,
      "positive_implication_probability": 0.02,
      "negative_implication_probability": 0.02,
      "concepts": [],
      "evidence_quotes": ["briefly mentioned Gamma"]
    }
  ]
}
```
