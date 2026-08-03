# News Synthesis V1

News Synthesis is deterministic, evidence-preserving semantic compilation of a
news document. It transforms preserved source metadata and rendered text into:

1. document communication and provenance;
2. atomic issuer/event statements;
3. normalized facts, entities, quantities and analyst opinions;
4. entity-specific semantic sentiment and strength, without using subsequent
   market reaction;
5. compact structured and readable synthesis;
6. derived operational eligibility with explicit reasons; and
7. an evidence trace back to the source text and point-in-time issuer identity.

It is not a market-reaction model, free-form summary, or renamed copy of the
existing V9 authority.

## Implementation boundary

This package starts fresh and does not import prior semantic-labeling or
classification versions. Verified V9 behavior and reviewed regression cases are
requirements, not an architecture dependency.

The approved V1 contract is frozen in:

- `schema/news_synthesis_v1.schema.json`;
- `concept_registry.json`; and
- `TAXONOMY_PROPOSAL.md`.

The taxonomy audit remains read-only:

```powershell
python -m research.text_intelligence.news_synthesis_v1.run_taxonomy_audit
```

It inventories the full 2,000-article gold contract, measures overlaps and
contradictions, and writes generated evidence under
`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1`. It does not change
the existing gold authority.

After taxonomy approval, the non-destructive draft migration is run with:

```powershell
python -m research.text_intelligence.news_synthesis_v1.run_migrate_gold
```

It writes only under
`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\gold_migration_v1`.
The existing V3 gold authority is never modified. Draft records marked
`review_required` must be manually certified before cutover.
