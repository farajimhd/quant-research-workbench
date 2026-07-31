# News Semantic Ground Truth Guidelines V1

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

## Pilot and taxonomy

The first 100 articles are a taxonomy pilot. Record unsupported but recurring
concepts in `taxonomy_proposals`; do not silently coerce them into an existing
concept. Record identity, passage, rendering, and scope uncertainty in
`ambiguity_notes` and lower confidence accordingly. Freeze revised guidelines
before re-reviewing the pilot and proceeding to the remaining 900 articles.
