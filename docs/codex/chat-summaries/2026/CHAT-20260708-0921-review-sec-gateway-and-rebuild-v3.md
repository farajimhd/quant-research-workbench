# Review SEC Gateway Errors and Rebuild the SEC v3 Data Lifecycle

- Chat started: 2026-07-08 09:21:36 PDT (America/Vancouver)
- Chat ended or last activity: Active when summarized on 2026-07-27
- Summary written: 2026-07-27 10:51:37 PDT (America/Vancouver)
- Chat/task identifier: `019f4288-d619-7731-965e-a3bbfb55cbd8`
- Repository or scope: `D:\TradingCodes\quant-research-workbench`; SEC/EDGAR historical and live ingestion, source and rendered text, identity, timestamps, revision handling, taxonomy, embeddings, and Reference Gateway maintenance
- Related task-history entries: `TASK-0037`, `TASK-0046`, `TASK-0056`, `TASK-0087`, `TASK-0089`, `TASK-0091`, `TASK-0140`
- Source completeness: Complete for this active Codex task and its local session record; separately linked predecessor ChatGPT conversations were not reviewed

## Narrative

The chat began with a live SEC Gateway error that appeared inconsistent with
the underlying data: a request for a company submissions JSON returned an SEC
404, but the user could later download the URL. The first review separated
source availability from gateway state. A submissions metadata 404 could be
temporary or absent while accession text remained available, yet the live
worker treated it as fatal. The shared terminal also classified lifetime error
counters as active, so a recovered request could remain visibly unresolved.
The gateway was changed to use accession-text metadata fallback for submissions
404s and to track error resolution after successful forward progress. This
established a recurring rule for the rest of the chat: source errors, stale
historical warnings, identity-quality warnings, and daemon health must remain
distinct.

The discussion quickly widened from one error to the entire SEC lifecycle. The
user asked how live filings were discovered, how historical archives were
filled, how CIKs and listings were bridged, and how downstream tokens and
embeddings consumed filing text. Initial inspection found that the existing
text path could clip source text, limit the number of text rows per filing,
truncate text again before tokenization, and truncate token arrays. Because all
downstream representations inherited those losses, the user chose to rebuild
SEC data rather than patch only recent rows.

The upstream contract was then made explicit. A submission artifact can contain
multiple filing documents. Every text-bearing document must be separated and
stored in `q_live.sec_filing_text_v3` with its content format and provenance;
images, ZIP files, and other binary payloads remain artifacts rather than
database text. Inline XBRL HTML remains a renderable filing document even though
it contains XBRL tags. Structured XBRL is extracted to its own tables. The raw
source table must preserve the complete source without character caps,
minimum-length filters, or parser-normalized replacement. The derived
`q_live.sec_filing_text_rendered_v3` contains deterministic packed text for
models and reading. This superseded the old design in which an upstream
"readable extraction" distorted tables before storage.

The historical rebuild was moved to versioned v3 tables so prior data remained
available during validation. The gap-fill workflow was expanded to mirror SEC
bulk submissions and companyfacts into `sec_core`, process daily archives,
populate filing, document, entity, source-text, rendered-text, XBRL, bridge, and
context products, and finalize integrity checks. The workstation's 128 cores
did not justify unconstrained concurrency: archive work ultimately used 32
workers, while extraction, parsing, and ClickHouse insertion were bounded
independently. Each worker owns a complete archive unit, checkpoints only after
durable insertion, deletes only its own temporary files, resumes reusable
intermediates, and reports stable per-worker stages. Failures became fail-fast
instead of appearing only after a long run.

Timestamp handling required several corrections. The accepted rule is based on
the source syntax, not a blanket timezone assumption. An explicit ISO timestamp
ending in `Z` is already UTC and must never be converted as New York time. An
exact 14-digit SGML `ACCEPTANCE-DATETIME` without an offset represents SEC
submission wall time and is converted from `America/New_York` to UTC with
date-appropriate daylight-saving rules. A filing date without an exact
acceptance value remains an explicit date-only fallback rather than fabricated
precision. Bulk submissions became the primary mirror authority; direct
`data.sec.gov/submissions/CIK##########.json` retrieval, under SEC rate limits,
is used for cases absent from the bulk snapshot. Repair writes were made
cross-partition safe and were integrated into historical finalization instead
of left as a manual-only operation.

This work exposed a separate identity error. The first component of an
accession number may identify the submitting filer or agent and is not a safe
issuer CIK. For example, accession `0002143285-26-000002` was relevant to CIK
`0000766421`. Historical archive ingestion and the live gateway therefore parse
CIKs from SGML entity blocks and submissions metadata instead of deriving them
from accession prefixes. `q_live.sec_filing_entity_v3` records each
accession-to-entity relationship and role. Documents and text remain keyed to
the filing/accession identity; security mapping uses only appropriate issuer or
subject-company relationships. `sec_core` remains a mirror and audit aid, not a
relational authority for `q_live`.

