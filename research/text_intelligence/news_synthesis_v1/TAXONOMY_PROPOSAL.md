# News Synthesis V1 Taxonomy Proposal

Status: **approved 2026-08-03**. This document defines the frozen V1 contract
boundary. Gold migration is non-destructive and requires separate manual
certification before cutover.

## 1. Objective

News Synthesis converts preserved news metadata and rendered text into a
deterministic, evidence-grounded semantic document. It answers five separate
questions without allowing one answer to stand in for another:

1. **What kind of document is this?**
2. **Who or what is each statement about?**
3. **What is asserted, observed, expected or opined?**
4. **What does the language imply for each affected issuer?**
5. **For which downstream uses is the evidence suitable?**

It does not predict market reaction. Later reaction products may consume the
synthesis, but subsequent prices cannot influence it.

## 2. Why the current contract must be decomposed

The read-only audit of all 2,000 reviewed articles found:

- 2,000 unique articles, 6,880 issuer units and 18,643 ticker dispositions;
- 82 populated annotation field paths;
- 9 `content_role` values crossed with 6 `source_origin` values in 40 observed
  combinations;
- `automated_summary` used as a role in 118 articles, an origin in 254, and both
  in 108;
- 159 of 306 `analyst_event` articles have no structured analyst opinion;
- 547 `mentioned_subject` records stored as issuer-event units even though a
  mention is a relationship, not an event;
- 3,572 event-concept spellings: 3,161 dotted and 411 flat. Parent, child and
  synonym forms coexist (`earnings`, `earnings.results`, `earnings.beat`,
  `analyst_action`, `analyst.rating_action`, `ma_transaction`).

The existing authority is structurally consistent: there are zero
decision/unit-count contradictions and every issuer unit has evidence, but its
dimensions mix document structure, authorship, production method, event type
and downstream policy.

## 3. Proposed primitive contract

Only primitive observations are annotated. Summaries, categories and
eligibility are derived deterministically from them.

### 3.1 Document envelope

| Field | Allowed values | Meaning |
|---|---|---|
| `document_structure` | `single_subject`, `multi_subject_digest`, `market_overview`, `reference_list` | How the document is organized; not who wrote it or what event it contains. |
| `communication_purpose` | `report`, `analyze`, `preview`, `recap`, `explain_move` | What the document primarily does. Exactly one. |
| `information_origin` | `issuer`, `regulator`, `analyst`, `editorial`, `mixed`, `unknown` | Primary origin of the substantive claim, not the publishing website. |
| `production_method` | `original`, `aggregated`, `syndicated`, `automated`, `unknown` | How the document was produced. |
| `text_availability` | `rendered`, `title_only`, `unrendered`, `invalid` | Whether semantic text was available. |

`subject_scope` is derived from resolved entities and document structure; it is
not separately annotated.

#### `document_structure`

This field describes the organization of the document, not the number of
tickers in its metadata.

| Value | Definition | Boundary example |
|---|---|---|
| `single_subject` | One coherent narrative about one event, decision, thesis or closely related event chain. It may involve several issuers playing different roles. | An acquisition article covering both acquirer and target is still one subject. |
| `multi_subject_digest` | Two or more independent issuer or event items assembled into one document. Each item remains meaningful if removed from the others. | "Ten stocks moving after earnings" with a separate paragraph for each issuer. |
| `market_overview` | A market-, sector-, macro- or cross-asset-level narrative whose main subject is collective conditions rather than a list of independent issuer events. Tickers may appear as examples. | A discussion of inflation, rates and index weakness that cites several stocks. |
| `reference_list` | A primarily enumerative calendar, ranking, screening result or factual lookup list with little connecting narrative. | An earnings calendar or analyst-rating table. |

`single_subject` is determined by narrative unity, not by issuer count.
`multi_subject_digest` requires separable item blocks. `market_overview` requires
one collective market thesis. `reference_list` requires enumeration to be the
document's main utility.

#### `communication_purpose`

This field describes the document's primary communicative job. Exactly one
value is selected from the headline, lead and dominant substantive statements.

