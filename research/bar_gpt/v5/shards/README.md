# Combined-bar pretraining shards

This package is engineering only. It does not change or import the v5 model.
It reads `market_sip_compact.events_YYYY` through frozen
`events_ticker_day_index FINAL` bounds. It never opens historical flatfiles,
creates ClickHouse tables, or launches training.

## Fixed sample format

Each `.npy` file contains an array of structured records, directly readable
with `np.load(path, mmap_mode='r', allow_pickle=False)`. One record contains:

* `values`: Float32 `[64,21]`: trade OHLC/volume/count, bid OHLC/closing size/count,
  ask OHLC/closing size/count, halted-open, halted-close, halt-start count.
* `valid`: UInt8 `[64,21]`, separate per-field evidence masks.
* `end_us`: Int64 `[64]`, UTC completed-bar clocks; zero denotes right padding.
* `timeframe_us`: Int64 scalar: 100ms, 1s, 10s, 1m, 1h, or 1d.
* `split`: UInt8: 0 training, 1 development from 2025-10-01.

These are final **unscaled** model-input arrays, not fitted normalized features.
Float32 rounding is intentional; exact source observations remain in canonical
ClickHouse. Future scaling and artificial masking belong in vectorized/GPU batch
operations. The loader need not aggregate, join, decode Parquet, or construct
windows. Coarse contexts are split-adjusted as described below; the training
loader lives in `../data_v1.py`. Symbols never join across ticker-year jobs.

Use only files named in valid `receipt.json` publications, never arbitrary
directory globs while a campaign is writing. Shards are capped at 32 MiB by
default, uncompressed for mmap and asynchronous prefetch. Small ticker-day
shards are allowed. Shuffle shard references and batches in the future loader;
the corpus does not pretend adjacent samples are independent.

## Extraction and sampling

Ticker-years are deterministically shuffled within years, then interleaved
across 2019–2025. All historical index symbols are candidates, independent of
scanner membership, later returns, or labels. This is ticker-year sampling,
not event-weighted random ordinal sampling or a claim of certified instrument
identity. Each worker processes trading sessions chronologically so halt state
and coarse context can be carried without rereading prior days repeatedly.

The v4 contract imports `bar_semantics_v1.py`, shared with future model phases.
ClickHouse aggregates a sparse 100ms base, rolls up sufficient statistics,
and returns samples of **64 consecutive observed bars**, plus transient coarse bars.
Observation indices are assigned before sampling; clock gaps remain in timestamps.
Reporting status does not exclude encoder trades. Structurally valid trades use
ARTE sale-condition rules independently for last price, extremes and volume. Receipts
record total trades, unknown trades, delayed trades and structurally valid
trades, in that order, in `reporting_counts`.
Sessions are XNYS trading dates, 04:00–20:00 New York, including early-close
dates under that explicitly extended-hours definition. Daily uses 09:30–16:00
New York with the same trade and quote eligibility as intraday. DST changes UTC boundaries.

Fine frames use a deterministic random offset in 0–63, then nonoverlapping
64-bar blocks with 10% retention. All complete nonoverlapping coarse windows
are retained because they are scarce. Frame counts are exposed; equal storage
by timeframe is not forced through duplication. Coarse windows are packed by
NumPy across sessions but never across a missing ticker-day, year, or split.
Fine session tails shorter than 64 are right-padded with zero values, timestamps,
and evidence masks. Short sessions start at their first observed bar. Tail blocks
use the same hash retention as full blocks. Hourly/daily contexts remain complete
64-bar windows; their incomplete final remainders are not published.
No empty clock slots are manufactured;
missing ticker-days are recorded with explicit reasons. Quote-only and status-only
buckets are not observations. A retained bar requires complete trade OHLC and
positive eligible volume/count; every real row is checked before publication.
Apply this requirement after rollup at each timeframe. Volume-only contributions
in a 100ms bucket must still contribute to a longer complete bar. No reporting
clock evidence, participant timestamp, or timely-status bit is required.

