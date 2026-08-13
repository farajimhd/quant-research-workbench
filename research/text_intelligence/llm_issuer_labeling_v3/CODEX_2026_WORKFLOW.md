# Codex 2026 issuer-labeling controller

This controller creates a provenance-bound, restart-safe Codex teacher-label dataset from the frozen 2026 Benzinga news authority. It is independent of deterministic News Synthesis. News Synthesis predictions, candidate tickers, provider ticker arrays, market outcomes, and consolidated gold labels are never placed in worker packets.

New output is never called human-certified gold. The only automatic authority levels are:

- `codex_single_pass`: one schema-valid blinded Codex run; teacher/silver only.
- `codex_agreement_confirmed`: two blinded same-model runs agree on the required discrete labels; provisional, not human-certified.
- `codex_adjudicated`: a third blinded same-model run resolves a disagreement; not human-certified.
- `human_certified`: reserved and never assigned by this controller.

## Authorities

- Population and publication time: `q_live.benzinga_news_event_v2 FINAL`, keyed by `canonical_news_id` and bounded by `published_at_utc`.
- Original source lineage: `raw_artifact_path`, `raw_payload_hash`, and the structured rows in `q_live.benzinga_news_source_v2`.
- Rendered text: the exact current `q_live.benzinga_news_rendered_v2 FINAL` row joined by provider identity and `source_revision_key`.
- Renderer: `pipelines.news.benzinga.news_benzinga_render_v2`; valid existing artifacts are not overwritten.
- Gold exclusion: `source_id` membership in the hash-verified consolidated certified-gold manifest. Labels and gold tickers are not loaded into packets.
- V3 semantics: the current `PROMPT.md`/`prompt.py`, `gold_examples.json`, and `schema.py`. The exact prompt, example, and schema hashes are frozen.

The current runtime default is:

`D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v3\codex_2026_v1`

All repository Python invocations must set `PYTHONDONTWRITEBYTECODE=1`.

## Command order

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\g835l\miniconda3\python.exe -m research.text_intelligence.llm_issuer_labeling_v3.run_codex_2026 inventory
C:\Users\g835l\miniconda3\python.exe -m research.text_intelligence.llm_issuer_labeling_v3.run_codex_2026 render-missing
C:\Users\g835l\miniconda3\python.exe -m research.text_intelligence.llm_issuer_labeling_v3.run_codex_2026 freeze
C:\Users\g835l\miniconda3\python.exe -m research.text_intelligence.llm_issuer_labeling_v3.run_codex_2026 prepare-packets --pilot-size 20
C:\Users\g835l\miniconda3\python.exe -m research.text_intelligence.llm_issuer_labeling_v3.run_codex_2026 label
```

`freeze` requires the task-owned controller source to be committed. Once a packet has been labeled, the frozen population must not be mutated; corrections require a new dataset version.

`label` prints pending packet IDs and their complete worker-task files. The controller does not silently substitute an API model for Codex subagents. The primary Codex controller spawns each task with no inherited context and supplies the task file contents directly. A completed JSON array is ingested through standard input:

```powershell
Get-Content -Raw D:\tmp\worker-result.json |
  C:\Users\g835l\miniconda3\python.exe -m research.text_intelligence.llm_issuer_labeling_v3.run_codex_2026 label `
    --packet-id single_pass-pilot-000001 --worker-run-id codex-run-id --stdin
```

The runtime copy preserves raw bytes separately from canonicalized validated labels. Re-ingesting byte-identical output is idempotent; a second distinct completion for the same packet fails closed.

After the pilot is clean, prepare the complete packet set without `--pilot-size`, label every pending packet, and continue:

```powershell
C:\Users\g835l\miniconda3\python.exe -m research.text_intelligence.llm_issuer_labeling_v3.run_codex_2026 validate
C:\Users\g835l\miniconda3\python.exe -m research.text_intelligence.llm_issuer_labeling_v3.run_codex_2026 prepare-qc
C:\Users\g835l\miniconda3\python.exe -m research.text_intelligence.llm_issuer_labeling_v3.run_codex_2026 label-qc
C:\Users\g835l\miniconda3\python.exe -m research.text_intelligence.llm_issuer_labeling_v3.run_codex_2026 prepare-adjudication
C:\Users\g835l\miniconda3\python.exe -m research.text_intelligence.llm_issuer_labeling_v3.run_codex_2026 adjudicate --packet-id ... --worker-run-id ... --stdin
C:\Users\g835l\miniconda3\python.exe -m research.text_intelligence.llm_issuer_labeling_v3.run_codex_2026 consolidate
C:\Users\g835l\miniconda3\python.exe -m research.text_intelligence.llm_issuer_labeling_v3.run_codex_2026 report
C:\Users\g835l\miniconda3\python.exe -m research.text_intelligence.llm_issuer_labeling_v3.run_codex_2026 status
```

`prepare-qc` includes every declared high-risk record, adds deterministic representatives for provider, length, issuer-count, forecast class, sentiment, event tag, multi-issuer state, and detectable roundup/recap/analyst/preview strata, then fills deterministically to at least 15%.

Agreement uses the declared 0.5 thresholds. Resolved ticker sets (with null-ticker identities kept separate), forecast class, derived sentiment, event tags, and issuer roles require exact equality. Evidence disagreement is recorded but does not alone force adjudication. Probability differences are reported in tolerance bands and do not alone define disagreement.

## Required gates

Before full labeling:

1. Run focused tests.
2. Run `synthetic-dry-run` and verify the idempotent second ingestion.
3. Freeze the real population and prepare an approximately 20-article pilot.
4. Use fresh no-context subagents for pilot packets and validate coverage, schema, isolation attestations, hashes, persistence, and restart behavior.
5. Report pilot results and estimated full workload.
6. Continue only when no correctness blocker exists.

Calendar-year completeness is explicit. A freeze created before `2027-01-01T00:00:00Z` is a current 2026 snapshot, not a completed calendar-2026 population, even if every record currently present has been labeled.
