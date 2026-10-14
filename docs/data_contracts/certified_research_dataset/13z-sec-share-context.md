# Retained SEC share-context extraction

[Reference discovery](13m-reference-discovery.md) · [Completion audit](13i-completion-audit.md)

## Implemented input and output

`sec_share_context.extract` parses retained `q_live.sec_filing_text_v3.source_text`.
The caller supplies the expected source-text SHA-256 and issuer CIK. Hashes are
checked against the complete UTF-8 text before parsing. No provider request,
flatfile read, persistence or allocation certificate is produced by this helper.

The bounded parser accepts strict XML, optionally wrapped by the explicit SEC
`<XBRL>` envelope. Envelope and leading whitespace removal affect only the
parsing view; source bytes remain unchanged, with removed prefix/suffix byte
counts recorded. Entity declarations and XML recovery are prohibited. Default
limits are 16 MiB, 250,000 XML nodes, depth 128 and 10,000 candidate share facts.

| Observation field | Preserved meaning |
|---|---|
| `source_position`, `fact_id` | Original inline numeric-fact position and optional source ID; distinct facts are not silently deduplicated |
| `concept`, `context_id`, `unit_id` | Expanded supported DEI/US-GAAP share concept and original context/unit identities |
| `period_end` | Instant date proved by the fact's context |
| `dimensions` | Sorted expanded axis/member QNames, including stock class and any additional explicit dimensions |
| `value` | Exact decimal string after the original scale; no Float64 conversion or decimal-context rounding |
| `decimals`, `scale`, `format` | Original numerical precision and transformation metadata |
| `source_text_hash` | SHA-256 of the complete retained payload, including any envelope |

Candidate facts with unsupported units, transformations, typed dimensions,
issuer mismatches, missing contexts or invalid numbers remain in `rejected`
with their source identities and reasons. Malformed document structure,
duplicate context/unit IDs or bound violations fail the whole document.
Unscoped observations retain empty dimensions; they are not interpreted as
single-class evidence. Every result reports `allocation_certified=false`.

## Evidence and remaining producer work

An October 14 read-only test used complete retained 2025 10-K payloads:

| Issuer | Accession | UTF-8 bytes | Accepted share observations | With dimensions |
|---|---|---:|---:|---:|
| Apple | `0000320193-25-000079` | 1,520,222 | 9 | 6 |
| Alphabet | `0001652044-25-000014` | 2,528,358 | 11 | 9 |
| Meta | `0001326801-25-000017` | 2,369,783 | 12 | 12 |

Retained hashes matched the payloads; none of these candidate facts was rejected.
This is representative parser evidence, not historical allocation certification.

Next, the ingestion-owned producer must bind each observation to its qualified
original filing/document vintage and public availability, then independently
map stock classes to dated securities. It must retain conflicting observations,
prove any completeness claim, and publish normalized source evidence before
`float_allocation` or class-level supply receipts can authorize downstream float.
Neither today's tradable-class count nor one unscoped share fact supplies that
proof. This parser is not yet wired as a certified launcher producer.
