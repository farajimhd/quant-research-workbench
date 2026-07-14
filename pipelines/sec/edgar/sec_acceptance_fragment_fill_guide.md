# SEC Acceptance Fragment Fill Guide

`sec_acceptance_fragment_fill.py` is the targeted authoritative fallback after the nightly submissions bulk mirror and before `sec_acceptance_raw_metadata_repair.py`.

The bulk ZIP is updated nightly, while each per-CIK submissions endpoint is updated throughout the day. This stage refreshes only CIKs whose canonical filing rows still have a date-only fallback, matches current filings, and then downloads only older fragment files referenced by the fresh per-CIK payload.

## What It Does

1. Queries `q_live.sec_filing_v3` rows whose acceptance timestamp is missing or still uses a date-only fallback.

2. Anti-joins against:

```text
sec_core.sec_bulk_mirror_filing_acceptance_v3
```

3. Downloads and validates `https://data.sec.gov/submissions/CIK##########.json` using the filing's parsed CIK, never the accession prefix.

4. Matches exact `(CIK, accession_number)` keys in the current payload and persists the refreshed JSON under `bulk/submissions/current`.

5. Uses the fresh payload's `filings.files` index to plan only fragments whose `filingFrom` / `filingTo` range overlaps unresolved filing dates. The bulk ZIP index is used only when a direct payload could not be obtained, and the run fails after writing diagnostics if any direct request failed.

6. Downloads fragment JSON files from:

```text
https://data.sec.gov/submissions/<fragment-name>.json
```

7. Parses matching accessions and appends only explicit UTC acceptance values to:

```text
sec_core.sec_bulk_mirror_filing_acceptance_v3
```

8. Writes local diagnostics:

- `direct_submission_results.jsonl`
- `fragment_jobs.jsonl`
- `fragment_results.jsonl`
- `accepted_rows.jsonl`
- `still_not_found_keys.jsonl`
- `still_not_found_ciks.jsonl`
- `sec_acceptance_fragment_fill_manifest.json`
- `sec_acceptance_fragment_fill_summary.md`

## Workstation Commands

Dry run with a small fragment cap:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_acceptance_fragment_fill.py --limit-fragments 25 --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_fragment_fill
```

Execute a small fragment cap:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_acceptance_fragment_fill.py --execute --limit-fragments 25 --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_fragment_fill
```

Full execute:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_acceptance_fragment_fill.py --execute --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_fragment_fill
```

Then run `sec_acceptance_raw_metadata_repair.py --execute`. The unified `sec_historical_gap_fill.py --execute` command runs both stages in this order automatically.

## Useful Arguments

- `--download-workers`: concurrent per-CIK and fragment workers, default `8`.
- `--sec-request-min-interval-seconds`: global request interval, default `0.11`, which stays below SEC's 10 requests/second guidance.
- `--limit-fragments`: smoke-test cap.
- `--download-all-fragments-per-cik`: ignore date ranges and download every older fragment for each remaining CIK.
- `--force-redownload`: redownload fragments even if already saved.
- `--execute`: inserts matched rows into the staging table. Without it, files are downloaded/parsed and local diagnostics are written, but no ClickHouse insert happens.

## Expected Runtime

Runtime depends on how many fragment files are needed. At SEC fair-access speed, a few thousand fragments should usually finish in minutes, plus parsing and ClickHouse insert time. If the still-missing set falls back to accession-level header files later, that pass will be slower.
