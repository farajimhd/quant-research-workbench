# SEC EDGAR Pipeline

This package contains the SEC EDGAR historical workflow:

- SEC bulk and daily archive download helpers;
- daily archive validation and content discovery;
- exact-file failed archive deletion;
- acceptance timestamp repair helpers;
- legacy bulk mirror ingest helpers retained for traceability.

Preferred module path:

```powershell
python -m pipelines.sec.edgar.sec_validate_downloaded_archives --help
```

Old `research/mlops/sec_*.py` wrappers are archived under `pipelines/archive/legacy_wrappers/research_mlops/`. Do not use them for new runs.
