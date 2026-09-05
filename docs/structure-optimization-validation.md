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


## Consumer rollout (September 5, 2026)

Normal QMD Live and QMD History builds now enable `structural-prominence-v18`
by default. The normal server entrypoints reject a legacy algorithm build.
The immutable algorithm-16 campaign remains available only with
`--no-default-features --features historical-campaign-v16`; it must never be
used to serve new charts or backtests.

The shared historical seed default is
`canonical-tradable-20250101-20260904-prominence-v18-v1`, declared in
`services/qmd-gateway/src/config.rs`. QMD History uses this set for both chart
preparation and causal strategy session seeds. Charts and structural indicators
project the QMD book; there is no separate frontend checkpoint selector.
History's derived-cache revision is now `qmd-derived-v58`, invalidating old
prepared chart/indicator artifacts. Existing source fingerprints also invalidate
new-run execution caches. Saved run results retain their original provenance.

Live retains its own operational `live` write namespace. If no compatible live
daily seed exists, it reads the new campaign through
`QMD_STRUCTURE_SEED_CHECKPOINT_SET_ID`, validates identity and certification,
and advances the shared algorithm-18 engine. It does not write into the campaign
namespace. `QMD_STRUCTURE_CHECKPOINT_SET_ID` is still an explicit History/write
namespace override; do not set it to the campaign on Live merely to change its
seed. Both variables participate in managed-service drift detection.

Backtest initialization now checks the compiled algorithm and effective
checkpoint set in addition to source hashes. Identical source built with old
Cargo features is not accepted. The History launcher checks algorithm 18,
calculation revision 58, checkpoint table and set before reusing a process.

Coverage is separate from code readiness: at inspection, the new set had 3,687
completed rows across 95 tickers; JUNS had certified rows through November 13,
2025 and SUGP had none. The set is still being built. Missing seeds retain the
existing bounded canonical reconstruction path and its explicit readiness
failures, without selecting an older campaign. An older available seed can
require substantial causal advancement. Both checkpoint tables use
`live_market_ssd`, and observed active checkpoint parts were on that disk.

The running History service was still algorithm 17/revision 57. An active
backtest (`9aaa9307-a555-4563-b110-005eb320a762`) prevented an in-place rollout;
its executors were deliberately left intact. After that run ends, use the
managed service restart for `qmd-history` and `backend`, then verify History
`/health` reports algorithm 18, revision 58 and the September 4 set. QMD Live
was not running at inspection; its next normal build uses algorithm 18.
Do not interpret a successful restart as complete JUNS/SUGP historical coverage.


Validation: 245 shared QMD library tests, 104 History library tests, and 25
Python provenance/lifecycle checks passed. Live and the immutable v16 campaign
compiled. The algorithm-18 History release was tested on isolated port 18801:
health reported revision 58 and the new set, and the real backend runtime gate
returned ready. That test exposed and fixed Rust/Python source-hash ordering
for nested modules; both now hash normalized relative paths in the same order.
The isolated process was stopped. No new backtest or rendered-chart acceptance
was performed. Evidence is in
`D:\TradingML\runtimes\structure-validation\consumer-alignment\health-smoke.json`;
the staged binary is in
`D:\TradingML\runtimes\qmd_history_gateway\consumer-v18-target\release`.
