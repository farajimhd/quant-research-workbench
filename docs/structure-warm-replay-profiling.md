# Bounded warm structural replay

`scripts/profile_warm_structure_replay.py` runs on DESKTOP-SAAI85T. It accepts
`--binary` for the committed `structure_checkpoint_replay_probe.exe` and
`--source-commit` for a deployed source mirror. Activate ml4t first.

The two defaults reproduce continuations for SDOT on 2026-05-05 and ASST on
2025-05-07. Each loads the last prior certified algorithm-18 checkpoint from the
stopped v18-v3 campaign, validates its payload and certification chain, and requires
the immediately preceding day. It uses the same pinned daily ordinal range and
split schedule as the campaign. No checkpoints are copied to the laptop.
Checkpoint table policy and actual part placement are validated. The executable
never calls schema initialization, checkpoint persistence, or campaign restart.
Canonical imported SIP events remain the only replay input.

Each probe processes at most 20,000 events or 180 seconds, with a separate
15-second exit allowance. Source loading and validation count toward that time
budget. A stop file is checked between individual events and while waiting for
batches. Ctrl+C requests cooperative shutdown. If one function or I/O operation
does not return, the launcher terminates only its own probe process. Production
workers are never terminated by this launcher; active production workers block
startup. A process lock prevents duplicate warm profiling launchers.

A separate heartbeat thread publishes the current function and completed event
count every two seconds, even while the calculation thread is busy. The launcher
records process CPU seconds and resident memory, enforces a 4 GiB resident-memory
limit, and preserves last-phase diagnostics after a forced stop. It stops the
sequence after the first failure or interruption.

Output lives under `D:\TradingML\runtimes\structure-validation\warm-replay-<id>`:
`report.json`, per-ticker result/status files, and logs. A killed probe is a failed
bounded diagnostic, not a certified checkpoint or a parity pass. For completed
prefixes, an independent normally executed engine must agree with the profiled
engine on emitted events and the complete checkpoint hash. The source revision
must remain unchanged. Event-budget completion validates only that prefix, not
the full target day. No full checkpoint payload is written by this probe.

Timing covers session reset, trade extrema, unified lifecycles, volume,
footprints, native lifecycles, directional legs, timeframe structures, and
unified-track refresh. Reference execution time and profiled execution time are
separate; observer overhead is not a claimed production speedup. The production
entry point uses the same ordered implementation with a no-op observer.

Vectorization is a candidate optimization for independent price/zone comparisons
and evidence scans within one event. Events themselves cannot be reordered or
evaluated independently: a trade can change the state consumed by the next trade.
Spatial indexing or updating only affected levels may outperform SIMD by removing
unnecessary scans. Choose after identifying the measured hot function; preserve
stable ordering, floating-point semantics, score-independent construction, and
full checkpoint/emitted-event parity. This patch adds measurement, not an
unmeasured algorithm rewrite or a production shutdown behavior change.
