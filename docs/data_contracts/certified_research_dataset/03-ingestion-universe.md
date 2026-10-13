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

Certified existing days now skip downloads and raw-file audits. Their event integrity is checked against the unique compatible ClickHouse source-day certificate, retaining exact integer timestamp/count precision. Existing clock repair is restricted to canonical/sidecar reads; missing clock rows require an explicit migration. `--force-day-rebuild` cannot reopen a certified day from retained files. Raw count and encoding audits remain part of new-source acquisition.

### Source-day classification and G-to-D staging

At plan time classify each source day: already certified and reusable; imported but requiring reporting/provenance repair; cached but not imported; or absent and requiring remote acquisition. Reuse certified days without re-reading retained raw files. Copy required existing G files into verified D cache; configure fresh downloads and ClickHouse file paths to that D cache. Validate Windows/UNC and ClickHouse mount paths refer to the same files. Download/import missing sources, certify encoding and source receipts, then run applicable legacy migrations. This ingestion-owned path is the only raw-file consumer.

The updater's optional `--archive-root-win` stages only new-source files. It validates designated G/D roots and path containment, copies through a unique D temporary file in 8 MiB chunks, checks source stability and SHA-256 readback, then atomically publishes the cache file. Interrupted copies remove their own temporary file. Existing cache files are preserved; unavailable or wrong-sized archive files are reported and left to normal remote downloading. Copy/verification byte progress and results use the existing updater terminal and runtime report. The launcher source phase selects this with `--stage-archive`.

### Source preparation command

`python -B scripts/prepare_certified_research.py prepare-sources --start-date 2026-01-01 --end-date 2026-10-02 --acquire-missing --stage-archive` runs only source preparation. It requires secure Keeper canonical-source ownership, designated storage placement and accessible configured runtime/cache roots. Optional path overrides are `--runtime-root`, `--cache-root`, `--archive-root` and `--clickhouse-cache-root`; the Windows cache and ClickHouse mount must address the same D SSD cache.

The phase refuses incomplete imported days and internal ordinal holes, acquires appendable missing ranges, rereads import certificates, runs source-bound reporting repair, and verifies all source/reporting receipts plus mutation barriers before returning the frozen source hash. `--context-start-date` includes required earlier source sessions without changing the 2026 target start; the full planner must derive this bound from compatible state and rolling-feature requirements. Query byte budgets pass through exactly without rounding a small budget up to a GiB. Child ownership loss drains the child; interruption requires receipt reconciliation on resume. Certified existing ranges are reused without launching ingestion. The full derived-data launch command is still under implementation; missing historical sidecars continue to block reporting repair.

### Avoid duplicate bar production

The updater also builds legacy macro/daily-session bars by default. For this unified campaign use its existing `--skip-bars` mode when the shared strict-policy producer owns all derived products. Its current daily-session identity/quote-fallback behavior is not automatically equivalent to the new research price contract. Reuse compatible daily products only after explicit parity/source checks. Do not publish two competing daily-price authorities.

### Sidecar storage placement

New-day ingestion now captures `historical_trade_reporting_ns_v1` before its successful import manifest. The compact sidecar stores source date, ticker/ordinal, exact SIP nanoseconds, nullable participant nanoseconds (no SIP substitution), the full trimmed condition list, reason mask and source digest. It uses monthly `MergeTree` partitions, `(ticker, source_date, ordinal)` ordering and explicit lossless codecs on `live_market_ssd`. Original clocks remain distinct from execution-time fallback semantics.

The ingestion-owned stage reconciles every encoded field and reporting flag to canonical rows. Unmatched or ambiguous source ordering blocks certification. Published evidence must match staging in both directions; staging and table placement are checked. Capture refuses already-certified days. Native certification independently recomputes reason/flags from original clocks and full conditions, verifies complete unique canonical matching and the source digest, and publishes the existing versioned reporting coverage contract before the import success manifest. Verified reuse performs no inserts; an unknown-clock trade marked timely is rejected. Historical evidence acquisition and migration consumption of this new sidecar still require integration; this does not repair previously imported days automatically.