| Value | Definition | Excludes |
|---|---|---|
| `report` | Introduces a newly disclosed event, decision, result, action or other concrete information as the main point. | A later story whose main purpose is explaining a move caused by already-public information. |
| `analyze` | Interprets known evidence, compares alternatives, argues a thesis or evaluates implications without a new event being the main point. | A chronological summary of what already happened. |
| `preview` | Describes expectations, scenarios or preparation before a scheduled or possible future event occurs. | Forward guidance newly issued by a company; that is a reported event containing a forecast statement. |
| `recap` | Summarizes events or market activity after a defined period or sequence has completed. | An argument about why those events matter; that is analysis if interpretation dominates. |
| `explain_move` | Starts from an already observed price, volume or attention move and primarily explains its possible catalyst or context. | A primary catalyst report that merely appends a price-action sentence. |

When purposes coexist, the new-information test is applied first: a genuinely
new event that anchors the headline and lead is `report`. Otherwise the dominant
function decides. A tie is recorded as a quality-review issue; it is not
silently converted to another semantic dimension.

#### `information_origin`

This field identifies where the document's main substantive claim originated.
It does not identify the website that published the article.

| Value | Definition | Important boundary |
|---|---|---|
| `issuer` | The principal claim originated from an issuer, its authorized representative or an issuer-filed disclosure. | An issuer-filed 8-K is `issuer`, not `regulator`; the regulator is only the filing venue. |
| `regulator` | The principal claim originated from a regulator, exchange or government authority acting in its official capacity. | An SEC enforcement action is regulatory; an issuer's SEC filing is not. |
| `analyst` | The principal claim is an identifiable analyst or research firm's rating, target, estimate, thesis or recommendation. | Editorial commentary about an analyst industry trend is not analyst-origin unless a specific research claim anchors it. |
| `editorial` | The principal claim or synthesis was produced by the publisher's reporting or analysis rather than by an issuer, regulator or analyst authority. | Quoting an issuer does not make the origin `issuer` when the article's own investigation is the substantive claim. |
| `mixed` | Two or more distinct origin classes provide indispensable co-primary claims and none dominates. | Use only when removing either origin changes the document's main meaning, not merely because several sources are quoted. |
| `unknown` | Available evidence cannot reliably identify the substantive claim's origin. | This is an explicit evidence limitation, not a default for difficult documents. |

Multiple issuers in a shared event still have the single origin `issuer`.
Origin is selected from claim provenance, not provider tags alone.

#### `production_method`

This field describes how the published document was assembled. It is
independent from information origin and communication purpose.

| Value | Definition | Important boundary |
|---|---|---|
| `original` | The publisher authored the document's current narrative or reporting. It may quote external sources. | Original reporting about an issuer announcement remains `original`; origin may still be `issuer`. |
| `aggregated` | The publisher combined or rewrote material from multiple prior items or sources into a new composite document. | Requires synthesis of multiple inputs, not a substantially unchanged reprint. |
| `syndicated` | One external article, release or research item was republished substantially intact, with only formatting or minor editorial changes. | A press release copied nearly verbatim is syndicated even when the provider hosts it. |
| `automated` | A template or software process generated the document from structured inputs with no material human-authored narrative. | The word "automated" inside the subject matter is not evidence of automated production. |
| `unknown` | Metadata and text structure do not support a reliable production determination. | Prefer `unknown` over guessing from writing style. |

#### `text_availability`

This field records whether the semantic compiler received trustworthy content.
It does not describe writing quality.

| Value | Definition | Processing consequence |
|---|---|---|
| `rendered` | The available source content was successfully converted into readable semantic text, with provenance retained. | Full synthesis is permitted. |
| `title_only` | The source legitimately contains a usable title but no substantive body. | Synthesis is restricted to title evidence and explicitly marked title-only. |
| `unrendered` | Source metadata indicates that substantive content exists or should exist, but it was not retrieved or rendered successfully. | Preserve the document, expose the missing-text state and do not treat it as genuinely title-only. |
| `invalid` | The available payload is corrupted, transport/server text, unrelated content or otherwise not valid news evidence. | Reject semantic synthesis while preserving the failure reason and source provenance. |

