# Source-aware News and SEC Classification Authority V2

V2 combines the existing production News metadata rules with the typed,
exact-evidence semantic authority. It is a certification-stage authority and
does not cut over the live News Gateway or overwrite canonical source tables.

The contract keeps these concerns independent:

- `source_origin`: who produced the content;
- `content_role`: what role this document plays;
- `issuer_relationship`: how the content relates to the linked issuer;
- `source_type` and `source_subtype`: provider format or SEC form/document type;
- `event_concepts`: exact-evidence semantic events;
- `semantic_direction`: component-weighted language direction;
- four operational eligibility flags, including an explicit
  `reaction_evaluation_eligible` boundary.

`company news` is therefore not inferred from ticker count. A direct company
announcement requires `source_origin=issuer_direct`,
`issuer_relationship=direct_announcement`, and a primary event role.

Roundups, mover lists, and why-moving follow-ups are context products rather
than causal forecast triggers. Their document-wide text can mention many
issuers and conflicting events, so V2 does not promote document-wide event
concepts or direction into any linked ticker. It marks
`ticker_scoped_semantics_required`, retains the document as episode-follow-up
context, and excludes it from reaction-direction scoring. A later issuer-memory
consumer must extract only the ticker-relevant structured bullet or sentence
before assigning background semantics.

Run the real, runtime-only certification sample:

```powershell
C:\Users\g835l\miniconda3\envs\ml4t\python.exe -m research.text_intelligence.classification_authority_v2.run_evaluation
```

The evaluation reports taxonomy distributions separately from descriptive
language-direction versus market-reaction agreement. Only reaction-eligible
primary evidence is scored. Market movement is not a ground-truth label for
whether an article was correctly classified.
