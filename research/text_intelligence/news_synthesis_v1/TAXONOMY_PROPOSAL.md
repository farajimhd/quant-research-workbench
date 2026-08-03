# News Synthesis V1 Taxonomy Proposal

Status: **approval required**. This document defines the first contract boundary;
no gold record has been migrated.

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

### 3.2 Atomic statement

Every evidence span belongs to one atomic statement. A sentence containing two
independent claims becomes two statements.

| Field | Allowed values | Meaning |
|---|---|---|
| `statement_kind` | `event`, `analyst_opinion`, `forecast`, `market_observation`, `background`, `reference` | Semantic function of the statement. |
| `concept_leaf` | one approved leaf in the concept registry | One normalized event or fact concept; parents are derived. |
| `modality` | `confirmed`, `planned`, `expected`, `opinion`, `rumored` | Epistemic status. No `mixed`; split the statement instead. |
| `time_relation` | `historical`, `current`, `forward` | Relation to publication time. No `mixed`; split the statement instead. |
| `language_direction` | `positive`, `negative`, `neutral` | Text-implied issuer effect. Composite `mixed` is derived across statements. |
| `direction_strength` | integer `0..4` | Evidence strength, not probability or reaction magnitude. |
| `evidence_span` | source field, start, end, exact quote | Mandatory trace to preserved input. |

Facts such as money, percentages, dates, ratings and price targets are stored as
typed fact records linked to a statement. Analyst `rating_from`, `rating_to`,
`target_from` and `target_to` remain separate fields.

### 3.3 Entity participation

Entities and statements are many-to-many. Participation is stated per
statement, so a shared acquisition can have different meaning for acquirer and
target without duplicating or discarding the source text.

| Field | Allowed values |
|---|---|
| `entity_kind` | `issuer`, `security`, `index`, `fund`, `commodity`, `currency`, `person`, `organization`, `place`, `product` |
| `participation_role` | `subject`, `acquirer`, `target`, `counterparty`, `analyst_subject`, `observer`, `context_mention` |
| `identity_status` | `resolved`, `ambiguous`, `unresolved`, `not_tradable_as_of` |

Point-in-time identity evidence is mandatory for security resolution. Provider
tickers are candidate evidence, never semantic truth.

### 3.4 Derived document synthesis

The engine derives, rather than annotates independently:

- affected issuers and ticker-specific issuer views;
- parent concept families from the leaf registry;
- composite issuer direction and conflicting evidence;
- concise evidence-preserving structured summary and readable synthesis;
- document patterns such as analyst report, regulatory report, market roundup,
  mover recap, automated digest and why-moving follow-up;
- forecast, reaction-study, issuer-history and analyst-evaluation eligibility,
  each with a rule identifier and reason;
- quality and unresolved-identity flags.

## 4. Non-overlap rules

1. Automation is only a production method; it cannot be a communication
   purpose or information origin.
2. Analyst and regulator are information origins or statement kinds; they
   cannot be document purposes.
3. Roundup is a derived document pattern from structure and statement
   composition, not a competing event type.
4. A context mention is an entity relationship, never an issuer event.
5. `mixed` is never a primitive modality or time value. Conflicting atomic
   statements remain separate and produce a derived mixed result.
6. Every statement has one concept leaf. Concept parents and aliases are
   registry-derived and cannot coexist as independent predictions.
7. Eligibility is downstream policy and cannot alter semantic direction or
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
| `analyst_event` | analyst opinions become statement units; document purpose determined independently | requires decomposition |
| `regulatory_event` | regulatory concepts/origin move to their proper dimensions | requires decomposition |
| `automated_summary` role/origin | `production_method=automated`; other dimensions re-derived | requires decomposition |
| `issuer_role=mentioned_subject` | entity participation `context_mention` | exact structural move |
| `modality/time= mixed` | split into atomic statements | manual review required |
| `semantic_direction=mixed` | preserve component evidence; derive composite mixed | rule-assisted review |
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

## 6. Approval decisions

Approval is requested for:

1. the News Synthesis objective and exclusion of market reaction;
2. the five document-envelope dimensions;
3. atomic statements with no primitive `mixed` modality/time;
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
