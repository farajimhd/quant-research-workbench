# SEC Gateway Lifecycle and Operations

## Scope

The SEC gateway is the live acquisition and canonical-write service. It discovers
new filings, downloads accession artifacts, parses the same v3 filing/document/
entity/text contracts used by historical processing, writes live XBRL when
available, maintains restart-safe XBRL context work, and detects historical gaps.

It does not own the global SEC-to-market bridge and it does not create SEC text
tokens or embeddings. `reference_gateway` routinely maintains
`q_live.id_sec_market_bridge_v3`; `text_embed_gateway` consumes audited v3
rendered text later.

For canonical table contracts, rebuild stages, timestamp authority, revision
semantics, and the full defect ledger, see the
[SEC pipeline lifecycle and remediation reference](../../pipelines/sec/edgar/SEC_PIPELINE_LIFECYCLE_AND_REMEDIATION.md).

## Startup and Preflight

The service performs these checks before live polling:

1. Load configured environment files without logging secret values.
2. Verify SEC user-agent, ClickHouse connectivity, source/read database, and
   configured write database.
3. Create or validate v3 write schemas, the live accession-ingest manifest, and
   coverage manifests. Active startup never clones a stale v1 or v2 schema.
4. Bootstrap coverage from canonical tables only when the manifest is empty.
5. Audit duplicate and orphan state.
6. Detect historical filing, text, and XBRL gaps.
7. Generate a workstation historical-fill command with explicit arguments and
   sync its required runtime files.
8. Optionally start the bounded historical job when workstation auto-run policy
   permits it.

A failed required preflight prevents polling. A temporary ClickHouse or SEC
outage is reported as degraded state; it is not allowed to produce false durable
coverage.

## Live Filing Flow

```text
SEC current Atom feed
  -> completed-revision lookup in sec_filing_live_ingest_manifest_v3
  -> bounded filing queue and worker assignment
  -> optional per-CIK submissions enrichment
  -> accession .txt artifact download
  -> shared SGML header, entity, document, and revision parse
  -> text-bearing source preservation and readable derivative
  -> canonical filing/entity/document/text/skip write
  -> companyfacts fetch when XBRL or inline-XBRL is present
  -> canonical XBRL write
  -> point-in-time bridge lookup
  -> sec_xbrl_context_v3 write or durable pending manifest
  -> complete live-ingest manifest transition
  -> targeted write audit and coverage update
```

The queue is bounded. A worker does not report completion until every applicable
canonical insert and context transition has returned successfully and
`sec_filing_live_ingest_manifest_v3` records the exact source revision as
`complete`. `pending`, `pending_source`, and `failed` revisions remain replayable.
A durable `retry_after_utc` prevents a lagging SEC source from causing repeated
downloads and ClickHouse writes on every feed poll.

## Source and Parsing Rules

The accession `.txt` artifact is the live filing source when submissions
enrichment is absent or lagging. The parser:

- reads CIK and entity roles from SGML content, never from the accession prefix;
- treats explicit `Z` acceptance timestamps as UTC;
- converts exact 14-digit SGML acceptance values from New York wall time to UTC;
- splits `<DOCUMENT>` payloads and preserves each text-bearing HTML, text, or XML
  document independently;
- keeps inline-XBRL primary HTML in the readable filing path;
- records binary, image, archive, and XBRL-sidecar decisions as classified skip
  or structured-XBRL outcomes;
- writes deterministic source hashes and revision lineage.

Submissions JSON is enrichment and relationship authority when it contains the
accession. If a cached payload does not contain a newly discovered accession,
the gateway bypasses the cache and fetches it once more. A 404 or still-lagging
response does not invalidate a complete SGML filing artifact. When SGML has no
exact acceptance timestamp, the live manifest remains `pending_source` so the
metadata can be retried instead of declaring a date-only fallback complete.

## Canonical Write Order and Recovery

The live writer and accession manifest preserve recoverability and idempotency:

