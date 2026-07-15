# SEC EDGAR Pipeline Lifecycle and Remediation Reference

## Purpose and Status

This document describes the active v3 SEC data lifecycle, the authority used at
each stage, the defects found while rebuilding the corpus, and the durable
remedies now present in the code. It is an operational and engineering reference,
not evidence that a particular run completed successfully. Every historical or
finalizer run still requires its own structured-log and database integrity audit.

Anything in the SEC path that is not a `v3` table is stale unless a compatibility
tool explicitly says otherwise. The v3 rebuild is non-destructive: it does not
derive authority from v1 or v2 data.

## Data Authority and Layering

The pipeline keeps acquisition, canonical data, derived text, identity, and model
products separate.

| Layer | Authority | Main products |
| --- | --- | --- |
| Durable source artifacts | SEC bulk ZIPs, daily archive members, accession `.txt` payloads, direct submissions JSON | `D:/market-data/sec_core`, live raw artifact roots |
| SEC mirror | Replaceable organization and audit copy of SEC bulk data | `sec_core.sec_bulk_mirror_*_v3` and snapshot manifests |
| Canonical SEC | Filing, relationship, document, source text, revision, PAC, and XBRL rows | `q_live.sec_*_v3` |
| Identity | Point-in-time SEC-to-market relationship | `q_live.id_sec_market_bridge_v3` |
| Derived XBRL context | Ticker-associated XBRL rows needed by packed-model consumers | `market_sip_compact.sec_xbrl_context_v3` |
| Model text | Tokens and embeddings generated from audited rendered documents | `market_sip_compact.sec_filing_text_tokens_v3`, `sec_filing_text_embeddings_v3` |

`sec_core` is not a relationship authority for `q_live`. It is a source mirror
used to accelerate historical loading, reconciliation, and integrity checks.

### Canonical Text Contract

One submitted SEC document can create several rows for one filing:

1. `sec_filing_document_v3` identifies the submitted document and its role,
   format, source hash, and revision lineage.
2. `sec_filing_text_v3` preserves the complete text-bearing document source.
   It is not prefix-capped and is not a lossy normalizer output.
3. `sec_filing_text_rendered_v3` is the readable derivative associated with the
   same filing and document lineage.
4. `sec_filing_document_skip_v3` records non-text or intentionally excluded
   payloads and the reason; skipping must be observable.

Images, archives, binaries, and similar payloads remain in the artifact store.
They are not copied into a text table. Inline-XBRL HTML is still a renderable
filing document; XBRL tags do not make the primary HTML a sidecar.

## Full Historical Lifecycle

The active entry point is `sec_historical_gap_fill.py --execute`. Its stage order
is deliberate:

| Stage | Responsibility | Durable result |
| --- | --- | --- |
| `bulk-download` | Acquire current authoritative SEC bulk snapshots and required ticker sources | Verified ZIP artifacts |
| `bulk-ingest` | Replace `sec_core` mirror snapshots through validated staging and cutover | Current bulk mirror tables |
| `bulk-canonicalize` | Populate canonical company, submission relationship, and XBRL rows | Canonical v3 bulk-derived data |
| `daily-archive-download` | Download only missing daily archives for the requested range | Durable `.nc.tar.gz` artifacts |
| `validate-downloaded` | Scan archives and redownload selected corrupt artifacts | Validated archive inputs |
| `filing-entity-backfill` | Parse SGML entity blocks and submission relationships | `sec_filing_entity_v3` |
| `archive-text-rebuild` | Split filing payloads, preserve source text, render readable text, and insert | Document/source/rendered/skip rows |
| `sec-revision-reconcile` | Resolve deterministic source revisions and PAC evidence | Current revision lineage and PAC events |
| `missing-document-repair` | Repair only archive-backed parents with provable documents | Targeted document/text recovery |
| `filing-parent-reconcile` | Remove relationship-only parents and reconcile parent authority | Canonical filing parents |
| `acceptance-submissions-enrichment` | Resolve missing accession metadata from bulk and direct submissions | Authoritative raw acceptance metadata |
| `acceptance-raw-metadata-repair` | Replace date-only fallbacks with explicit UTC metadata | Corrected canonical event time |
| `acceptance-archive-repair` | Use exact SGML acceptance values where submissions lack them | Corrected archive-backed event time |
| `archive-identity-repair` | Rekey existing document/text rows stored under a non-primary entity CIK | Verified subject-company document lineage |
| `archive-identity-audit` | Compare canonical identity with embedded archive identity | Explicit identity findings |
| `xbrl-companyfacts-catchup` | Fill recent or missing XBRL from companyfacts | Canonical XBRL rows |
| `xbrl-integrity-repair` | Repair XBRL keys and relationships | Consistent XBRL graph |
| `sec-bridge-rebuild` | Rebuild historical SEC-to-market associations | `id_sec_market_bridge_v3` |
| `sec-context-build` | Refresh derived XBRL context; text copy is skipped | `sec_xbrl_context_v3` |
| `integrity-audit` | Enforce final source, relationship, timestamp, text, and orphan checks | Run report and coverage |

