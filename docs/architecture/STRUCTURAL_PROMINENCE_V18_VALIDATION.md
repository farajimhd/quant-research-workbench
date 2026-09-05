# Score-independent structural book: v18 validation

Status: opt-in validation, not the default service algorithm. The running v17
service and immutable v16 campaign retain their original semantics. Do not
enable v18 for production or relabel existing books without a separate rollout.

## Construction contract

Historical and current-session events use the same GenericStructureEngine in
causal order. The selected chart timeframe does not control structure creation.
The engine observes its fixed timeframe family throughout processing.

A prominent candidate requires either:

- A confirmed, unbroken two-sided pivot on a fixed engine timeframe of one
  second or longer. Confirmation requires the right-hand neighborhood; a
  forming candle cannot provide that confirmation.
- At least two independent event-native pivots in the same bounded price area.
  Repeated projections of the same pivot are not independent evidence.

Subsecond timeframe swings can corroborate a candidate but cannot found a
standalone level. Native reversal detection retains the existing adaptive
trade-movement/spread geometry. The prominence rules are structural conditions,
not thresholds on confidence, hold probability, quality, or percentile scores.

Construction, candidate selection, ordering, geometry, source retention and
track retention do not rank or filter by those scores. Scores are still
calculated from evidence for downstream consumers. No score is fabricated to
make a new level eligible. The strategy's existing 20% ticker-relative gate is
unchanged; its policy for newly created levels is deferred by the user.

The former 32-candidate, 16-output, 256-track, 512-raw-level and 16-source
evictions do not apply to the v18 authoritative book. Only terminal retired
episodes are removed from active state. Historical levels are not displaced
merely because new levels arrive or because they are far from current price.
Lifecycle changes still determine current support/resistance roles.

Clustering bounds the entire candidate group's price span; adjacent links may
not chain indefinitely. Existing zones merge only when each anchor lies within
the other's observed zone, with numerical tolerance only. Identity and geometry
remain fixed for a retained role episode. Source pivots and their confirmation
times remain available for the construction audit.

## Isolated validation

Build the shared engine and read-only probe with `structural-prominence-v18`.
This feature cannot be combined with `historical-campaign-v16`. It reports
algorithm version 18; checkpoints from v16/v17 are not compatible seeds.

The `structure_prominence_probe` binary takes ticker, UTC start, UTC end,
absolute runtime output path, and optional UTC snapshot times. It reads canonical
ClickHouse events through HistoricalEventSource, applies canonical trade rules
and splits, starts from fresh state and never writes production checkpoints or
submits strategy orders. Output is confined to `D:\TradingML\runtimes`.

The probe records source revision, actual event count, elapsed processing time,
serialized checkpoint size, causal snapshots and source-to-level construction
decisions. It compares uninterrupted processing against JSON checkpoint restore
followed by the same remaining events. Resource limits are two million events,
45 calendar days, fifteen processing minutes and a 256 MiB serialized
checkpoint. Exceeding a limit fails the probe instead of silently dropping data.

Initial targeted windows cover JUNS through 2026-08-21 07:17:40 ET and SUGP
through 04:12:00 ET, with fresh warm-up from August 17. They are not a complete
multi-month production reconstruction or a strategy profitability backtest.

## Remaining production gate

Do not infer unlimited capacity from removing correctness-breaking eviction.
Measure longer-history CPU and memory before rollout; the present algorithm
still traverses the retained book for lifecycle updates and reclusters on
structural events. It has not yet been replaced with a price-indexed incremental
implementation. The validation runner bounds resource use explicitly.

Promoting v18 requires validating that longer-history resource behavior,
reviewing prominent-level output, planning versioned book regeneration with
sufficient historical warm-up, and deciding the deferred strategy quality gate.
Preserve old book and backtest provenance rather than overwriting it.

## September 5 bounded validation results

Final probes start at 2026-08-17 04:00 ET and use fresh construction, without
seeding old versioned books. Elapsed times include processing a second engine
after a serialized checkpoint restore, plus snapshot/audit generation; they
are local measurements rather than a throughput guarantee.

| Ticker | End ET on August 21 | Events processed | Elapsed | Checkpoint bytes | Resume parity |
| --- | --- | ---: | ---: | ---: | --- |
| JUNS | 07:17:40 | 30,199 | 0.454 s | 543,307 | Exact |
| SUGP | 04:12:00 | 195,683 | 23.123 s | 4,083,141 | Exact |

JUNS retains 23 pre-session levels and has 78 total at 07:16:11 ET. Current-day
zones around the marked areas are present, including 6.79, 6.86/6.88, 6.50 and a
zone covering 6.39. Roles and lifecycle states change causally; these are not
all still active resistance at that cursor. SUGP publishes resistance near
3.52, 3.5494 and 3.60 at 04:10:23 while retaining prior-session evidence.

Detailed snapshots, source-to-level audits and source revisions are in
`D:\TradingML\runtimes\structure-validation\prominence-v18\JUNS-final.json`
and `SUGP-final.json` in the same directory. No production checkpoint writes,
service restart, strategy execution, or full-session trading backtest occurred.

## Extended validation and campaign preparation

The probe now uses the production `structure_checkpoint_json` decoder and
compares the full checkpoint state, not just published snapshots. An earlier
diagnostic mismatch came from the probe bypassing that exact decoder; global
serde float behavior was not changed, preserving existing certificate semantics.

| Ticker | Fresh window (UTC) | Events | Elapsed | Checkpoint bytes | Full resume parity |
| --- | --- | ---: | ---: | ---: | --- |
| SUGP | Aug 17 08:00 to Aug 22 00:00 | 777,920 | 241.892 s | 14,121,046 | Exact |
| JUNS | Jul 20 08:00 to Aug 21 11:18 | 777,509 | 476.210 s | 22,545,692 | Exact |

SUGP's earlier full-state probe took 524.390 seconds on the same source
revision. Its final snapshots and construction audit match the optimized
probe exactly. The improvement comes from indexing retained source identities
during merges and avoiding rebuilding unchanged source aggregates on every
trade. Lifecycle updates and consolidation still run with their original
ordering; a differential test covers repeated crossings and role flips.

Reports are `SUGP-full-optimized.json` and `JUNS-month-optimized.json` under
the validation runtime directory above. The campaign preparation and 96-worker
commands are documented in
[STRUCTURAL_CHECKPOINT_CAMPAIGN_V18.md](STRUCTURAL_CHECKPOINT_CAMPAIGN_V18.md).
