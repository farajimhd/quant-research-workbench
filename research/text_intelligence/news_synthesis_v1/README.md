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

## Production engine

`engine.py` independently derives the document envelope, point-in-time
issuer/security identities, atomic statements, exact evidence spans, typed
facts, issuer participation, semantic sentiment, issuer views, readable
evidence-preserving synthesis, and operational eligibility. Provider tickers
are candidates only; text evidence and the point-in-time identity index decide
which issuers participate.

Generated documents use `production` provenance, distinct from manual
`certification` and draft `migration`. Exactly one provenance type is valid.
`storage.py` owns the versioned `q_live.news_synthesis_v1` table. Historical
backfill and live Text Intelligence call the same engine and contract. Source
news remains visible when synthesis is missing, and title-only/unrendered rows
are processed from their title with explicit text-availability state.

Dry-run the resumable daily build:

```powershell
python -m research.text_intelligence.news_synthesis_v1.run_backfill `
  --start 2010-01-01 --end-exclusive 2026-08-04 --workers 16
```

Persist it after the dry run succeeds:

```powershell
python -m research.text_intelligence.news_synthesis_v1.run_backfill `
  --start 2010-01-01 --end-exclusive 2026-08-04 --workers 16 --execute
```

Completed dates are checkpointed and skipped on restart. Backend News list and
detail responses load V1 without making canonical News availability depend on
it. Canvas News Detail presents the envelope, issuer views, concepts, exact
evidence, typed facts, sentiment strengths, eligibility and quality state.
V5/V9 News labels are not used as a semantic fallback.

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

Initialize the separate V1-only certification workspace with:

```powershell
python -m research.text_intelligence.news_synthesis_v1.run_initialize_certification
```

Review packets contain preserved source evidence and the V1 draft only. They
exclude prior V3 label fields. Certified labels are written separately under
`manual_certification_v1/certified_labels` and cannot contain unresolved
quality flags.