Coverage can skip immutable completed range work, but mutable bulk snapshots are
always reconciled. A failed required stage stops the run and does not write final
semantic coverage.

## Finalizer Lifecycle

`sec_historical_gap_fill.py --finalize-only --execute` performs the bounded
post-rebuild reconciliation without reprocessing the entire text corpus:

1. Backfill filing-to-entity relationships.
2. Inventory archive occurrences and repair archive-backed missing documents.
3. Reconcile filing parents.
4. Enrich unresolved acceptance metadata from submissions.
5. Apply exact acceptance timestamp repairs from submissions and archives.
6. Repair document/text rows stored under a non-primary SGML entity CIK.
7. Audit archive identity.
8. Rebuild the SEC bridge and XBRL context.
9. Run the fail-fast integrity audit.

Write permission is an explicit per-stage contract. A stage that mutates data
must receive `--execute`; filenames are not used to infer whether a stage is
write-gated.

## Identity Semantics

An accession identifies a filing, not necessarily the issuer. The first ten
digits are the submitting filer CIK and can differ from the subject company.
Therefore the pipeline never derives issuer identity from the accession prefix.

`sec_filing_entity_v3` stores the relationship between an accession and every
embedded or submission-sourced entity. Roles include issuer, subject company,
filer, reporting owner, and filed-by. Documents and text remain attached to the
accession or filing ID. Only appropriate issuer or subject-company relationships
may participate in market-security mapping.

The relationship authority is:

1. Parsed SGML entity blocks in the filing source.
2. SEC submissions JSON keyed by the CIK whose submission history contains the
   accession.
3. Direct per-CIK submissions JSON and referenced history fragments when the
   bulk snapshot does not yet contain the filing.

`sec_filing_archive_accession_v3` separately records archive occurrence,
embedded CIKs, member path, document count, acceptance value, content hash,
revision rank, and private-to-public evidence. It supports repair decisions; it
does not collapse multi-entity relationships into one guessed CIK.

## Timestamp Semantics

Timestamp conversion follows source syntax rather than a blanket timezone rule:

1. A submissions timestamp ending in `Z` is already UTC and is preserved as UTC.
2. A 14-digit SGML `ACCEPTANCE-DATETIME` is a New York wall-clock value and is
   converted to UTC with DST-aware timezone rules.
3. Ambiguous, nonexistent, malformed, or absent exact values are not silently
   guessed.
4. A filing-date-only fallback remains explicit and low quality until an exact
   source becomes available.

The repair order is explicit UTC submissions metadata, exact SGML acceptance,
then date-only fallback. Replacement is cross-partition safe: replacements are
staged and inserted, verified, and only the exact superseded fallback rows are
deleted. Genuine source omissions remain reported nonfatal unresolved rows;
source-repairable omissions fail the final audit.

