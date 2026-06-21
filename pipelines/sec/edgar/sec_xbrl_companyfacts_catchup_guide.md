# SEC XBRL Companyfacts Catch-Up

Use this script when `sec_filing_v2` and `sec_filing_text_v2` are current but
`sec_xbrl_company_fact_v1` is behind.

The script reads XBRL-looking filings from `sec_filing_document_v2`, excludes
accessions already present in `sec_xbrl_company_fact_v1`, fetches SEC
`companyfacts` once per CIK, extracts only the missing accessions, and writes the
same canonical XBRL tables used by the live SEC gateway:

- `sec_xbrl_concept_v1`
- `sec_xbrl_company_fact_v1`
- `sec_xbrl_frame_v1`
- `sec_xbrl_frame_observation_v1`

It does not rewrite filing parents or filing text.

## Dry Run

Run this first. It only queries ClickHouse and writes a manifest; it does not
call SEC and does not insert rows.

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_xbrl_companyfacts_catchup.py --read-database q_live --write-database q_live
```

## Production Catch-Up

This repairs `q_live` directly.

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_xbrl_companyfacts_catchup.py --read-database q_live --write-database q_live --workers 4 --batch-size 10000 --execute
```

## Smoke Test Into Temp DB

Use this when testing a new code version. It reads candidates from `q_live` but
writes rows to `q_sec_tmp`.

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_xbrl_companyfacts_catchup.py --read-database q_live --write-database q_sec_tmp --start-date 2026-05-20 --end-date 2026-06-22 --limit-ciks 1 --workers 1 --batch-size 1000 --execute
```

## Important Arguments

- `--start-date`: inclusive filing/document date. Defaults to the latest
  `filed_at_utc` date already present in the write database.
- `--end-date`: exclusive filing/document date. Defaults to tomorrow UTC.
- `--workers`: number of CIK workers. SEC requests are still globally rate
  limited, so this mostly overlaps network waits and parsing.
- `--sec-request-min-interval-seconds`: default comes from
  `SEC_REQUEST_MIN_INTERVAL_SECONDS`; keep it near `0.12` to stay below SEC's
  10 requests/second guidance.
- `--limit-ciks` and `--limit-accessions`: smoke-test caps.
- `--execute`: required for SEC requests and ClickHouse inserts.

## Outputs

Each run writes under:

```text
D:/market-data/prepared/sec_xbrl_companyfacts_catchup/<run_id>/
```

Key files:

- `sec_xbrl_companyfacts_catchup_manifest.json`
- `sec_xbrl_companyfacts_catchup_results.jsonl`
- `sec_xbrl_companyfacts_catchup_no_facts.jsonl`
- `sec_xbrl_companyfacts_catchup_summary.json`

`no_facts` rows are not necessarily errors. They mean SEC companyfacts did not
expose matching numeric facts for an accession that had XBRL-looking documents.
