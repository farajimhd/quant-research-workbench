# Shared After-Hours Maintenance

This package coordinates after-hours checks for the live/reference data gateways:

- QMD market-data gateway
- Benzinga news gateway
- SEC gateway
- reference market-publication gateway

It does not stop or restart any live service. It only inspects durable coverage
tables/source tables, records maintenance task rows, and generates or runs the
same service-specific gap-fill commands that the gateways already use.

Auto-run eligibility uses the shared service market-hours policy. Massive
`/v1/marketstatus/now` supplies the current active/closed state, and Massive
`/v1/marketstatus/upcoming` supplies full closures and early closes. The local
New York extended-hours clock is only a fallback when Massive is unavailable.

## Source Of Truth

QMD:

- Historical source: `market_sip_compact.events` and
  `market_sip_compact.events_ordinal_continuity`
- Live source: `q_live.qmd_live_event_coverage_v1`
- Repair rule: recent `q_live` gaps are repaired through QMD's REST replay
  fanout so `live_market_events_v1`, continuity rows, and `live_market_bars`
  stay coherent. The runner does not copy historical rows directly into
  `q_live`.

News:

- Coverage source: `q_live.benzinga_news_coverage_manifest_v1`
- Data source for gaps: Benzinga provider through Massive
- Repair command: `pipelines/news/benzinga/news_benzinga_provider_gap_fill.py`

SEC:

- Coverage source: `q_live.sec_coverage_manifest_v1`
- Data source for gaps: SEC current feed, daily archives, submissions, and
  companyfacts
- Repair command: `pipelines/sec/edgar/sec_historical_gap_fill.py`

Reference market publications:

- Coverage source: `q_live.market_reference_publication_coverage_v1`
- Data source for gaps: FINRA daily short-sale volume files and SEC
  fails-to-deliver files, with Massive/IBKR publication sources added as their
  writers are enabled
- Repair command:
  `pipelines/reference_data/market_publications_historical_gap_fill.py`

## Run Commands

Dry run from the laptop:

```powershell
python -m services.maintenance.runner --services qmd,news,sec,reference
```

Execute checks and write maintenance rows:

```powershell
python -m services.maintenance.runner --services qmd,news,sec,reference --execute
```

Execute after-hours from the workstation and allow small eligible gap fills to
run automatically:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\services\maintenance\runner.py --services qmd,news,sec,reference --execute --auto-run
```

PowerShell wrapper from this repo:

```powershell
.\scripts\run_after_hours_maintenance.ps1 -Execute
```

PowerShell wrapper from workstation runtime:

```powershell
D:\TradingML\codes\quant_research_workbench_pipelines\scripts\run_after_hours_maintenance.ps1 -Execute -AutoRun
```

Reference publication maintenance can use the same temporary write database as
the reference gateway:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\services\maintenance\runner.py --services reference --reference-read-database q_live --reference-write-database q_reference_tmp --execute
```

## Output

Each run writes:

- `q_live.service_maintenance_run_v1`
- `q_live.service_maintenance_task_v1`
- `<market-data>/prepared/service_maintenance/<run_id>/maintenance_summary.json`
- `<market-data>/prepared/service_maintenance/<run_id>/maintenance_summary.md`

Tasks include the service, source of truth, affected window, status, generated
command, and compact JSON details. This is meant for after-hours review and
debugging when a gateway has been down or a historical process failed.