## Revision and PAC Semantics

SEC archives can contain later versions of the same accession. Selecting the
winner by ingestion time makes worker scheduling determine canonical data. The
v3 contract instead stores `source_version_key`, `source_revision_at`,
`source_revision_rank`, `source_revision_kind`, and `pac_event_id`, and current
rows are selected by deterministic source revision rank.

Post-acceptance correction (PAC) evidence is retained in
`sec_filing_pac_event_v3`. PAC can describe deletion, header, form, or document
changes; it is lineage evidence and does not automatically replace document text.
A later complete ordinary source can supersede a correction stub when its source
rank and content prove it is the better canonical occurrence.

## XBRL, Bridge, and Embeddings

Readable filing text and structured XBRL are separate products. Historical and
live paths populate canonical XBRL tables. `sec_xbrl_context_v3` is retained
because packed-model consumers require ticker-associated XBRL; the live SEC
gateway maintains it with restart-safe pending work.

Routine bridge maintenance belongs to `reference_gateway`. The historical SEC
fill rebuilds the bridge at the end of a rebuild so its downstream audit has a
coherent point-in-time mapping. The live SEC gateway consumes, but does not own,
the bridge.

Text embeddings do not depend on copied `sec_filing_context_v3` or
`sec_filing_text_context_v3` tables. The combined token/embedding builder joins
`sec_filing_text_rendered_v3`, filing and document metadata, and the point-in-time
bridge directly. It excludes date-only acceptance fallbacks and preserves every
configured token chunk rather than prefix-capping or tail-capping a filing.

## Reliability and Resume Model

Each archive worker owns a bounded complete unit: extract one archive, parse it,
write byte-bounded Parquet shards, validate them, insert and verify them, record
completion, and only then remove temporary shards. The original archive remains
durable.

The archive rebuild uses fixed worker lanes and stable Rich progress. It fails
all lanes after the first required failure and retains the first actionable
exception. Recovery can reuse proven complete intermediate parts. Failed partial
inserts are identified by `(source_run_id, archive_date, dataset)`, synchronously
deleted and verified for that unit, and then reinserted. Successful datasets for
the same archive are not rewritten.

Large source documents use bounded Parquet row groups and files with native
ClickHouse readers. One submitted document remains one database row even when it
is larger than a row-group target. This replaced unbounded serial JSON staging.

## Defects Found and Durable Remedies

### Acquisition and Mirror Integrity

| Defect | Impact | Remedy |
| --- | --- | --- |
| Per-CIK submissions could return 404 while the filing artifact existed | A live filing job was reported as failed despite usable SGML | Treat submissions enrichment as optional for that job, continue from SGML authority, and retain the HTTP outcome for later reconciliation |
| SEC bulk submissions lagged direct per-CIK JSON | Recent accessions and exact timestamps remained unresolved | Add rate-limited direct submissions and referenced-fragment fallback for unresolved relationships only |
| A changed bulk ZIP could reuse a stale completed-member manifest | Old mirror rows survived an authoritative snapshot refresh | Load into validated staging tables and atomically replace the complete snapshot |
| Bulk companyfacts contained source-compatible JSON edge cases and very large values | Snapshot staging rejected valid members or facts | Parse with source-compatible validation, preserve oversized facts, and validate row/member ratios before cutover |
| Historical fill initially omitted ticker bulk sources | Mirror and bridge inputs were incomplete | Require submissions, companyfacts, company tickers, exchange tickers, and mutual-fund tickers in a full unified run |
| SEC throttling and transient errors were conflated | Retries could intensify source pressure | Shared minimum request spacing, bounded retries, separate 403/429 cooldown, and transient 5xx/network cooldown |

### Filing Identity, Parents, and Revisions

