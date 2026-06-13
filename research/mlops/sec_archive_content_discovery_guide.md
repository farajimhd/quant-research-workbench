# SEC Archive Content Discovery

Use this before designing the final SEC filing text loader. It reads the backed-up daily SEC `.nc.tar.gz` archives and reports what is actually inside each filing container.

The script is read-only:

- It does not write ClickHouse.
- It does not download from SEC.
- It does not fetch `.hdr.sgml` timestamps.
- It does not extract all `.nc` files to disk.

## What It Produces

Each run writes a timestamped folder under `--output-root-win`:

```text
sec_archive_discovery_manifest.json
archive_summary.jsonl
document_samples.jsonl
aggregate_summary.json
errors.jsonl
```

The aggregate report includes counts by form type, document type, file extension, content format, empty text, binary-like payloads, non-ASCII text, and representative text samples.

## Smoke Test

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_archive_content_discovery.py --artifact-root-win D:/market-data/sec_core --archive-subdir daily_archives --output-root-win D:/market-data/prepared/sec_archive_content_discovery --start-date 2026-06-05 --end-date 2026-06-06 --archive-workers 1 --max-filings-per-archive 50 --sample-limit 50
```

## Broader Sample

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_archive_content_discovery.py --artifact-root-win D:/market-data/sec_core --archive-subdir daily_archives --output-root-win D:/market-data/prepared/sec_archive_content_discovery --start-date 2026-01-01 --end-date 2026-06-11 --archive-workers 4 --max-filings-per-archive 250 --sample-limit 500
```

## Full Discovery

This scans every downloaded archive and every filing inside each archive. It can take a while because it decompresses the SEC daily archives, but it does not duplicate archive storage.

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_archive_content_discovery.py --artifact-root-win D:/market-data/sec_core --archive-subdir daily_archives --output-root-win D:/market-data/prepared/sec_archive_content_discovery --start-date 2019-01-01 --end-date 2026-06-11 --archive-workers 4 --sample-limit 1000
```

## Important Arguments

- `--artifact-root-win`: root containing `daily_archives`.
- `--archive-subdir`: default `daily_archives`.
- `--archive-workers`: archive-level worker processes.
- `--max-filings-per-archive`: optional cap for fast exploration; `0` means all filings.
- `--sample-limit`: number of representative document samples to keep.
- `--sample-text-chars`: text prefix length in sample rows.

