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
Automatic launcher-wide discovery and durable coverage publication remain work.
