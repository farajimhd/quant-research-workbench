# News Semantic Ground Truth Guidelines V2

These guidelines govern the blinded human review used to calibrate the News
semantic authority. The publication text is the only sentiment evidence. Do
not use later price action, current V5 output, or the locked dataset split.

## Review unit

Review the complete publication first. Decide whether it contains supported
issuer-specific semantic evidence. Provider ticker links are candidates, not
proof that every linked issuer owns every statement.

- Use one issuer unit per materially affected issuer and issuer role.
- A shared event such as a merger may use the complete publication for both
  issuers, with independent issuer roles, concepts, directions, and evidence.
- A roundup or mover list may contain short ticker-specific passages. Label
  only those passages; do not transfer document-wide language between tickers.
- A market commentary that merely uses tickers as illustrations is
  `non_issuer_market_content` and has no issuer units.
- If the publication contains no supported event, abstain rather than forcing
  neutral.

## Semantic direction

Direction describes the language's economic implication for the scoped issuer,
not realized market reaction.

- `positive`: positive evidence is materially stronger.
- `negative`: negative evidence is materially stronger.
- `neutral`: factual content has no material directional implication; both
  evidence levels must be at most weak.
- `mixed`: independent material positive and negative evidence coexist and
  neither should be erased.

Record positive and negative evidence independently on the ordinal 0-4 scale.
Do not invent article-specific rule weights.

## Evidence provenance

Every issuer unit must include verbatim quotes and exact character spans from
the title, teaser, final rendered text, or a named source lane. Record a concise
semantic rationale explaining how those facts support the direction and
levels. Evidence must describe text available at publication time.

## Eligibility

- `forecast_trigger_eligible`: this publication can introduce new information
  that could justify a prospective reaction forecast for the scoped issuer.
- `reaction_evaluation_eligible`: the publication is an appropriate causal
  trigger for studying subsequent issuer reaction. Recaps and why-moving
  follow-ups are false even if they describe a large already-observed move.
- `issuer_history_context_eligible`: the item is useful causal or explanatory
  history for the issuer, including high-quality follow-ups.

Eligibility is independent of sentiment direction and must include a reason.

## Analyst opinion contract

Analyst research is textual issuer context, not a primary issuer event. Any
issuer unit containing analyst opinion must set `forecast_trigger_eligible` and
`reaction_evaluation_eligible` to false. It remains eligible for issuer history
and may be eligible for the separate analyst-evaluation product.

Extract only facts stated in the publication. Do not join prices, later market
reaction, target attainment, or analyst accuracy during labeling. Those are
separate downstream evaluations.

Each distinct analyst opinion records:

- whether it is an individual, firm-level, or consensus-aggregate opinion;
- analyst and research-firm names plus stated aliases;
- rating action and separate `rating_from` and `rating_to` fields;
- price-target action and separate numeric `price_target_from` and
  `price_target_to` fields with currency;
- explicit forecast horizon, when stated;
- verbatim reasoning and opinion evidence with exact source spans; and
- ambiguity and annotation confidence.

A maintained Overweight rating with a target raised from 360 to 364 is encoded
as `rating_action=maintained`, `rating_to=Overweight`,
`price_target_action=raised`, `price_target_from=360`, and
`price_target_to=364`. A price target is an analyst forecast, not the stock's
current market price.

When an article provides an action but no reasoning, set
`reasoning_not_provided=true`; never invent rationale. Employment validity and
aliases may remain null/empty unless supported by source evidence or a separate
certified entity glossary.

For aggregate statements such as "five analysts downgraded the issuer," use a
`consensus_aggregate` opinion. Preserve the stated upgrade/downgrade action but
leave unavailable from/to values null and explain the missing endpoints in
`ambiguity_notes`; do not manufacture ratings.

## Pilot and taxonomy

The first 100 articles are a taxonomy pilot. V1 remains immutable. Contract
corrections are persisted as V2 review round 2 under a separate annotation
directory. Record unsupported but recurring
concepts in `taxonomy_proposals`; do not silently coerce them into an existing
concept. Record identity, passage, rendering, and scope uncertainty in
`ambiguity_notes` and lower confidence accordingly. Freeze revised guidelines
before re-reviewing the pilot and proceeding to the remaining 900 articles.
