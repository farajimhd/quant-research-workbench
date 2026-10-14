# Compact reported-value projection storage

[Original evidence](13ze-sec-original-fundamental-context.md) · [Precision policy and measurements](13zg-sec-extraction-measurement.md)

`sec_reported_publication` now persists the candidate reported-value projection
without copying values, concepts, units or dimensions already retained in the
normalized original bank. Exact DDL and columns appear in the unified launcher
`schema --format sql/json`.

| Table | Key | Stored form |
|---|---|---|
| `research_sec_reported_group_v1` | Projection hash + semantic context hash | Extraction/source-position references, all original positions, exact interval bounds and inclusivity |
| `research_sec_reported_document_v1` | Binding hash | Projection/extraction/source-reconciliation/document/witness/time hashes, explicit numeric mode, gate hash, availability/precision, row count and binding proof |

Both use monthly MergeTree, explicit `live_market_ssd` and lossless codecs.
Different public-time/source reconciliations can add binding rows while sharing
one unchanged projection group set. Different numeric modes have independent
projection hashes. Existing originals and earlier binding rows remain immutable.

Publication independently requalifies document-time evidence, recomputes the
reported projection, and verifies its original bank through the exact source
reconciliation locator before and after publication. Ownership/current-source
guards still run before every write. The shared immutable SEC writer checks
schema/actual part placement, measured byte admission, keys, missing/conflicting/
duplicate rows, bounded inserts and complete readback. Group rows are inserted
before the completion binding row. Unconfirmed publication never reports success.

An isolated live ClickHouse fixture verified linked original-bank publication,
two projection rows, exact readback and zero-insert reuse. Inputs were synthetic
sealed source/time witnesses, not a production original-vintage certification.
No production tables or historical campaign were populated by that test.

These are persisted candidates: `certified=false`, `allocation_certified=false`.
Whole-period census, authority certification, dated class allocation and measured
production capacity remain necessary. The execution CLI is not yet wired to
publish these tables; schema visibility does not imply an active producer or a
permission to consume candidate data as certified research inputs.
