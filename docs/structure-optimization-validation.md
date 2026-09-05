# Structural campaign optimization and comparison

The algorithm-18 campaign uses a transient price index for unified-track
consolidation and candidate matching. It preserves the exact existing geometry
predicate, source aggregation, floating-point calculations and ordering. A
consolidation selects the first eligible track in the original sorted vector;
candidate reinforcement selects the nearest eligible track, breaking equal
distances by original vector position. New episodes enter the index immediately.
Merging cannot invalidate the index because an episode's price and geometry are
fixed. Lifecycle and side predicates are evaluated against current state.

The index query expands by the candidate tick's numerical epsilon, which is a
superset of the predicate's minimum-of-two-ticks epsilon. The original predicate
still decides every match. Non-finite/reversed query bounds use a complete scan.
Legacy algorithms retain their linear search. No score gates, level caps, event
sampling or changed checkpoint schema are introduced.

After a consolidation pass makes no merges, lifecycle updates may reuse that
fact while side, pending side and visibility remain unchanged. A pass that did
merge tracks must run again on the next eligible event; it is not assumed to
have reached a fixed point. Candidate refresh always consolidates. Snapshot or
checkpoint restore invalidates the fact, as does a split through checkpoint
reseeding. This transient flag is excluded from serialized checkpoints.

## Laptop correctness procedure

Build `structure_optimization_probe` with `structural-prominence-v18` against the
original engine first and retain that executable outside the repository. Build
the same probe against the optimized engine. The probe reconstructs complete
days directly from certified `market_sip_compact` ordinal ranges using the
campaign's daily split schedule. It never imports production checkpoint payloads
or writes database checkpoints. Each day persists a new local checkpoint and
verifies its exact JSON restore hash before continuing into the next day.

Run the original before the candidate, using the existing secret environment
configuration; never place credentials in commands or reports:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python scripts/validate_structure_optimization.py --binary D:\TradingML\runtimes\structure-validation\optimization-20260905\baseline.exe --output D:\TradingML\runtimes\structure-validation\optimization-20260905\baseline
python scripts/validate_structure_optimization.py --binary D:\TradingML\runtimes\structure-validation\optimization-20260905\candidate.exe --output D:\TradingML\runtimes\structure-validation\optimization-20260905\candidate --baseline D:\TradingML\runtimes\structure-validation\optimization-20260905\baseline
```

Those output directories must be new. Defaults are JUNS, SUGP and SDOT, August
19–21, 2026, inclusive. Each case is bounded to seven calendar days, two million
events, a 256 MiB checkpoint and 15 minutes. The launcher enforces a 16-minute
external deadline and terminates only its own child on failure or interruption.
No partial result qualifies as a completed comparison.

Exact comparison includes the input-event digest, emitted-event digest, full
checkpoint hash every 5,000 events, each daily checkpoint hash, published daily
book, state counts, source revision and event counts. Timing is excluded from
parity. `apply_seconds` measures engine calls; end-to-end elapsed time also
includes network, evidence hashing and checkpoint serialization. Repeat timing
without concurrent builds before interpreting speed ratios. Fresh three-day
books are not a certification of every multi-month production history.

## September 5 laptop results

Original engine: commit `92315b8c`, retained before optimization. All three
tickers completed August 19–21, 2026: 2,025,248 events, nine daily checkpoints
and 403 intermediate checkpoint hashes. Both optimization stages and a repeated
original run matched exactly, including event digests, published books and
checkpoint restore state. No production checkpoints were used or written.

Sequential runs without concurrent builds measured the following engine-call
times (network and validation work excluded):

| Ticker | Events | Original repeat | Optimized | Speedup |
| --- | ---: | ---: | ---: | ---: |
| JUNS | 812,169 | 85.515 s | 80.838 s | 1.058x |
| SUGP | 759,413 | 99.245 s | 99.020 s | 1.002x |
| SDOT | 453,666 | 12.368 s | 7.029 s | 1.760x |

SUGP's difference is effectively noise. These results do not substantiate a
large universal speedup or a new full-campaign ETA. The last-day books contained
274, 172 and 558 unified tracks respectively; the older workstation bottleneck
involved more than 2,000 tracks. Full-history throughput remains to be measured.
The unchanged source-construction and evidence-aggregation work can still
dominate. Detailed artifacts are under
`D:\TradingML\runtimes\structure-validation\optimization-20260905` on the laptop.

## Full campaign

`scripts/start_structure_checkpoint_full_campaign.ps1` covers January 1, 2025
through **September 4, 2026**, inclusive, with 96 workers and the existing priority
ranking. It uses a new immutable checkpoint set and retains previous sets. It
accepts `-Binary`, `-SourceCommit` and `-NoBuild` for a validated workstation
deployment without a Git checkout. Use the deployment's hash-checking wrapper.
Validation and synchronization do not start the full campaign.
