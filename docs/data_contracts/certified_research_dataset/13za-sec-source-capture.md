# Normalized SEC source-candidate capture

[Extraction and lineage](13z-sec-share-context.md) · [Completion audit](13i-completion-audit.md)

## Boundary

`sec_share_capture` persists the bounded normalized preparation result in
ClickHouse. It never publishes `research_reference_receipt_v1`, establishes
public availability or certifies allocation. The document header marks a
complete **candidate capture**. Reference consumers must continue requiring
independent qualified source and allocation receipts.

The exact schema declarations are in `sec_share_capture.tables()`. Installation
requires the global database dictionary claim. Capture requires source ownership,
a current source guard and explicit source-write admission for its metadata-byte
request. This admission is a caller dependency, not a measured production
capacity profile fabricated by the capture stage. Launcher integration and a
production source-write pilot qualification remain unfinished. The launcher
binding is now implemented as described below.

## Persistence forms

All three tables use ordinary `MergeTree`, monthly `source_archive_date`
partitions, explicit `live_market_ssd` policy and lossless ZSTD column codecs.
Integers use T64 or width-correct Delta where appropriate. No insertion-ranked
replacement, local prepared-data files or repeated document payload is used.
Production capture is restricted to `q_live`; isolated test namespaces are the
only alternative. Verified reuse performs no write admission or inserts.

| Table suffix after `research_sec_share_source_` | Sorting key | Contents |
|---|---|---|
| `document_v1` | `document_key` | One capture header: archive date, UInt64 CIK, prepared/text hashes, parser version, UInt32 source bytes/context/observation counts and source proof JSON |
| `context_v1` | `document_key,context_key` | Archive date, original context ID, instant date and explicit axis/member array; contexts are shared by facts |
| `observation_v1` | `document_key,source_position` | Archive date, nullable context key, original fact ID, concept/unit/decimals/scale/format, nullable exact decimal string, extraction status, rejection reason and nullable rejected context ID |

Content/context fingerprints are `FixedString(64)`; nullable links use
`Nullable(FixedString(64))`. Repeated concepts, context IDs, unit IDs and
transformation/status metadata use `LowCardinality(String)`. Dimension arrays
are `Array(Tuple(String,String))`, compressed once per normalized context.
Decimal strings preserve values exceeding Float64 precision and original
unsupported scale metadata. Rejections remain explicit rows with null values;
they are not dropped to reduce storage.

## Execution and restart

1. Verify prepared fingerprint, distinct keys and context/value relationships.
2. Verify exact existing schemas/codecs/policies and all active/inactive parts;
   reject mismatches before writers. Recheck source lineage and admission.
3. Read bounded retained rows and append only missing, exact context/fact rows.
   Conflicting or duplicate physical keys require explicit reconciliation.
4. Independently read back both tables, then append/read back the immutable
   document header. Recheck lineage, ownership, placement and admission.

An interruption before the header can resume by reconciling retained partial
rows. Once the header exists, every table must exactly match the prepared
capture; missing or changed rows fail rather than being silently repaired.
Inserts use bounded 1,024-row batches and the shared transport byte limit. A
document is capped at 10,000 contexts/facts and 64 MiB total metadata; larger
units fail explicitly. The raw XML byte cap remains 16 MiB.

## Validation and remaining work

A real isolated ClickHouse test captured the retained Apple source, interrupted
before header insertion, resumed with one inserted header and then reused with
zero inserts. It verified exact schema/codec/SSD placement. Deliberately removing
a completed observation then failed without an insert. No production capture,
provider request or full campaign was run.

These source tables now have optional unified launcher installation/execution.
Production pilot qualification, source-stage/vintage qualification, competing
revision handling, dated stock-class mapping and certified float/supply
publication remain required. Candidate capture must not substitute for them.

## Unified launch path

Repeat `--sec-share-document CIK,ACCESSION,DOCUMENT_NAME` on `prepare-sources`,
`launch` or `launch-collection` for 1–1,000 unique retained documents. There is
no automatic broad source scan, provider request or acquisition fallback.
Frozen `run`/`resume` routes reject the option. The selected stage runs after
requested SEC catch-up and before market source preparation/graph freezing.
It owns the q_live schema claim, then uses one bounded worker pool with private
clients and a per-document reservation of 2 GiB plus the query-memory budget.
Clients and claims are drained on completion/failure/interruption.

`--capacity-profile` must contain a separately qualified `sec_share_source`
pilot rate on `live_market_ssd`. Its recipe hash binds the source algorithms,
exact DDL, Python major/minor version and extraction bounds. The stage admits
all selected document outputs plus concurrent staging/merge peaks without a
reuse discount, and checks current disk/physical-volume headroom before writes.
Missing/incompatible rates block before candidate schema installation. No rate
or qualified peak bound was created from the three small parser examples.

Use the existing offline command to inspect the required recipe and schemas:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/prepare_certified_research.py schema --format json
```

The JSON `sec_source_candidates` section contains the recipe and column catalog;
`--format sql` includes the optional q_live candidate DDL. Default
`install-schema` still installs the common research catalog. Candidate schema
installation occurs only on explicit document selection after source admission.
Fresh-launch JSON reports candidate capture counts separately from dataset
certification. Text progress labels source work as prepared candidates rather
than certified fundamentals/allocation.
