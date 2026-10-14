# SEC acquisition stage lineage

[Launcher](13j-launcher-execution.md) · [SEC range admission](13k-sec-acquisition-admission.md)

## Corrected authority

The historical gap-fill runner previously emitted complete acquisition records
for every family after a successful partial/finalize-only run. A successful
integrity audit alone does not prove that archive or bulk acquisition ran.

The runner now finalizes only families whose required stages actually succeeded
without source truncation, with integrity audit last. It no longer labels
historical filling as live-feed coverage. Resume excludes bootstrap, errored and
unfinished stage records. Older family labels remain preserved and require
separate stage-lineage qualification.

`sec_acquisition_contract.REQUIRED` is the shared prerequisite authority:

| Family | Required source stages, plus final integrity audit |
|---|---|
| Submissions | Bulk download/ingest/canonicalization; acceptance enrichment |
| Daily archive / text | Download/validation; entity/text/revision/document/parent repair; acceptance and archive identity repair/audit |
| Companyfacts | Bulk download/ingest/canonicalization; XBRL catch-up and integrity repair |
| Integrity | All 21 lifecycle stages |

Qualified stage intervals must continuously cover the parent range and finish
before its completed clock. Unknown, failed, bootstrap, future-completed or
source-limited evidence does not qualify. Batch sizes and audit sampling are
distinct from acquisition truncation. This certifies acquisition **lineage**;
issuer fact fingerprints and original public-time/value evidence remain separate
mandatory inputs. It does not invent missing filings, float allocation or short
publication timestamps.

## Launcher paths

`launch` qualifies retained SEC lineage before canonical source preparation.
The independent `qualify-sec-lineage --fundamental-start-date YYYY-MM-DD
--end-date YYYY-MM-DD` route prepares the same metadata without market-data
dependencies. It uses the configured ClickHouse endpoint and global source
claim, performs no download or REST request, and returns incomplete when the
requested SEC range still has gaps. October catch-up remains a separate
acquisition requirement.

Source manifests and stage records are never rewritten. Qualification is
idempotent; ambiguous certificates or altered parent/witness hashes fail closed.
The v2 SEC range gate accepts only records with independently verified lineage
certificates, not old `completed` labels alone.

## Exact additional schema

`q_live.sec_acquisition_certificate_v1`: `MergeTree`, partition
`toYYYYMM(coverage_start_utc)`, order
`(coverage_kind,coverage_start_utc,source_coverage_id,source_proof_hash)`.
Storage policy and actual parts must be `live_market_ssd`.

| Column | Type | Codec |
|---|---|---|
| certificate_hash, source_proof_hash | FixedString(64) | ZSTD(1) |
| source_coverage_id | String | ZSTD(1) |
| coverage_kind, contract_version | LowCardinality(String) | ZSTD(1) |
| coverage_start_utc, coverage_end_utc | DateTime64(3,'UTC') | Delta(8),ZSTD(1) |
| stage_witnesses | Array(Tuple(String,String,FixedString(64))) | ZSTD(1) |
| certified_at_utc | DateTime64(6,'UTC') | Delta(8),ZSTD(1) |

Each witness contains stage name, original coverage ID and original row hash.
The certificate hash binds the parent row hash, exact range, witnesses and
contract version. Certification time is operational metadata. Full table types,
codecs, engine, keys and actual disk placement are checked before reuse/writes.

## Verification and inspected data

The bounded real ClickHouse test proves promotion, source preservation, zero-row
resume inserts, and rejection after a witness changes. Pure tests cover partial
runs, source limits, bootstrap, gaps, clocks and audit ordering.

An October 13 read-only production check found 28 completed records in the four
required families, all emitted by the old runner. Including text extraction,
16 of 35 parent records had sufficient prior stage lineage. No production
certificate was inserted in that check. Their latest acquisition endpoint was
August 13 exclusive; these records cannot certify the later October range.
