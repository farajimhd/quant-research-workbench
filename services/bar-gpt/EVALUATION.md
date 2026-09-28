# Serving capacity and August evaluation

Run `run_evaluation.py` with a Python environment containing the service/research
dependencies. The launcher disables bytecode writes. All generated output must
be under `D:\TradingML\runtimes` on the executing machine. No command changes
production releases or operational intent, trains weights, or writes market data.

## Phases and evidence boundaries

1. `prepare` runs on CPU. It uses the existing `HistoricalBootstrap`, checks
   source/ticker-day evidence and unchanged source revisions, and writes immutable
   ticker-session SQLite packets. Packets include historical context, future bars,
   native physical targets and validity masks. Future rows stay on disk until their
   availability clock. Preparation processes one ticker/session at a time and resumes
   verified completed packets. It does not certify a new source-day import.
2. `replay` loads one explicitly hash-pinned v3 release through the real service
   loader. It uses the service cache, forward pass, decoder and prediction journal.
   Application publication is deliberately disabled for isolated evaluation.
   Every requested ticker/model/origin/hash must match exactly. Completed origins
   commit atomically; unpaced replay resumes without duplicate results. Dataset,
   source code, model, device/precision and run settings are bound to manifests.
3. `benchmark` sweeps explicit symbol counts and batch sizes. `--paced` schedules
   origins against a fixed one-second market clock, rather than sleeping after each
   inference. Queue-equivalent completion lag accumulates when processing is slow;
   exceeding the configured backlog stops the trial. Paced trials never resume into
   a misleading stitched latency distribution. Warmup is reported separately.
4. `score` joins saved predictions with native targets after inference. It reports
   physical OHLC return error, no-change skill, a causal 60-second log-momentum
   baseline for trade close, direction, pinball loss, interval coverage/width and
   crossing counts. Momentum/model comparisons use identical support. Scorecards
   include pooled, per-day and per-symbol rows; paired whole-day bootstrap intervals
   require at least five days. Never treat overlapping seconds as independent trials.
5. `live-observe` reads the actual service's prediction WebSocket and health before/
   after the observation. It never launches GPU work or changes scopes. This checks
   actual live/paper publication delay, duplicates and health counters. Missing
   eligible origins still need independent market-origin reconciliation: silence
   cannot distinguish an inactive symbol from lost predictions.

**No benchmark automatically grants real-time certification.** Paced replay excludes
live compact-event aggregation, HTTP transport and backend delivery. Its stage
timing synchronizes CUDA for clear preparation/forward/decode attribution and can
therefore differ from uninstrumented production timing. Full certification also
requires a sustained actual live-path test at the intended population and cadence,
with complete eligible-origin coverage, stable queue depth, no drops/stale outputs,
and accepted end-to-end latency with headroom. A 500-symbol scope limit is not a
measured 500-symbol-per-second capacity.

The scorer intentionally covers physical OHLC return forecasts. AR next-event,
volume/count/volatility, availability and condition heads are not scored by this
report. Missing condition-support data is never converted into negative labels.
Target coverage ends conservatively at observed one-second support/session end.
These reports do not establish trading profitability or approve a production release.

## Complete commands

Use an evaluation-only `releases.json` array with `model_id`, `version: "v3"`,
`checkpoint`, `checkpoint_sha256`, and `contract_hash`. Copy identities from verified
immutable artifacts; never use a mutable latest checkpoint. Paths are host-specific.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$py = 'C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe'
$root = 'D:\TradingML\runtimes\bar_gpt_evaluation\aug2026'
$runner = 'services\bar-gpt\run_evaluation.py'

# CPU only. Pilot population is fixed before examining model performance.
& $py -B $runner prepare --releases "$root\releases.json" --model-id v3_5b `
  --tickers AAPL,MSFT,NVDA --days 2026-08-03,2026-08-04 `
  --start 09:30:00 --end 09:40:00 --output "$root\pilot_data"

# Fails closed when training or unidentified GPU work is present.
& $py -B $runner gpu-status

# Real GPU inference starts only after the guard passes inside replay as well.
& $py -B $runner replay --releases "$root\releases.json" --model-id v3_5b `
  --dataset "$root\pilot_data" --output "$root\pilot_v3_5b" `
  --device cuda --batch-size 4 --steps 120 --parity
& $py -B $runner score --dataset "$root\pilot_data" --run "$root\pilot_v3_5b" --allow-partial

# Full replay: same source packets for each compatible checkpoint, separate output.
& $py -B $runner replay --releases "$root\releases.json" --model-id v3_5b `
  --dataset "$root\pilot_data" --output "$root\full_v3_5b" --device cuda --batch-size 8 --steps 0
& $py -B $runner score --dataset "$root\pilot_data" --run "$root\full_v3_5b"

# Bounded paced pilot matrix. Prepare the actual larger population before using 500.
& $py -B $runner benchmark --releases "$root\releases.json" --model-id v3_5b `
  --dataset "$root\pilot_data" --output "$root\capacity_pilot" --device cuda `
  --symbol-counts 1,3 --batch-sizes 1,4 --steps 120 --paced --parity

# Read-only observation of a separately managed actual live service.
& $py -B $runner live-observe --url http://127.0.0.1:8805 --model-id v3_5b `
  --tickers AAPL,MSFT,NVDA --seconds 900 --output "$root\live_observation"
```

Freeze a point-in-time eligible broader population before scaling, including liquid
and sparse symbols; do not select August winners or silently remove failed warms.
Default replay is a bounded 120-origin-clock pilot, not a complete session. A full
500-symbol day may produce millions of predictions plus large raw journals; measure
pilot bytes, throughput and warm cost before choosing a campaign size.

## Sealed acceptance

Use August 3–14 for development and August 17–31 for acceptance only after checking
prior experiment exposure. Preparing acceptance packets is not permission to inspect
their outcomes. `prepare --partition acceptance` labels the partition. Acceptance
replay requires `--selection <path>` with `checkpoint_hash` and
`development_evidence_hash`; freeze this before opening the period. Acceptance
scoring rejects `--allow-partial`. No automatic candidate selection uses acceptance
scores. Freeze thresholds, population, dates and intended use before that run.

## Verification

`--parity` compares single/batched service predictions and checks service features,
masks and target clocks against native training collation on the same raw support.
It does not claim independent extraction parity or full live-event parity.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = "$PWD\services\bar-gpt\src;$PWD"
& $py -B -m unittest discover -s services\bar-gpt\tests -v
```

Tests exercise real CPU checkpoint loading, inference, decoding, journaling, restart,
native target equality, future-data exclusion, reconciliation failures, immutable
manifests, GPU contention refusal and scoring. Tiny synthetic test weights and bars
are implementation evidence only, never market-quality or capacity evidence.
