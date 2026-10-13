# SEC acquisition admission

[Launcher](13j-launcher-execution.md) · [Controller](13-campaign-controller.md)

SEC fact fingerprints prove the selected values and public-clock evidence;
they do not prove all source acquisition has finished. Planning and SEC
producers also check `q_live.sec_coverage_manifest_v3`, including its actual
SSD placement.

| Requirement | Admission rule |
|---|---|
| Required families | Daily archives, bulk submissions, bulk companyfacts, integrity audit |
| Completion | `completed` or `covered_empty`, zero errors, non-null completion timestamp |
| Range | Continuous half-open UTC intervals covering every requested UTC date |
| Gaps | Even a one-millisecond gap blocks admission |
| Other statuses | Running jobs and table-recency bootstraps do not establish coverage |
| Evidence | Bounded manifest metadata and content hashes; no fact-count inference |

Adjacent half-open intervals compose. Sparse filings and zero fact rows cannot
establish or disprove acquisition coverage by themselves. Completion of a live
poll does not substitute for bulk or integrity coverage.

The range is inclusive in UTC dates, including the UTC date reached by the final
AH boundary. Each SEC producer checks its actual fundamental context range;
the launcher's initial inspection checks target dates or the selected earlier
context bound. Original-public timestamp and fact-provenance checks remain
separate. Coverage inspection never downloads data or scans facts to infer a
range. Its implementation participates in the frozen reference algorithm
contract, so older capacity profiles must be qualified for the updated contract.

`pipelines/certified_research/sec_coverage.py` defines the range admission and
bounded metadata query. No new persistence schema is introduced: it reads the
SEC Gateway's existing manifest. Producers fail before staging/publication when
coverage is incomplete; saved authority verification applies the same check.

## Verified current boundary

October 13 inspection confirms completed acquisition through **August 12**;
the exclusive completed boundary is August 13 at 00:00 UTC. January–October 2
requests require acquisition/integrity catch-up beginning August 13. No SEC
catch-up or provider request was launched by this change.

Production manifest inspection and seven bounded ClickHouse SEC tests passed.
Tests cover covered-range fundamentals/derived calculations, stage/source
fingerprints, receipt reuse, and rejection before publication for an uncovered
range. This is acquisition admission, not proof that the entire unified fresh
launch or all SEC context dependencies are complete.
