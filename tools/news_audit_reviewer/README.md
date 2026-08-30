# News Label Studio

News Review is a standalone, localhost-only reviewer for all 347,515 articles
in the 2025-2026 training/development population. V61 mismatches are one
predefined view over that population, not the data source. The tool does not
import the main frontend or run through a production service.

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

1. Start in the flat **All training news** table or select a predefined view
   such as V61 mismatches, false negatives, false positives, or unreviewed.
2. Search and filter Gold, V61, policy-expected, policy-status, synthesis-path,
   title-pattern, provider, ticker, dates, or current operator state.
3. Optionally group the filtered table from the left panel by path and title
   pattern, label matrix, title pattern, ticker, month, review status, or a
   custom combination of up to three dimensions.
4. Select a group to keep the articles in the main table while adding the V61
   group rationale and exact group actions above it.
5. Choose **All eligible** or **All ineligible** to immediately append one
   operator label for every matching article. Choose **Mixed** to label rows
   individually with the Eligible/Ineligible controls.
6. Add campaign, current-view, or article notes as needed, then mark the
   exact query-defined group complete.

Article titles open the full canonical ClickHouse text.

## Persistence and authority

The source gold label is never overwritten. Current operator state is derived
from append-only ClickHouse history using the latest revision per article.
Overlapping bulk groups are therefore safe to revise and fully auditable.

The tables are:

- `q_live.news_synthesis_v61_review_source_v3`: frozen searchable source
- `q_live.news_synthesis_v61_review_manifest_v3`: source lineage/readiness
- `q_live.news_synthesis_v61_operator_label_history_v3`: article decisions
- `q_live.news_synthesis_v61_review_group_history_v3`: query-group state
- `q_live.news_synthesis_v61_review_note_history_v3`: general/workspace/group notes

The source snapshot is restricted to `training_development`. All 5,044 exposed
August holdout articles, including its 677 mismatches, are excluded and cannot
be queried or labeled here.
