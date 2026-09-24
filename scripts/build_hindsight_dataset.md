# Multi-day hindsight dataset: Phase 1 and Phase 2

For the new persisted `arte` source, use
[build_hindsight_arte_dataset.py](build_hindsight_arte_dataset.md). It runs both
phases without QMD or event reads, uses versioned completed-100 ms targets, and
retains certified build/population identities. The workflow below remains the
legacy event-exact dataset for reproducibility.

Use `build_hindsight_dataset.py` for the full workflow. It runs the certified
Phase 1 extraction followed by the fractional, discounted greedy Phase 2 for
each requested trading date. It does not change label semantics, train a model,
run a strategy backtest, or add full-path dynamic programming.

## Workstation commands

From an activated `ml4t` environment in the synchronized checkout:

```powershell
./scripts/services.ps1 start qmd-history
python -B scripts/build_hindsight_dataset.py preflight --workers 4
python -B scripts/build_hindsight_dataset.py benchmark --date 2026-08-21 --tickers AAPL SUGP AEG AEHL --workers 4
python -B scripts/build_hindsight_dataset.py run --date 2026-08-21 --workers 4
```

The managed service must report ready before the campaign can run. Its first
startup may need the existing Rust toolchain/offline Cargo cache. The campaign
uses the authoritative configured ClickHouse connection and QMD History endpoint;
it does not start services itself or silently choose another server. Workstation
secrets remain outside the checkout. If an existing remote QMD History authority
is intended, pass `--qmd-url http://<host>:8801` explicitly instead of starting a
new one. Preflight checks service readiness, fingerprint, ClickHouse connectivity,
runtime-root availability and local CPU/RAM admission.

For a larger dataset, set an inclusive range of **published, completed** sessions:

```powershell
python -B scripts/build_hindsight_dataset.py run --start 2026-08-21 --end 2026-08-21 --workers 4
```

Replace the dates with your desired range. XNYS calendar weekends/holidays are
excluded explicitly. Every trading date must have a dated tradable universe;
missing dates fail preflight rather than borrowing another day's population.
Every listing must have certified canonical source and complete MACD coverage.
On the September 21 validation, Aug 20 lacked a published universe, so the
Aug 20–21 campaign was correctly rejected before extraction.

`--day-workers 2 --workers 8` allows two dates concurrently, with four listing
workers each, if the machine's admission check permits it. `--workers` is the
**total** listing budget, not workers per date. Defaults admit up to four workers,
one active date, two Polars threads, two ClickHouse threads per listing query,
and two concurrent hourly MACD readers per listing. Available RAM limits worker
admission conservatively; the external QMD/ClickHouse services also need headroom.
Increase workers only after inspecting benchmark throughput and service load.

## Changes that improve throughput

- Phase 1 quotes transfer as `CSVWithNames`, parsed natively by Polars, instead
  of decoding tens of thousands of JSON objects. Quote checkpoints are Zstandard
  Parquet. Exact target microseconds, invalid latest quotes, and query pruning
  are unchanged.
- Phase 1 timing is persisted for coverage, MACD, extrema, quotes and final output.
  Checksums stream in bounded blocks. Progress no longer rewrites the growing
  full listing-results array on every heartbeat.
- Certified hourly MACD pages remain the default. Larger pages (2/4/8 hours)
  are optional for benchmarking with `--macd-window-hours`; they were **slower**
  on the measured AAPL/SUGP paths and are not promoted as an optimization.
- Phase 2 listing compilation runs concurrently with a bounded queue. Each
  already-ordered listing's three summaries use direct columns and pairwise
  comparison rather than repeated hash grouping and joins. Market reduction
  remains deterministic, including ties and unavailable labels.
- Phase 2 retains the same O(N*T) expected work and bounded per-worker listing
  frames, plus O(T) market summaries. It does not materialize all portfolio states
  or fractional action combinations. Arbitrary invested-state actions still use
  the exact coefficient evaluator.

## Versioning, output and resume

Campaign directories are under local `D:\TradingML\runtimes\hindsight-campaigns`
on the workstation. The launcher deliberately uses the local disk alias instead
of writing through its own SMB share. A configured `QW_RUNTIME_ROOT` is honored;
an unavailable root fails instead of falling back.

Campaign identity includes source-code hashes, requested dates, population hashes,
QMD fingerprint and label parameters. Worker counts do not change numerical
identity. Phase 1 is reusable across changes to Phase 2's discount/cost parameters.
A changed source fingerprint produces a new campaign; old immutable artifacts are
not overwritten. Standalone Phase 1 now names the specific mismatched plan fields.

Each day records:

- Phase 1 and Phase 2 output locations.
- Separate logs and copied phase summaries, including listing failures.
- Phase durations including Python startup and sampled child-process peak RSS.
- Hashed, immutable per-listing outputs reused by subsequent invocations.

The campaign's `summary.json` is the benchmark report. Per-stage times appear in
`days/<date>/phase1.json`; Phase 2 compilation times in `phase2.json`. Memory figures
cover each Python child, **not** external QMD/ClickHouse service memory. Warm caches
and restored intermediate files must be distinguished from cold preparation.

Rerun the same command to resume with integrity verification. A benchmark has
explicit canary scope and can never publish full-market completion. `complete.json`
is published only after both phases complete for every requested day. Failures
are retained, never silently skipped. Automatic retries are disabled; rerunning
retries failed listings while reusing verified stages. Exit 2 means incomplete.

Ctrl+C once or a campaign `STOP` file stops scheduling dates and propagates STOP
to phase workers. Active listing work drains before processes exit. To resume,
remove the campaign STOP **and the STOP files in the phase output directories
listed in each day's result**, then rerun. No child process is intentionally left
running. Monitor `progress.json` for current phase, completed/reused/failed counts,
active dates and queued dates; detailed logs stay under the runtime root.

## Validation limits

The optimization tests cover output parity, null/negative/tied scores, exact MACD
page boundaries, columnar quote parity, bounded concurrency, failures and STOP.
The real four-listing canary validates both runnable phases, not all 6,545 listings
or multi-day source coverage. Full-market time and disk requirements remain to be
measured on the workstation; no linear runtime promise follows from a warm canary.
