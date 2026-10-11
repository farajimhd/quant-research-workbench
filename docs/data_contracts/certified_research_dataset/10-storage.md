# Compact persistence and compression

[Overview](../certified_research_dataset_design.md) · [Previous](09b-launcher.md) · [Next](10a-capacity.md)

Review the physical schema rules shared by every persisted dataset.

## On this page

- [Mandatory compact persistence contract](#mandatory-compact-persistence-contract)
- [Compaction, compression and hot caches](#compaction-compression-and-hot-caches)

## Mandatory compact persistence contract

### Mandatory lossless storage rules

Compression and compaction are schema requirements across canonical, intermediate, checkpoint, model-ready and certification layers. Lossless compression does not justify lossy price/volume rounding, dropping masks or shortening history. Physical MergeTree part merging is distinct from logical deduplication; neither changes the declared authoritative population. Default policy: ZSTD-compressed durable columns/files, narrow validated numeric types, reusable identity dictionaries, sparse/change-point records and explicit missingness. Final codec levels are selected by representative benchmarks, not assumed universally optimal.

### Existing event encoding as a baseline

Current event DDL is the baseline example: `UInt8` packed metadata/exchange/condition codes, scaled `UInt32` prices, `UInt64` ordinal/time, `LowCardinality(String)` ticker; ordinal/prices use `T64,ZSTD(1)`, time uses `DoubleDelta,ZSTD(1)` and source sizes use Float32/ZSTD(1), ordered by ticker/ordinal. Preserve this existing precision contract; do not copy Float32 size precision into exact derived counts/threshold authorities without a proven bound. Audit overflow for both earlier and future data.

### Physical layout by dataset family

| Persisted family | Compact layout | Compression and physical organization |
| --- | --- | --- |
| Canonical events/indexes | Existing encoded fields; dictionaries and yearly tables; compact continuity/index records | Existing approved codecs; monthly partitions and ticker/ordinal order; bounded bulk inserts, no per-ticker partition explosion |
| Shared aggregates/bars/liquidity | Sparse buckets, dictionary listing keys, scaled prices, exact counters, bit-packed flags where lossless | Monthly partitions; order supporting build/listing/date/resolution/bucket reads; benchmark count/price Delta or T64 + ZSTD; explicit staging retention |
| Indicators | Typed numeric columns, compact readiness flags, shared clocks/identity; omit synthetic padded rows | Compare Gorilla/ZSTD versus ZSTD for Float64 under actual ordering; exact values/state retained; measured narrow projections avoid duplicating full tables |
| Identity/universe/actions | Stable UInt64 surrogate IDs backed by immutable string-ID mapping; dated intervals and membership changes | Dictionary/repeated-value compression and ZSTD; materialize session views only where useful; never renumber IDs when earlier data is added |
| Fundamentals/short data | Long-form typed metric/vintage change records; metric IDs and evidence dictionary; sparse validity intervals | ZSTD/dictionary encoding; period and availability indices/order; immutable revisions, no second-by-second unchanged copies |
| V7 updates/checkpoints | Numeric level/observation arrays, stable level IDs, sparse updates; full state at explicit boundaries | Lossless compressed versioned binary/columnar payload plus searchable header/hash; no huge repeated JSON on every bar; optional bounded delta checkpoints require a complete reachable base chain |
| Model banks | Actual rows once, identity offsets, integer clocks, packed masks; no overlapping 120-row copies | Compressed columnar shards as durable form; bounded verified mmap hot-cache expansion where current loaders require NPY; schema/storage adapter version changes are explicit |
| Optimizer history/cache | Existing lossless run-length rows and indices; reachable selected-channel tiles | ZSTD/packed validity for durable shards; preserve NaN/validity semantics and Float64 authority where required; optional decoded cache under a size cap |
| Labels/trajectories | Sparse targets/transitions with integer identity/clock references; no repeated market feature payload | Compressed Parquet/Arrow-compatible shards and dictionary categories; targets kept separate from observation bank |
| Manifests/certificates | Compact per-unit receipts and content-addressed source/evidence dictionaries | Small inspectable JSON headers permitted; large inventories/proofs in compressed indexed sidecars, not duplicated in every row/receipt |

### Stable dictionaries and numeric bounds

Scalar identity dictionaries must retain security/listing distinctions and original external IDs. Hash collisions cannot be accepted as identity; stable surrogate assignments need verified uniqueness. Choose counters/offsets after cardinality bounds (UInt64 for unbounded archive/global indices); do not downcast all history offsets to UInt32. Keep unavailable reasons in small enums/dictionaries plus precise evidence.

### Codec benchmarks and merge behavior

Candidate codecs follow [ClickHouse compression guidance](https://github.com/ClickHouse/clickhouse-docs/blob/main/docs/data-compression/compression-in-clickhouse.md). Sort order affects codec efficiency; benchmark encoded bytes/row, read/decode throughput, write CPU, representative as-of/range reads, merge cost and peak memory. Schema acceptance requires round-trip parity plus overflow/mask tests. Record both pre-merge and settled part sizes, row counts and small-part counts. Use bounded batches and normal merges; do not make routine `OPTIMIZE FINAL` a campaign dependency.

### Adding earlier and future data

Extend by appendable month/year/listing shards with stable dictionaries and independently sealed receipts. Adding earlier history may change initialization/RVOL/V7 lineage for later derived units; recompute only declared affected descendants into a new dataset version. Adding future data must not rewrite past public vintages or certificate hashes. Old versions remain readable; no implicit TTL deletion/move to backup disks. Codec changes do not automatically rewrite existing parts: any physical re-encoding is an explicit verified migration with temporary-space estimates.

### Required compressed-bank reader upgrade

Current NPY banks/histories are mmap-friendly but uncompressed. Compatibility alone does not satisfy the new durable-compression requirement: implement compressed archive plus bounded decode-cache adapters, or an equivalent measured compressed random-access reader. Decoded caches have lifecycle/space limits and are reproducible from certified shards. This is a required storage upgrade, not a claim that the current loader already supports it.

## Compaction, compression and hot caches

**Compaction** avoids logical repetition: store an unchanged fundamental once with a validity interval, or store a candle once and reference it from overlapping model windows. **Compression** encodes those stored bytes more efficiently without changing values. A **hot cache** may expand certified compressed shards into mmap arrays for fast training reads, but it must be bounded and reproducible. All three serve different purposes.

[Overview](../certified_research_dataset_design.md) · [Previous](09b-launcher.md) · [Next](10a-capacity.md)
