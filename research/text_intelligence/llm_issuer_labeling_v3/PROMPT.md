# Standalone LLM Issuer-News Labeling Prompt V3

V3 uses fixed issuer-level labels and compact evidence sentence IDs. It is
independent of News Synthesis: issuer extraction and all labels come from the
LLM.

## Input

Split normalized text deterministically into ordered sentences before calling
the LLM. Sentence numbering is formatting, not semantic extraction.

```json
{
  "published_at_utc": "2026-01-15T14:30:00Z",
  "normalized_sentences": [
    {"sentence_id": 1, "text": "Normalized title."},
    {"sentence_id": 2, "text": "Normalized article sentence."}
  ],
  "metadata": {},
  "market_context_before_publication": []
}
```

Sentence IDs must be unique, positive, consecutive integers starting at `1`.
Optional market observations must be timestamped no later than publication.
Post-publication prices are excluded because they reveal the reaction.

## Output schema

```json
{
  "schema_version": "llm_issuer_news_labels_v3",
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
      "evidence_sentence_ids": [1]
    }
  ],
  "unresolved_issuer_mentions": []
}
```

Return one object per distinct issuer found in the news. Merge aliases for the
same issuer and keep different issuers separate.

## Allowed values

All probabilities are finite JSON numbers from `0.0` to `1.0`.

- `identity_source`: `explicit_text | metadata | llm_inference`
- `time_scope`: `current | forward | historical | mixed | unclear`
- `claim_source`: `issuer | regulator | analyst | editorial | mixed | unknown`
- `event_tags`: zero or more sorted unique values from:

```text
acquisition | analyst_action | asset_sale | capital_return |
capital_structure | clinical_trial | commercial_contract | earnings |
financing | financial_condition | guidance | legal | listing |
management_governance | market_observation | operations | ownership |
partnership | product | regulatory | solvency | strategy | workforce |
other_material
```

- `issuer_roles`: zero or more sorted unique values from:

```text
primary_subject | acquirer | target | buyer | seller | partner | customer |
supplier | borrower | lender | investor | investee | plaintiff | defendant |
regulatory_subject | competitor | mentioned_other
```

- `evidence_sentence_ids`: one to three unique sentence IDs from the input,
  ordered by their appearance in the article. Together they must support the
  issuer identity and the material labels. Do not copy sentence text into the
  output. Do not return an ID absent from the input.

## Forecast relevance

`forecast_relevance_probability` is the probability that **all** conditions are
true for this issuer:

1. The issuer is identifiable as a security tradable at publication time.
2. Trustworthy evidence in the article is specifically about that issuer.
3. The article newly reports a current issuer event or new issuer guidance.
4. The event is material to earnings, cash flow, assets, liabilities,
   financing, operations, regulatory/legal position, capital structure, or
   survival. Its directional implication may be positive, negative, mixed,
   neutral, or uncertain.
5. The article reports the event itself, rather than only analyst opinion,
   preview, historical recap, price-move explanation, market observation,
   background, or a reference to another article.

Use a low probability if any condition is missing. An analyst rating, scheduled
earnings call, conference appearance, old event, generic description, or
observed price change without a new underlying event is not forecast relevant.

Positive and negative probabilities are independent. Both high means mixed;
both low means neutral or no directional implication.

A neutral material event can therefore have high forecast relevance. Forecast
relevance is event eligibility, not a synonym for directional sentiment.

## Ready-to-use system prompt

