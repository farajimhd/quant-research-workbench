# Quote evidence repair

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