The October 12 live audit found microsecond-only `historical_event_execution_clock_v1` and `historical_event_execution_clock_rest_v2` schemas, with 1,267,591,761 and 1,208,692 physical rows respectively. The original updater substitutes SIP time when participant time is absent, so those sidecars cannot distinguish missing original clocks from valid equal clocks or resolve nanosecond threshold edges. AAPL's January 2 clock-coverage row was absent. These are input gaps, not permission to infer timely trades. The launcher `plan` now reports clock columns, placement, physical sizes and dated coverage, explicitly without reporting certification. Existing recovery code can reacquire REST evidence; broad reporting integration must preserve full clock/condition evidence in ingestion-owned ClickHouse sidecars and independently reconcile it to canonical events.

Audit optional execution-clock sidecar ownership separately: the current updater DDL passes the common storage-policy argument to its clock sidecars too. Integration must enforce the appropriate explicit workload policy for each table and verify actual parts; one global storage-policy override is not proof that all source and operational products are correctly placed.

### Earlier-range extension and source repair

#### Native nanosecond repair adapter

`native_reporting_repair.step` repairs imported canonical trades from a frozen, ingestion-owned original-nanosecond reconciliation receipt. It accepts no flatfile path and does not establish historical clock provenance by inspecting a current snapshot. Production calls require the canonical `SourceClaim`, `sip_raw_ssd` target placement and `live_market_ssd` sidecars/metadata. `prepare-sources` now routes previously frozen native repair headers through its owned writer connection before the global mutation barrier, permitting interrupted native jobs to resume. Matching complete native receipts are reused without another day scan. It rejects changed/ambiguous headers and never creates a trusted repair job from unsealed NS rows. Historical REST evidence acquisition, initial job admission and the full derived launcher remain unfinished.

The adapter verifies complete ticker/ordinal/SIP association, original condition/clock reasons and the acquisition fingerprint. A compressed immutable repair header stores the source/evidence hashes, target, column list, preserved-field fingerprint and deterministic mutation marker. The header uses `MergeTree`, `live_market_ssd`, `ZSTD(1)` throughout, with `Delta(8)` for its UTC microsecond creation clock; hashes use `FixedString(64)` and the column list uses `Array(String)`. It stores one metadata row per repaired source day, with no market payload on disk.

Only the high reporting bits change. All other canonical fields and quote rows are independently fingerprinted before and after repair. Coverage changes to `native_mutating` before submission, blocking downstream admission even if the server mutation finishes before certification. The controller receives `submitted`, `mutating` (IDs and remaining parts), or `complete`; it owns waits/cancellation. Active polls read control metadata instead of rescanning the source day. Completed mutations are not blindly resubmitted.

Final certification rechecks frozen NS evidence, reporting flags and preserved fields. Native receipts retain the original-clock fingerprint, and source freezing includes that fingerprint. Resume reuses matching headers/receipts; changed source evidence, duplicate/incomplete association, unrelated active mutations or preserved-field changes fail closed. The existing legacy raw/map adapter remains separate until acquisition supplies the required native reconciliation receipts.

Never enable `--force-day-delete` or rebuild-existing deletion flags by default. Existing-source repair requires a separately planned restart-safe migration and lineage invalidation. Earlier imports must respect yearly ordinal continuity, not merely insert rows into an older date partition; audit updater support before permitting earlier-range extension. An ordinal-changing canonical migration invalidates indexes/sidecars and descendant receipts. Encoding/schema evolution must be versioned without reinterpreting old bitfields.

### Shared strict trade gate

The new shared trade gate requires the timely classification, valid reporting provenance and ordinary product-specific condition eligibility. Current reporting bits encode timely as `(event_meta & 0xC0) == 0x40`; delayed, unknown and invalid combinations fail. Current rules include conditions 5/13/30/31/32/33, prior NY reporting date and lag greater than 10 seconds. Missing, zero or inconsistent/future participant clocks must not escape as timely. Classification and aggregation eligibility are separately hashed.

