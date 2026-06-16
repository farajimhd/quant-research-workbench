# SEC EDGAR Pipeline

This package contains the SEC EDGAR historical workflow:

- SEC bulk and daily archive download helpers;
- daily archive validation and content discovery;
- exact-file failed archive deletion;
- acceptance timestamp repair helpers;
- historical backfill orchestration over the stages that exist today;
- legacy bulk mirror ingest helpers retained for traceability.

Preferred current orchestration path:

```powershell
python -m pipelines.sec.edgar.sec_historical_backfill_orchestrator --help
```

Targeted validation path:

```powershell
python -m pipelines.sec.edgar.sec_validate_downloaded_archives --help
```

See `sec_historical_backfill_orchestrator_guide.md` for one-command historical runs and PowerShell history export commands.

Old `research/mlops/sec_*.py` wrappers are archived under `pipelines/archive/legacy_wrappers/research_mlops/`. Do not use them for new runs.
