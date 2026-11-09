# Quote evidence repair

## Source-first replacement

The user authorized replacing the payload-first repair after a bounded recall
comparison against previously detected changed bars. The replacement uses
`quote_repair_campaign_v1 --source-first` and separate `-quote-v4` roots. The v4
suffix identifies the repair algorithm; the final quote policy remains v3.

`quote_candidates_v1` queries the frozen ordinal plan for source issue intervals.
It nominates condition/size changes, wide paired books, quote prices dislocated
from the previous/next eligible extreme trade, and trades dislocated from their
carried quote book. Reverse-prefix windows avoid quadratic suffix aggregation.
Quote hints include their possible carry interval until the next actual quote.
These are conservative OFFLINE nominations only: completed-bar repair/quarantine
still determines output evidence. No future adjacent trade is an encoder input
or a replacement price. Session state resets and trade rules remain shared.

`quote_selective_v1` caches those source intervals once per frozen ticker/session,
tests sealed full-context file envelopes, and decodes only candidate payloads.
Packed files need two timestamp reads per bar segment because their descriptors
lack clock bounds. Unchanged payloads STILL receive complete source SHA256 and
independent target-hash verification. Coverage is reconciled from sealed source
coverage plus exact deltas for decoded files. Missing bounds take the full repair
path. Completed immutable v2 receipts may be reused after ancestry/hash checks;
they are not deleted or recomputed merely because the selector changed.

Native source probes: AAPX 40,828 events ~0.10s; AAPL 912,687 events ~0.69s;
WTID 263,350 events ~0.24s; AIRTW/LARK ~0.05s. These are individual bounded
session measurements, not whole-corpus throughput guarantees. The old jobs must
only be drained after real changed-bar recall and output parity checks pass.
Training is not stopped by this replacement.

Repair query engineering now batches up to 4,096 requested quote closes per
cache lookup/source pass (previously 512), within the existing statement bound.
This amortizes repeated full-session prefixes while retaining exactly the same
clocks, causal book and cache keys. Cache writes use executemany; fixed-size
records are decoded as a vector after checking every cached record's byte size.

`quote_query_v1` adds redundant UTC-month partition predicates only to recognized
generated repair SELECTs with an existing literal half-open SIP time predicate
and source ordinal spans no larger than 250,000 events. Whole-session limits and
all original conditions remain intact. Large scans keep their prior physical
path because a live pilot found date filtering could regress that case. UTC
month/year boundaries, NY DST and exclusive ends are covered by focused checks.
This transport optimization does not change shared bar-generator source, sealed
selector code, logical query/cache bindings or completed repaired receipts.
Deployment requires native parity/profile evidence and graceful owned drain;
never relax the existing output-contract checks merely to resume a revision.

The repair uses one bounded query/payload worker pool, not separate I/O workers.
It accepts up to 32 threads. Each admitted thread can query, read/hash a payload
and publish its receipt; only payload I/O lowers its thread's Windows priority.
Training validation/startup/stale-status admission remains four lanes, dense
memory retries remain limited to two, and RAM/disk reserves are unchanged.
Increasing the pool requires a short live throughput/headroom check; completed
receipts and data/model contracts remain unchanged.

Repair transport also recognizes the exact ordered candidate prefix windows.
Their unique `(sip_timestamp_us, ordinal)` ordering lets `anyLastIf` return the
same eligible value as ascending `argMaxIf` / descending `argMinIf`, without
retaining and comparing another tuple per aggregate. The logical selector SQL
and its sealed hash remain intact; unknown window shapes are left unchanged.

A complete source-bound candidate-cache session with zero `source_changes`
proves that raw reconstruction has no changed condition/size messages that day.
The rebuilder can skip its redundant source-interval lookup for that session;
the final paired bar guard still runs. Cache binding, dictionary and exact frozen
source-row hash must match. Missing metadata/counts or any nonzero change takes
the original interval path. Candidate anomaly hints are never reinterpreted as
source rejection, and this shortcut never masks or invents a price.

A native worker dump found 14 of 16 repair threads waiting on the same ticker's
candidate-cache lock because receipt discovery order clustered one source job.
`quote_work_queue_v1.GroupedWork` now admits at most one receipt per job at once
and retains a bounded set of independent job cursors. This preserves all unit
identities, payload content and resume receipts; only repair publication order
changes. It avoids bypassing the cache coalescing/integrity lock to gain speed.

