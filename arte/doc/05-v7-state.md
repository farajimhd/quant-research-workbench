# V7 historical seeds and streaming state

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

## Initial ARTE historical MLE algorithm

`arte-historical-mle-seed-1` is a new completed-session algorithm. It is not the
parent application's `historical_checkpoint` export of a streaming object.

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

Keep detector/local-swing state, indicator state, and strategy state separate from
the level-book authority. Include each in recovery when the strategy depends on it.
Do not use retrospective chart candles as strategy inputs.

## New interval contract

The contract is inspired by half-open intervals. It does not copy the earlier
experimental level algorithm or assume its row fields are sufficient for V7.

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

1. Pin the completed source generation and preceding certified seed.
2. Run historical V7 and validate fit, lineage, identity, and split handling.
3. Write immutable level/state objects to ClickHouse.
4. Verify referenced object counts and hashes.
5. Publish the seed manifest as certified.

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
