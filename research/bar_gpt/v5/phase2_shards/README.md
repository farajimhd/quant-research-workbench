# Phase-two causal input streams

The generator stores raw bars and causal block references. It does no GPU work,
does not change the running phase-one trainer, and does not cache embeddings
from a checkpoint that is still learning. Each future encoder view produces 512
values through the existing full-feature path.

| Frame | Observed context | Window width | Stride | Views per decision |
| --- | ---: | ---: | ---: | ---: |
| 1 second | 544 | 64 | 32 | 16 |
| 10 seconds | 288 | 64 | 32 | 8 |
| 1 minute | 288 | 64 | 32 | 8 |
| 1 hour | 160 | 64 | 32 | 4 |
| Daily | 80 | 64 | 16 | 2 |

Blocks contain 256 consecutive observed 1s decisions, with actual UTC gaps.
Their 38 encoder views are indexed, not copied. Missing history makes a view
unavailable; no artificial bars are added. Final shorter tails remain in the
checkpoint with an explicit deferred count. Training may shuffle whole blocks.

The plan freezes the entire 2019–2025 ordinal inventory, source authority,
condition dictionary, corporate actions, liquidity membership, four-TB capacity,
and fourteen-calendar-day ticker-range rule. Execution was initially two TB;
the user increased the current cap to four TB. Raise
`--target-bytes` against the same output to extend within four TB. Completed core
ranges and decision IDs are not regenerated. Four TB is an allocation, not a
claim about unique source capacity; exhaustion is explicit.

Complete source sessions are coalesced inside each logical range up to the
immutable `--chunk-events` setting. Large sessions remain whole. Context is
carried across subdivisions and ranges. Each ticker-year stream warms preceding
fine/coarse history once. Small bootstrap histories may overlap across independent
ticker-year streams; published core ranges and decision blocks do not.

Only canonical `market_sip_compact` is read. The unchanged phase-one producer
owns trade conditions, separate OHLC/extreme/volume eligibility, quote
invalidation and within-session carry. Valid trades are not excluded for missing
reporting status or participant clocks. Every output bar requires complete trade
attributes; quotes alone cannot create a bar. There are 18 market fields, no
halts and no 100ms output. The selection adapter changes only arbitrary chunk
edge cuts to known session boundaries. Source parity is checked against the
original producer over a wider span.

Small immutable NPY segments contain raw `[18]` Float32 values, `[18]` UInt8 masks
and Int64 bar-end clocks. The reference window reader applies phase one's own
backward split adjustment at each 64-bar window endpoint. Raw caches never use
a future range-end share basis. The existing input transform adds nine UTC
features, for 27 encoder channels; transformed tensors are not duplicated here.

The append-only catalog accepts exactly three liquid blocks per less-liquid
block within each chronological split. Classification is inherited from phase
one's top quartile of ticker-year mean event counts. Unmatched candidates stay
balance-deferred. The split matches the already-training encoder's cutoff,
2024-04-16 04:00 UTC, and purges blocks whose full context crosses it.

Each fsynced publication includes hashes, a checkpoint and a sealed receipt.
Receipt recovery is idempotent; an OS lock rejects duplicate producers. Final
selected-block releases are immutable Parquet files. Future loader access must
validate payload hashes; the reference reader implements that requirement.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u -m research.bar_gpt.v5.phase2_shards.run --source-revision <commit>
python -B -m research.bar_gpt.v5.phase2_shards.progress
```

Use a committed verified workstation snapshot. Generated output defaults to
`D:/TradingML/runtimes/bar_gpt/phase2-streams-v1`. Eight query/two I/O workers,
3 GiB per query, 96 GiB free RAM and 150 GiB free disk reserves are the defaults.
No Torch/CUDA is imported. Producers and I/O overlap across bounded lanes;
each stream advances only after its prior checkpoint is durable. Low resources
pause query admission. Root `STOP` gracefully drains; closing the panel stops
only the viewer. A source or publication failure is surfaced and fails closed.

`--max-units 2 --only-stream TICKER:2023` runs a bounded pilot without changing
the complete plan. `audit --output ... --max-receipts 4` checks a bounded receipt
sample and ledger; `audit --output ... --ticker ... --day YYYY-MM-DD` compares
exact source bars. Neither audits every payload in the corpus.

Sparse-history performance uses bounded multi-session warmup queries, larger
streaming hourly/daily warmup spans, and thread-private persistent transports.
An all-empty current range defers bootstrap until its first actual observation.
Legacy checkpoints are treated as already initialized, so existing indices and
publications are never rewritten. Core chunk layout stays unchanged.

A frozen-core-year count of **all** raw trade messages can prove that fewer than
256 decisions are possible. Only those impossible full-block streams are
marked skipped, with sealed counts in the ledger. Total source rows must first
reconcile with the ordinal plan. This is not a trade validity, delayed-reporting
or missing-timestamp filter. Existing publications remain immutable.

W&B is online by default in `bargpt-phase2-shards-v1`; progress is coalesced and
uploaded by a separate thread every 30 seconds. Its persistent local journal is
uploaded live. Failures remain visible in progress metadata and local evidence.
Use `--wandb-mode disabled` only for isolated offline fixtures or local tests.

Publication buffers one <=16 MiB segment per I/O worker, preserving NPY/NPZ
formats and fsync boundaries while hashing serialized source bytes. Audits/readers
still verify disk hashes. Frozen yearly proofs are not rewritten for every unit.
Lane metadata loads on extraction workers. A single CPU-only ledger process owns
bounded batch transactions, bulk SQL selection and commit acknowledgments; lanes
advance only after acknowledgment. This isolates SQLite from extraction-thread
GIL contention without moving any bar arrays through IPC. Existing units, plan,
phase-one bar math and exact per-split 75/25 selection remain unchanged.

`--process-workers 16 --workers 64 --io-workers 16` runs up to sixteen independent
CPU-only producers, sixty-four bounded in-flight lanes, and sixteen globally
limited publication slots. Each producer writes its own arrays directly; only
sealed receipts return to the coordinator. Aggregate per-process query/response
allowances cannot exceed 128 GiB, and actual memory/disk admission gates remain.
The default `--process-workers 0` retains the thread topology for comparisons.

`--storage-format packed-v2` packs each unit's identical BAR/DECISION/context
bytes into mmap containers capped at 16 MiB. It reduces per-unit small files and
fsyncs; offsets, dtypes, shapes and source checksums are sealed in the receipt.
Legacy NPY/NPZ units stay immutable and readable in the same cohort. Context
restore verifies packed checksums, including when abandoned legacy files exist.
No timestamps, values, masks, split rules or encoder-window geometry change.

`--training-campaign PATH --validation-lanes 4` reads the current controller and
trainer status without modifying them. It caps admitted lanes during validation,
startup or stale evidence, then restores the configured full admission limit
during training. Already-admitted units drain; no producer/trainer restart is
needed at an epoch transition. The panel and W&B expose the current cap.

`--background-workers` applies Windows background CPU/I/O/memory scheduling only
to generator and ledger subprocesses. It never changes the trainer, ClickHouse
server settings or system security. Use it alongside measured producer counts
when training shares D:; failures to apply the requested priority fail visibly.
