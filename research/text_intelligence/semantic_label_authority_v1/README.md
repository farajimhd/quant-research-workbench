# Deterministic News and SEC Semantic Authority V1

This package is a certification-stage, versioned authority over already rendered
News V2 and SEC V3 text. It does not replace the source renderers and does not
overwrite either canonical table.

The pipeline applies a strict precedence contract:

1. rendered structure and suppressible blocks;
2. SEC and market identifiers;
3. dates, times, fiscal periods, and durations;
4. metadata-backed entities and ticker/exchange symbols;
5. typed financial quantities with table context;
6. generic numeric fallback;
7. exact-evidence canonical event labels;
8. sentiment, modality, and time orientation from those labels;
9. keywords and n-grams retained only as discovery evidence.

Candidate phrases cannot become labels by frequency alone. Every canonical label
contains exact source offsets and evidence text. Unsupported concepts remain
unlabeled and receive `no_supported_canonical_event`.

The current V4 concept authority also excludes negated legal events from their
adverse concepts and represents explicit regulatory clearance separately. M&A
direction remains generic at this layer; the scoped V4 authority applies the
issuer role before producing the final per-issuer direction.

Generate a real five-News/five-SEC audit set outside the repository:

```powershell
C:\Users\g835l\miniconda3\envs\ml4t\python.exe -m research.text_intelligence.semantic_label_authority_v1.run_audit
```

The default output is:

`D:\TradingML\runtimes\text_intelligence\semantic_label_authority_v1\audits\text_semantic_label_audit_v1`

The semantic authority is consumed by the issuer-scoped V4 classifier. Full
historical persistence still requires the clean scoped certification manifest
and an explicit `--execute` invocation.
