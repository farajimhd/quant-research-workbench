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
  "concept_registry_version": "news_synthesis_concepts_v1_31",
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
          "concept_id": "guidance.issued",
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
| `concept_registry_version` | Exactly `news_synthesis_concepts_v1_31` |
| `issuer_name` | Nonempty canonical name inferred by the LLM |
| `ticker`, `exchange` | String or `null` |
| `identity_source` | `explicit_text`, `metadata`, or `llm_inference` |
| All probability fields | JSON number in `[0, 1]` |
| `concepts[].concept_id` | One exact ID from the fixed concept list below |
| Evidence quotes | Verbatim substrings of `normalized_text` |

Positive and negative probabilities are independent. Both high means mixed;
both low means neutral or no directional implication.

## Fixed concept IDs

Concepts are closed-vocabulary multilabel classifications. Use only these
approved News Synthesis leaf IDs; omit a concept rather than inventing one:

```text
unclassified.semantic_claim
market.price_move_observed
market.volume_move_observed
market.short_interest_observed
market.trading_status
market.options_activity
market.money_flow_observed
market.fixed_income_observed
market.currency_move_observed
market.commodity_price_observed
market.catalyst_absent
market.context
market.technical_analysis
analyst.rating_action
analyst.price_target_action
analyst.short_thesis
analyst.issuer_assessment
external.issuer_assessment
earnings.performance
earnings.release_schedule
earnings.restatement
guidance.issued
corporate_transaction.acquisition
corporate_transaction.asset_sale
capital.financing
capital.return
capital.deleveraging
capital.structure
regulatory.action
regulatory.rulemaking
legal.proceeding
clinical.regulatory_milestone
clinical.trial_result
commercial.contract
commercial.competitive_position
commercial.demand_condition
commercial.partnership
product.milestone
corporate.communication_event
governance.management_change
governance.executive_compensation
governance.shareholder_vote
governance.auditor_change
governance.conflict_of_interest
governance.government_formation
governance.legislation
operations.business_update
operations.cost_efficiency
operations.capacity_change
operations.workforce
strategy.valuation_assessment
strategy.portfolio_assessment
strategy.strategic_alternatives
strategy.operational_priority
listing.market_structure
financial.margin
financial.operating_performance
financial.cash_flow
financial.liquidity
financial.loss_exposure
financial.tax_expense
financial.internal_control
financial.credit_quality
financial.interest_rate
financial.monetary_system
financial.system_stability
estimate.revision
ownership.position_change
ownership.position
credit.solvency
index.membership
technology.cybersecurity_incident
technology.nuclear_incident
digital_asset.policy_assessment
labor.unionization
media.appearance
media.coverage_assessment
organization.founding
politics.campaign
politics.policy_assessment
public_health.event
public_safety.incident
natural_disaster.event
commodity.production
commodity.inventory
macro.inflation
macro.household_credit
macro.economic_outlook
macro.trade_activity
macro.foreign_investment
macro.policy_outlook
macro.consumer_confidence
macro.consumer_spending
macro.personal_income
macro.employment
macro.growth
macro.business_inventories
macro.housing_activity
macro.external_balance
geopolitical.sanctions
geopolitical.trade_relations
geopolitical.cooperation
geopolitical.military_risk
geopolitical.human_rights
geopolitical.defense_action
geopolitical.military_event
```

## Forecast-relevance examples

| News about the issuer | Forecast relevant? | Reason |
|---|---|---|
| Reports earnings, raises guidance, receives approval, signs a material contract | Yes, when current and directional | New issuer event can change expectations |
| Announces a material offering, acquisition, lawsuit outcome, recall, or restructuring | Yes, when current and directional | Changes financing, assets, liabilities, legal position, or operations |
| Analyst upgrades a rating or changes a price target | No under this policy | Analyst opinion is labeled as a concept but is not an issuer event |
| Schedules an earnings call or conference appearance | No by itself | Scheduling communication does not change issuer fundamentals |
| Shares rose or fell, with no newly reported underlying event | No | Observed reaction is not causal news |
| Repeats an old event or provides historical background | No | It is not newly reported current information |

## Ready-to-use system prompt

```text
Read the supplied financial news and return only JSON matching
llm_issuer_news_labels_v1 with concept_registry_version
news_synthesis_concepts_v1_31. Do not output reasoning, Markdown, or extra keys.

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
How likely is it that this issuer satisfies every condition below?
1. It is identifiable as a security that was tradable when the news was published.
2. The text contains trustworthy evidence specifically about this issuer.
3. The article newly reports a current issuer event, or newly issued forward
   guidance from the issuer.
4. That event has a material positive or negative implication for expected
   earnings, cash flow, assets, liabilities, financing, operations, regulatory
   or legal position, or survival.
5. The text reports the event itself; it is not solely an analyst opinion,
   preview, historical recap, price-move explanation, market observation,
   background description, or reference to another article.

Use a high probability only when all five conditions are likely satisfied.
Use a low probability when any required condition is absent. Examples that can
qualify include material results or guidance, financing, acquisitions, major
contracts, regulatory or legal decisions, clinical or product milestones,
capital returns, management changes, and operational changes. A passing mention,
scheduled earnings date, conference appearance, old event, repeated known fact,
generic company description, analyst rating, or observed price change alone is
not forecast relevant.

positive_implication_probability is the probability that the new information
is materially favorable for that issuer. negative_implication_probability is
the probability it is materially adverse. They are independent: both may be
high for mixed news and both may be low for neutral news. Do not transfer an
event or direction from one issuer to another.

Concepts are closed vocabulary. Use only a concept_id from the fixed concept
list in this contract. Include only concepts supported by the text, at most 12
per issuer. Never invent, shorten, extend, or combine concept IDs. Use
unclassified.semantic_claim only for material evidence that fits no other ID.

Use normalized_text as semantic evidence. Metadata may clarify publication and
source facts. Use optional market_context_before_publication only when its
timestamp is no later than published_at_utc. Never use a later price or event.

All probabilities must be finite numbers in [0, 1]. Use uncertainty rather than
forcing 0 or 1. Every evidence quote must be a short verbatim substring of
normalized_text. Sort issuers by issuer_name and concepts by concept_id.
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
  "concept_registry_version": "news_synthesis_concepts_v1_31",
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
          "concept_id": "guidance.issued",
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
  "concept_registry_version": "news_synthesis_concepts_v1_31",
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
          "concept_id": "corporate_transaction.acquisition",
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
          "concept_id": "corporate_transaction.acquisition",
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

### Analyst action: directional but not forecast relevant

Input: `A broker upgraded Delta to Buy and raised its price target to $40.`

```json
{
  "schema_version": "llm_issuer_news_labels_v1",
  "concept_registry_version": "news_synthesis_concepts_v1_31",
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
      "concepts": [
        {
          "concept_id": "analyst.rating_action",
          "probability": 0.99,
          "evidence_quote": "upgraded Delta to Buy"
        },
        {
          "concept_id": "analyst.price_target_action",
          "probability": 0.99,
          "evidence_quote": "raised its price target to $40"
        }
      ],
      "evidence_quotes": [
        "upgraded Delta to Buy",
        "raised its price target to $40"
      ]
    }
  ],
  "unresolved_issuer_mentions": []
}
```