Each envelope decision must retain its evidence and rule identifier. Envelope
fields describe the document only; ticker-specific meaning belongs exclusively
to atomic statements and entity participation.

### 3.2 Atomic statement

Every evidence span belongs to one atomic statement. A sentence containing two
independent claims becomes two statements.

| Field | Allowed values | Meaning |
|---|---|---|
| `statement_kind` | `event`, `assessment`, `forecast`, `market_observation`, `background`, `reference` | Semantic function of the statement. |
| `concept_leaf` | one approved leaf in the concept registry | One normalized event or fact concept; parents are derived. |
| `epistemic_status` | `confirmed`, `planned`, `expected`, `rumored`, `conditional` | How the claim is asserted. No `mixed`; split the statement instead. |
| `time_relation` | `historical`, `current`, `forward` | Relation to publication time. No `mixed`; split the statement instead. |
| `evidence_span` | source field, start, end, exact quote | Mandatory trace to preserved input. |

#### `statement_kind`

This field classifies what one atomic proposition does. The identity of its
author or source is represented separately.

| Value | Definition | Boundary rule |
|---|---|---|
| `event` | States that an action, occurrence, decision, result or state change happened, exists or was announced. | Issuing guidance is an event; the future value inside that guidance is a separate forecast statement. |
| `assessment` | Expresses an evaluation, recommendation, interpretation or judgment. | An analyst rating, management judgment or editorial valuation thesis is an assessment regardless of who authored it. |
| `forecast` | States a projected future quantity, outcome, timing or condition. | A forecast is the projected proposition itself, not the event of publishing it. |
| `market_observation` | Reports an already observed price, volume, volatility, halt or attention condition. | It records what the market did; it does not establish what caused the move. |
| `background` | Supplies substantive historical or contextual information needed to understand another statement but introduces no current focal claim. | Prior-quarter performance used to contextualize current earnings is background. |
| `reference` | Identifies or locates an entity, instrument, source, calendar item or document without making a substantive claim about it. | A ticker/company pair, source link or list heading can be reference evidence; it is not an issuer event. |

Analyst is deliberately not a statement kind. An analyst's rating is an
`assessment`; an analyst's EPS estimate is a `forecast`; the analyst or research
firm is linked as the claim source.

#### `concept_leaf`

A concept leaf is a stable identifier from a versioned registry, for example
`earnings.eps_beat`, `guidance.lowered` or
`corporate_transaction.acquisition_announced`.

Each registry entry must define:

- one canonical identifier and plain-language definition;
- exactly one parent path;
- accepted lexical and metadata evidence patterns;
- incompatible concepts and required typed facts, if any;
- default sentiment guidance by entity participation role; and
- registry version and deprecation aliases.

One statement receives one leaf. If one sentence says EPS beat while revenue
missed, it becomes two statements. Parent concepts such as `earnings` and
aliases such as `ma_transaction` are derived or retained as migration
provenance; they are not additional predicted concepts.

#### `epistemic_status`

This field records the assertion status, independently from statement kind and
time.

| Value | Definition | Example |
|---|---|---|
| `confirmed` | The source asserts that the event, condition, observation or expressed opinion is real as of the stated time. | "The board approved the buyback." |
| `planned` | An identified actor has stated an intention, commitment or scheduled action that is not yet complete. | "The company plans to open a facility." |
| `expected` | The proposition is an estimate, projection or probabilistic expectation rather than a committed action. | "Revenue is expected to reach $50 million." |
| `rumored` | The proposition is explicitly unverified, anonymously sourced or presented as market speculation. | "The company is rumored to be considering a sale." |
| `conditional` | The proposition applies only if an explicit condition or hypothetical scenario occurs. | "If approved, the transaction would close in June." |

`confirmed` does not mean independently proven true; it means the source
presents the claim as factual. Source attribution and evidence remain visible.