Independent lanes exposed a dense QQQ session: the query log measured 3.46M rows
and a 1-GiB per-query memory failure. The resource-only recovery retries the
IDENTICAL SELECT at 2/4/8 GiB, with at most two dense requests and a 64-GiB free
RAM reserve. Settings are worker-local and restored afterward; server-wide
limits, event coverage, predicates and selector hashes are unchanged. Failure
at the bounded maximum remains visible/resumable rather than dropping events.

## Opening-spread refinement (v3)

Bounded canonical probes confirmed additional positive-size, regular-condition
opening spikes: LARK 2020-05-08 ask 246.96 against bid 15 and AIRTW 2019-06-20
ask 1.21 against bid 0.05. Their closes recovered, and their trade envelopes were
wide enough to pass the v2 distance gate. V3 also applies the existing tenfold
spread limit to the paired OPEN. It uses no future neighbor to make that decision.
Canonical probes and per-message conditions are in the runtime residual-source
probe, not in source control.

The running v2 canonical reconstruction retains its original immutable snapshot.
`quote_guard_campaign_v1` waits for each complete v2 member, then applies only the
stricter bar guard into separate `-quote-v3` roots. This pass verifies payloads
and every bar, makes NO ClickHouse queries, retains all trades/clocks/decisions,
and hardlinks unchanged bytes. It preserves transitive receipt/contract ancestry.
The repaired supplement binds to the repaired v3 base, rather than its old base.
Only the v3 cohort is intended for the training handoff. This avoids discarding
or repeating the expensive canonical reconstruction already in progress.

The v3 pass is a full migration, not an expanded pretraining qualification.
Release audits remain bounded. Both v2 and v3 counts are retained separately;
do not sum their stored-bar counts as unique market observations. V3 masks
additional bad evidence; it does not certify that every possible source error
has been detected. Check remaining candidates before activating new readers.

## Earlier reconstruction and handoff contract

The user authorized repairing both immutable phase-one v6 members and the stopped
phase-two stream, fixing future producers, retaining active training until a
checkpoint-boundary handoff to the audited repaired cohort, and continuing this
work overnight. Do not restart the old phase-two producer or remove its STOP.

The shared v4 source policy uses the frozen canonical quote token dictionary,
strictly positive displayed sizes and structural two-sided validity. Massive
quote modifier 81 `Corrected_Price` is retained when its values are usable. It is
not the same namespace as trade corrections. Explicit non-firm, closed, cancel,
range/band/pause, unknown and one-sided indication conditions are ineligible.
Every actual invalidation still clears carry; no stale earlier book is revived.
Canonical quote slots 1–4 contain quote conditions; slot 5 is an indicator in a
different family. Token 221 is LULD “NBB NBO Executable”, not an unknown quote
condition. Manual quotes remain eligible absent another rejection condition.
The failed unreleased attempt is retained separately and must not enter training.
Documentation: https://massive.com/glossary/conditions-indicators

The final-bar policy additionally quarantines nonfinite/nonpositive fields,
inconsistent OHLC/counts, crossed closes and ten-fold trade-distance/spread
violations. Quarantine masks both quote families and never fabricates a price.
The threshold is a conservative model-quality policy, not a proof that every
wide market is wrong. Neighbor comparisons are offline diagnostics only; no
future bar determines a causal input. Corporate actions retain the existing
common-origin share-basis rule. There is no new quote-age limit.

`quote_repair_v1` examines every sealed payload, preserves source files and
trade/time/index bytes, caches canonical 100ms source-change intervals, rebuilds
only possibly affected quote bars and reuses raw quote results across overlapping
windows. Source queries are SELECT-only and return final aggregates. Unchanged
payloads use same-volume hardlinks; changed payloads are separate fsynced files.
Receipt publication is last. New roots are gated until repair-result.json is
complete. A pilot never publishes a complete release.

Example (native workstation, committed immutable source snapshot):

