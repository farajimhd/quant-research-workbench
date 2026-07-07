# SEC Acceptance Timezone Repair

Use this script when SEC rows were inserted with `accepted_at_utc` shifted by a timezone-sized offset relative to the explicit timezone in `acceptance_datetime_raw`.

## What It Repairs

Target table:

```text
q_live.sec_filing_v2
```

The repair reads rows from `sec_filing_v2 FINAL`, recomputes `accepted_at_utc` from `acceptance_datetime_raw` using normal ISO/RFC3339 timestamp semantics, and inserts newer replacement rows into the same `ReplacingMergeTree(inserted_at)` table.

It does not mutate rows in place.

## Why It Exists

SEC submissions API values ending in `Z` are UTC timestamps. A bad parser version incorrectly converted those rows through New York time, shifting some live rows four or five hours too late. That breaks live consumers such as `text_embed_gateway`, which selects SEC rows by `accepted_at_utc`.

## Safety Rules

- Dry-run is the default.
- Only rows inside the `inserted_at` window are inspected.
- Only configured `accepted_at_source` values are inspected.
- The default source list is intentionally limited to live submissions rows:
  `submissions_recent`.
- Historical bulk mirror sources are not repaired by default. Use an explicit
  `--repair-sources` value for bulk rows only after auditing the `sec_core`
  mirror tables and planning the historical rewrite.
- A row is repaired only if the corrected timestamp differs from the stored timestamp by 3 to 6 hours.
- Rows whose corrected timestamp moves to another month partition are skipped by default.
- Replacement rows get a new `source_run_id` and `accepted_at_source` suffix `_timezone_repair`.
- Secrets are redacted in manifests.

## Dry Run

Run this first:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\sec\edgar\sec_acceptance_timezone_repair.py --lookback-hours 96
```

The script writes:

```text
D:\market-data\prepared\sec_acceptance_timezone_repair\<run_id>\
```

Important files:

```text
timezone_repair_candidates.jsonl
timezone_repair_skipped.jsonl
sec_filing_v2_timezone_repair_rows.jsonl
sec_acceptance_timezone_repair_manifest.json
sec_acceptance_timezone_repair_summary.md
```

## Execute

After reviewing the dry-run summary:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\sec\edgar\sec_acceptance_timezone_repair.py --lookback-hours 96 --execute
```

For an explicit UTC inserted-at window:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\sec\edgar\sec_acceptance_timezone_repair.py --start-inserted-at "2026-07-07T00:00:00Z" --end-inserted-at "2026-07-08T00:00:00Z" --execute
```

## Historical Bulk Sources

The `sec_core` bulk mirror contains sources such as:

```text
submissions_bulk
submissions_bulk_recent
submissions_bulk_fragment
submissions_bulk_recent_fallback_repair
submissions_bulk_fragment_fallback_repair
```

Those sources can represent millions of rows. Do not include them in the live
repair command. The current review indicates their `Z` timestamps are already
stored as UTC wall-clock values; if a historical repair is ever considered, run
it as a separate planned operation with an explicit audit, date window, and
source list.

## Validate

Use `FINAL` because `sec_filing_v2` is a `ReplacingMergeTree`:

```sql
SELECT
    accepted_at_source,
    min(accepted_at_utc) AS min_accepted,
    max(accepted_at_utc) AS max_accepted,
    count() AS rows
FROM q_live.sec_filing_v2 FINAL
WHERE source_run_id LIKE 'sec_acceptance_timezone_repair_%'
GROUP BY accepted_at_source
ORDER BY rows DESC;
```

Spot-check recent rows:

```sql
SELECT
    cik,
    accession_number,
    acceptance_datetime_raw,
    accepted_at_utc,
    accepted_at_source,
    source_run_id,
    inserted_at
FROM q_live.sec_filing_v2 FINAL
WHERE source_run_id LIKE 'sec_acceptance_timezone_repair_%'
ORDER BY inserted_at DESC
LIMIT 20;
```

## After Repair

Restart `text_embed_gateway` or let its next live cycle run. If it already built bad SEC context rows before this repair, rebuild that affected context/embedding window separately.
