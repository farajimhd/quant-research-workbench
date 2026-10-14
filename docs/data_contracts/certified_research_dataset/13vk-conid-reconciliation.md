# Massive ticker and IBKR conid reconciliation

[Identity prerequisites](13v-identity-prerequisites.md) · [Acquisition](03-ingestion-universe.md)

## Responsibility

The unified launcher is a reusable batch pipeline for any requested range,
including future dates once acquired. It does not maintain the live universe.
Optional flatfile download, compact encoding and canonical import remain owned
by `download_update_events.py` behind `--acquire-missing`; they precede derived
processing. Reconciliation never reads SIP flatfiles or calls Massive.

Keep identity resolution separate from admission. OTC and inactive provider
entities remain in the mapping inventory. Event-time OTC filtering belongs to
the market reader after verifying exchange-field semantics. Broker contract
definitions, dated alias/lifecycle evidence and broker execution availability
are separate requirements; a positive canonical conid does not prove them.

## Run

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/prepare_certified_research.py reconcile-conids --start-date 2026-01-01 --end-date 2026-10-02 --query-memory-gib 8 --format json
```

Add `--publish-conid-mappings` to persist the result. Publication requires the
configured secure Keeper connection, database dictionary ownership and 256 MiB
of unreserved SSD headroom. Exit status 2 means unresolved entities remain;
the inventory is complete but the full population is not certified. No bars,
fundamentals or live admission writes start from this command.

## Identity rules

- Census every retained, non-deleted Massive US `stocks` entity, including
  inactive entities and all provider instrument types. The summary separates
  instrument types; an ETF is not silently treated as a common stock.
- Resolve through canonical composite/share-class FIGIs. Conflicting FIGIs,
  duplicate active entity spellings, ambiguous security/conid associations,
  conids shared by different securities, open mapping issues and conflicting
  retained coverage block resolution. Ticker or company-name matching alone
  cannot assign a conid.
- Recipe `retained-massive-conid-v2` also uses retained SEC evidence: an active
  provider's normalized CIK and class-preserving ticker must match both
  `sec_core.sec_bulk_mirror_company_ticker_v3` and a unique active direct
  issuer/listing row in `q_live.id_sec_market_bridge_v3`. SEC rows retain their
  source-file locator; bridge rows retain their source hash. CIK alone, a
  listed-parent bridge, conflicting FIGI ownership (including expired bindings), ambiguous bridges
  or a different share class cannot establish this association. SEC tables
  do not supply native FIGIs: they connect a provider identity to an existing
  canonical security and listing without manufacturing a FIGI.
- Retain all candidate conids and listing identities. Only a unique positive
  UInt64 canonical conid with source provenance produces `status=resolved`.
  Unknowns use `status=blocked`, `conid=0` and an explicit reason.
- Report event tickers without a resolved current provider identity. Some need
  dated aliases rather than a new conid. Current spellings are never projected
  backward as certified historical membership.

## Compact persistence

| Table in `q_live` | Form and authority |
|---|---|
| `research_massive_conid_reconciliation_v1` | One row per snapshot/provider entity; UInt64 conid, compact categorical status/type/exchange, candidate conid/listing arrays, source-evidence hash |
| `research_massive_conid_snapshot_v1` | One receipt per complete, independently read-back snapshot; recipe version, source fingerprint, requested range, counts and row fingerprint |

Both are MergeTree on `live_market_ssd`, sorted by immutable snapshot identity,
with explicit lossless ZSTD codecs. `schema --format sql` exposes their DDL.
Consumers must require the matching receipt. A receipt certifies exact mapping
inventory publication, including blockers, **not** universal tradability.

## Per-ticker review and resolution

`review-conid-issues` freezes the unresolved provider-classified common-stock
and ADR identities present in the requested events, including inactive ones.
It records a separate decision for each entity, so ticker reuse does not merge
two issuers or share vintages. `--case-ticker` filters display only; the review
and publication still cover the entire frozen cohort. `--case-details` shows
every record in text mode; JSON includes all normalized decisions by default.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/prepare_certified_research.py review-conid-issues --start-date 2026-01-01 --end-date 2026-10-02 --query-memory-gib 8 --publish-conid-mappings --case-ticker JUNS --case-ticker LGHL
```

This batch review uses full broker contract definitions already retained in
`id_source_mapping_v1.evidence_json`, not a broker API. The receipt must match
the exact provider FIGIs and CIK; the existing resolver verifies stock/USD/US,
primary exchange, share-class symbol, restriction flag and issuer name. A
unique retained definition can select among old and newer conids. ADR name
suffix handling requires broker type ADR and provider depositary-share wording.

