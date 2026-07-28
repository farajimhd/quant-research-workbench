# Scoped News and SEC Labeling V1

This package implements six bounded stages and intentionally stops before
related-content linking or downstream cutover.

## Plain-language stages

1. **News Relevant Text Extractor** — extracts ticker-specific passages from
   roundups and multi-ticker articles while preserving the canonical article.
2. **News Text Labeler and Content Classifier** — labels only the selected
   passage. Market observations and why-moving/roundup context cannot become
   forecast or reaction-evaluation targets.
3. **SEC Relevant Document/Section Extractor** — selects meaningful sections
   from the already-rendered filing document and excludes known administrative,
   signature, contact, and boilerplate blocks from semantic labeling.
4. **SEC Text Labeler and Content Classifier** — labels the selected sections
   with exact evidence and retains filing/document provenance.
5. **Certification review** — produces five News and five SEC Markdown audit
   files plus a machine-readable self-review under the machine runtime root.
6. **Versioned persistence** — provides a dry-run-by-default, resumable bounded
   backfill into a new table. It does not change canonical News or SEC tables
   and it does not change any current consumer.

## Stage 5: create certification audits

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_certification
```

Generated files are written under:

```text
<machine runtime root>/text_intelligence/scoped_labeling_v1/certification
```

## Stage 6: persistence script

Read-only planning is the default:

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_persist
```

After the certification set has been reviewed, execution requires explicit
authorization:

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_persist --execute
```

Execution also verifies that the runtime certification manifest contains the
same labeling version, five News audits, five SEC audits, and no unresolved
self-review items.

The executable path creates only:

- `q_live.scoped_text_labels_v1`
- `q_live.scoped_text_labels_v1_build_status`

Work is partitioned by corpus and seven-day window by default. Each worker owns
one bounded window,
labels it, inserts in batches, and records completion. Completed partitions
resume safely. Use `--rebuild-completed` only for an intentional rebuild of the
same labeling version. `--period-days` may be set from 1 to 31; the default
keeps rendered-text memory bounded while avoiding one job per article.

## Explicit non-goals

- No Related Content Linker.
- No News Gateway, SEC Gateway, backend, UI, embeddings, prompt, or reaction
  model cutover.
- No mutation or replacement of canonical rendered text.