```text
Extract every distinct issuer from the supplied financial news and return only
JSON matching llm_issuer_news_labels_v3. Do not output reasoning, Markdown,
comments, copied sentence text, or extra keys.

An issuer may be a public company, private company, listed fund, or other
security issuer. Do not treat a person, product, index, government, regulator,
exchange, analyst, or generic phrase as an issuer unless the text identifies a
real issuing entity. Merge aliases for the same issuer. Keep parents,
subsidiaries, counterparties, and competitors separate when the text does.

Infer identity only from normalized_sentences and metadata. Never invent a
ticker. Use null for an unknown ticker or exchange. Put unresolved issuer-like
mentions in unresolved_issuer_mentions. Return exactly one object per resolved
issuer and sort issuers by issuer_name.

forecast_relevance_probability is the probability that all five are true:
(1) a security tradable at publication is identified; (2) trustworthy local
issuer evidence exists; (3) the article newly reports a current issuer event or
new issuer guidance; (4) the event is material to fundamentals, financing,
operations, assets, liabilities, legal/regulatory position, capital structure,
or survival even when its direction is neutral or uncertain; and (5) the text
reports the event rather than only analyst opinion, preview, recap, observed
price movement, background, or a reference. If any requirement is absent, use
a low probability.

positive_implication_probability is the chance that the issuer-specific news is
materially favorable. negative_implication_probability is the chance it is
materially adverse. They are independent and must not sum to one. Do not borrow
events or direction from another issuer.

A neutral material issuer event may have high forecast relevance while both
directional probabilities remain low.

Use only the allowed categorical values. Add every supported event tag and role
and no unsupported value. Use other_material only for a material event that fits
no other event tag. Sort tags and roles and remove duplicates.

For each issuer return one to three evidence_sentence_ids that jointly support
its identity and material labels. IDs must exist in normalized_sentences, be
unique, and follow article order. Never copy the evidence text into the output.

Use metadata only to clarify publication or source facts. Use optional market
context only when timestamped no later than published_at_utc. Never use a later
price or event. All probabilities must be finite numbers in [0, 1].
```

## Ready-to-use user prompt

```text
Extract and label every issuer using llm_issuer_news_labels_v3:

{{INPUT_JSON}}
```

## Gold-250 Batch workflow

Generated samples, answer keys, raw responses, labels, and metrics are written
under `D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v3`; none
are stored in the repository. The default run is reproducible and disjoint from
the fixed gold-example bank.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.llm_issuer_labeling_v3.run_batch prepare
python -m research.text_intelligence.llm_issuer_labeling_v3.run_batch submit --authorize-cost-usd <plan protected_batch_cost_usd>
python -m research.text_intelligence.llm_issuer_labeling_v3.run_batch status
python -m research.text_intelligence.llm_issuer_labeling_v3.run_batch collect
```

`collect` persists validated issuer labels and automatically evaluates a
completed batch. `evaluate` can be rerun independently from the persisted
labels and disjoint gold answer key. The CLI also provides bounded
`prepare-retry`, `submit-retry`, `collect-retry`, and `merge-retry` commands for
responses that are valid but truncated at the configured output-token ceiling;
retry inputs and lineage remain separate from the primary run.

## Examples

The executable prompt uses the fixed bank in `gold_examples.json`: 11 short,
contrasting examples covering positive, negative, mixed, and neutral eligible
events; analyst actions, price observations, and previews that are not forecast
eligible; and a two-issuer acquisition. The preparation script resolves each
example input from the certified source catalog, verifies its labels against
gold, injects compact cards into the system prompt, and excludes every example
source from the 250-row evaluation sample. Source IDs are never sent to the
model.

The examples below are readable illustrations of the same decision boundaries.

### Current positive guidance

Input sentences:

```text
[1] Acme raised full-year revenue guidance from $500 million to $560 million.
```

```json
{
  "schema_version": "llm_issuer_news_labels_v3",
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
      "evidence_sentence_ids": [1]
    }
  ],
  "unresolved_issuer_mentions": []
}
```

### Analyst action is directional but not forecast relevant

Input sentences:

```text
[1] A broker upgraded Delta to Buy and raised its price target to $40.
```

```json
{
  "schema_version": "llm_issuer_news_labels_v3",
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
      "evidence_sentence_ids": [1]
    }
  ],
  "unresolved_issuer_mentions": []
}
```

### Separate buyer and target evidence

Input sentences:

```text
[1] Alpha agreed to acquire Beta.
[2] Beta shareholders will receive a 30% premium.
```

```json
{
  "schema_version": "llm_issuer_news_labels_v3",
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
      "evidence_sentence_ids": [1]
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
      "evidence_sentence_ids": [1, 2]
    }
  ],
  "unresolved_issuer_mentions": []
}
```
