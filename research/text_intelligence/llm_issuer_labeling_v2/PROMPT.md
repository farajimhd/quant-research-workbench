# Standalone LLM Issuer-News Labeling Prompt V2

> Superseded by `research/text_intelligence/llm_issuer_labeling_v3/PROMPT.md`,
> which replaces copied evidence quotes with compact sentence IDs.

This compact contract uses only fixed issuer-level tags. The LLM extracts
issuers and labels each issuer independently.

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
prices are excluded because they reveal the reaction to the news.

## Output schema

```json
{
  "schema_version": "llm_issuer_news_labels_v2",
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
      "event_tags": [],
      "issuer_roles": [],
      "time_scope": "current",
      "claim_source": "issuer",
      "evidence_quotes": []
    }
  ],
  "unresolved_issuer_mentions": []
}
```

Return one object per distinct issuer found in the news. Merge aliases for the
same issuer and keep different issuers separate.

## Allowed values

All probabilities are finite JSON numbers from `0.0` to `1.0`.

`identity_source`:

```text
explicit_text | metadata | llm_inference
```

`event_tags` is a sorted array containing zero or more of:

```text
acquisition
analyst_action
asset_sale
capital_return
capital_structure
clinical_trial
commercial_contract
earnings
financing
financial_condition
guidance
legal
listing
management_governance
market_observation
operations
ownership
partnership
product
regulatory
solvency
strategy
workforce
other_material
```

`issuer_roles` is a sorted array containing zero or more of:

```text
primary_subject
acquirer
target
buyer
seller
partner
customer
supplier
borrower
lender
investor
investee
plaintiff
defendant
regulatory_subject
competitor
mentioned_other
```

`time_scope`:

```text
current | forward | historical | mixed | unclear
```

`claim_source`:

```text
issuer | regulator | analyst | editorial | mixed | unknown
```

`evidence_quotes` contains zero to three short verbatim substrings of
`normalized_text`. Tags have no separate probability or evidence object; this
keeps the response compact.

## Forecast relevance

`forecast_relevance_probability` is the probability that **all** of these are
true for this issuer:

1. The issuer is identifiable as a security tradable at publication time.
2. The article contains trustworthy evidence specifically about that issuer.
3. It newly reports a current issuer event or newly issued forward guidance.
4. The event materially changes expectations for earnings, cash flow, assets,
   liabilities, financing, operations, regulatory/legal position, or survival.
5. The article reports the event itself. It is not solely an analyst opinion,
   preview, historical recap, price-move explanation, market observation,
   background description, or reference to another article.

A low probability is required when any condition is missing. An analyst rating,
scheduled earnings call, conference appearance, old event, generic description,
or observed price change without a new underlying event is not forecast relevant.

Positive and negative probabilities are independent. Both high means mixed;
both low means neutral or no directional implication.

## Ready-to-use system prompt

```text
Extract every distinct issuer from the supplied financial news and return only
JSON matching llm_issuer_news_labels_v2. Do not output reasoning, Markdown,
comments, or extra keys.

An issuer may be a public company, private company, listed fund, or other
security issuer. Do not treat a person, product, index, government, regulator,
exchange, analyst, or generic phrase as an issuer unless the text identifies a
real issuing entity. Merge aliases for the same issuer. Keep parents,
subsidiaries, counterparties, and competitors separate when the text does.

Infer identity only from normalized_text and metadata. Never invent a ticker.
Use null for an unknown ticker or exchange. Put unresolved issuer-like mentions
in unresolved_issuer_mentions. Return exactly one label object per resolved
issuer and sort issuers by issuer_name.

forecast_relevance_probability is the probability that all five are true:
(1) a security tradable at publication is identified; (2) trustworthy local
issuer evidence exists; (3) the news newly reports a current issuer event or
new issuer guidance; (4) the event has a material positive or negative effect
on expected fundamentals, financing, operations, legal/regulatory position, or
survival; and (5) the article reports the event itself rather than only analyst
opinion, preview, recap, observed price movement, background, or a reference.
If any requirement is absent, use a low probability.

positive_implication_probability is the chance that the issuer-specific news is
materially favorable. negative_implication_probability is the chance it is
materially adverse. They are independent and must not sum to one. Do not borrow
events or direction from another issuer.

Use only the allowed event_tags, issuer_roles, time_scope, claim_source, and
identity_source values in this contract. Add every supported tag and no
unsupported tag. Use other_material only for a material event that fits none of
the other event tags. Tags and roles must be sorted and contain no duplicates.

Use normalized_text as semantic evidence. Metadata may clarify publication and
source facts. Use market_context_before_publication only when its timestamp is
no later than published_at_utc. Never use a later price or event.

All probabilities must be finite numbers in [0, 1]. Use uncertainty rather than
forcing 0 or 1. Every evidence quote must be a verbatim substring of
normalized_text, with at most three quotes per issuer.
```

## Ready-to-use user prompt

```text
Extract and label every issuer using llm_issuer_news_labels_v2:

{{INPUT_JSON}}
```

## Examples

### Current positive guidance

Input: `Acme raised full-year revenue guidance from $500 million to $560 million.`

```json
{
  "schema_version": "llm_issuer_news_labels_v2",
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
      "event_tags": ["guidance"],
      "issuer_roles": ["primary_subject"],
      "time_scope": "forward",
      "claim_source": "issuer",
      "evidence_quotes": ["raised full-year revenue guidance from $500 million to $560 million"]
    }
  ],
  "unresolved_issuer_mentions": []
}
```

### Directional analyst action, but not an issuer event

Input: `A broker upgraded Delta to Buy and raised its price target to $40.`

```json
{
  "schema_version": "llm_issuer_news_labels_v2",
  "issuers": [
    {
      "issuer_name": "Delta",
      "ticker": null,
      "exchange": null,
      "identity_source": "explicit_text",
      "identity_confidence_probability": 0.94,
      "forecast_relevance_probability": 0.03,
      "positive_implication_probability": 0.93,
      "negative_implication_probability": 0.02,
      "event_tags": ["analyst_action"],
      "issuer_roles": ["primary_subject"],
      "time_scope": "current",
      "claim_source": "analyst",
      "evidence_quotes": ["upgraded Delta to Buy", "raised its price target to $40"]
    }
  ],
  "unresolved_issuer_mentions": []
}
```

### Buyer and target receive separate labels

Input: `Alpha agreed to acquire Beta. Beta shareholders will receive a 30% premium.`

```json
{
  "schema_version": "llm_issuer_news_labels_v2",
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
      "event_tags": ["acquisition"],
      "issuer_roles": ["acquirer"],
      "time_scope": "current",
      "claim_source": "editorial",
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
      "event_tags": ["acquisition"],
      "issuer_roles": ["target"],
      "time_scope": "current",
      "claim_source": "editorial",
      "evidence_quotes": ["Beta shareholders will receive a 30% premium"]
    }
  ],
  "unresolved_issuer_mentions": []
}
```