#### `time_relation`

Time is anchored to the article's publication timestamp and the statement's
event time, not to verb tense alone.

| Value | Definition |
|---|---|
| `historical` | The stated event or condition completed before the current focal event and is being used retrospectively or as background. |
| `current` | The event, disclosure, active condition or observation is contemporaneous with publication or newly announced at publication. |
| `forward` | The stated event, period, quantity or outcome lies after publication. |

A current guidance announcement therefore produces at least two statements:
the issuance event is `current`, while its projected revenue is `forward`.
Calendar dates and time ranges are retained as typed facts so the relation can
be audited.

#### `evidence_span`

Every statement must retain `source_field`, zero-based `start`, exclusive `end`
and the exact `quote`. The quote must equal the referenced source substring.
Normalization may add typed facts, but cannot rewrite the evidence. A statement
without a valid span is rejected from the gold authority rather than inferred
from an unexplained rule.

Facts such as money, percentages, dates, ratings and price targets are stored as
typed fact records linked to a statement. Analyst `rating_from`, `rating_to`,
`target_from` and `target_to` remain separate fields.

### 3.3 Entity participation

Entities and statements are many-to-many. Participation is stated per
statement, so a shared acquisition can have different meaning for acquirer and
target without duplicating or discarding the source text.

| Field | Allowed values | Meaning |
|---|---|---|
| `entity_kind` | `issuer`, `security`, `index`, `fund`, `commodity`, `currency`, `person`, `organization`, `place`, `product` | What the resolved or mentioned entity is. |
| `semantic_role` | `affected_subject`, `acquirer`, `target`, `counterparty`, `none` | How the entity participates in the proposition. Exactly one. |
| `discourse_role` | `claim_source`, `context_mention`, `none` | How the entity participates in the communication. Exactly one. |
| `semantic_sentiment` | `positive`, `negative`, `neutral` | Text-implied favorable, adverse or neutral effect of this statement for this entity. |
| `sentiment_strength` | integer `0..4` | Entity-specific sentiment strength; not confidence or expected return. |
| `identity_status` | `resolved`, `ambiguous`, `unresolved`, `not_tradable_as_of` | Point-in-time resolution outcome. |

Semantic and discourse roles are separated because one entity may be both the
affected subject and the source of a claim. Specialized transaction roles take
precedence over generic `affected_subject`.

#### `entity_kind`

| Value | Definition |
|---|---|
| `issuer` | A legal entity that issues securities or is the corporate subject of disclosures and events. |
| `security` | A point-in-time tradable instrument issued by an issuer, including its symbol and listing interval. |
| `index` | A defined non-issued benchmark or index measuring a market or basket. |
| `fund` | A pooled investment vehicle such as an ETF, mutual fund or closed-end fund. |
| `commodity` | A physical or standardized commodity referenced as an economic subject. |
| `currency` | A fiat or digital currency pair/component referenced as a monetary asset. |
| `person` | A natural person, such as an executive, analyst or official. |
| `organization` | A non-issuer institution in context, such as a regulator, exchange, research firm, court or government body. |
| `place` | A country, jurisdiction, market region or physical location. |
| `product` | A named drug, device, service, platform or other commercial product. |

An issuer and its security are distinct entities. The issuer may persist while
its ticker, exchange or security changes over time.

#### `semantic_role`

| Value | Definition |
|---|---|
| `affected_subject` | The entity is directly evaluated, forecast, affected by or performs the proposition and no more specific transaction role applies. |
| `acquirer` | The entity is the buyer or acquiring party in the stated transaction. |
| `target` | The entity or asset is being acquired in the stated transaction. |
| `counterparty` | The entity is another direct party to a contract, partnership, dispute, financing or transaction where `acquirer` and `target` do not apply. |
| `none` | The statement does not assign the entity a substantive participation role. |

Only one semantic role is allowed for one entity-statement link. If the role
changes across claims, separate statements preserve each role.

#### `discourse_role`

