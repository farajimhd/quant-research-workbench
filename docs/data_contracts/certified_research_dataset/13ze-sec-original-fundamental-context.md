# Original-document fundamental observations

[Listed-security cover evidence](13zf-sec-listed-security-cover.md)

[Read-only extraction measurement](13zg-sec-extraction-measurement.md)

`sec_fundamental_context.extract` applies the frozen app metric catalog to exact
retained document text. It reuses the bounded strict XML parser and source hash
checks used for shares. The default share extraction contract stays unchanged.

The result retains every selected observation or rejection, source position,
concept namespace, context identifier, explicit dimensions, unit identifier,
decimals, scale, exact decimal value, instant/duration period and document hash.
The extraction hash binds this result and the metric catalog hash. Supported
concept namespaces currently cover reviewed DEI and US GAAP namespace forms;
other namespaces are rejected explicitly. No HTML recovery or entity expansion
is enabled. Currency, shares, pure ratios and currency-per-share units are
supported; unsupported transformations, typed dimensions or malformed periods
remain visible rejections. This output is bounded in memory and is not persisted
by this module.

A live bounded read of the retained Apple accession `0000320193-25-000079`
passed extraction, including more than 50 observations, duration contexts and
USD units. This establishes parser behavior for that filing, not full catalog,
issuer, taxonomy or historical-period coverage. Synthetic fixtures separately
verify exact negative scaled decimals, per-share units and rejected contexts.

These observations are explicitly `certified=false` and
`original_vintage_qualified=false`. Publication must still connect each exact
document to its archive witness and public-time qualification, reconcile original
values against existing bulk/company-fact records with matching concept, unit,
period and dimensions, preserve revision conflicts, and certify coverage.
Class/issuer allocation and corporate-action basis are separate dependencies for
float. Existing bulk fundamental projection does not acquire original-document
parity merely because this parser succeeds.

`sec_fundamental_reconciliation.reconcile` now compares bounded original and bulk
observations by taxonomy, concept, unit, complete period and explicit dimensions.
It checks the extraction seal and independently requalifies the archive/time
evidence; the extracted document hash must match the witnessed document. Exact
decimal comparison retains missing contexts, competing original values and value
mismatches. Matching decimal renderings of Float64/unknown numeric storage remain
`source_precision_unqualified`; preserved decimal text is required for parity.
Unmatched original source positions and parser rejections remain in the result.
An accession-bounded CH loader retrieves at most 10,001 rows (the extra row detects
overflow), retains the actual numeric storage type and normalizes explicit dimension
maps. Its retained Apple accession read passed a live ClickHouse test.

Reconciliation output is still `certified=false`: it is not a complete coverage
certificate. Production `reference_producer.run_sec` now requires a caller-supplied
independent original parity coverage guard, bound to the frozen source hash and
qualified public vintage, before writes and throughout materialization. The current
launcher does not yet produce that certificate, so production fundamental output
remains blocked rather than mislabeling bulk source data as original-vintage proven.
Isolated `certified_research_test_` SQL fixtures are explicitly outside that production
certification path. Whole-period parity coverage certification remains work.

Original observations and comparison evidence now have a bounded durable publisher
in `sec_fundamental_publication`. Its four exact schemas are exported by the unified
launcher `schema --format sql/json` (current v2 catalog):

- `research_sec_fundamental_source_context_v2`: one unit/period/dimension context
  per extraction/context hash, shared by observations.
- `research_sec_fundamental_source_observation_v2`: exact decimal strings, concepts,
  context references, source positions and parsing provenance.
- `research_sec_fundamental_source_comparison_v2`: bulk source identity/value,
  comparison status, context hash and original-position references. Original values
  are referenced rather than copied into each comparison.
- `research_sec_fundamental_source_document_v2`: document/catalog/witness/time/
  reconciliation hashes, availability, precision, row counts and rejected/unmatched
  evidence. This completion row is inserted last.

All tables are monthly MergeTree with explicit `live_market_ssd` and lossless codecs.
Original context/observation rows are keyed by extraction identity; comparison and
completion rows are keyed by reconciliation identity. New bulk or time evidence can
therefore append a reconciliation version while reusing the exact original rows.
Existing v1 tables are neither altered nor overwritten; no automatic v1 migration
or qualification is performed. A live isolated fixture verified a second comparison
version inserted only its comparison and completion rows and left the original
version exactly reusable.
The publisher recomputes reconciliation from its input evidence, checks source and
storage guards before writes, requires measured write admission, inserts missing
rows in bounded batches, and independently verifies the complete group. Completed
groups with missing/conflicting/duplicate rows fail explicitly. An isolated live
ClickHouse fixture verified partial resume and zero-insert reuse; it used synthetic
source/time evidence and does not certify a real filing or production capacity.
The unified launcher now wires this evidence capture through
`--prepare-sec-fundamental-evidence`, which requires `--verify-sec-archives` and
explicit selected SEC documents. It runs after completed archive verification,
uses retained CH document text and published archive/time/download receipts, and
requires a measured SSD `sec_fundamental_source` rate matching the recipe exported
in `schema --format json`. It processes one document at a time, checks a 2GiB plus
query-memory reservation, and rechecks current document lineage, immutable
archive/time proof and accession-bounded bulk census on each publication guard.
Missing evidence, changed sources or missing pilot rates stop publication.
No SEC/Massive request or training export is made by this stage. Guard queries and
stage throughput have not yet been qualified over a production document selection.
Whole-period coverage certificates remain unfinished. Production normalized
fundamental authority and float publication therefore remain blocked; these source
tables preserve the evidence needed to implement those remaining steps.