Quotes carry the last causal valid pair within a session when no new quote is
available; an invalid quote message clears that state. Carry does not create
quote counts, and no future quote is used. Existing source events and ARTE tables
are unchanged. Only final sample arrays are persisted; partial ticker-year
recovery replays canonical days to rebuild the small in-memory coarse context.

Each sample shard records its full context bounds. The training split uses one
chronological cutoff for all tickers/timeframes and purges crossing contexts,
including contexts whose observed bars have gaps across that cutoff.

An audited execution-only resume may optimize SQL while preserving the frozen
logical dataset identity. Such a resume requires a sealed equivalence report
covering at least 32 real ticker-days and every dataset year, identical packed
arrays and provenance, pinned baseline/candidate SQL hashes, and unchanged source
selection. `query_hash` continues to describe the original logical query;
`execution_query_hash` records the query actually executed. Original receipts
are never rewritten. The optimized query carries aggregates forward and reduces
the quote lookup to the last actual update per 100ms bucket; observed OHLC/counts
still consume all eligible events, and quote invalidation still clears carry.

Hourly/daily prices are backward-adjusted to the share basis at the end of each
sample using the existing `q_live.market_stock_split_v1` executed-action catalog.
The complete acquisition receipt, catalog rows, and hashes are frozen in the
dataset contract. As in ARTE `market_day_sql.split_factor`, earlier prices multiply
by `split_from/split_to`; trade volume and bid/ask sizes use the inverse. Counts
and timestamps are unchanged. Multiple action IDs compound, and future actions
after a sample's end never affect that sample. No flatfiles or trade-flag repair
workflow is used. Unknown halt state remains masked; missing evidence is not zero.

Halt state follows source-observed halt/resume token mappings from the shared
market-signals implementation. Open is before `[start,end)`, close is after
events before `end`. Count is known 0→1 transitions, not repeated messages.
Unknown initial state or conflicting status is masked; silence never means
resumed. No news or LULD-state channels are added. State evidence is independent
of price eligibility. Trade eligibility uses the shared condition dictionary;
quote bars use structurally valid bid/ask pairs. These use finalized canonical
event semantics, not a claim of fully reconstructed historical correction pairs.

## Operation

```powershell
python -B -u research/bar_gpt/v5/run_shards.py --workers 6 --io-workers 2
python -B -m research.bar_gpt.v5.shards.progress
```

Default output: `D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v4`.
The limit is 1,000,000,000,000 bytes, with a 100 GB free-space reserve. A final
batch can leave the dataset slightly below the cap. Exhaustion is reported
honestly rather than duplicating samples to reach an artificial size.
Source metadata and checkpoint overhead are additional small operating costs.

Use a separate output for pilots, e.g. `--tickers AAAU --years 2019 --max-days 70
--max-jobs 1 --target-bytes 2000000000`. Contract changes require a new output
directory; resume checks the frozen dictionary, code, inventory, and receipts.
An OS lock prevents concurrent controllers. Each query worker queues at most
two writes; the shared disk pool bounds I/O concurrency. Queries have explicit
memory, thread, timeout, and result-size limits. Network failures retry up to
three attempts; other failures drain and publish a failed status.

Create `STOP` in the output root to stop admission and drain current work.
Remove it before resume with the same command. `progress.json` is refreshed
every second during extraction; the observer shows stale snapshots explicitly.
`complete.json` seals a ticker-year; `result.json` distinguishes scope completion,
budget termination, interruption, inventory exhaustion, and failure.

Windows/SMB readers can briefly deny atomic JSON replacement. Publication now
retries these sharing conflicts for up to ten seconds. If only progress remains
locked, extraction continues and reports `status_publish_errors`; failures to
publish required shard receipts still stop the run. An engineering-only recovery
may use `--resume-code-receipt`: this explicit audit pins exact old/new code,
checks unchanged data parameters and extraction/storage hashes, and preserves
the original contract binding and all sealed shard identities. It is not a
general override for changed sample semantics.
