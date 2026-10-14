# Authorized retained-file reporting recovery

[Unified launcher](13j-launcher-execution.md) · [Checkpoint seeds](13a-checkpoint-seeds.md)

`prepare-sources --recover-retained-reporting` enables the explicitly approved
ingestion migration for existing imported days. It uses retained trade files;
it performs no REST request or provider download. Derivation consumers continue
to read canonical events and certified sidecars exclusively.

The launcher checks actual D-volume headroom before planning, before each chunk,
and before and during copies. Defaults reserve the larger of **256 GiB or 10% of
D capacity**, with a **256 GiB maximum chunk**. Customize with
`--staging-reserve-gib`, `--staging-reserve-fraction`, and
`--staging-chunk-gib`; a zero chunk bound permits all available headroom.
Files are processed sequentially inside these bounds. G originals and existing
D originals are preserved. Private D copies are hash verified and removed only
after original-clock reconciliation and native reporting certification.

The sizing pass reads the staged trade gzip; the extraction pass materializes
it into an explicit compact, losslessly compressed `live_market_ssd` temporary
table. Sixteen ticker groups reconcile in ClickHouse against canonical fields
and ordinals, using bounded query memory and external sorting. Missing rows,
field differences and ambiguous ordering block publication. The full original
condition array and nanosecond clocks remain in the certified sidecar. Native
repair changes only reporting bits and independently verifies preserved fields.
Counts-only legacy reporting receipts do not qualify as original-clock proof.

Native repair now uses the most common independently audited reporting class
as its default and materializes only nonempty minority key sets. Complete,
immutable original-clock/canonical coverage is required before this choice;
independent final certification still checks every flag and preserved field.
Uniqueness is counted per ticker and summed, avoiding repeated ticker strings
in one global ordinal set. Existing active mutations retain their original
command/marker and are verified rather than resubmitted.

A controlled one-million-row ClickHouse read tested 990,000 timely, 5,000
delayed and 5,000 unknown rows. The previous formulation exceeded a 64 MiB
query limit (83.35 MiB requested); the minority formulation passed that same
limit with zero classification mismatches in 0.159 seconds. It materialized
10,000 keys instead of 995,000. This is a bounded algorithm check, not a
production full-day throughput or storage qualification.

Exact temporary and receipt schemas are defined by `raw_table()` and
`receipt_table()` in `pipelines/market_sip/flatfiles/retained_reporting.py`;
the durable original-clock schema is `reporting_evidence.ddl()`. Every column
has a lossless codec and all tables require `live_market_ssd`. Existing table
column types and actual part placement are checked before raw staging writes.
Interrupted captures retain reconciliation state. A frozen capture receipt can
resume native repair without reopening an original file. Conflicting evidence
blocks reuse.

October 13 metadata inspection found 189 January–October 2 trade files totaling
597,054,672,991 bytes: 179 on G and ten already on D. This is compressed input
size, **not** a certified sidecar/output storage projection. The bounded real
ClickHouse integration test proves reconciliation, flag repair, certification,
preserved prices and receipt reuse; full-day throughput is not yet qualified.
