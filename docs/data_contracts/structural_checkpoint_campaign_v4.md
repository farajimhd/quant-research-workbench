# Structural Checkpoint Campaign v4

## Authority

Campaign v4 runs the shared Generic Structure algorithm v16 over canonical
compact quote and trade events. It never substitutes bars or aggregates for
the event stream. Per-ticker continuity rows define exact session ordinal
ranges; split terms are loaded once per ticker and applied causally by the same
engine used by live and historical QMD.

Daily books are written to `q_live.qmd_structure_daily_checkpoint_v2`. Every
row is keyed by an explicit `checkpoint_set_id`, ticker, session, algorithm,
and source revision. Set lifecycle and immutable universe identity are recorded
in `q_live.qmd_structure_checkpoint_set_registry_v1` as `building`, `sealed`,
`failed`, or `interrupted`. Historical consumers select one exact set through
`QMD_STRUCTURE_CHECKPOINT_SET_ID`; rows from another campaign cannot mask its
resume seed.

## Process and fetch model

The Python launcher plans the universe once and persists its hash under the
runtime directory. Reruns reject a different set/date identity and reuse that
same plan. It assigns whole tickers to load-balanced shards in liquidity order,
so explicit priorities such as SUGP and JUNS start first.

Each shard is a separate Rust process with one current-thread runtime pinned to
one logical CPU, including Windows processor groups above 64 CPUs. One process
owns one ticker across the full period, keeps one level book in memory, writes
an end-of-day checkpoint, releases that book, and then takes its next ticker.
No ticker is split across workers.

Archive reads use physical `[first_ordinal, next_ordinal)` bounds. A worker
fully drains each bounded HTTP response before applying CPU-heavy function F,
so ClickHouse is never left blocked while the worker computes. Chunk size
starts at 250,000 ordinals and adapts between 100,000 and 1,000,000 toward a
three-second fetch. Complete session count, SIP-time order, first/last SIP
timestamps, ticker, date, cursor, source revision, and split lineage are still
validated before persistence.

Daily checkpoint persistence is retry-safe. Every insert carries a deterministic
deduplication token derived from the checkpoint set, ticker, session, algorithm,
source identity, cursor, and serialized structural state. The writer can retry
pre-response transport failures, response-stream failures, HTTP 429, and HTTP
5xx responses without creating a second logical checkpoint. Both daily
checkpoint tables retain the latest 10,000 non-replicated block identities per
partition so the ClickHouse token is effective rather than advisory. Exhausted
transient write failures remain fail-closed: that ticker's later sessions are
blocked and the same immutable campaign must be resumed from its latest complete
checkpoint.
Campaign progress counts recovered persistence retries as well as full-session
retries, and terminal error records retain the complete transport cause chain.

All `market_sip_compact.events_YYYY` reads use the immutable common historical
schema. Function F orders and timestamps causal state exclusively by
`sip_timestamp_us`; it neither requires nor probes an execution-time column.
The adapter supplies `0` for the optional live-wire execution clock. Campaign
code must never alter an archive table to satisfy a reader schema.

## Resume, purge, and progress

`--checkpoint-set-id` is mandatory. `--purge-existing-checkpoints` deletes only
that set, and the launcher records a durable purge marker so rerunning the same
command resumes instead of purging again. Other checkpoint sets are untouched.

The Rich dashboard aggregates every process and shows durable/queued/failed/
blocked units, active ticker-days, retries, total events, aggregate five-minute
throughput, elapsed time, and event-weighted ETA. It updates in place without
screen flicker. Redirected output emits plain snapshots every 15 seconds.
Worker details and errors remain in per-worker logs and atomic JSON status
files. Ctrl+C gives workers time to publish interruption state and preserves
completed daily checkpoints for the identical rerun.

## Capacity and acceptance

The launcher accepts 1–64 process workers. Worker count is a CPU allocation,
not a promise that ClickHouse can serve that many concurrent reads faster.
Increase it only while measured aggregate event throughput improves without
transport errors, server memory pressure, or query queuing. A checkpoint set is
sealed only when every shard exits successfully; backtest use should target a
sealed set.
