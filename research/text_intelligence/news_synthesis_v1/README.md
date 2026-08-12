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
`certification`. Historical draft-migration provenance is accepted only by the
archived certification tooling and is never a production input.
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

`taxonomy_audit.py`, `migration.py`, and their runners are retained only to
reproduce the completed V1 design and certification lineage. They are not
exported from the package root, are not imported by the production engine,
service, backend, or frontend, and must not be used to generate production
News semantics.

Refresh the separate V1-only certification workspace from the complete current
certified authority with:

```powershell
python -m research.text_intelligence.news_synthesis_v1.run_initialize_certification
```

A historical migration draft may be supplied only as an explicit bootstrap via
`--bootstrap-draft`. The default certification path neither imports migration
configuration nor reads migration output.

Prepare a non-authoritative review workspace for every source article that is
not already certified with:

```powershell
python -m research.text_intelligence.news_synthesis_v1.run_prepare_pending_conversion
```

This writes only under `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\manual_conversion_v2`.
It freezes the certified exclusion set and source hashes, generates improved
atomic review candidates and packets, and cannot write certified labels. A
candidate remains untrusted until its complete source, envelope, entities,
statements, sentiment and eligibility have been manually reviewed.

Review packets contain preserved source evidence and the V1 draft only.
Certified labels are written separately under
`manual_certification_v1/certified_labels` and cannot contain unresolved
quality flags. Production evaluation reads those certified V1 documents, not
the historical migration or annotation contracts.

Manual review specifications may use `issuer_view_overrides` when independently
recorded positive and negative strengths support a dominant overall direction
that the default composition rule would otherwise render as `mixed`. The
compiler rejects overrides without a non-empty reason or without strength
dominance consistent with the requested direction. Audited corrections to the
original annotations and V1 review specifications are applied with:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_apply_news_synthesis_manual_gold_corrections
```

The correction runner validates every source-bound replacement against the
reviewed annotation, updates the affected review specifications, and
recertifies only those affected articles. It does not recompile the remaining
gold population or change production prediction logic.

The direct-trading sentiment regression audit is generated by the durable V1
runner, not an ad hoc evaluator. It repairs the frozen prediction-blind
candidate contract, writes a versioned offline identity snapshot, records the
failure stage for every missing issuer view, and hashes the exact source,
certified-document, eligible-unit, registry and engine authorities. A comparison
is identity-valid only when every population-authority hash matches:

```powershell
python -m research.text_intelligence.news_synthesis_v1.run_direct_trading_sentiment_audit `
  --output-root D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\direct_trading_sentiment_audit_<run> `
  --previous-manifest D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\_archive\direct_trading_sentiment_audit_<prior>\manifest.json `
  --publish-current
```

The offline snapshot is evaluation evidence only. Historical and live
production continue to load the canonical point-in-time issuer, security,
listing and symbol authority from ClickHouse through
`storage.load_identity_index`.

`current_audit.json` is the integrity-checked current audit pointer and
`artifact_authority.json` records the active runtime authority. Superseded runs
are moved recoverably beneath `_archive` with a dry-run-first command:

```powershell
python -m research.text_intelligence.news_synthesis_v1.run_archive_stale_runtime_artifacts
python -m research.text_intelligence.news_synthesis_v1.run_archive_stale_runtime_artifacts --apply
```

## Qwen embedding supervision baseline

`run_embedding_supervision.py` provides a reproducible learned baseline over the
certified consolidated labels and durable `Qwen/Qwen3-Embedding-0.6B`
embeddings. It keeps every article and all of its issuer units in one official
partition, assigns 75% of embedding-complete articles to training and 25% to an
untouched validation set, and uses a grouped tuning slice inside training for
early stopping. Generated arrays, checkpoints, manifests, and reports are
written only beneath `D:\TradingML\runtimes`.

Run the stages independently:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.news_synthesis_v1.run_embedding_supervision prepare
python -m research.text_intelligence.news_synthesis_v1.run_embedding_supervision train
python -m research.text_intelligence.news_synthesis_v1.run_embedding_supervision evaluate
```

Or prepare and train in one invocation:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.news_synthesis_v1.run_embedding_supervision all
```

The evaluation JSON reports article and issuer forecast-eligibility metrics,
four-class issuer sentiment metrics, and multilabel concept metrics with
per-label support, precision, recall, F1, and accuracy where meaningful. The
baseline is not a production replacement: rare concepts and unmatched
ticker-specific embeddings remain explicit coverage limitations.

Run the controlled representation comparison with:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.news_synthesis_v1.run_representation_comparison
```

This trains the same residual multi-task MLP over Qwen embeddings, training-only
TF-IDF features decoded from the exact durable Qwen token chunks, and stored
OpenAI `text-embedding-3-large` vectors. Because OpenAI coverage is smaller, the
runner reports both the full-population Qwen/TF-IDF comparison and a separately
retrained three-way comparison on the exact OpenAI/Qwen/TF-IDF intersection.
It never imputes missing OpenAI vectors or compares different validation
denominators in the same table.

TF-IDF V2 applies the generic structural lessons from News Synthesis without
using certified outputs or prediction labels as inputs. It separates title,
teaser, body, supplemental and exact target-ticker-local lexical features; adds
title/teaser character n-grams; normalizes financial quantities; and adds
generic temporal, conditional, origin, directional, concept-family, focality
and source-structure indicators. All vocabularies are fitted only on the frozen
training partition:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.news_synthesis_v1.run_tfidf_supervision_v2
```

V2 is a separately versioned experiment and does not replace V1. Its validation
report and direct Qwen/V1/V2 comparison are written beneath
`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\tfidf_supervision_v2`.