| Defect | Impact | Remedy |
| --- | --- | --- |
| Accession prefix was treated as issuer CIK | Filings could attach to the submitter instead of the subject company | Parse SGML entity blocks and submissions membership into `sec_filing_entity_v3`; never infer issuer from accession text |
| Existing Form 144 documents remained keyed to the reporting person after entity backfill | Source and rendered text could map to an insider instead of the subject security | Reparse only audited mismatched members, verify replacement subject-CIK lineage, invalidate stale model rows, and delete the old document key last |
| Submissions relationships were materialized as dependency-free filing parents | Canonical filing counts and relationships drifted | Keep submissions as accession-to-CIK relationships and remove parent rows without document or source authority |
| Filing parents existed without documents | Missing text was indistinguishable from metadata-only filings | Build archive occurrence inventory, classify the reason, and repair only occurrences that prove documents exist |
| Duplicate archive occurrences were resolved by insert time | Worker timing selected the canonical revision | Store deterministic source lineage and use `source_revision_rank` for current-row selection |
| PAC metadata was treated like replacement filing content | Correction evidence could erase valid text | Persist immutable PAC events separately and reconcile source versions by content-aware revision rank |

### Timestamp Correctness

| Defect | Impact | Remedy |
| --- | --- | --- |
| Explicit `Z` timestamps were interpreted as New York time | Event time shifted and contaminated temporal tokens and embeddings | Preserve explicit UTC values without conversion |
| Raw SGML acceptance values were treated without source-specific semantics | Naive timestamps could be shifted or guessed | Parse exact 14-digit SGML values as New York wall time with DST-aware conversion |
| Date-only fallback looked like an exact timestamp | Downstream models could learn false intraday ordering | Track timestamp source/quality, exclude fallback rows from embeddings, and repair only from exact authority |
| One repair INSERT crossed too many partitions | ClickHouse rejected the repair after metadata audit | Bound affected partitions, stage replacements, and apply verified cross-partition replacement safely |

### Text Preservation and Parsing

| Defect | Impact | Remedy |
| --- | --- | --- |
| Raw and downstream text had character and per-filing row caps | Canonical source, tokens, and embeddings were incomplete | Remove upstream text caps, SQL `substring`, per-filing row limits, and token-tail truncation |
| Inline-XBRL primary HTML was classified as an XBRL sidecar | Readable filing content was skipped | Classify HTML-like primary documents before generic sidecar rules and provide targeted stale-skip repair |
| Old extraction collapsed all filing payloads into a readable-text interpretation | Source formatting and per-document provenance were lost | Split SGML `<DOCUMENT>` blocks, preserve each text-bearing source with its format, and keep rendered text as a derivative |
| All payloads were candidates for database text storage | Binary artifacts could bloat or corrupt text tables | Keep raw artifacts in durable storage; insert only text-bearing HTML, text, and XML source rows |
| Separator cleanup distorted fragmented financial tables | Labels, dates, and values could lose association | Add structural HTML table handling with inferred headers and row labels; preserve XML paths/tags where they carry meaning |
| Short repeated labels were removed as duplicates | Legitimate headings and table labels disappeared | Detect duplicates only for blocks of at least 200 characters and emit a traceable `DUPLICATE of [first 15 chars]` marker |
| Mojibake, repeated headers, empty scaffolding, and layout residue inflated model input | Token count increased without semantic value | Apply deterministic structural cleanup while preserving source text and block hashes for audit |

### Storage, Throughput, and Recovery

| Defect | Impact | Remedy |
| --- | --- | --- |
| Serial JSON staging failed on very large text and was slow | Archive workers failed late and the terminal could appear successful | Use parallel byte-bounded Parquet shards and ClickHouse native file reads; fail the whole run immediately |
| Repair Parquet inferred lineage and inventory fields as strings | ClickHouse rejected a repair insert at runtime, after an earlier dataset had already committed | Define all SEC v3 numeric, temporal, array, and text types in the shared Parquet schema authority and reject name or type mismatches during local preflight before any insert |
| Source text partitioning and ordering did not suit large revision-aware inserts | Inserts hit partition and merge pressure | Recreate v3 source text with bounded hash partitioning and revision-aware ordering |
| A failed archive could leave partial ClickHouse rows | Reruns could skip or duplicate incomplete units | Detect failed units from manifests, delete and verify only their partial rows, then reinsert the whole unit |
| Temporary extracted files accumulated until the entire run ended | Disk exhaustion stopped long rebuilds | Delete a worker's temporary shards only after its archive is durably inserted and checkpointed |
| More workers were assigned without stage-specific bounds | CPU availability could overwhelm disk, memory, or ClickHouse | Use 32 bounded archive lanes and independently bounded insert threads, shard sizes, and queues |