| Value | Definition |
|---|---|
| `claim_source` | The entity is the attributed speaker, author or authority responsible for the proposition. |
| `context_mention` | The entity is mentioned for comparison, identification or background but is not affected by the statement. |
| `none` | No discourse-specific role applies. |

For an analyst upgrade, the analyst and research firm are claim sources, while
the evaluated security is the `affected_subject`. The security is not assigned
a new issuer event merely because an opinion was published; it is attached to
an assessment statement.

#### `semantic_sentiment`

Sentiment belongs to the entity-statement link because one shared statement can
have different implications for different participants. Here, sentiment means
the text-implied favorable, adverse or neutral consequence for that entity. It
is not emotional tone, observed price movement or a forecast of market
reaction.

| Value | Definition |
|---|---|
| `positive` | The statement explicitly indicates a favorable outcome, improvement, benefit, reduced risk or strengthened position for this entity. |
| `negative` | The statement explicitly indicates an adverse outcome, deterioration, cost, dilution, increased risk or weakened position for this entity. |
| `neutral` | The statement has no defensible favorable or adverse implication for this entity, or the entity is only a claim source/context mention. |

An acquisition premium may be positive for the target while financing terms
are negative for the acquirer. An observed price increase remains a
`market_observation`; its market-move direction is stored as a typed market
fact, not as semantic sentiment.

#### `sentiment_strength`

| Value | Definition |
|---:|---|
| `0` | No favorable or adverse implication; required when semantic sentiment is neutral. |
| `1` | Weak, indirect, highly conditional or low-materiality implication. |
| `2` | Clear but moderate implication supported by the statement. |
| `3` | Strong, material implication with explicit or quantified evidence. |
| `4` | Exceptional, potentially transformative or existential implication explicitly supported by the text. |

Strength is ordinal evidence severity, not confidence, probability or expected
return. Positive and negative entity-statement links remain separate and are
never cancelled inside an atomic statement.

#### `identity_status`

| Value | Definition | Consequence |
|---|---|---|
| `resolved` | Evidence identifies one entity valid at the publication timestamp. | The canonical point-in-time identity may be used downstream. |
| `ambiguous` | Evidence supports multiple plausible entities and cannot select one safely. | Preserve candidates; block ticker-specific eligibility until reviewed. |
| `unresolved` | No authoritative identity satisfies the available evidence. | Preserve the mention and evidence without inventing a ticker. |
| `not_tradable_as_of` | The issuer or security is identified, but the referenced security was not tradable at that publication timestamp. | Preserve semantics and history; block contemporaneous forecast/reaction use for that security. |

Point-in-time identity evidence is mandatory for security resolution. Provider
tickers are candidate evidence, never semantic truth. Each resolution stores
the matched name/alias, symbol interval, exchange, identifier and rule used.

### 3.4 Derived document synthesis

The engine derives the products below from document-envelope fields, atomic
statements and entity participation. They are not separately annotated labels.

#### Affected issuers and ticker-specific views

An issuer is affected only when a resolved issuer/security participates with a
non-`none` semantic role. Claim sources and context mentions do not become
affected issuers. Each affected issuer view contains:

- the applicable statement IDs and participation roles;
- canonical issuer and point-in-time security identity;
- leaf concepts and typed facts relevant to that issuer;
- positive, negative and neutral evidence kept separately; and
- unresolved conflicts and quality flags.

This permits one shared article to produce different, evidence-correct views
for an acquirer, target and counterparty.

#### Concept hierarchy

The engine expands each approved leaf through its single registry path. For
example:

```text
earnings.eps_beat -> earnings.performance -> earnings
```

Only the leaf is extracted. Parents support search, filtering and aggregation.
Aliases are provenance, not extra concepts, so evaluation cannot award or
penalize the same meaning multiple times.

#### Composite issuer sentiment

The engine groups atomic statements by issuer and retains:

- maximum positive strength and its evidence statements;
- maximum negative strength and its evidence statements;
- all neutral statements relevant to interpretation; and
- whether positive and negative evidence concern the same or different facts.