### Rejection evidence and archival limits

Preserve counts/notional/volume by rejection reason, source date and unit. Existing five-condition archival limitations remain explicit; required missing provenance needs a versioned ingestion migration/sidecar, never a derived flatfile fallback.

## Historical universe and reference dimensions

### Dated identity acquisition

Reference Gateway's Massive adapter now accepts `as_of` calendar dates for ticker overviews and all-ticker discovery, including inactive symbols. Existing undated service calls retain their behavior. `historical_identity.normalize` extracts only identity/lifecycle fields and `reconcile` checks stable FIGI and issuer binding; missing/contradictory identity evidence fails closed. Financial fields are excluded from both the certified observation and its evidence hash.

This distinction is required by the [provider's ticker-overview contract](https://massive.com/docs/rest/stocks/overview): the historical query can select SEC information by report-period date, which cannot establish original public availability for fundamentals. A bounded live January 2, 2026 AAPL request returned FIGI `BBG000B9XRY4`, exchange `XNAS` and active status. This verifies the dated API path, not broad historical coverage or a continuous listing interval.

An empty ticker-event history does not prove a listing start. Dated observations prove their requested dates only; historical interval reconstruction must combine verified listing dates, stable identity, dated endpoints and complete intervening change coverage. Persistence, canonical historical mapping and bulk repair orchestration for these observations remain unfinished; the current unresolved universe intervals remain blocked.

Plan output now reports independent Reference Gateway repair requirements. A first rejection such as `missing_dated_symbol_mapping` can also hide missing listing lifecycle or history binding. Counts deduplicate listing/stage jobs. Identity/listing/history evidence must precede dated symbol reconstruction; admission is rerun afterward. The inventory is inspection only and does not infer historical dates or launch a broad history download. Live samples of rejected January listings had null listing dates and absent interval/history fields, confirming the need for this ordering.

### Prepared reference forms

Forms: immutable dated identity/listing intervals; broker-conid evidence; pre-open membership certificates; small per-session listing dictionaries; corporate-action intervals; session calendar.

### Admission conditions

Reference Gateway publication currently requires active listing and symbol, positive conid, USD, US exchange, non-OTC venue, supported STK/STOCK/STOCKS, durable issuer identity, no duplicate durable issuer identifier and no open mapping issues. Contract resolution also enforces compatible unique contracts and exchange/security identity evidence. Reconcile predicate differences between graph admission, universe publication and consumer scope before freezing one shared rule revision.

### Current reporting repair authority

Imported-day reporting repair now reads only canonical events and existing source-bound ClickHouse sidecars. `backfill_trade_reporting_flags.py` refuses flatfile hydration and raw-file reconstruction before file access or mutation. It requires staged/mutating sidecar certificates, verifies counts, placement, canonical equality and the persisted mapping, and retains authoritative evidence after completion. Missing sidecars require an explicit ingestion-owned versioned acquisition/migration path; the campaign cannot reopen retained files. G→D staging remains available only to the canonical acquisition/import owner while importing new source days.

The older flatfile-repair procedure is superseded by this authority rule. Existing certified days remain reusable; unresolved legacy clock fields block strict derivation rather than receiving invented timeliness.

### Historical reconstruction process

Build the historical listing census from dated source evidence, symbol/event intervals and listings including delisted securities. Resolve provider/SEC/broker identities and apply rules on the corresponding date. Current conid evidence may establish a mapping only with explicit validity evidence; do not claim historical broker execution eligibility from conid existence alone. Unresolved cases remain visible and block strict membership certification. IPOs, delistings, OTC transfers, relistings, symbol changes and share-class changes require distinct intervals.

### Context identities and existing ticker history

Do not filter context using today's universe. A ticker admitted during the target range may need older bars under its prior symbol/listing identity. Preserve competing or unresolved mappings instead of picking the first match. Reference Gateway ticker-event history already exists: inspect its coverage and reuse it rather than blindly redownload it.

[Overview](../certified_research_dataset_design.md) · [Previous](02-evidence.md) · [Next](04-market-data.md)
