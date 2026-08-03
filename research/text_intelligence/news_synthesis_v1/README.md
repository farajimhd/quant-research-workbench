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

The first runnable stage is read-only:

```powershell
python -m research.text_intelligence.news_synthesis_v1.run_taxonomy_audit
```

It inventories the full 2,000-article gold contract, measures overlaps and
contradictions, and writes generated evidence under
`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1`. It does not change
the existing gold authority.

The replacement schema and gold migration remain blocked on explicit approval
of `TAXONOMY_PROPOSAL.md`.