The derived sentiment is calculated from the strongest positive and negative
primitive strengths. Opposing evidence is comparable only when both strengths
are at least `2` and their absolute difference is at most `1`. This freezes the
V1 materiality rule without subtracting ordinal strengths or treating them as
probabilities:

| Result | Rule |
|---|---|
| `neutral` | No positive or negative statement exists. |
| `positive` | Strongest positive evidence exceeds negative evidence and the opposing evidence is not comparable under the V1 rule. |
| `negative` | Strongest negative evidence exceeds positive evidence and the opposing evidence is not comparable under the V1 rule. |
| `mixed` | Both strongest strengths are at least `2` and differ by no more than `1`. |

Equal weak strengths (`1` and `1`) derive `neutral`: neither is material enough
to support a document-level directional synthesis. The component evidence is
still retained in separate positive and negative statement lists.

#### Structured and readable synthesis

The structured synthesis is assembled from evidence-backed fields in a stable
order:

1. newly reported event or primary assessment;
2. affected entities and their roles;
3. material quantities, dates and before/after values;
4. forward forecasts or conditions;
5. positive and negative implications;
6. observed market context, explicitly separated; and
7. conflicts, ambiguity and missing evidence.

The readable synthesis is a deterministic rendering of that structure. It may
compress wording, but every clause must cite statement IDs and no unsupported
fact may be introduced. It is therefore auditable and can be regenerated after
presentation changes without relabeling the document.

#### Presentation facets, not another taxonomy

Terms such as analyst report, regulatory report, market roundup, mover recap,
automated digest and why-moving follow-up are readable compositions of the
primitive fields. They are not competing stored classes. For example:

```text
multi_subject_digest + recap + editorial + automated
-> "Automated multi-subject recap"
```

The UI may display those facets as separate badges. It must not collapse them
back into one ambiguous `content_role` field.

#### Derived eligibility

Eligibility is computed per issuer and use case after synthesis:

| Product | Minimum contract |
|---|---|
| Forecast trigger | Resolved tradable security; new current issuer event with evidence and semantic implication; not solely preview, recap, explain-move, market observation, background or reference. Analyst-origin material is excluded from the default issuer-event trigger policy and retained separately. |
| Reaction study | All forecast-trigger causal requirements plus a trustworthy availability timestamp and no evidence that the article merely reports an already observed move or previously public event. |
| Issuer history | Resolved issuer and at least one substantive event, assessment, forecast or material background statement. Tradability at publication is not required. |
| Analyst evaluation | Identified analyst/research firm as claim source, resolved affected subject, evidence-backed assessment or forecast, and separate before/after values where applicable. |

Each result includes `eligible`, a versioned policy rule ID, reasons and blocking
quality flags. Changing policy can recompute eligibility without altering the
semantic authority.

#### Quality and identity flags

Derived flags expose, rather than hide:

- ambiguous, unresolved or non-tradable identities;
- invalid, title-only or unrendered text;
- unmatched or overlapping evidence spans;
- unresolved communication-purpose ties;
- registry concepts missing required facts;
- conflicting issuer sentiments; and
- migration records requiring human review.

No quality flag deletes source evidence. It controls certification and
downstream use.

#### Worked derivation example

For: "Company A agreed to acquire Company B for $15.25 per share and expects
the transaction to be accretive next year":

| Layer | Derived or extracted result |
|---|---|
| Document envelope | `single_subject`, `report`, `issuer`, production method from provenance, `rendered` |
| Statement S1 | `event`, `corporate_transaction.acquisition_announced`, `confirmed`, `current` |
| S1 participation | A=`acquirer`; B=`target`; B positive strength 3 if the premium is explicit; A neutral unless the text supplies a supported benefit or cost |
| Statement S2 | `forecast`, `corporate_transaction.expected_accretion`, `expected`, `forward` |
| S2 participation | A=`affected_subject`, positive strength 2; issuer representative=`claim_source` if identified |
| Typed facts | offer price `$15.25/share`; forecast period `next year` |
| A issuer view | acquisition event plus accretion forecast, positive unless separate negative evidence exists |
| B issuer view | acquisition target, positive only from the evidence-backed premium/consideration terms |
| Presentation | badges for `single subject`, `report`, `issuer origin`; no new ambiguous content-role class |
| Eligibility | evaluated independently for A and B with policy IDs and identity/timestamp checks |

