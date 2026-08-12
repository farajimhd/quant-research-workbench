# Standalone LLM Issuer-News Labeling Prompt

This contract is independent of News Synthesis. The LLM extracts the issuers,
resolves their visible identities, and predicts all labels itself.

## Input

```json
{
  "published_at_utc": "2026-01-15T14:30:00Z",
  "normalized_text": "Normalized title and article text",
  "metadata": {},
  "market_context_before_publication": []
}
```

Only `published_at_utc` and `normalized_text` are required. Optional market
observations must be timestamped no later than publication. Post-publication
prices are excluded because they reveal the reaction to the news rather than
the meaning of the news.

## Output

```json
{
  "schema_version": "llm_issuer_news_labels_v1",
  "issuers": [
    {
      "issuer_name": "Canonical issuer name",
      "ticker": null,
      "exchange": null,
      "identity_source": "explicit_text",
      "identity_confidence_probability": 0.0,
      "forecast_relevance_probability": 0.0,
      "positive_implication_probability": 0.0,
      "negative_implication_probability": 0.0,
      "concepts": [
        {
          "name": "generic_lower_snake_case_name",
          "value": null,
          "probability": 0.0,
          "evidence_quote": "verbatim text"
        }
      ],
      "evidence_quotes": ["verbatim text"]
    }
  ],
  "unresolved_issuer_mentions": ["verbatim issuer-like mention"]
}
```

## Allowed values

| Field | Allowed value |
|---|---|
| `schema_version` | Exactly `llm_issuer_news_labels_v1` |
| `issuer_name` | Nonempty canonical name inferred by the LLM |
| `ticker`, `exchange` | String or `null` |
| `identity_source` | `explicit_text`, `metadata`, or `llm_inference` |
| All probability fields | JSON number in `[0, 1]` |
| `concepts[].name` | Generic `lower_snake_case` string |
| `concepts[].value` | Any valid JSON value |
| Evidence quotes | Verbatim substrings of `normalized_text` |

Positive and negative probabilities are independent. Both high means mixed;
both low means neutral or no directional implication.

## Ready-to-use system prompt

```text
Read the supplied financial news and return only JSON matching
llm_issuer_news_labels_v1. Do not output reasoning, Markdown, or extra keys.

First identify every distinct issuer materially named or clearly referenced in
the news. An issuer may be a public company, private company, listed fund, or
other security issuer. Do not treat a person, product, index, government,
regulator, exchange, analyst, or generic phrase as an issuer unless the text
also identifies a real issuing entity.

Create exactly one result per discovered issuer. Merge aliases that clearly
refer to the same issuer. Keep different parents, subsidiaries, counterparties,
buyers, sellers, and competitors separate when the text treats them as distinct.

Extract issuer identity from normalized_text and metadata. A ticker or exchange
may be null. Use identity_source=explicit_text when the identity is written in
the text, metadata when it comes from metadata, and llm_inference only when the
model inferred it. Lower identity_confidence_probability when identity, listing,
ticker, time period, parent/subsidiary relationship, or alias is uncertain.
Never invent a ticker to fill a missing value. Put issuer-like mentions that
cannot be resolved into unresolved_issuer_mentions.

For each issuer, forecast_relevance_probability answers:
How likely is it that the article contains new information about this issuer
that could reasonably change an investor's expectation of its future earnings,
cash flow, assets, liabilities, financing, operations, regulatory or legal
position, or survival?

Material results, guidance, financing, acquisitions, major contracts,
regulatory or legal decisions, clinical or product milestones, capital returns,
management changes, and operational changes are normally forecast-relevant.
Passing mentions, old background, repeated known facts, generic descriptions,
and price movement without a new underlying event are normally not.

positive_implication_probability is the probability that the new information
is materially favorable for that issuer. negative_implication_probability is
the probability it is materially adverse. They are independent: both may be
high for mixed news and both may be low for neutral news. Do not transfer an
event or direction from one issuer to another.

Concepts are open vocabulary. Use generic lower_snake_case names and any valid
JSON value. Include only material concepts supported by the text, at most 12 per
issuer. Do not put issuer names, tickers, source IDs, or headline wording in a
concept name.

Use normalized_text as semantic evidence. Metadata may clarify publication and
source facts. Use optional market_context_before_publication only when its
timestamp is no later than published_at_utc. Never use a later price or event.

All probabilities must be finite numbers in [0, 1]. Use uncertainty rather than
forcing 0 or 1. Every evidence quote must be a short verbatim substring of
normalized_text. Sort issuers by issuer_name and concepts by name.
```

## Ready-to-use user prompt

```text
Extract all issuers and label each one using llm_issuer_news_labels_v1:

{{INPUT_JSON}}
```

## Examples

### One issuer

Input: `Acme raised full-year revenue guidance from $500 million to $560 million.`

```json
{
  "schema_version": "llm_issuer_news_labels_v1",
  "issuers": [
    {
      "issuer_name": "Acme",
      "ticker": null,
      "exchange": null,
      "identity_source": "explicit_text",
      "identity_confidence_probability": 0.95,
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
      "evidence_quotes": ["raised full-year revenue guidance"]
    }
  ],
  "unresolved_issuer_mentions": []
}
```

### Two issuers with different roles

Input: `Alpha agreed to acquire Beta for $2 billion. Beta shareholders will receive a 30% premium.`

```json
{
  "schema_version": "llm_issuer_news_labels_v1",
  "issuers": [
    {
      "issuer_name": "Alpha",
      "ticker": null,
      "exchange": null,
      "identity_source": "explicit_text",
      "identity_confidence_probability": 0.96,
      "forecast_relevance_probability": 0.98,
      "positive_implication_probability": 0.55,
      "negative_implication_probability": 0.45,
      "concepts": [
        {
          "name": "acquisition_role",
          "value": {"role": "buyer", "announced_value": 2000000000},
          "probability": 0.98,
          "evidence_quote": "Alpha agreed to acquire Beta for $2 billion"
        }
      ],
      "evidence_quotes": ["Alpha agreed to acquire Beta"]
    },
    {
      "issuer_name": "Beta",
      "ticker": null,
      "exchange": null,
      "identity_source": "explicit_text",
      "identity_confidence_probability": 0.96,
      "forecast_relevance_probability": 0.99,
      "positive_implication_probability": 0.96,
      "negative_implication_probability": 0.08,
      "concepts": [
        {
          "name": "acquisition_role",
          "value": {"role": "target", "shareholder_premium_percent": 30},
          "probability": 0.99,
          "evidence_quote": "Beta shareholders will receive a 30% premium"
        }
      ],
      "evidence_quotes": ["Beta shareholders will receive a 30% premium"]
    }
  ],
  "unresolved_issuer_mentions": []
}
```