### Derived Data and Operations

| Defect | Impact | Remedy |
| --- | --- | --- |
| Text context tables duplicated rendered filing text | Copies could drift by renderer version and mapping freshness | Join rendered text, filing/document metadata, and the point-in-time bridge directly in the embedding builder |
| XBRL context was considered redundant with source tables | Packed-model XBRL consumers lost their maintained ticker-associated product | Retain `sec_xbrl_context_v3` and maintain it in historical and live paths |
| Bridge ownership was ambiguous | SEC and reference services could compete to maintain identity | Keep routine bridge sync in `reference_gateway`; historical SEC rebuild performs a bounded final rebuild |
| Recovered failures stayed visible as active errors | Operators saw stale incidents after successful polls | Track active and resolved error state and clear active state on successful recovery |
| Historical archive failures were hidden in message output | Operators could miss a stopped or invalid run | Stable per-worker stages, structured failure records, and fail-fast orchestration |
| Finalizer write mode depended on a script filename allowlist | Newly added mutating stages ran as dry-run and exited quickly | Declare write-gated stages explicitly and propagate `--execute` by stage contract |
| Current-view SQL used unsupported `FINAL` alias order | ClickHouse 26.3 rejected finalizer queries | Use ClickHouse-valid `AS alias FINAL` syntax and validate against the live server |

## Current Open Work

The following items must not be mistaken for completed remediation:

1. **Packed renderer integration:** `sec_packed_text_renderer.py` contains the
   advanced v6 structural renderer, table labeling, XML path preservation,
   duplicate hashes, and quality flags. The active historical context stage uses
   `--skip-text`, while the embedding gateway consumes
   `sec_filing_text_rendered_v3` directly. The archive extractor currently writes
   that table through its own simpler normalizer. Before building embeddings, the
   active producer must be audited and unified with the intended packed renderer
   or the advanced renderer must be declared obsolete.
2. **Rendered text statistics:** source and rendered length distributions,
   compression ratios, content-type distributions, and largest-document audits
   are still required before choosing document concatenation or chunk policy.
3. **Finalizer acceptance:** the finalizer is not accepted until archive identity
   repair and the remaining stages complete, and its database audit shows zero
   source-repairable timestamps, zero archive-backed missing documents, and no
   source/rendered lineage errors.
4. **Embedding rebuild:** v3 tokens and embeddings must be regenerated only
   after the renderer and timestamp audits are accepted.

## Run and Acceptance Commands

Full historical reconciliation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_gap_fill.py --execute
```

Bounded post-rebuild finalization:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_gap_fill.py --finalize-only --execute
```

Do not declare either run complete from process exit alone. Review its JSONL
report, final stage statuses, unresolved classifications, and canonical v3
integrity counts. A nonfatal unresolved row must state why no authoritative exact
source exists; a repairable unresolved row is a failure.

## Related References

- [Pipeline README](README.md)
- [Live SEC Gateway lifecycle](../../../services/sec_gateway/SEC_GATEWAY_LIFECYCLE_AND_OPERATIONS.md)
- [SEC Gateway README](../../../services/sec_gateway/README.md)
- [SEC text parser and source extractor](sec_filing_text_extract_parts.py)
- [Historical orchestration](sec_historical_gap_fill.py)
- [Advanced packed renderer](../../market_sip/events/sec_packed_text_renderer.py)
