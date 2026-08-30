# News Label Studio

News Label Studio is a standalone, localhost-only reviewer for the 31,856
News Synthesis V61 training mismatches. It does not import the main frontend or
run through a production service.

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
snapshot from the V61 mismatch evaluation, its title-pattern assignments, and
canonical ClickHouse news content. Later starts validate the ready manifest and
do not reimport the population.

Use `--prepare-only` to prepare or validate ClickHouse without starting the UI.

## Review workflow

1. Enter a title/template/teaser search. Select **Include full article text**
   when the phrase may occur only in the canonical body.
2. Optionally filter by gold label, synthesis label, review status, ticker, or
   date range.
3. Choose one to three grouping dimensions. The default exactly reproduces the
   useful path → title pattern → gold hierarchy. Other choices include title
   template, ticker, channel, provider tag, author, provider, month, confusion
   cell, and current review status.
4. Select a result group. The main panel shows its News Synthesis rationale,
   common decision reasons, counts, and articles.
5. Choose **All eligible** or **All ineligible** to immediately append one
   operator label for every matching article. Choose **Mixed** to label rows
   individually with the Eligible/Ineligible controls.
6. Add campaign, workspace, group, or article notes as needed, then mark the
   exact query-defined group complete.

Article titles open the full canonical ClickHouse text. `E`, `I`, and `M` are
group-decision shortcuts outside text fields; `J` advances to the next group
that still has unlabeled articles.

## Persistence and authority

The source gold label is never overwritten. Current operator state is derived
from append-only ClickHouse history using the latest revision per article.
Overlapping bulk groups are therefore safe to revise and fully auditable.

The tables are:

- `q_live.news_synthesis_v61_review_source_v2`: frozen searchable source
- `q_live.news_synthesis_v61_review_manifest_v2`: source lineage/readiness
- `q_live.news_synthesis_v61_operator_label_history_v2`: article decisions
- `q_live.news_synthesis_v61_review_group_history_v2`: query-group state
- `q_live.news_synthesis_v61_review_note_history_v2`: general/workspace/group notes

The source snapshot is restricted to `training_development`. The 677 exposed
August holdout mismatches are excluded and cannot be queried or labeled here.
