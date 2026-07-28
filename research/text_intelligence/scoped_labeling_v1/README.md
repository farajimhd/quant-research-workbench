# Issuer-Scoped News and SEC Intelligence V3

This package implements the eight-stage authority used to organize News and
SEC evidence without replacing or duplicating canonical rendered text.

## Eight stages

1. **News structure extraction** parses provider-body structure once and
   excludes later external enrichment from semantic ownership.
2. **News issuer/event scoping** resolves article-local exchange symbols,
   point-in-time aliases, headings, subjects, and relational event
   participants. Provider ticker arrays remain retrieval hints.
3. **Issuer-specific News classification** gives every directly affected
   issuer access to the complete provider publication while labeling only its
   issuer-scoped evidence. Acquisition, partnership, litigation, and analyst
   comparisons may therefore affect multiple issuers without copying another
   issuer's direction or concepts.
4. **SEC document/section extraction** selects meaningful rendered filing
   sections and excludes administrative, signature, contact, and boilerplate
   blocks.
5. **SEC event classification** labels exact section evidence with filing,
   document, point-in-time issuer, and event provenance.
6. **Certification and persistence** writes five exact News and five exact SEC
   runtime audits, checks human-readable expected outcomes, and provides a
   dry-run-by-default resumable full-corpus launcher.
7. **Related-content relationships** persists normalized source, unit, event,
   issuer, and concept edges. It does not copy publication or filing text.
8. **Live and downstream consumption** makes News Intelligence use this same
   issuer-scoping authority for live notifications and historical
   reconciliation. Market AI and prior-news context read the resulting V2
   semantic stream.

## Multi-issuer contract

- The canonical rendered article is the publication-text authority.
- Each directly affected issuer receives the same publication hash and can use
  the intact publication as model input.
- `semantic_evidence_text`, `issuer_role`, `event_id`, `event_tickers`, and
  `evidence_scope` are issuer-specific.
- A shared relationship clause is evidence for each explicit participant.
- Independent clauses in a roundup remain separate ticker observations and
  cannot become forecast triggers.
- Incidental or unresolved background entities do not invalidate a resolved
  event. An unresolved direct counterparty is retained as
  `shared_ambiguous`; it is never silently assigned a false ticker.
- Later `Source [external:*]` enrichment stays auditable but cannot introduce
  subjects, labels, or trigger eligibility.

## Certification

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_certification
```

Generated evidence is written only to:

```text
<machine runtime root>/text_intelligence/scoped_labeling_v3/certification
```

The exact regression set includes a multi-issuer acquisition/analyst case,
clinical events, an alias-conflict case, a market roundup, SEC guidance and
settlement evidence, preferred-stock/warrant financing, an employee-plan
amendment, historical prospectus context, and an administrative SEC
abstention.

## Full-corpus build

Read-only planning is the default:

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_persist
```

Execution requires the matching clean certification manifest:

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_persist --execute
```

The bounded, resumable worker path creates only new versioned products:

- `q_live.scoped_text_labels_v3`
- `q_live.scoped_content_relations_v1`
- `q_live.scoped_text_labels_v3_build_status`

The label table stores only issuer evidence and the canonical publication hash;
the relationship table stores only graph edges. Canonical rendered News and SEC
tables are never mutated. Work is partitioned by corpus and date window, with
bounded label and relationship insert batches.

## Live integration

News Gateway continues to own acquisition and canonical rendering. It sends one
complete candidate to News Intelligence. News Intelligence:

1. runs V3 scoping once;
2. selects eligible issuer units;
3. independently applies the point-in-time QMD price gate;
4. sends the intact article plus issuer-scoped evidence to the model route;
5. persists one `news_semantic_label_v2` row per issuer unit; and
6. dispatches Market AI independently per issuer.

The idempotency identity includes article, unit, ticker, rendered-text hash, and
V3 labeling version.
