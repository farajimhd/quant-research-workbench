# Acquisition, event repair and historical universe

[Overview](../certified_research_dataset_design.md) · [Previous](02-evidence.md) · [Next](04-market-data.md)

Follow download through certified events, and reconstruct dated eligibility.

**Status:** design requirements; existing-code facts and proposed upgrades are distinguished below. Audit figures are the October 11 snapshot, not a fresh campaign status.

## On this page

- [Canonical events and ingestion repairs](#canonical-events-and-ingestion-repairs)
- [Historical universe and reference dimensions](#historical-universe-and-reference-dimensions)

## Canonical events and ingestion repairs

### Canonical authority

Authority: `market_sip_compact.events_YYYY`, source-day stats and reporting coverage. Maintain full quote/trade lineage, ordinals, price/size scales, condition eligibility, reporting state and source hash. Canonical quote handling is separate from the trade reporting gate.

### Existing updater capabilities

The ingestion entry point is `pipelines/market_sip/flatfiles/download_update_events.py`. Its current code supports remote discovery or explicit start/end source dates, bounded download workers, compact encoding/ClickHouse insert, continuity checks, sampled raw-to-encoded audits, ticker/day index and source-day statistics. New imports compute reporting flags. An updater `ok` manifest is not sufficient by itself to satisfy downstream reporting coverage: reconcile/publish the source-matching reporting receipt required by market-day consumers.

### Source-day classification and G-to-D staging

At plan time classify each source day: already certified and reusable; imported but requiring reporting/provenance repair; cached but not imported; or absent and requiring remote acquisition. Reuse certified days without re-reading retained raw files. Copy required existing G files into verified D cache; configure fresh downloads and ClickHouse file paths to that D cache. Validate Windows/UNC and ClickHouse mount paths refer to the same files. Download/import missing sources, certify encoding and source receipts, then run applicable legacy migrations. This ingestion-owned path is the only raw-file consumer.

### Avoid duplicate bar production

The updater also builds legacy macro/daily-session bars by default. For this unified campaign use its existing `--skip-bars` mode when the shared strict-policy producer owns all derived products. Its current daily-session identity/quote-fallback behavior is not automatically equivalent to the new research price contract. Reuse compatible daily products only after explicit parity/source checks. Do not publish two competing daily-price authorities.

### Sidecar storage placement

Audit optional execution-clock sidecar ownership separately: the current updater DDL passes the common storage-policy argument to its clock sidecars too. Integration must enforce the appropriate explicit workload policy for each table and verify actual parts; one global storage-policy override is not proof that all source and operational products are correctly placed.

### Earlier-range extension and source repair

Never enable `--force-day-delete` or rebuild-existing deletion flags by default. Existing-source repair requires a separately planned restart-safe migration and lineage invalidation. Earlier imports must respect yearly ordinal continuity, not merely insert rows into an older date partition; audit updater support before permitting earlier-range extension. An ordinal-changing canonical migration invalidates indexes/sidecars and descendant receipts. Encoding/schema evolution must be versioned without reinterpreting old bitfields.

### Shared strict trade gate

The new shared trade gate requires the timely classification, valid reporting provenance and ordinary product-specific condition eligibility. Current reporting bits encode timely as `(event_meta & 0xC0) == 0x40`; delayed, unknown and invalid combinations fail. Current rules include conditions 5/13/30/31/32/33, prior NY reporting date and lag greater than 10 seconds. Missing, zero or inconsistent/future participant clocks must not escape as timely. Classification and aggregation eligibility are separately hashed.

### Rejection evidence and archival limits

Preserve counts/notional/volume by rejection reason, source date and unit. Existing five-condition archival limitations remain explicit; required missing provenance needs a versioned ingestion migration/sidecar, never a derived flatfile fallback.

## Historical universe and reference dimensions

### Prepared reference forms

Forms: immutable dated identity/listing intervals; broker-conid evidence; pre-open membership certificates; small per-session listing dictionaries; corporate-action intervals; session calendar.

### Admission conditions

Reference Gateway publication currently requires active listing and symbol, positive conid, USD, US exchange, non-OTC venue, supported STK/STOCK/STOCKS, durable issuer identity, no duplicate durable issuer identifier and no open mapping issues. Contract resolution also enforces compatible unique contracts and exchange/security identity evidence. Reconcile predicate differences between graph admission, universe publication and consumer scope before freezing one shared rule revision.

### Historical reconstruction process

Build the historical listing census from dated source evidence, symbol/event intervals and listings including delisted securities. Resolve provider/SEC/broker identities and apply rules on the corresponding date. Current conid evidence may establish a mapping only with explicit validity evidence; do not claim historical broker execution eligibility from conid existence alone. Unresolved cases remain visible and block strict membership certification. IPOs, delistings, OTC transfers, relistings, symbol changes and share-class changes require distinct intervals.

### Context identities and existing ticker history

Do not filter context using today's universe. A ticker admitted during the target range may need older bars under its prior symbol/listing identity. Preserve competing or unresolved mappings instead of picking the first match. Reference Gateway ticker-event history already exists: inspect its coverage and reuse it rather than blindly redownload it.

[Overview](../certified_research_dataset_design.md) · [Previous](02-evidence.md) · [Next](04-market-data.md)
