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

The subsequent fundamental parser v2 implements `fixed-zero` for the exact
[XBRL 2020 registry namespace](https://www.xbrl.org/Specification/inlineXBRL-transformationRegistry/REC-2020-02-12/inlineXBRL-transformationRegistry-REC-2020-02-12.html),
preserving the display text while producing exact zero. Cover extraction now
supports reviewed exchange-name inputs for the exact SEC 2015 `exchnameen`
namespace according to the [SEC XBRL guide](https://www.sec.gov/files/edgar/filer-information/specifications/xbrl-guide-2026-08-14.pdf).
Raw exchange text and transformed EDGAR codes are both retained; unsupported
inputs and namespaces remain rejected. Candidate grouping uses transformed codes.
The reviewed finite input set does not claim complete implementation of every
SEC exchange-name spelling. New parser/source hashes require measured profile
requalification; prior extraction rows are not overwritten.

The three-filing live launcher rerun after these changes produced 280/350/267
numeric observations and 17/6/3 cover facts for Apple/Alphabet/Meta respectively,
with zero numeric or cover rejections in this bounded sample. Total observations
rose from 883 to 897, retaining the 14 previously rejected zero-valued facts.
Extraction metadata was 145,379/183,348/127,833 bytes; observed parse times were
0.068/0.084/0.070 seconds. This proves these transform cases for these filings,
not whole-period or all-taxonomy coverage, class completeness or float authority.

Add `--compare-sec-bulk` to the same command for an accession-bounded read-only
original/bulk value audit. It compares exact concept/period/dimensions/value keys,
checks a second matching bulk-source fingerprint, and reports source numeric
types, comparison statuses and unmatched original counts. It does not require or
assert archive public-time qualification and creates no records or certificates.

The live three-filing audit found all 273 selected bulk values preserved as
`Nullable(String)`. Reconciliation v3 accepts non-null nullable decimal-text types
and normalizes existing `pure`/`ratio` and supported currency `/shares`/`/share`
aliases for matching; the original unit still participates in the retained context
fingerprint. Float64 and unknown storage remain unqualified. After these fixes,
Apple matched 100/100 bulk facts, Alphabet 87/87, and Meta 85/86 with one competing
original-value context. The original facts not matched by bulk remained
126/219/126 respectively. These counts prevent full parity-input readiness even
where every bulk fact matches. They are not waived, and their class/dimension/
coverage dependencies remain work. New reconciliation recipes require explicit
requalification and preserve earlier versions rather than overwrite.

The follow-up live audit classified the unmatched originals as 126 dimensioned
Apple facts, 219 dimensioned Alphabet facts, and 124 dimensioned plus two unscoped
Meta facts. The selected bulk set contains no dimensioned rows. The two unscoped
Meta observations are `StockRepurchasedAndRetiredDuringPeriodValue`, with values
29,754,000,000 (`decimals=-6`, `scale=6`) and 29,750,000,000 (`decimals=-7`,
`scale=9`) in context `c-1`. These are competing values under the current exact
comparison policy, not proof that the filing is internally inconsistent. Precision-
aware duplicate interpretation remains a separate policy/implementation dependency.

Reports and independent readback now separate dimensioned versus unscoped unmatched
counts. JSON measurement reports include up to eight conflict examples and eight
original precision witnesses per example, with exact counts and explicit truncation
flags. This is bounded diagnostic sampling only; authoritative observations remain
fully preserved in normalized source tables. Diagnostic metadata above 1MiB per
document fails explicitly rather than generating an unbounded report. No dimensioned
fact is dropped, no precision tolerance is invented, and none of these counts is
treated as certification.

`sec_precision.assess` now supplies exact bounded interval diagnostics based on
the [corrected XBRL Calculations 1.1 specification](https://www.xbrl.org/Specification/calculation-1.1/REC-2023-02-22%2Bcorrected-errata-2024-02-14/calculation-1.1-REC-2023-02-22%2Bcorrected-errata-2024-02-14.html),
sections 5.2–5.5. It uses rational arithmetic, checks finite values and declared
precision, and preserves closed rounding versus sign-dependent half-open
truncation bounds. INF precision is a point interval; excess digits, unknown
precision and inputs exceeding the bounded precision envelope fail explicitly.
The normalized fact value already includes inline scale; it is not scaled again.

The read-only conflict audit reports both explicit modes. Meta's two observations
intersect at [29,753,500,000, 29,754,500,000] under rounding, and
[29,754,000,000, 29,755,000,000) under truncation. It returns
`compatible_intervals`, `certified=false` and `value_selected=false`. This is interval
compatibility, not a full OIM duplicate-equivalence validator or a calculation-linkbase
certification. The caller must independently establish matching semantic contexts
and any report-wide rounding policy. Existing exact-comparison status and readiness
blockers stay unchanged. Source values are never averaged, overwritten or replaced
with an inferred midpoint. Adopting precision-aware duplicate authority and choosing
any downstream point-value policy remain separate versioned certification work.
