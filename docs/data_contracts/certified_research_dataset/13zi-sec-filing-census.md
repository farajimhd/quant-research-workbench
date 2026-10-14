# Retained filing census for coverage discovery

[Reported projection storage](13zh-sec-reported-projection-storage.md)

`sec_filing_census.inventory` supplies the missing bounded primary-document
discovery primitive. Scope requires explicit issuers, filing-date range and forms,
with at most 256 issuers, 32 forms, 10,000 filings and 16MiB metadata. It reads only
retained ClickHouse filing metadata, not payloads or provider endpoints.

The inventory includes filings with unknown dates as unresolved records instead
of silently dropping them from a date filter. Missing/oversized primary-document
names, invalid accessions and missing filing source hashes remain explicit issues.
Duplicate accession identity and out-of-scope rows fail. A server count/byte/hash
census is checked before and after reading the metadata to detect source changes.
Resolved entries retain issuer/accession/form/date/document/source-hash identity.

A live read for Apple, Alphabet and Meta, forms `10-K`, `10-K/A`, `10-Q`, `10-Q/A`,
January 2025 through October 2, 2026 found
21 filings with resolved primary-document metadata and no metadata issues.
This is `discovery_ready=true`, `coverage_certified=false`. It does not prove all
expected submissions were ingested, that every payload exists or parses, or that
all corresponding archive/time/projection bindings are verified. The acquisition
coverage manifest and an independent submission completeness census must still
be reconciled with this inventory before whole-period coverage can be certified.
Bounded launcher discovery is now available through repeated `--sec-census-cik`
and explicit `--sec-census-forms`, with `--fundamental-start-date` and a measured
capacity profile. It is supported by source preparation and fresh launch routes;
manual document selection cannot be mixed with census selection. Resolved primary
documents feed the existing share/archive/fundamental/projection stages, retaining
their opt-in flags and admission requirements. At most 1,000 documents are selected
per invocation; overflow fails rather than truncates. Empty or unresolved discovery
blocks processing. The full census hash is rechecked after source preparation;
changes stop completion while keeping the prepared source evidence.

For example, add these source-selection arguments to the existing complete launch
command instead of listing each `--sec-share-document`:

```text
--sec-census-cik 320193 --sec-census-cik 1652044 --sec-census-cik 1326801
--sec-census-forms 10-K,10-K/A,10-Q,10-Q/A
--fundamental-start-date 2025-01-01
```

This discovers retained source documents for the explicit cohort; it does not
automatically choose all forms, infer an issuer context length or certify acquisition
completeness. Whole-universe cohort scheduling, submission snapshot/fragment coverage
and durable coverage publication remain unfinished. No historical campaign was run
to test this launcher wiring.

### Independent filing identity check

Add `--reconcile-sec-submissions` to require the retained census to agree with
`sec_core.sec_bulk_mirror_filing_v3` before source preparation. This inventory
comes from the bulk submission parser, which reads all submission entries.
The acceptance overlay cannot substitute for it: its producer filters to an
existing wanted-accession list.

The gate compares issuer/accession identity, form, filing date and primary document.
Missing, unconfirmed, conflicting or unresolved entries stop preparation before
writers start. Both inventories are fingerprinted and rechecked after preparation;
source changes stop completion while preserving prepared evidence. Discovery without
this flag remains explicitly uncertified candidate preparation.

Identity agreement alone is insufficient for coverage certification. Bulk snapshot
freshness, original download receipts and complete referenced submission fragments
must also be verified. These checks do not download data or certify allocation.
