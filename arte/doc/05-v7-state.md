# V7 historical seeds and streaming state

## Current persisted historical authority

The user has selected the existing `arte` V7 persistence contract for
continued use. `arte.structural_levels_v7` stores coalesced historical closing
states as half-open `[valid_from, valid_to)` intervals. Here `valid_from` is
the checkpoint's session-end availability, not an intraday confirmation.
`arte.structural_level_coverage_v7` is the publication fence and session audit;
`arte.structural_level_builder_checkpoint_v7` stores the latest complete
historical engine checkpoint per ticker. A missing or empty-certified session
must remain distinguishable.

The stored contract is `arte-structural-levels-v7-1`. Preserve role-transition
ancestry, mixture parent identity, fit geometry, and the terminal checkpoint.
Read only a published coverage generation. Never expose a future `valid_to` as
a strategy feature. Before ARTE advances a builder checkpoint, verify source,
trade-condition/reporting, split, algorithm, numerical, and predecessor
compatibility. A changed historical source resumes from the last compatible
predecessor, not by relabeling a current checkpoint.

These tables exist as a persistence design and, per the user's update, are
being populated. This document does not claim that the full population,
source coverage, storage placement, or ARTE Rust parity has been verified.
ARTE must not build a redundant historical checkpoint store merely because
an earlier proposal described one. A separate version is justified only by a
validated incompatible change and an explicit migration.

## Two outputs, separate authority

| Output | Producer | Allowed use |
|---|---|---|
| Historical seed | Historical V7 after complete-session reconciliation | Initialize a later session |
| Streaming state | Causal streaming V7 during the session | Current decisions, audit, optional recovery |

Historical V7 may inspect the completed session's price action. Its results are
retrospective for that session. They become available only after computation and
certification. Streaming output must never be relabeled as a historical seed.

Shared primitives do not make these two algorithms interchangeable. Version the
historical extractor, streaming updater, and seed compatibility contract separately.

## Historical MLE algorithm and Rust parity target

`arte-historical-mle-seed-1` described a new completed-session Rust algorithm.
Its steps remain a parity target, not permission to replace the existing
historical V7 authority. It is not a `historical_checkpoint` export of a
streaming object. ARTE must compare its levels, identities, fit results, and
next-session decisions with the pinned existing V7 producer before promotion.

- Require a session certificate with matching input hash and completed time bounds.
- Use whole-session extraction to propose reaction areas. Keep rejected candidates
  available for evidence collection; a rejected proposal is not a tradeable level.
- Match today's proposals to at most one prior identity by association distance.
  Break distance ties by identity. Do not chain-merge prior identities.
- Re-evaluate prior bands against the completed session. Annotate actual turning
  extremes. Admit the first nonoverlapping resolved rejection per role.
- Preserve observations across sessions. Apply explicit split factors to fit
  coordinates without altering the predecessor seed or original observation IDs.
- Fit Student-t geometry and evaluate conservative component splits. Keep the
  closest component under the original identity; assign deterministic child IDs.
- Retain insufficient candidates without usable fitted geometry. If a previously
  qualified fit fails, reject the whole seed instead of retaining stale geometry.
- Hash the resulting state and predecessor identity. Its availability is the
  completion time of this build, never a retroactive session-open timestamp.

This algorithm has offline boundary tests. It still requires representative
multi-session validation and integration with certified ClickHouse publication.
The caller supplies the ingestion certificate; hashing bar inputs alone does not
prove upstream market coverage. The maintenance authority must certify that.

## Intraday processing

Every eligible event updates the appropriate ordered market state. Developing bars
update on arrival. Streaming V7 advances on its defined completed causal one-second
observations. This does not require refitting V7 on every tick.

The `market_structure` bridge uses end-stamped one-second observations. A market
bar `[start_ns, end_ns)` becomes a V7 candle at `end_ns / 1_000_000_000`, only after
completion. The bridge accepts no trade at the session's exclusive end. Historical
adapters must use the same coordinate convention before creating input hashes;
start-stamped source arrays must not be relabeled without an explicit conversion.
This bridge is a new contract, not evidence of full legacy streaming parity.

Market and structure updates share an owned runtime. A calculation failure or late
event blocks both strategy-facing projections until recovery. Partial state remains
internal and cannot supply stale levels to a strategy. The bridge itself does not
certify coverage, establish watermarks, activate strategies or send orders.

Keep detector/local-swing state, indicator state, and strategy state separate from
the level-book authority. Include each in recovery when the strategy depends on it.
Do not use retrospective chart candles as strategy inputs.

