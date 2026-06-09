# SEC Acceptance Date Fallback Fill

This is the final low-precision fallback for SEC filings that still have no staged accepted timestamp after:

1. `submissions.zip` recent filing backfill
2. older submissions fragment backfill
3. accession `.hdr.sgml` header fallback

It inserts rows into:

```text
sec_core.sec_bulk_mirror_filing_acceptance_v1
```

using:

```text
accepted_at_utc = filing_date 00:00:00 UTC
accepted_at_source = filing_date_midnight_fallback
acceptance_datetime_raw = YYYYMMDD
```

These rows are date-level placeholders only. They keep the date field consistent, but they are not exact intraday event timestamps.

## Dry Run

Local laptop:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_acceptance_date_fallback_fill.py --output-root-win D:/market-data/prepared/sec_acceptance_date_fallback_fill
```

Workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_acceptance_date_fallback_fill.py --output-root-win D:/market-data/prepared/sec_acceptance_date_fallback_fill
```

## Execute

Local laptop:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_acceptance_date_fallback_fill.py --execute --output-root-win D:/market-data/prepared/sec_acceptance_date_fallback_fill
```

Workstation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_acceptance_date_fallback_fill.py --execute --output-root-win D:/market-data/prepared/sec_acceptance_date_fallback_fill
```

## Arguments

- `--execute`: inserts candidates. Without it, only writes diagnostics.
- `--target-database`: default `q_live`.
- `--target-table`: default `sec_filing_v2`.
- `--stage-database`: default `sec_core`.
- `--stage-table`: default `sec_bulk_mirror_filing_acceptance_v1`.
- `--max-rows`: optional safety cap. Default `0`, meaning no cap.
- `--batch-size`: insert batch size. Default `5000`.
- `--output-root-win`: run output root.
- `--storage-policy`: defaults from `SEC_CLICKHOUSE_STORAGE_POLICY`, then `CLICKHOUSE_LIVE_STORAGE_POLICY`.

## Outputs

Each run writes:

- `candidate_rows.jsonl`
- `inserted_rows.jsonl`
- `sec_acceptance_date_fallback_fill_manifest.json`
- `sec_acceptance_date_fallback_fill_summary.md`