`sec_fundamental_readback.verify` independently reconstructs the exact extraction
and reconciliation seals from the normalized tables, including context foreign
keys, source positions, original comparison-value references, row counts and
header metadata. It bounds reads, audits exact schemas/part placement, rejects
duplicate/unreferenced contexts and missing references, and rereads the complete
group after source guards to detect changes during verification. No filing payload
is fetched. Canonically ordered reconciliation makes reconstruction deterministic;
the current v3 recipe also supports exact nullable decimal-text storage and known
unit aliases. Earlier recipes require explicit requalification under the current
value-comparison policy rather than guessed source ordering. Live isolated tests verified
both retained versions and duplicate rejection; fixtures separately reject mutated
values, context hashes, statuses and availability metadata.

Readback verification is a content-integrity result, not public-time or coverage
certification. The caller must still qualify the corresponding original witness,
time receipt, current source lineage and whole-period census. It returns
`certified=false` and cannot authorize production fundamental publication alone.

The original-fundamental launcher stage now requires this independent readback
after capture. It first recomputes the reconciliation hash from current inputs,
then verifies the persisted extraction/reconciliation seals under current lineage,
archive/time and bulk-source guards. A failed or mismatched readback prevents the
document from entering the completed count. Results expose the exact reconciliation
keys plus matched/unmatched document counts, and terminal progress identifies
verified content separately from successful comparison matching. A verified
unmatched comparison remains visible evidence and cannot pass production parity
coverage. The source-capacity recipe now includes the readback implementation;
older measured recipe profiles must be requalified before this stage runs.

SEC source-write admission now also accounts for the current serialized metadata
envelope: the persisted projection is the larger of the compatible measured pilot
projection and `current_metadata_bytes × requested_documents`, with the configured
safety factor applied to both. Staging and merge allowances remain additive and
physical-volume grouping/reserves still apply. This deliberately gives no reuse
discount and prevents a small pilot from underbudgeting a larger current document.
The envelope is capped at 64MiB per unit; archive witness publication has its
stricter 8MiB bound. Admission reports the pilot and known-envelope contributions
separately. The envelope is a conservative planning allowance, not a measured
compressed disk size or a qualification of unobserved peak usage. Missing or
incompatible measured source pilot rates still block; this change does not create
a bootstrap exemption or qualify the production campaign.

Independent readback and launcher results now also expose `parity_inputs_ready`
and exact `parity_input_blockers` counts. Matching every bulk row is insufficient:
unmatched original numeric facts, numeric/cover parser rejections, absent bulk
census and any non-matched comparison status keep readiness false. The launcher
aggregates these counts across documents and prints them separately from verified
content and completed capture. Evidence capture can complete with blockers so
the rejection/conflict evidence survives; this is never a certification success.
Even readiness true only clears these per-document input conditions. Whole-period
source census, original-vintage qualification and class allocation remain separate
required authorities before production data can be certified.

### Campaign admission and producer wiring

Production `ExecutionLaunch` now checks the original-parity verifier for every
fundamental scope before storage admission or campaign writers. The same verifier
is passed through `ReferenceHandlers` to `run_sec`, which rechecks it with the
source guard during publication. Stored-plan restoration preserves this injected
verifier; it is application code, never executable code in persisted arguments.
The required evidence remains certified whole-scope coverage, all bulk values
matched, original public vintage qualified and the exact frozen source hash.

Missing or incompatible evidence blocks admission immediately. This closes the
previous late failure where a controller could prepare other products and only
then discover that it never supplied the required parity verifier. The CLI still
cannot manufacture this authority from candidate captures, reported projections,
or a matching retained filing list. Automatic whole-period specification assembly
remains unfinished; no production certification was granted by this wiring change.

### Concrete original-scope verifier

`sec_scope_parity.Guard` now supplies the application verifier. Its frozen metadata
specifies issuer/CIK, target range, earlier context start, metric names, filing forms,
source hash and one accession/document/reconciliation/reported-binding locator per
filing. Specifications are hashed, copied and indexed by exact scope; competing,
changed or absent specifications fail. These are locators for existing ClickHouse
evidence, never assertions that a document is certified.

For each invocation it independently checks:

1. Qualified SEC acquisition coverage and retained submission/fragment inspection.
2. Exact binding membership against the whole selected filing census.
3. Current document lineage, original normalized readback, full catalog and fresh
   exact bulk parity, including unmatched original facts as blockers.
4. Original public-time readback linked to the same extraction/reconciliation.
5. Every selected projection's accession and availability against its original
   binding. Live-source fallback rows and projection clocks earlier than the
   proven original availability are rejected.
6. Unchanged projection, filing inspection and acquisition fingerprints afterward.

The verifier is read-only, bounds documents to 1,000 and projection provenance to
10,000 rows/16 MiB, and never certifies class allocation. It returns certified
scope evidence only after every check succeeds. Mocked dependency tests exercise
composition and rejection; no real production scope has passed. Automatic frozen
specification construction and wiring through fresh/stored/collection launcher
adapters remain required. Existing acceptance clocks may precede conservative
archive-day evidence; those inputs require a versioned clock/source migration,
not relaxed verification.
