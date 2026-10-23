# Retained fundamentals and reference observations

This producer normalizes retained SEC, Massive, corporate-action, FINRA and broker
observations into ARTE. It never downloads data or starts gateways. Backfill and
forward-fill use the same publisher; forward-fill runs after an owning gateway
has durably inserted updates. Re-running an unchanged source reads metadata and
receipts, not historical fact payloads.

## Authority and qualification

The approved `qualified-original-filing-authority-v1` path is implemented in
`pipelines.certified_research.sec_original_authority.verify_document`. Qualified
original filings are authoritative for their reported observations; missing or
disagreeing bulk observations remain reconciliation evidence. The verifier binds
current document lineage to independently reconstructed original rows, exact
reported-value selection and a verified archive/public-time binding. It rejects
unresolved numeric and identity-cover observations. Dimensional contexts remain
separate, and repeated presentations of the same semantic fact retain their
source positions without being added together.

This is document qualification, not complete-history certification. Its result
explicitly sets `whole_scope_certified=False`. The old bulk-derived publisher and
its strict parity guard remain unchanged: relaxing that guard would still publish
bulk values and would not implement original-filing authority. The retained ARTE
normalization receipts are unaffected. Full-census orchestration and publication
of the qualified original authority into downstream historical products remain
separate required steps; do not advertise normalization or a single-document
pilot as their completion.

The original parser recognizes dated US GAAP, DEI and HTTP/HTTPS IFRS-full
namespaces without merging their concept identities. Version 4 preserves empty
`xsi:nil` declarations as missing observations; they never become zero or a
selected numeric value. Nonempty nil declarations remain invalid. Original
precision and all source/public-time/readback checks still apply.

Live-feed documents may form an explicit archive-verification candidate when
exactly one reproducible live occurrence and one ordinary archive occurrence
agree on issuer/accession. Both transport identities are sealed separately.
This is not archive equivalence: the archive witness must still verify exact
document bytes and the original download receipt must match the container.
Competing occurrences, PACs and mismatched archive receipts remain blocked.

The new tables contain retained observations, **not automatically certified
public fundamentals**. Missing publication timestamps remain NULL. Bulk/company
SEC facts retain `original_document_parity_required`; invalid Reg SHO date
relationships remain invalid. No source-derived confidence flag substitutes for
the existing original-document parity guard.

Use `pipelines.certified_research.facts.lookup_sql` for certified research facts
and the existing `reference_producer` / `aligned-public-fundamentals-v1` formula
catalog for the 19 derived formulas. This implementation does not create a second
formula engine, transfer authority from those tables, or fabricate missing
original-vintage coverage. Provider-reported ratios and independently derived
ratios are distinct evidence. Bulk and canonical SEC sources retain separate
lineage until qualified reconciliation establishes equivalence.

Reference Gateway owns corporate actions, short/borrow, identity and account
eligibility acquisition; SEC Gateway owns SEC acquisition. Neither gateway is
started here. The intentionally cancelled Massive subscription is respected.
Legacy Massive statement/ratio retention does not establish an active refresh
job. Missing account-specific eligibility tables are not inferred from borrow
availability or the old universe snapshot.

## Normalized layout

All tables require `live_market_ssd`, verified against both policy routes and
active parts before/after a run. Existing incompatible schemas fail closed.

| Table | Grain and access |
|---|---|
| `arte.reference_fact_v1` | One numeric/categorical metric observation. Ordered by entity kind, entity ID, metric, recorded time, source, context; partitioned by recorded month. |
| `arte.reference_context_v1` | Content-addressed period, taxonomy, dimensions and unit; shared across observations. |
| `arte.reference_source_v1` | Content-addressed observation provenance, source key/hash, public and recorded clocks, quality/reason, recipe. Ordered by family/entity/source. Published only after fact/context readback succeeds. |
| `arte.reference_batch_receipt_v1` | Completed source-partition/cohort fingerprint, recipe/population identity, row counts and verification digest. |
| `arte.reference_stage_v1` | Shared transient staging, one partition per active batch. Removed only after verified publication; no table per family or job. |

Issuer facts are stored once per issuer, not copied to every ticker or day.
Source IDs include all normalized values and clocks. Context IDs include period,
unit, taxonomy and dimensions. All source versions observed by this producer are
retained. A failed write is never blindly replayed: a new attempt stages missing
source IDs and verifies logical output before publishing headers. Physical
retry duplicates are collapsed by identical `ReplacingMergeTree` keys; consumers
use FINAL. Completed headers/receipts are never reset during resume.

An unchanged receipt is a durable publication acknowledgement, not a fresh
re-hash of all historical output. `--audit` deliberately performs exact
bidirectional source/output comparison, including completed work. Use it after
suspected external modification. Table UUIDs bind receipts so table replacement
cannot masquerade as an unchanged destination. External mutation of operational
output is unsupported; it must be investigated rather than hidden by a receipt.

## Efficient admission and execution

Admission makes one narrow source-key discovery per changed family, identifying
occupied partitions and the actual leading index keys (CIK, issuer, symbol, or
provider ticker). It caches that mapping under the runtime directory against
source-part/schema fingerprints. Older physical source versions can only admit
extra work; the writer still reads FINAL. Batches carry both reviewed entity IDs
and their source index keys, so ticker keys optimize reads without replacing
entity identity. A source change during discovery/staging blocks the batch.

