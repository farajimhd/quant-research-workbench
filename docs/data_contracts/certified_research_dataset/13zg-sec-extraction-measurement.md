# Bounded retained SEC extraction measurement

The unified launcher has a read-only `measure-sec-extraction` command. Select
one to eight unique retained documents; no capacity profile is needed because
it creates no tables, writes no data and acquires no provider sources.

```powershell
python -B scripts/prepare_certified_research.py measure-sec-extraction `
  --measurement-sec-document '320193,0000320193-25-000079,aapl-20250927.htm' `
  --measurement-sec-document '1652044,0001652044-25-000014,goog-20241231.htm' `
  --measurement-sec-document '1326801,0001326801-25-000017,meta-20241231.htm' `
  --format json
```

Each document requires a unique bounded source census, one payload fetch, a
source-hash-checked parse and a second matching census. The 16MiB source limit
and parser complexity/fact bounds still apply. Results include fetch/parse time,
payload bytes, observation/context/cover counts, rejection reasons, extraction
metadata size and extraction/content hashes. Payloads and parser outputs are
discarded after each unit; the returned result contains measurements only.

An October 14 live launcher run observed:

| Filing | Source bytes | Observations | Rejections | Contexts | Fetch seconds | Parse seconds | Extraction JSON bytes |
|---|---:|---:|---:|---:|---:|---:|---:|
| Apple | 1,520,222 | 271 | 9 | 106 | 0.396 | 0.064 | 134,565 |
| Alphabet | 2,528,358 | 350 | 0 | 172 | 0.418 | 0.078 | 175,939 |
| Meta | 2,369,783 | 262 | 5 | 106 | 0.376 | 0.070 | 121,492 |

Total: 6,418,363 source bytes, three payload fetches and 883 observations.
Timing is environment/run dependent and excludes other source metadata queries,
archive copying, reconciliation, publication, readback, merges and downstream
features. Extraction JSON size is not compact ClickHouse disk size. The report
explicitly returns `observed_only`, `upper_bound_proven=false` and
`storage_rate_qualified=false`; it cannot serve as an admitted production pilot
profile. Required compressed-storage, staging/merge peak and representative
full-path efficiency qualification remain unfinished. Rejections remain evidence
to resolve before full feature coverage, not silently omitted observations.

A subsequent Apple command confirmed all nine numeric rejections were
`unsupported_numeric_transform` and identified eight additional rejected cover
facts. The report now includes both numeric and cover rejection-reason counts so
those parser coverage gaps can be inspected and resolved explicitly. Current
successful extraction totals therefore do not establish complete filing coverage.