Additional paths require independent SEC CIK/class-symbol evidence, canonical
issuer CIK or a checksum-valid US ISIN, unique stock/conid ownership, issuer-name
agreement and full provenance. ISIN-backed weak issuer associations explicitly
retain a canonical CIK-binding repair dependency. Historical aliases are read
with dates and source hashes; an old alias/conid is not automatically assigned
to the current listing.

Recipe `retained-massive-conid-v3` preserves original Massive wire ticker case.
`BCpC` and `TpC` must remain different from `BCPC` and `TPC`; the canonical event
index already preserves those distinctions. This corrects batch identity
comparison without editing the live Gateway's stored inventory or event data.

Review recipe `retained-conid-case-review-v2` also distinguishes contracts for
different symbols under one security. It selects an exact wire-symbol owner
only when the retained FIGI coverage binds that listing and the conid is unique
among its US/USD stock owners. Other symbol/conid versions retain a dated-link
dependency; they are not substituted for the requested symbol.

The current v4 recipe additionally checks original broker-public receipts.
The original country assertion must be US, the source conid and stock symbol
must bind the canonical owner, and the source ISIN must match a checksum-valid
canonical identifier. US-listed foreign issuers may have non-US ISINs; domicile
is not the same as listing country. Conflicting canonical country assertions
remain explicit repair dependencies. CIK contradictions are retained even when
their source hash is missing. A qualified issuer CIK can explain a stale issuer
name; weak issuers require independent name agreement. The retained SEC ticker
catalog's company name can corroborate provider abbreviations or spelling errors
only for the exact CIK/class-symbol pair. No fuzzy-name threshold is relaxed.

| Additional table in `q_live` | Form |
|---|---|
| `research_conid_case_review_v1` | One immutable decision per review/entity, with selected/candidate conids, method, unresolved dependencies, historical aliases and proof hash |
| `research_conid_case_evidence_v1` | Content-addressed, compressed original review evidence; decisions reference it by hash |
| `research_conid_case_review_receipt_v1` | Complete review receipt, source/range fingerprints, exact counts and independently read-back row fingerprint |

These three tables use ReplacingMergeTree and explicit ZSTD on
`live_market_ssd`. Keys include content hashes: identical concurrent retries
compact; competing legitimate payloads retain different keys and are rejected
by exact readback. Readers require the complete matching receipt and must not
choose an arbitrary review hash as "latest". `schema --format sql` lists all
five reconciliation/review tables and their actual engines and codecs.

Review publication writes only immutable audit/proposal objects; it allocates
no canonical dictionary identities and therefore needs no canonical dictionary
lease. Source rechecks, 128 MiB unreserved SSD admission, exact schema/part
placement checks, content/preimage validation and independent readback remain
mandatory. An interrupted publication resumes missing exact records; no receipt
is written after changed source evidence. Canonical mapping or issue updates
retain their existing ownership controls. The older `reconcile-conids` snapshot
publisher still requires secure Keeper dictionary ownership.

`identity_resolved` means a supported research conid association, not closed
canonical issues, verified current broker execution, certified event ownership
for an inactive issuer, or a corrected ticker-history timeline. Remaining
dependencies are explicit. A history-only conflict can coexist with a unique
FIGI/conid association; its history remains quarantined. Inactive identities
without a matching retained contract remain blocked pending dated evidence.

Validation: the full January–October 2, 2026 cohort contained 775 records for
774 tickers. All records and evidence were published and independently checked;
The latest v4 review retains 355 supported associations and 420 blockers.
Earlier reviews remain immutable. The three review tables together use about
1.03 MB with all four review vintages retained; evidence objects are reused by
content hash. Physical parts were
on `live_market_ssd`. Canonical mappings and historical
timelines were not overwritten.

Metadata counts and projections run in ClickHouse. Transport uses 4,096-row
pages, capped at 50,000 provider entities and 200,000 records per dependency;
publication uses 512-row batches. FIGI matching uses indexed metadata, never
market-event row loops. Interrupted writes can resume by verifying and inserting
only missing exact rows. Changed or competing rows are rejected; canonical
listing/symbol tables and live Gateway flags are never overwritten.

## Remaining evidence work

Full resolution requires additional retained or newly acquired broker/reference evidence for
missing or ambiguous conids, canonical FIGI binding repairs, and dated alias
proofs for historical spellings. Missing evidence remains a blocker. This
command is an independently runnable prerequisite, not yet the authority used
by the existing historical-universe compiler or automatic live repair.