The same audit found filing parents without documents and exact acceptance
times missing from some records. The solution was an archive accession
inventory that records embedded CIKs, archive occurrence, member, document
count, acceptance evidence, content hash, revision rank, and
private-to-public evidence. Historical finalization repairs missing children
only when an archive member proves that documents exist, classifies genuine
metadata-only cases, and repairs acceptance times only from exact source
evidence. Live ingestion maintains the corresponding entity and archive
inventory. Five Form 144 identities were later found under reporting-person
CIKs; the targeted repair reparsed only affected archive members, preserved all
entity relationships, replaced stale child keys in dependency order, and
verified complete source/rendered lineage.

Post-acceptance corrections introduced another authority problem. SEC/PDS PAC
events and ordinary later source revisions cannot be resolved by ClickHouse
insertion time. The v3 schema therefore carries deterministic source version
keys, revision timestamps, ranks, kinds, and PAC lineage. Current rows are
selected by SEC chronology, and `ReplacingMergeTree` uses
`source_revision_rank`. A production audit showed that the deployed source-text
table still used `inserted_at`; 54,086 document winners diverged from revision
authority. A resumable engine migration attached existing monthly partitions
without retransmitting terabytes of text, repaired bounded parent identities,
validated physical rows and hashes, and retained the old table with merges
stopped.

The renderer became the largest engineering portion of the chat. Audits over
24,378,949 logical source rows and about 8.80 trillion source characters showed
why text could be both huge and semantically poor. HTML tables lost structure,
XML tags that acted as headings disappeared, inline XBRL emitted repeated
blocks, page scaffolding recurred, image-only exhibits appeared empty, XML
comments held substantive ABS data, malformed HTML crossed head/body
boundaries, and legacy SEC fixed-width tables used nonstandard markers.

The renderer was consolidated into one SEC-owned implementation used by
historical fill, live ingestion, repair tools, market-event readers, and the
embedding path. It preserves complete raw source separately and performs only
deterministic structural packing. HTML tables resolve row and column spans and
repeat headers on each logical row. XML output retains tag-derived semantics
and substantive comments. Duplicate blocks are considered only at 200
characters or more and are replaced with a reference to the first block.
Structurally proven separators, repeated scaffolding, and page artifacts can be
removed, but company-authored risk, certification, exhibit, legal, signature,
and table content cannot be discarded as generic boilerplate. Image-only HTML
produces a deterministic inventory of referenced images and dimensions while
explicitly stating that OCR was not performed. Truly empty wrappers produce a
metadata-bearing presence record; observed visible content that the renderer
loses remains fatal.

Large-corpus rebuilds repeatedly exposed system-level limits. A global
`FINAL` join exhausted 32 GiB, Windows held open temporary Parquet readers,
oversized Parquet pages failed, parallel inserts pushed ClickHouse above 200
GiB, small inserts scattered parts across 64 partitions and caused merge
storms, global validation approached the server limit, and fixed row-count
blocks failed on text widths above 250 MB. Each failure was corrected at its
authority boundary: an indexed watermarked SQLite lookup replaced the global
join; handles close before cleanup; exports use byte-bounded row groups; render
workers share a small insert gate; monthly work tables avoid hash-partition
merge storms; bundle manifests and stable insert tokens make crash-window
retries idempotent; validation runs in bounded month and CIK lanes; and cutover
blocks are limited by bytes rather than rows. The completed rebuild reconciled
91 monthly partitions, 3,800 bundles, 24,378,949 source rows, 23,976,233
rendered rows, and 402,716 explicit exclusions. Atomic cutover retained the
prior target and later resumed cleanup without rerendering.

The user then asked which filings should be embedded. Official SEC form
definitions and observed filing titles were scraped under rate limits, exact
and ordered-word evidence was normalized, and fuzzy title distance remained a
candidate aid rather than automatic taxonomy authority. Manual labeling
produced 240 approved rules and measured source/rendered quantiles. The policy
was revised after the user rejected invisible `embed=no` filings: low-impact or
structured-only filings should still receive a presence representation.
Every chunk must carry a deterministic metadata header containing filing,
issuer, CIK, ticker, acceptance time, title, inventory, and source length.
Chunk size and count were deliberately deferred until the rendered corpus and
embedding model properties could be measured.

Security mapping remained owned by the Reference Gateway as routine reference
maintenance. `id_sec_market_bridge_v3` uses point-in-time entity/listing
relationships; it is not a one-time historical-fill side effect. Four
evidence-backed subsidiary-to-listed-parent relationships were added and
integrated into reference maintenance, historical fill, live XBRL
reconciliation, and targeted embedding selection. The Reference Gateway's
abandoned generic fact and alert layers were removed while source schedules,
mapping issues, audits, bridge maintenance, and tradability safeguards
remained.

