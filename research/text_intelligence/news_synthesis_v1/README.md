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

TF-IDF V3 is a feature-only experiment over the identical split and unchanged
V2 residual multi-task MLP. It adds point-in-time canonical issuer aliases to
identify issuer-local clauses, bounded one-clause anaphora, and generic
structured economic relationships (actual-versus-estimate, beat/miss,
increase/decrease magnitude buckets, profit/loss transitions, guidance
changes, financing/debt/liquidity, capital returns, and regulatory outcomes).
Issuer mentions in the new local-clause namespace are replaced by a generic
`<issuer>` token. V3 does not use gold labels, predictions, source IDs, or
company/ticker-specific hard-coded rules, and it does not perform supervised
feature selection. The resolved as-of aliases used for each document are
persisted in the runtime dataset for reproducibility:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.news_synthesis_v1.run_tfidf_supervision_v3
```

V3 refuses to overwrite an existing run. Its arrays, alias authority,
vocabulary, checkpoint, evaluation, and V1/V2/V3 comparison are written only
beneath
`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\tfidf_supervision_v3`.

### TF-IDF V4 direct normalized-text authority

V4 removes the Qwen tokenizer from TF-IDF feature extraction. It queries the
exact frozen 14,253 source IDs directly from
`q_live.benzinga_news_normalized_v1`, including its canonical ticker array,
and consumes the original normalized title,
teaser, body, external, and PDF fields. It reads neither `input_ids` nor Qwen
token rows and performs no decode step. The Qwen supervision dataset remains
only the frozen population, labels, and 75/25 split authority.

V4 deliberately keeps V3's feature families, budgets, point-in-time issuer
aliases, model architecture, hyperparameters, seed, internal tuning policy,
and evaluation procedure unchanged. Its runtime manifest records normalizer
and source hashes in `source_text_authority.jsonl`.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.news_synthesis_v1.run_tfidf_supervision_v4
```

V4 refuses to overwrite an existing run and writes generated data, model,
evaluation, and comparison artifacts only under
`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\tfidf_supervision_v4`.

### TF-IDF V5 original-provider authority

V5 replaces V4's normalized ClickHouse text fields with the retained original
Benzinga provider JSON for each frozen source ID. By default every raw artifact
must exist, match its retained BLAKE2b payload hash, and agree on provider
article identity. The explicit `--allow-revised-original-artifacts` mode permits
a current artifact with hash drift only when provider article ID, original
publication timestamp, and title remain identical; drift counts and both hashes
are retained in the runtime authority manifest.
V5 consumes the original title and teaser, deterministically removes markup
from the original provider body without using normalized-table text, and adds
generic features from original publication/update timestamps, author, URL
domain, channels, tags, and ticker-count metadata. Enriched external and PDF
text are deliberately excluded because they are not part of the original
provider payload.

The 14,253-article population, labels, 75/25 split, point-in-time alias logic,
model architecture, hyperparameters, seed, tuning boundary, and final
evaluation procedure remain unchanged. Generated artifacts are written only
under
`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\tfidf_supervision_v5`.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.news_synthesis_v1.run_tfidf_supervision_v5
```

### TF-IDF V6 controlled source-representation ablation

V6 isolates source representation from the confounded V4/V5 comparison. It
uses the same exact retained provider artifacts, frozen split membership,
labels, source/ticker views, point-in-time aliases, feature extractor and
budgets, model, seed, tuning boundary, and evaluation for three lanes. Only
title/teaser/provider-body representation changes: original provider text,
normalized provider text, or the structured renderer's provider-body text.
External/PDF enrichment, channels/tags, and V5 metadata features are excluded
from every lane. Vocabulary and IDF are fit on training articles only within
each representation.

Artifacts whose current raw bytes no longer match retained authority are
excluded from all lanes rather than accepted asymmetrically. Generated data,
checkpoints, metrics, and the causal comparison are written only under
`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\tfidf_source_ablation_v6`.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.news_synthesis_v1.run_tfidf_source_ablation_v6
```

### TF-IDF V7 provenance-separated multi-view features

V7 keeps the V6 exact-authority population, frozen split membership, labels,
point-in-time aliases, model, seed, tuning boundary, and evaluation unchanged.
It changes features only. Original provider title/teaser/body supply the lexical
view, with the target issuer anonymized. Provider channels/tags and invariant
metadata shape participate without author names or URL domains. Normalized
provider text supplies generic structural and economic-relation features.
External and PDF enrichment participate only through clauses locally grounded
to the target issuer and have separate namespaces.

