# Original SEC archive byte witness

[Source capture](13za-sec-source-capture.md) · [Extraction and lineage](13z-sec-share-context.md)

`sec_archive_witness.inspect_archive` verifies a selected retained SEC document
against its complete submission in a designated **D copy** of a SEC archive.
This is ingestion-owned SEC source verification; it does not reopen SIP files,
download data or publish reference/allocation receipts.

## Verification

The verifier checks the prepared fingerprint and archive file identity, then
streams the tar archive until the exact submission member is found. It checks
the complete raw submission SHA-256, the archive-member ordinal and reproduced
source version/rank, accession/CIK, exact document sequence/name/type and UTF-8
payload SHA-256. It reuses the existing SEC submission parser. Archive size and
modification time must remain unchanged through verification.

Defaults bound the compressed archive to 2 GiB, a selected submission to
64 MiB, expanded scan accounting to 32 GiB and member enumeration to 100,000.
Cancellation is checked during enumeration and verification. Missing members,
oversized units, mismatched hashes/identities/ordinals or changed files fail
explicitly. It does not extract archive paths to the filesystem.

Staging remains responsible for G→D copy, free-space reserve and whole-file
digest readback. A successful result reports `original_bytes_verified=true`,
but explicitly keeps `public_time_qualified=false` and
`allocation_certified=false`. A byte witness is not a public-availability clock
or evidence that one issuer total belongs to one tradable class.

V2 witnesses can additionally accept a verified staging receipt and independently
rehash the complete D container. They retain raw filing/change dates, public
document count and special-release markers. A [separate timing qualifier](13zd-sec-public-archive-time.md)
uses this evidence with an authenticated original download receipt; the byte
verifier itself never declares public-time or allocation qualification.

`inspect_archives` verifies up to 1,000 selected documents from the same
canonical archive/date with one container digest and one streaming archive
pass. Each selected submission is decoded once, even when it supplies multiple
documents; only requested document payload hashes are calculated. Results
preserve request order and are returned only after every selected document
matches and the file's size/mtime remains stable. Missing or conflicting
members/documents fail the whole group. The single-document `inspect_archive`
entry point uses this same implementation.

## Actual bounded validation — October 14

The archive recorded at D was missing; its corresponding workstation G backup
was present. Before copying, the workstation D volume had 3,680,428,720,128 bytes
free. Preserving the larger 256 GiB/10% reserve left 2,880,274,216,141 usable bytes.
A private copy under the workstation operational runtime root was made and
independently digest-read back; the G backup was preserved.

| Check | Verified result |
|---|---|
| Archive | `20251031.nc.tar.gz`, 1,521,439,675 bytes |
| Container SHA-256 | `8dc28c7b32b21ccb1b7d076f04c9f2989e3de78f4888f373c2be95ba3012a412` |
| Submission | `0000320193-25-000079.nc`, ordinal 2900, 9,392,537 bytes |
| Submission SHA-256 | `f9f7a8af64141fa6d6c182753cd583f56d29af70d8770636b5ab0c7aa444dadc` |
| Document SHA-256 | `77b7f5d24cd2ba72b140fd24ed039da8026343aae5c5a5ff744c1310378ad2d8` |
| Reproduced archive version | `3192e12891423cd9d848209bb14890aa12f7650d2a35a3561c171a2439848e33` |

Retained ClickHouse lineage was independently rechecked afterward. The owned
D copy and its private directory were removed only after successful verification.
No ClickHouse data was written and no provider or full campaign was invoked.

## Remaining integration

This verifier is callable but not yet integrated into the launcher's grouped
archive staging/capture flow. Durable witness publication, source-stage and
public-time qualification, competing revision handling and dated stock-class
mapping remain required before certified fundamentals/float publication.
The successful example proves byte parity for this filing, not whole-period
coverage or original-vintage parity for the existing bulk fundamental catalog.

`sec_archive_staging_plan.plan` now provides the read-only grouped staging plan.
It validates prepared fingerprints, canonical D date/quarter/file identity,
designated G backup roots and private D destinations below the operational
runtime root. Documents from the same archive share one planned copy. Exact
backup sizes/mtime and current D space feed the shared reserve/chunk planner;
missing, escaping or oversized archive units fail explicitly. The plan creates
no directories and starts no copy.

Unit fixtures verified shared-archive grouping and multiple chunks with a
preserved reserve. A live one-document plan found the 1,521,439,675-byte backup,
reserved 800,154,503,987 bytes and reported one chunk without copying. The
executor must still revalidate space and source stats before each chunk, copy
and verify each archive once, publish completed witnesses, then remove only
owned successful copies. Grouped execution and durable witness publication
remain unfinished; this plan is not authorization to use stale space readings.
