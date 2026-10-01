# Full-session performance profile

The measured Aug 18 premarket replay takes **0.979 s** once the tape and runner
are resident. A new process using the verified projection cache reaches its
first result in **17.35 s**. A fresh upstream-bank verification remains expensive:
the optimized preparation plus first replay took **164.63 s** in the measured
cache-creation run. These are distinct operating modes.

## Experiment contract

- Date: 2026-08-18, 04:00–09:30 America/New_York, the complete 5.5-hour premarket.
- Released Early Squeeze funnel: 6,100 listings → 2,289 price candidates → 882
  persistent watchlist listings, with causal admission timestamps.
- V7 expression policy, 12 completed source observations; one candidate.
- Strategy and broker resolutions: 1 s each; 19,800 decisions and intervals,
  plus the initial boundary, giving 19,801 replay ticks.
- 348,066 broker rows; five projected market fields; 1.567 GiB resident tape.
- RTX 5090 Laptop GPU; Torch 2.12.0+cu130; compiled CUDA graph backend.
- No neural inference or PPO training is included.

The initial run used a CPU cProfile trace and the complete Polars oracle. Later
runs compare the entire account trajectory and final state with that validated
baseline. Monetary tolerance is 1e-7 and integer state is exact. The measured
optimized runs have **zero account difference** and matching final state.

## Timings and cache state

| Stage, seconds | Original profile | Optimized cache creation | Optimized cache reuse |
|---|---:|---:|---:|
| Full V6 current/prior byte verification | 160.043 | 147.236 | Reuse verified derivative |
| Shared market preparation | 38.539 | 4.983 | 3.546 |
| V7 projection / cached-copy read and hash | 12.881 | 2.133 | 0.080 |
| Total session preparation, including certification | 211.486 | 154.822 | 9.108 |
| Tensor alignment | 5.741 | 5.412 | 3.854 |
| GPU transfer | 0.167 | 0.178 | 0.166 |
| Torch compilation | 14.161 | 2.839 | 2.981 |
| Graph capture | 0.009 | 0.014 | 0.015 |
| Resident replay, median of three | 1.020 | 0.976 | 0.979 |
| Preparation through first result | approximately 232.57 | 164.63 | 17.35 |

The original market cache missed; the later market cache hit. Compiler caches
were warm in later runs. cProfile also adds overhead. Thus the entire difference
cannot be attributed to code changes alone. Verification and market work overlap
in the optimized miss path, so their individual times must not be summed.

First-result timing includes strategy compilation, preparation, alignment,
transfer, backend preparation, replay, result cloning and synchronization. It
excludes module imports, argument/environment setup, host export, oracle checks
and diagnostic tracing. The original Polars oracle took 190.04 s under cProfile;
that validation cost is separate from the trading objective.

## Changes supported by the profile

1. **Retain the verified V7 projection.** Original buffered reads consumed about
   142 s of CPU-profile time, largely reading full banks across the workstation
   share. After the original full certificate checks pass, the adapter retains
   a 6,110,238-byte projected copy locally. Each reuse hashes the copy, binds its
   market source and checks producer seal bytes. It consumes the original
   verified derivative even if upstream arrays later change without new seals;
   it does not claim a fresh audit of those arrays. `--reaudit-v7` rechecks the
   upstream banks. Missing roots/seals and corrupt local copies fail closed.
2. **Overlap independent reads.** Two bounded workers verify V6 banks and prepare
   the shared market inputs concurrently. Both must pass before projection or
   device work starts.
3. **Validate the consumed slice once.** The original path validated 882 complete
   listing banks twice. Full file certification remains on misses, while the
   adapter validates just the consumed certified candle slices once and
   partitions market prices once. Projection fell from 12.88 s to 2.13 s.
4. **Share certification in-process.** A checked source receipt passes the pinned
   manifest/ledger result from the V7 wrapper to the shared market loader.
   Paths, day, authority token and current manifest bytes must match; storage,
   population and liquidity checks remain. There is no persistent unchecked
   ledger certificate shortcut.
5. **Reuse identical source-index scans.** Adjacent columns share one alignment
   scan only when their entire nonnull update bitmap and carry policy agree.
   Explicit NaN updates remain distinct from absent updates. Only one scan grid
   is retained, and it is released before transfer. Alignment fell to 3.85 s.
6. **Capture multiple sequential ticks.** `--graph-steps 32` reduces submissions
   from 19,801 to 619 and uses an exact tail graph. State still advances causally.
   Replay remains near one second, so launch reduction is not a 32-fold speedup.

## Remaining bottlenecks and the next useful optimizations

A second whole-process cProfile run with cached projection and graph size 1
found source certification at 5.37 s, including about 4.19 s in two SQLite
`fetchall` calls. Serialization and cache-key/provenance work also remain visible
under cProfile. Database projection was under one second of socket receive time.
The source-unit checks must remain authoritative; any further optimization should
measure and improve those queries, rather than silently skipping certification.

A bounded GPU profile preserved the causal prefix through slot 9,888 and sampled
32 subsequent ticks. It measured **320 kernels**, totaling **1,527.37 µs**:
approximately 10 kernels and 47.7 µs of device execution per tick. Large kernels
perform policy/source-window and fill/account reductions. Peak allocated device
memory was 1.570 GiB. GPU work, rather than host graph submissions, explains most
of the approximately one-second resident replay.

For strategy search, the highest-priority next step is to **keep the prepared
tape and runner alive and vary Theta in place**, amortizing the 17.35 s setup.
Then measure candidate batches along the existing `[B,N]` axis; multi-candidate
throughput has not been benchmarked here. Reducing device latency further would
require fusing the remaining kernels or reducing repeated source-window gathers,
with the same full-trajectory parity checks. The 100 ms full-session tape still
needs an explicit bounded-memory design; these results apply to 1 s / 1 s only.

## Reproduce and inspect

Use the README's real-session command and certified V6 paths, selecting
`--example v7 --lookback 12 --graph-steps 32`. Add `--compare-polars` for an
independent oracle, or `--baseline-report` with the baseline below for the saved
oracle-validated full comparison. Add `--profile-kernels` for a bounded trace.
Use `--reaudit-v7` for fresh upstream verification.

All artifacts are under
`D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v1`:

| Run directory | Evidence |
|---|---|
| `1a04c6361e924107be5183a661747bf5` | Original full session, complete Polars oracle |
| `931fa8c0dba9439ab8d46b5f78e902b3` | Full verification and projected-cache creation |
| `330424bef86b4b0e9d91d3451577d921` | Warm cache, CUDA kernel trace and grouped kernels |
| `4630dafb273547a8ab8c6e945cc9947d` | Final optimized cache-reuse timing and exact parity |
| `ebdb5b1ccbe143b08efdbeffc7e4cad7` | Warm whole-process cProfile run, graph size 1 |

Each directory contains its immutable `report.json`, accounts and final state.
CPU profiles are `v7-full-baseline.pstats` and `v7-full-optimized.pstats` under
`D:/TradingML/runtimes/vectorized_backtest`. Runtime artifacts are not committed.
