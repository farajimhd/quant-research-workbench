# News Label Studio

News Review is a standalone, localhost-only reviewer for all 352,559 articles
in the 2025-2026 review population: 347,515 training/development articles and
the 5,044 articles formerly reserved as the August 2026 holdout. V61
mismatches are one predefined view over that population, not the data source.
The tool does not import the main frontend or run through a production service.

The working source and all operator decisions live in ClickHouse. Markdown
audit files and the generated audit controller are not runtime dependencies.

## Start

From the repository root:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\g835l\miniconda3\envs\ml4t\python.exe scripts\run_news_audit_reviewer.py
```

The launcher validates the source lineage and opens
`http://127.0.0.1:8812`. Stop it with `Ctrl+C` in the terminal.

The first run creates the ClickHouse tables and materializes a frozen source
snapshot from the frozen evaluation assignments, deterministic V61 replay, and
canonical ClickHouse news content. The initial replay can take several minutes;
later starts validate the ready manifest and do not reimport the population.
Use `--workers 1..32` to control preparation parallelism.

Use `--prepare-only` to prepare or validate ClickHouse without starting the UI.

## Review workflow

1. Start in the flat **All review news** table or select a predefined view
   such as V61 mismatches, false negatives, false positives, or unreviewed.
2. Search and filter Gold, V61, policy-expected, policy-status, synthesis-path,
   title-pattern, provider, ticker, population split, dates, or current operator
   state.
3. Optionally group the filtered table from the left panel by path and title
   pattern, label matrix, title pattern, ticker, month, review status, or a
   custom combination of up to three dimensions.
4. Select a group to keep the articles in the main table while adding the V61
   group rationale above it. Article pagination and counts stay at the top of
   the table.
5. Add an optional lesson, then choose **Label all eligible** or **Label all
   ineligible** to label every article returned by the active search, filters,
   and selected group. The UI updates the visible page immediately while the
   exact result membership is persisted. Use row controls for mixed results.
6. Add campaign, current-view, or article notes as needed, then mark the
   exact query-defined group complete.

Article titles open the full canonical ClickHouse text and lineage in a centered
modal.

## Persistence and authority

The source gold label is never overwritten. Current operator state is derived
from append-only ClickHouse history using the latest revision per article.
Overlapping bulk groups are therefore safe to revise and fully auditable.
Every result-set operation freezes the search/filter/group specification, exact
ordered article membership, source revisions, result hash, operator label, and
lesson. Lessons are durable review evidence for a future News Synthesis upgrade;
they do not silently change the currently deployed model or its predictions.

The tables are:

- `q_live.news_synthesis_v61_review_source_v3`: frozen searchable source
- `q_live.news_synthesis_v61_review_manifest_v3`: source lineage/readiness
- `q_live.news_synthesis_v61_operator_label_history_v3`: article decisions
- `q_live.news_synthesis_v61_review_group_history_v3`: query-group state
- `q_live.news_synthesis_v61_review_note_history_v3`: general/workspace/group notes
- `q_live.news_synthesis_v61_result_label_batch_v1`: result-set query, label,
  lesson, status, count, and membership hash
- `q_live.news_synthesis_v61_result_label_member_v1`: exact articles captured by
  each result-set operation

`population_split` remains visible provenance. It can be filtered, but it does
not restrict personal review: the former August holdout and its 677 V61
mismatches are queryable and labelable like every other article.