The current targets have the suffix `-quote-v2`; the stopped partial `-quote-v1`
attempt remains evidence only. CPAC 2019-04-09 had an ask high near $117.70 against
trades near $10: this demonstrated that the initial 100-fold guard was too loose.
The v2 ten-fold guard catches it causally. Neighbor diagnostics use quote/trade
ratios so genuine market price jumps are distinguished from dislocated quotes.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.bar_gpt.v5.quote_repair_v1 --source D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v6 --output D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v6-quote-v2 --source-revision <commit> --workers 8
```

The supplement and phase-two roots need separate sibling outputs. Preserve the
75/25 selection, frozen ordinal inventory, original sample IDs, every bar clock,
five phase-two/six phase-one frames and split definition. Protect the trainer's
64 GiB free-RAM and 150 GiB free-disk reserve. Profile a small real migration
before scaling. Do not restart full generation or hand off training merely
because synthetic tests passed. Verify real ClickHouse SQL and parity against
the shared producer for all frames, receipt/hash recovery, split-adjusted quote
fields, unchanged trades/decisions, and representative source error examples.

Progress: repair-progress.json; authoritative per-receipt counters: repair.sqlite
and quote_repair in each receipt. Counts of stored bar occurrences are distinct
from unique canonical interval/message counts in quote-repair-cache-v1.sqlite.
Reason categories may overlap. Preserve failed/retried/unfinished units.

Training handoff must be explicit and checkpoint-boundary only. The repaired
cohort, split manifest, training-only scaling evidence and checkpoint ancestry
must be bound together. Do not broadly bypass checkpoint compatibility. Retain
optimizer/scheduler/model state and original W&B train-01 identity, record the
data change, retain historical best evidence and establish validation evidence
on the repaired data before comparing future learning-guard results. Model and
hyperparameters remain unchanged. This handoff needs validation before activation.

`quote_handoff_v1.prepare_split(old_split, repaired_cohort, new_split)` prepares
only the metadata portion after both repaired members are released. It checks
each repaired receipt's original ancestry and unchanged sample geometry, retains
the exact ordered train/validation row ranges and original overlap purge, and
seals an independent membership hash. Old derived loader indices are discarded.
Its certificate explicitly says `prepared-not-activated`: calibration review,
checkpoint-state migration, corrected-data validation and the safe operational
handoff are still required. The helper does not signal or modify the trainer.

`rebind_calibration` preserves the original training-only activity median/scale
vectors so that the trained encoder continues seeing the same input units. It
records the original calibration/split/selection hashes and explicitly states
that these are historical frozen units, not refitted corrected-data statistics.
It requires the sealed exact-membership certificate. Repaired validity masks
still apply. This preparation does not migrate a checkpoint or activate a loader.

`prepare_checkpoint_state` is a pure preparation operation on a loaded completed
epoch state. It retains model/AdamW/scheduler/RNG objects, epoch and global step;
stores old best/history/probe evidence separately; and requests new-data
baselines. Its extra strict contract field deliberately makes it incompatible
with the existing trainer. It must not be activated until explicit trainer
transition validation and corrected-data baseline integration are implemented
and tested. No live checkpoint has been changed by adding this helper.

`data_transition_v1` now supplies strict certificate/actual split/calibration and
scientific-contract validation, plus baseline collection that restores CPU/CUDA,
Python and NumPy RNGs. It checks untrained model state geometry, rejects partial
or nonfinite baseline results, and detects changes to trained tensor bytes.
The campaign now validates this explicit transition before loading state and
collects corrected-data untrained/trained probes plus full validation before
the next optimizer update. It first saves a pending checkpoint so interrupted
baselines can be retried. A sealed baseline best has an explicit epoch even
before new-domain history exists; subsequent resumes retain it. Old-domain
history remains separate. CPU orchestration tests exercise the normal path,
transition, baseline completion interruption and another resume with empty
new-domain history. This is NOT activated in the live pinned trainer.
`checkpoint_transition_v1.prepare_copy` writes an independent checkpoint in a
new runtime directory, fsyncs it, reloads it on CPU and verifies exact nested
model/optimizer/scheduler/RNG/epoch/step state plus all prepared metadata. It
rehashes the parent to reject concurrent replacement and seals source/target
file hashes. Existing preparation directories are never overwritten. Rejected
partials remain incident evidence. This is a preparation API, not a live switch.

`prepare_reporting_identity` validates that copy and the actual target split and
calibration, permits only the three certified data-path signature changes, and
prepares the original remote ID/name/writer-lease root beside the copy. It keeps
the parent's identity unchanged. Normal Reporter signature and exclusive lease
checks remain intact; this helper neither contacts W&B nor starts a writer.
Operational boundary activation and an actual-state native preparation proof
remain required before any data switch. The live trainer stays pinned until the
complete repaired cohort and bounded release audits are ready.

The overnight controller accepts the explicit combination `--resume-training
<prepared checkpoint> --data-transition-proof <checkpoint-preparation.json>`.
The proof must accompany the copy; file/state hashes, original owned campaign
parent, actual loader inputs and reporting bridge must verify before launch.
The prepared manifest is marked as a preparation artifact, not a prior training
attempt. Attempt numbering follows the retained original parent; ordinary resume
ancestry and STOP checks remain unchanged. This entry does not drain a trainer:
only use it after the previous owned controller/child have exited at a complete
checkpoint boundary and after the repaired release audit has passed.