1. Record the accession and exact source revision as `pending`.
2. Write complete source text first, preserving the recovery authority.
3. Write filing, entity, archive/accession inventory, and document rows.
4. Write the readable derivative or explicit skip outcome for every applicable
   document.
5. Write XBRL concept/fact/frame rows when the source has published them.
6. Complete or durably defer XBRL context synchronization.
7. Transition the exact accession revision to `complete` only after the required
   writes return successfully.
8. Advance coverage and audit status only after that completion boundary.

Current-row selection is based on deterministic source revision rank, not worker
completion time. A same-revision replay is allowed while the live manifest is
not complete; deterministic keys and revision-aware ReplacingMergeTree tables
make that replay idempotent. This repairs crashes after document insertion but
before rendered text, skip, XBRL, or context insertion.

## XBRL Context

The gateway does not copy readable filing text into a context table. For XBRL,
however, it maintains `market_sip_compact.sec_xbrl_context_v3` because that is a
required ticker-associated packed-model product.

After canonical XBRL writes, the gateway resolves the event-valid listing through
`q_live.id_sec_market_bridge_v3`. Context work is recorded in
`sec_xbrl_context_sync_manifest_v3`, allowing a crash or temporarily missing
bridge mapping to be reconciled later. Context insertion is key-idempotent and
bounded by configured batches, memory, and ClickHouse threads.

## SEC HTTP Policy

All SEC requests share the configured user agent and rate limiter. The default
minimum interval is 0.12 seconds, below the SEC maximum of ten requests per
second. Requests are serialized through that limiter even when filing workers
run concurrently.

Response handling is source-specific:

- `403` and `429`: enter the longer rate-limit cooldown and retry within bounds.
- `5xx` and network failures: enter a transient cooldown and retry within bounds.
- submissions `404`: record missing enrichment, continue from valid SGML when
  possible, and leave relationship/timestamp reconciliation pending.
- companyfacts `404`: cache the negative result for five minutes, then retry; an
  XBRL-bearing filing remains `pending_source` until the exact accession appears.
- accession artifact `404`: fail the filing job because the canonical source is
  not available.

Retries never advance coverage until the source unit is durably written.

## Error Lifecycle and Terminal State

Errors have active and recovered states. A failure becomes active with its
operation, accession or stage, timestamp, and concise cause. A later successful
poll, write, or reconciliation resolves the corresponding incident rather than
leaving it counted as an active error.

The Rich terminal reports:

- current poll and market schedule state;
- bounded queue and active worker accessions;
- completed, skipped, duplicate, retried, and failed filing outcomes;
- last durable filing and XBRL-context writes;
- active and recently recovered errors;
- coverage, audit, pending context, and cache state.

Compact terminals prioritize active work and failures. The terminal is a view of
structured service state; JSONL logs remain the diagnostic authority for a
traceback and its exact accession.

## Historical Gap Handoff

The gateway detects historical gaps but delegates bulk and archive rebuilding to
`sec_historical_gap_fill.py`. The generated workstation script contains explicit
dates, databases, artifact roots, v3 tables, worker bounds, and execution mode.
It does not depend on ambient shell defaults.

The generated runtime includes the SEC pipeline and shared ClickHouse helpers.
The finalizer and historical stages use explicit write-gated contracts, so
`--execute` reaches every mutating child stage. Coverage is recorded only after
the complete required chain succeeds.

## Live Defects and Remedies

