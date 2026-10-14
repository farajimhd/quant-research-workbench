# Conservative public SEC archive timing

[Archive byte witness](13zc-sec-archive-byte-witness.md) · [Source capture](13za-sec-source-capture.md) · [Original fundamental contexts](13ze-sec-original-fundamental-context.md)

## Source semantics and policy

SEC describes dated Feed archives, delayed dissemination for some late
submissions, and corrections affecting indexes. Acceptance is therefore not an
exact public posting clock. [SEC EDGAR guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)

The SEC-linked PDS specification distinguishes ordinary submissions,
private-to-public releases and corrections, and defines the official filing
date and date-of-change header fields. The currently linked document is marked
March 2025 draft; it supplies format evidence, not a guaranteed latency bound.
[SEC PDS specification](https://www.sec.gov/info/edgar/specifications/pds_dissemination_spec.pdf)

`sec_public_archive_time.qualify` implements the explicit engineering policy
`verified-sec-public-feed-day-v1`. It reconstructs a **public feed day** from
authenticated archive provenance and raw header dates, then permits use only
from the following New York midnight. That boundary is conservative day
precision (`2`), not an exact posting timestamp or the app's receipt time.

## Required evidence

- V2 byte witness: verified complete container digest, raw submission and
  selected document hashes, occurrence ordinal/version and prepared linkage.
- Ordinary public submission, positive declared public-document count,
  known submission type and no correction/deletion/private-to-public/paper
  marker. Special cases require another qualification contract.
- Original successful `downloaded` receipt for the exact official HTTPS SEC
  Feed URL/date, with matching whole-file SHA-256 and byte size. Stopped or
  reused records cannot replace original download evidence.
- Original HTTP Last-Modified must belong to the same New York feed day and
  precede observed download. Later representation modifications are rejected;
  Last-Modified itself is not treated as posting time.
- Raw official filing and filing-date-change dates must both match the feed day.

The default `acceptance_checked_day` mode additionally requires a valid,
unambiguous acceptance header. A known late same-day acceptance is rejected
unless a later verified feed day is used. `official_filing_day` is an explicit
date-only alternative for `.nc` payloads without acceptance fields; it requires
all the date/byte/receipt evidence above. If an acceptance field is present,
its chronology and late-day checks still apply. Unknown or contradictory dates
are rejected in both modes; no absent clock is synthesized.

## Bounded actual validation

The original successful download manifest for Apple's 2025 filing recorded:

| Field | Value |
|---|---|
| Source day | `2025-10-31` |
| Download | `2026-06-12T07:46:29.125026Z`, status `downloaded` |
| Original Last-Modified | `Sat, 01 Nov 2025 02:10:54 GMT` (October 31 in New York) |
| Archive SHA-256 | `8dc28c7b32b21ccb1b7d076f04c9f2989e3de78f4888f373c2be95ba3012a412` |

The receipt hash matched the independently staged G backup. The complete
container/submission/document witnesses were verified again; the raw `.nc`
header declared 91 public documents and matching filing/change dates but no
acceptance timestamp. The default mode correctly failed. Explicit
`official_filing_day` qualification returned `2025-11-01T04:00:00+00:00`,
conservative precision, `exact_posting_time=false` and
`allocation_certified=false`. Retained lineage remained unchanged. The owned D
copy was removed after success; no provider or ClickHouse writer was invoked.

This closes timing qualification for that witnessed source under this named
policy. It does not prove whole-period source coverage, class allocation or
original-value parity for the bulk fundamental catalog. Durable witness/time
publication and automatic launcher qualification remain unfinished.