Provider, normalized-semantic, enrichment, and metadata views are independently
L2-normalized before equal-weight concatenation. Consequently, a long external
page or PDF cannot numerically dominate the provider article. Vocabulary and
IDF remain training-only; no gold labels, predictions, validation outcomes,
company-specific rules, or learned feature selection enter extraction.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.news_synthesis_v1.run_tfidf_supervision_v7
```

Generated data, source authority, vocabulary, checkpoint, evaluation, and the
V6/V7 comparison are written only beneath
`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\tfidf_supervision_v7`.

### Causal market-cap context analysis

The market-cap context analyzer joins the decisive provider-filter article
features to point-in-time capitalization. It prefers provider snapshots whose
observation and insertion times precede publication, resolved by security
identity, and otherwise uses an explicitly marked SEC-shares-times-prior-close
estimate. Missingness, source, and observation age remain visible. It evaluates
market-cap bands together with metadata, rendered length, ticker count, session
timing, session ordinal, and time since prior ticker news; it does not modify
supervision labels or claim accuracy on an already observed holdout.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.news_synthesis_v1.run_provider_market_cap_analysis
```

All bulk article and path artifacts are written only under
`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_market_cap_context_analysis_v3`.

### Market-cap promotion and Funnel V5

The 26 eligible exceptions in the high-precision union received two blinded
compact reviews and selective two-reader full-text adjudication. No label change
was confirmed. Router V5 therefore promotes only two all-split, zero-exception
rules: nano+micro+small mover lists, and more-than-10-ticker lists whose maximum
known cap is micro. Market-cap values must be positive, use known buckets, and
be available strictly before publication; missing or invalid evidence fails
open. Historical backfill attaches bounded point-in-time provider snapshots.

Development routing and the observed holdout regression are documented in
`docs/codex/NEWS_SYNTHESIS_FUNNEL_V5.md`. The observed holdout is regression-only
and is not a fresh accuracy authority.

### Structured metadata Random Forest V1

The independent structured challenger builds a frozen 2010-2025 provider
category catalog, learns only 2025-supported dimensions, trains on decisive
2025 labels, and evaluates chronologically on 2026. It uses sparse provider
metadata, market time, ticker/article context, point-in-time market cap, and
bounded lexical flags. It excludes TF-IDF and rendered text, exact ticker and
source identity, deterministic synthesis outputs, and label provenance.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.news_synthesis_v1.run_structured_metadata_rf build
python -m research.text_intelligence.news_synthesis_v1.run_structured_metadata_rf train-evaluate
python -m research.text_intelligence.news_synthesis_v1.run_structured_metadata_rf validate
```

The contract, chronological metrics, feature-family importance, calibration,
and comparability limits are documented in
`docs/codex/NEWS_STRUCTURED_METADATA_RF_V1.md`. Bulk outputs remain under
`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\structured_metadata_rf_v1`.

### Finalized-label structured metadata RF diagnostics

The forward and reverse diagnostics reuse the exact frozen V1 matrices and
11,434 structured dimensions and replace both targets from the finalized
correction-grade authority. Selection stays within the training year before
the final model evaluates the other year.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.news_synthesis_v1.run_structured_metadata_rf_forward train-evaluate
python -m research.text_intelligence.news_synthesis_v1.run_structured_metadata_rf_forward validate
python -m research.text_intelligence.news_synthesis_v1.run_structured_metadata_rf_reverse train-evaluate
python -m research.text_intelligence.news_synthesis_v1.run_structured_metadata_rf_reverse validate
```

These are finalized-label agreement diagnostics, not release evidence, because
both test-year label populations were corrected after model-disagreement
selection. Results and limitations are documented in
`docs/codex/NEWS_STRUCTURED_RF_FINAL_BIDIRECTIONAL_EVALUATION_V1.md`. Bulk
outputs remain under `D:\TradingML\runtimes\text_intelligence\news_synthesis_v1`.

### Structured RF disagreement blind audit V1

The disagreement audit draws a 1,000-article, 335-stratum weighted sample from
the 2026 metadata-RF disagreements. Two prediction-blind compact reviewers see
only metadata, title, teaser, and three opening sentences. Compact disagreement
or insufficient evidence escalates to two fresh full-text reviewers. Every
vote is evidence-validated, and full-text disagreement remains unresolved.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.text_intelligence.news_synthesis_v1.run_structured_rf_disagreement_audit prepare
python -m research.text_intelligence.news_synthesis_v1.run_structured_rf_disagreement_audit prepare-full
python -m research.text_intelligence.news_synthesis_v1.run_structured_rf_disagreement_audit finalize
python -m research.text_intelligence.news_synthesis_v1.run_structured_rf_disagreement_audit analyze
python -m research.text_intelligence.news_synthesis_v1.run_structured_rf_disagreement_audit analyze-population
python -m research.text_intelligence.news_synthesis_v1.run_structured_rf_disagreement_audit validate
```

The findings and expansion policy are documented in
`docs/codex/NEWS_STRUCTURED_RF_DISAGREEMENT_BLIND_AUDIT_V1.md`. Audit artifacts
remain under
`D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\structured_rf_disagreement_blind_audit_v1`.