| Defect | Observable symptom | Durable remedy |
| --- | --- | --- |
| Submissions 404 aborted an otherwise valid filing | `SecHttpError` for a per-CIK JSON after the `.txt` artifact was available | Make submissions enrichment nonfatal when SGML is authoritative; preserve the unresolved metadata state |
| Inline-XBRL HTML was labeled as a sidecar | Primary filing text absent with an XBRL skip row | Prefer HTML-like primary classification and share the corrected role detector with historical parsing |
| Active errors remained after successful recovery | Terminal showed old errors indefinitely | Track resolution timestamps and clear active state on the matching successful operation |
| Batch failures lacked an actionable lifecycle | Generic `batch failed` messages without durable unit status | Attach accession/stage context, retain tracebacks in structured logs, and update coverage only after durable success |
| Large caches could grow with SEC payload size | Long-running memory growth | Bound submissions, companyfacts, missing-CIK, and recent-metadata caches by count and age |
| Live and historical identity could diverge | Different CIK selected for the same accession | Use shared SGML entity parsing and `sec_filing_entity_v3`; never parse issuer CIK from accession prefix |
| Live timestamps repeated the historical timezone bug | Explicit UTC values shifted by New York offset | Share source-aware acceptance parsing and preserve `Z` as UTC |
| XBRL source writes could succeed before context writes | Missing packed context after a crash or absent bridge | Persist pending context work and reconcile it idempotently after restart |
| A document row could suppress recovery of a partial accession | Rendered text, skip rows, or XBRL remained absent after a mid-write crash | Use `sec_filing_live_ingest_manifest_v3` as the only completion authority and replay the same revision until its final transition |
| Cached submissions or companyfacts predated a new accession | Missing exact acceptance metadata or zero XBRL facts became permanent | Bypass a positive cache once on accession miss, expire negative 404 entries, and durably defer incomplete source propagation |
| Fresh write databases could inherit stale schemas | A v3 table was cloned from a v1/v2 layout | Remove legacy schema fallback; require an explicit v3 schema authority |
| Gateway and reference service could both mutate the bridge | Conflicting identity ownership | SEC reads point-in-time bridge rows; routine bridge maintenance remains in `reference_gateway` |
| Generated historical jobs could silently dry-run new stages | Finalizer exited quickly without mutation | Propagate `--execute` through explicit stage metadata and test every write-gated child |

## Shutdown and Restart

Shutdown stops feed polling, prevents new queue admission, drains or cancels
bounded workers according to the configured timeout, closes HTTP/websocket and
terminal tasks, and leaves durable pending context work for restart. Coverage is
not advanced for interrupted filings.

After a ClickHouse interruption, restart is safe because incomplete accession
revisions remain non-complete, same-revision canonical writes are replayable,
and context work is separately manifested. Operators must still audit the exact
run log and verify `/health`, pending-ingest, and pending-context counts.

## Operator Commands

Preflight only:

```powershell
Set-Location D:\TradingML\codes\quant_research_workbench_pipelines
.\scripts\run_sec_gateway.ps1 -CheckOnly
```

Start the service:

```powershell
Set-Location D:\TradingML\codes\quant_research_workbench_pipelines
.\scripts\run_sec_gateway.ps1
```

Before accepting the live service after a rebuild, verify:

1. `/health` reports the intended read/write databases and no required preflight
   failure.
2. Active errors are zero or have a current, explained external cause.
3. New feed accessions produce filing, entity, document, source, and rendered
   rows with matching lineage.
4. An XBRL filing advances canonical XBRL and either completes context sync or
   creates a visible pending manifest row.
5. Coverage timestamps reflect durable writes, not only successful feed polls.

## Renderer Authority

The live gateway and historical archive path both call the shared
`sec_filing_text_extract_parts.build_rows` producer. That producer uses
`sec_packed_text_renderer_v8` for HTML, plain text, and eligible XML; there is no
separate live normalizer. Complete submitted source remains unchanged in
`sec_filing_text_v3`.

The pre-v8 historical rows already present in `sec_filing_text_rendered_v3` do
not change when the gateway code is updated. Rebuild that derivative from the
v3 source table and audit it before generating SEC tokens or embeddings. See
`SEC_TEXT_RENDERER_V8_AUDIT.md` in the historical pipeline directory.

## Related References

- [SEC Gateway README](README.md)
- [SEC historical lifecycle and remediation](../../pipelines/sec/edgar/SEC_PIPELINE_LIFECYCLE_AND_REMEDIATION.md)
- [Historical SEC pipeline README](../../pipelines/sec/edgar/README.md)
- [Text embedding gateway README](../text_embed_gateway/README.md)