The example illustrates why statements, participation and sentiment cannot be
collapsed into one article-level label.

## 4. Non-overlap rules

1. Automation is only a production method; it cannot be a communication
   purpose or information origin.
2. Analyst and regulator are information origins or claim-source entities; they
   cannot be document purposes or statement kinds.
3. Roundup is expressed by document structure and communication purpose, not a
   competing event type or another primary class.
4. A context mention is an entity relationship, never an issuer event.
5. `mixed` is never a primitive epistemic-status or time value. Conflicting atomic
   statements remain separate and produce a derived mixed result.
6. Every statement has one concept leaf. Concept parents and aliases are
   registry-derived and cannot coexist as independent predictions.
7. Eligibility is downstream policy and cannot alter semantic sentiment or
   suppress evidence.
8. Observed price movement is a market observation, not language sentiment and
   not proof of a causal issuer event.

## 5. Existing-to-V1 migration decisions

| Existing field/value | V1 treatment | Migration status |
|---|---|---|
| `market_roundup` | derive from document structure and statement composition | rule-assisted review |
| `mover_recap` | `communication_purpose=recap`; observed moves become market observations | rule-assisted review |
| `why_moving_followup` | `communication_purpose=explain_move` | rule-assisted review |
| `preview` | `communication_purpose=preview` | exact if evidence agrees |
| `editorial_analysis` | `communication_purpose=analyze`; origin determined separately | rule-assisted review |
| `primary_event` | `communication_purpose=report`; event statements split by evidence | requires decomposition |
| `analyst_event` | assessments/forecasts become statement units; analyst identity becomes claim source and origin | requires decomposition |
| `regulatory_event` | regulatory concepts/origin move to their proper dimensions | requires decomposition |
| `automated_summary` role/origin | `production_method=automated`; other dimensions re-derived | requires decomposition |
| `issuer_role=primary_subject` or `analyst_subject` | entity semantic role `affected_subject`, scoped to its statement | rule-assisted review |
| `issuer_role=mentioned_subject` | entity discourse role `context_mention` | exact structural move |
| `modality/time= mixed` | split into atomic statements and map modality to epistemic status | manual review required |
| legacy `semantic_direction=mixed` | preserve component evidence; derive composite mixed sentiment | rule-assisted review |
| event concepts | map aliases to one leaf registry; retain original value in provenance | registry mapping + review |
| eligibility booleans | recompute from V1 semantics and explicit policies | never copied as truth |
| evidence spans and point-in-time identity | preserve and verify byte-for-byte | exact or migration fails |

Every migrated record receives one status:

- `exact`: lossless structural move;
- `rule_mapped`: deterministic mapping with unchanged evidence;
- `review_required`: semantic decomposition or ambiguity remains;
- `rejected`: evidence/identity/hash validation failed.

No existing gold file is overwritten. Migration creates a new versioned gold
authority plus a per-record mapping manifest.

## 6. Approved decisions

Approval covers:

1. the News Synthesis objective and exclusion of market reaction;
2. the five document-envelope dimensions;
3. atomic statements with no primitive `mixed` epistemic-status/time;
4. one concept leaf per statement with derived hierarchy;
5. entity participation separated from event semantics;
6. eligibility as a derived policy product;
7. non-destructive migration with explicit mapping status.

After approval, implementation proceeds in this order:

1. freeze JSON Schema and concept-registry contract;
2. implement structural parser, identity resolver and statement compiler;
3. implement derived synthesis and eligibility policies;
4. draft-migrate all 2,000 records without overwriting V3;
5. generate migration coverage, disagreement and evidence-integrity audits;
6. manually certify all `review_required` records before cutover.
