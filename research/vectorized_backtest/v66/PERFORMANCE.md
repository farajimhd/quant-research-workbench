# Inherited reference

This document is retained from V64 for design provenance. Historical performance
and qualification statements below are not V66 results. See README.md for V66
input, initialization, and feature changes; workstation evidence lives in its runtime.

# Replay scheduling qualification

Performance settings do not change genomes, the objective, market inputs, price
programs, or execution accounting. The default remains two workers, 256 clocks,
and synchronous transfers. Optional `replay_tuning` accepts `time_block` of
128, 256, 512, or 1024 and `transfer_mode` of `synchronous` or `events`.
The workstation worker metadata must agree with `global_session_workers` (2, 4, 6, or 8).

Event transfers use a dedicated CUDA copy stream and two pinned host buffers.
A buffer is reused only after its ordered CPU execution completes. One execution
worker serializes position accounting; a separate loader prefetches existing
liquidation pages. GPU outputs remain alive until their transfer finishes.
The pipeline applies backpressure at two outstanding packets and drains on exit.

In event mode, `inference_seconds` measures host enqueue time, not GPU latency;
`inference_gpu_seconds` measures CUDA event elapsed time. Transfer event timing
includes waiting to obtain the copy stream. These overlapping components must
not be summed or compared directly with synchronous inference wall time.
Use `total_replay_seconds` and end-to-end multi-session throughput to select a
configuration. The actual block size is recorded and may be reduced by memory
admission. Spare GPU capacity alone does not establish a throughput improvement.

`python -B -m research.vectorized_backtest.v64.profile_throughput` runs four full
training sessions from the first 1,024 genomes in a hash-validated checkpoint,
in an isolated runtime. It includes loading and deferred result publication in
its measured total, and compares every metric plus each ordered execution journal
hash and order count against a reference. Paths are excluded from order equality.
This is a bounded throughput test, not a full campaign or a qualification of all
6,144 founders. It never changes the saved campaign or starts its optimizer.

Changing scheduling on a saved campaign still requires its explicit, pinned
implementation transition. A successful benchmark alone does not resume it.