## Interval contract and causal query

The original ARTE proposal below was inspired by half-open intervals. The
existing `arte.structural_levels_v7` contract now supplies this shape and is
the starting authority. The proposal does not copy the earlier experimental
level algorithm or assume its row fields are sufficient for V7.

| Record | Minimum responsibilities |
|---|---|
| Level version | Stable identity, geometry, roles, historical ancestry, required fit references |
| Validity | `valid_from`, optional `valid_to`, with `[from, to)` semantics |
| Knowledge | Publication generation and `available_at` |
| Seed manifest | Instrument/session, prior seed, algorithm/config hashes, source generation, referenced objects |
| Fit/state object | Immutable required observations and algorithm-specific state |

Query level versions at a cutoff and within a pinned published generation.
An open interval alone does not prove that a level existed at a past decision.
Never expose future interval endpoints as strategy features.

Persist changed versions. Reference unchanged objects from subsequent seeds.
Deduplicate immutable fit objects by a verified content hash. Keep the observations
needed by the actual V7 fit. Do not replace them with count/sum/variance unless
mathematical proof and replay validation establish equivalence.

## Seed publication

1. Pin the completed source generation and preceding certified seed or builder
   checkpoint from the existing `arte` V7 authority.
2. Run historical V7 and validate fit, lineage, identity, and split handling.
3. Write changed level intervals and the terminal builder checkpoint to
   ClickHouse under a compatible versioned contract.
4. Verify referenced object counts, hashes, interval continuity, and the
   terminal checkpoint.
5. Publish V7 coverage last as the certification fence.

Readers ignore incomplete generations. Multi-table writes are not assumed atomic.
Retries use deterministic publication identity. A crash between steps must not
produce a visible partial seed. Old referenced objects remain until safe retention.

## Availability and splits

Track split effective time and when the reference was known. Preserve pre-split
versions. Adjust coordinates under a versioned rule without importing future
metadata into earlier live decisions. Required missing metadata blocks readiness.

For backtests, historical seed availability must be explicit. Use recorded publication
time where available. Otherwise use a declared simulation schedule and label it as
an assumption. Never claim observed next-day readiness from today's recomputation.

## Recovery

Streaming audit/recovery snapshots may persist in a separate table. They include
input cursors and the exact historical seed ID. They are not daily seed authority.
If snapshots are disabled, replay from the seed and durable observations.
This saves snapshot storage but increases restart time.

There is no arbitrary older-seed fallback. Missing required sessions must be built
and certified. A corrupt seed or a qualified failed fit blocks readiness; do not
silently substitute a fixed-width book or stale fitted values.

## Earlier seed-object envelope proposal

The earlier ARTE-specific persistence proposal is `arte-seed-objects-1`.
A root object lists
ordered level-object hashes and the historical seed metadata. Level objects
contain the fit and observations needed to resume. Raw object bytes use SHA-256.
Historical seed identity continues to use its versioned typed-content hash.
Exact floating-point JSON round trips are required before hash verification.

Publication writes and reads back all objects before inserting the manifest.
Repeated identical rows are accepted. Multiple distinct payloads for one immutable
identity are an error. Loading verifies every object and reconstructs the seed
before returning it. Table merges must not conceal conflicting payloads.

`schemas/001-seed-storage.sql` defines those proposed tables with
`live_market_ssd`. It is not applied by build or validation commands.
This proposal must not become a parallel historical seed authority while the
existing `arte.structural_levels_v7` contract is compatible. Existing tables
require schema review; `IF NOT EXISTS` is not proof that a deployed schema
matches the contract.

## Causal stream implementation boundary

`arte-causal-v7-1` consumes completed epoch-second bars. Its policy identifies the
input generation separately from its historical seed hash. Each update checks
observed time, ordering, session bounds and bar geometry before mutation.
The state owns rolling noise, pending encounters, proposals, observations and fits.

Gaps and reaction timeouts resolve encounters as unresolved. A crossing needs two
adjacent completed seconds before acceptance. A rejection requires its turning
extreme to lie inside the contacted fitted band. Candidate association radii never
serve as actionable fit geometry. Only fitted levels appear in the strategy projection.

A failure after mutation marks the state failed. It cannot continue, supply an
actionable projection or emit a recovery checkpoint. Restore a prior verified
checkpoint or replay from the historical seed. Recovery uses a separate versioned
contract and cannot be submitted to historical seed publication.