The final operational work corrected Reference Gateway publication semantics.
Source health now collapses retries by logical publication window, SEC
fails-to-deliver data uses exact official page links, the January 9, 2025 FINRA
closure is catalogued, and ClickHouse schedule queries use bounded retry with
compact checkpoint payloads. Seventeen tests, gateway smoke, compilation,
workstation hash checks, and a forced production child cycle passed. The last
reviewed runtime evidence showed two successful daemon cycles,
`source_failed=0`, no traceback, health `RUNNING`, and zero active errors. SEC
FTD July data was correctly labeled not yet published; remaining degraded
mapping warnings were nonfatal quality findings rather than daemon failures.

## Durable decisions

### Confirmed requirements

- `sec_filing_text_v3` preserves complete text-bearing source documents without
  truncation; `sec_filing_text_rendered_v3` is a separate deterministic
  derivative.
- Historical and live SEC paths must share parser, renderer, schema, timestamp,
  identity, revision, and integrity authorities.
- `Z` timestamps stay UTC. Offset-free 14-digit SGML acceptance values are
  converted from New York wall time. Date-only values remain explicit fallback.
- Accession prefixes never establish issuer identity; embedded entity evidence
  does.
- Every model chunk includes filing and issuer metadata. Low-impact filings
  remain visible through at least a presence representation.

### Architectural decisions

- `sec_core` is a replaceable mirror and audit accelerator; `q_live` owns
  canonical SEC relationships.
- The Reference Gateway routinely maintains issuer relationships and
  `id_sec_market_bridge_v3`; the SEC Gateway consumes that authority.
- Source revision selection follows SEC chronology and PAC evidence, not
  database insertion order.
- Rendering is uncapped and loss-averse. Chunking and embedding limits belong
  downstream and must be model- and distribution-driven.

### Rejected approaches

- Dropping old SEC tables before validating a versioned rebuild.
- Repairing only downstream tokens or timestamps while source text remains
  clipped or misidentified.
- Treating every SGML time as Eastern, every accession prefix as issuer CIK, or
  every duplicate-looking block as removable boilerplate.
- Materializing redundant text context tables solely for embeddings when a
  point-in-time join can use canonical v3 tables.
- Solving large-text failures with serial execution, arbitrary row caps, or
  higher memory limits instead of bounded byte-aware algorithms.

### Assumptions and unresolved uncertainty

- The latest accepted embedding model, tokenizer limits, and economic learning
  objective still determine final chunk sizes.
- A current SEC frontier audit is required before claiming that all dates after
  the last recorded historical coverage are complete.

## Delivered outcomes

- `TASK-0046` records the v3 source, filing, document, entity, XBRL, bridge,
  revision, renderer, rebuild, and integrity work, including 125 focused SEC
  tests at the completed renderer cutover stage.
- `TASK-0056` records the 240-rule disclosure taxonomy and observed v3 size
  distributions in `pipelines/sec/edgar/SEC_DISCLOSURE_TAXONOMY_V3_ANALYSIS.md`.
- `TASK-0087` records evidence-backed issuer-to-listed-parent mapping and
  point-in-time bridge integration.
- `TASK-0089` and `TASK-0091` record Reference Gateway simplification,
  publication retry semantics, and successful workstation runtime validation.
- The principal operational references are
  `pipelines/sec/edgar/SEC_PIPELINE_LIFECYCLE_AND_REMEDIATION.md` and
  `services/sec_gateway/SEC_GATEWAY_LIFECYCLE_AND_OPERATIONS.md`.

## Unfinished or hanging work

- **SEC v3 token and embedding build:** Current state: not completed in this
  chat. Why: chunk size/count and presence policy must be evaluated against the
  final rendered distributions and selected embedding model. Next action:
  measure model tokenization by taxonomy, approve chunk policy, then run the
  combined v3 token/embedding builder. Owner: user plus next SEC/model agent.
  Related tasks: `TASK-0037`, `TASK-0056`.
- **Current SEC data frontier:** Current state: the ledger retains a bounded
  catch-up dependency after the renderer cutover, while later live-gateway
  operation was reviewed. Why: this summary does not substitute for a fresh
  database frontier audit. Next action: compare canonical v3 filing, document,
  source, rendered, XBRL, entity, and bridge frontiers and run only the bounded
  gap fill if needed. Owner: next SEC operations agent. Related task:
  `TASK-0046`.
- **Separate predecessor conversations:** Current state: ChatGPT conversations
  linked to PAC, missing documents, and filing impact were discoverable but not
  reviewed here. Why: their contents were outside the active Codex session.
  Next action: summarize each only after direct access and verification. Owner:
  chat-continuity work. Related task: `TASK-0140`.

## Handoff to the next chat

Read `TASK-0046` first, then the two SEC lifecycle documents, `TASK-0056`, and
the identity/reference rows `TASK-0087`, `TASK-0089`, and `TASK-0091`. Do not
reintroduce source caps, accession-prefix identity, insertion-time revision
selection, stale normalizers, or independent historical/live implementations.
Before starting embeddings, verify the current v3 frontiers and measure the
chosen tokenizer on the final rendered corpus. Any production backfill,
cutover, or GPU embedding run requires the workstation runtime and database
access controlled by the user.
