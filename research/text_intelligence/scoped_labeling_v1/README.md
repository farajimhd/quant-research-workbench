# Scoped News and SEC Labeling V2

This package implements six bounded stages and intentionally stops before
related-content linking or downstream cutover.

## Plain-language stages

1. **News Relevant Text Extractor** — resolves passage ownership independently
   of provider ticker count. It uses explicit symbols, exchange-qualified
   symbols, point-in-time issuer names and aliases, headings, and analyst-action
   subjects. Only a document whose material text resolves to one
   provider-linked issuer remains one semantic unit. Mixed articles are split;
   conflicting or unresolved issuer passages abstain.
2. **News Text Labeler and Content Classifier** — labels only the selected
   passage. Market observations, mixed-issuer passages, and
   why-moving/roundup context cannot become forecast or reaction-evaluation
   targets.
3. **SEC Relevant Document/Section Extractor** — selects meaningful sections
   from the already-rendered filing document and excludes known administrative,
   signature, contact, and boilerplate blocks from semantic labeling.
4. **SEC Text Labeler and Content Classifier** — labels the selected sections
   with exact evidence and retains filing/document provenance.
5. **Certification review** — produces five News and five SEC Markdown audit
   files plus a machine-readable self-review under the machine runtime root.
6. **Versioned persistence** — provides a dry-run-by-default, resumable bounded
   backfill into new V2 tables. It does not change canonical News or SEC tables
   and it does not change any current consumer.

## Issuer-scope safety contract

- A provider ticker link is a retrieval hint, not proof that every paragraph
  belongs to that issuer.
- Document structure is evaluated before ticker count.
- A forecast or reaction-evaluation trigger requires one text-resolved issuer
  that is also the exclusive provider-linked issuer.
- Roundups, mixed-issuer editorials, and mixed-issuer analyst coverage are
  ticker-scoped context only.
- A passage that names conflicting issuers is not copied to all of them.
- A passage with an unresolved company-like mention abstains instead of
  inheriting the sole linked ticker.
- Provider-body text is the publication-time semantic authority. Later
  `Source [external:*]` enrichment remains visible for audit but cannot
  introduce issuer subjects, semantic labels, or trigger eligibility.
- Certification Markdown exposes each passage decision and its exact symbol or
  issuer-alias evidence.

## Stage 5: create certification audits

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_certification
```

Generated files are written under:

```text
<machine runtime root>/text_intelligence/scoped_labeling_v2/certification
```

## Stage 6: persistence script

Read-only planning is the default:

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_persist
```

After the V2 certification set has been reviewed, execution requires explicit
authorization:

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_persist --execute
```

Execution verifies that the runtime certification manifest contains the same
labeling version, five News audits, five SEC audits, no unresolved self-review
items, and all required News scope boundaries: a true single-issuer trigger, a
single-link mixed-issuer article, an unresolved-issuer abstention, an
aggregation observation, and scoped analyst/editorial context. It also
requires the canonical reference identity tables; there is no ticker-count
fallback when identity resolution is unavailable.

The executable path creates only:

- `q_live.scoped_text_labels_v2`
- `q_live.scoped_text_labels_v2_build_status`

Work is partitioned by corpus and seven-day window by default. Each worker owns
one bounded window, labels it, inserts in batches, and records completion.
Completed partitions resume safely. Use `--rebuild-completed` only for an
intentional rebuild of the same labeling version. `--period-days` may be set
from 1 to 31; the default keeps rendered-text memory bounded while avoiding one
job per article.

## Explicit non-goals

- No Related Content Linker.
- No News Gateway, SEC Gateway, backend, UI, embeddings, prompt, or reaction
  model cutover.
- No mutation or replacement of canonical rendered text.
