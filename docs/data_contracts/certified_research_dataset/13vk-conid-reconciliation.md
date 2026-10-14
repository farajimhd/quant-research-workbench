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

Metadata counts and projections run in ClickHouse. Transport uses 4,096-row
pages, capped at 50,000 provider entities and 200,000 records per dependency;
publication uses 512-row batches. FIGI matching uses indexed metadata, never
market-event row loops. Interrupted writes can resume by verifying and inserting
only missing exact rows. Changed or competing rows are rejected; canonical
listing/symbol tables and live Gateway flags are never overwritten.

## Remaining evidence work

Full resolution requires broker contract definitions/search evidence for
missing or ambiguous conids, canonical FIGI binding repairs, and dated alias
proofs for historical spellings. Missing evidence remains a blocker. This
command is an independently runnable prerequisite, not yet the authority used
by the existing historical-universe compiler or automatic live repair.