Each active batch performs INSERT SELECT, array expansion, hashing, deduplication,
and exact output verification in ClickHouse. Python holds only plans, IDs,
compact aggregate results and progress. Contexts already present are not written
again. Unchanged source IDs are excluded before publication. Queues are bounded
to the worker count. Defaults are 4 workers, 1 query thread and 2 GiB per query;
at most 8 workers are accepted. These are query budgets, not whole-host RAM caps.

## Commands

Use the machine's Python environment, `-B`, and `PYTHONDONTWRITEBYTECODE=1`.
Generated files must stay under `D:/TradingML/runtimes`.

Export an explicit retained snapshot cohort (not live account permission):

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/export_fundamentals_population.py --snapshot-date 2026-10-05 --output D:/TradingML/runtimes/fundamentals-v1/population.json
python -B scripts/run_fundamentals.py backfill --population D:/TradingML/runtimes/fundamentals-v1/population.json --run-dir D:/TradingML/runtimes/fundamentals-v1
```

The second command is plan-only. Add `--execute` after reviewing `plan.json`.
The default range retains all source dates from 1900 through 2140; explicit
`--start`/`--end` bounds are supported. These bounds select source event/filing
(or recorded fallback) dates, not membership or public-availability dates.

For forward-fill, invoke the same command with `forward-fill --execute`, the
same frozen population, dates and runtime. Part fingerprints catch late changes
in old periods; a max-date watermark alone is never used. There is no polling
service or acquisition loop. New population membership requires a new manifest
and run directory; content-addressed publication still avoids rewriting shared
facts. Source recipe changes require explicit migration, not receipt deletion.

One OS lock per database prevents duplicate controllers, including endpoint
aliases. Production ARTE publication is restricted to DESKTOP-SAAI85T and its
operational root; laptop executions are plan-only or isolated validation. A runtime STOP
file or Ctrl+C stops admission and drains active batches. Failure leaves STOP and
completed receipts. Remove STOP only after diagnosis and confirming the previous
owner has drained. No automatic restart is installed.

`status.json` and plain terminal output separate active, queued, published,
unchanged, audited and failed batches. `batches.jsonl` records actual wall time
for metadata, staging, writing, exact verification and receipt publication.
"Complete" means the selected retained observations were processed, not that
historical public-time coverage or current gateway freshness is certified.

## Downstream access

`queries.observations_sql(database, entity_kind, entities, metrics, cutoff)`
returns the latest recorded vintage **within each period/unit/dimensional
context**, with source/quality information. Timestamp arguments must be timezone
aware. Same-time conflicting observations are returned with `conflict=1`, never
arbitrarily selected by hash. Consumers must reject unresolved conflicts.

`since=` returns changes in the context window plus the immediately preceding
seed for each context. It does not materialize daily forward-filled copies.
`clock='public'` additionally requires certified quality and a real public clock;
the retained-source normalizer does not grant either. Missing data stays missing.
Public mode uses the qualified historical public clock independently of later
acquisition. Add `known_at=` to also enforce a system-knowledge cutoff; recorded
mode always bounds recorded time. These two clocks are never substituted.

`population.resolve_ticker` resolves a ticker through date-valid symbol intervals;
missing/ambiguous mappings block instead of substituting today's ticker. Issuer
mapping is separately qualified and may be unavailable while security facts are
accessible. `known_at=` additionally bounds mapping knowledge. The snapshot
export records unresolved SEC mapping counts; those are not silently marked as
covered. No event row is not proof that a corporate action was applicable or
missing; source-coverage receipts remain the evidence for that distinction.

## Validation

`tests/test_fundamentals_retained.py` covers bounded inputs, plain progress,
publication/reuse/audit, ambiguous write recovery, corrections, context seeds,
same-time conflicts, missing-output detection and schema drift. Live tests require
`FUNDAMENTALS_LIVE_TEST=1`; they create isolated SSD databases and remove them.
Production coverage must be assessed separately from these bounded tests.

## Direct identity and subsidiary associations

Snapshot policy `retained_snapshot_population_v2_direct_issuer` admits direct
issuer-to-listing bridges only. `filing_issuer_to_listed_parent` is a disclosure
association, never an alternative stock identity. A unique retained issuer CIK
identifier can supply a snapshot identity where no direct/rejected/unknown
bridge exists; it never supplies dated ticker history. Dated resolution keeps
interval and optional knowledge-time guards. Subsidiary SEC observations retain
the subsidiary CIK; they are not copied to or summed into parent fundamentals.
Existing population manifests are immutable: export a new manifest and re-plan;
do not replace the request of an existing receipt-bearing campaign.

`q_live.id_issuer_relationship_v1` remains the verified association authority.
Do not create a second ownership table from co-filing or name similarity.
Run the read-only retained-evidence audit before publication:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/audit_fundamentals_associations.py --snapshot-date 2026-10-05 --run-dir D:/TradingML/runtimes/fundamentals-association-audit --endpoint http://192.168.1.218:18123
```

The audit searches all selected issuers for EX-21/subsidiary document metadata
and distinct co-filing entities. It scans each co-filing partition once and
bounds document lookups to 256 issuers. Per-unit hashed receipts support resume;
source/schema/part fingerprints invalidate changed input. STOP drains the active
bounded read. It writes coverage, candidate evidence locators and existing
verified relationships under the runtime directory, without production writes.
Candidates require document review to establish parent/child direction, entity
identity, ownership type and effective dates. Archive dates are not ownership
inception or public availability. No candidate found is unknown coverage, not
proof of no subsidiaries. Original-document/public-time qualification remains
an independent backfill gate.
