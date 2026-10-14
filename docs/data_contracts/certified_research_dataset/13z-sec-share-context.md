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

## Document-vintage qualification dependency

An additional October 14 live metadata check found all three inspected sources
marked `source_revision_kind=legacy_archive_occurrence`, with an empty
`source_version_key`. Extraction success and a matching text hash therefore do
not establish an independently qualified archive occurrence. Do not assign
these payloads the original filing's acceptance timestamp or publish allocation
receipts from them yet.

The existing SEC revision kernel derives version keys from the archive date,
occurrence order, archive path/member and submission content hash. A document
text hash is not a substitute for the original submission hash. A versioned
ingestion-owned lineage repair must recover and reconcile that provenance,
retain any competing/PAC versions and independently verify its readback.
It must not generate a plausible version key from incomplete metadata.

After qualification, a conservative source-availability policy can admit a
payload only after its proven archive day ends (and no earlier than the filing
acceptance bound). Later occurrences must not be backdated to original filing
acceptance. This is a proposed qualification policy, not implemented admission.

The current fundamentals projection uses retained bulk/company-fact values and
filing acceptance clocks. Its acquisition-stage closure and range coverage
checks do **not** independently demonstrate that every selected value matches
the original public document vintage. Original-vintage qualification of those
values remains a separate completion gap; passing the existing SEC producer
fixture is not proof of that requirement.
