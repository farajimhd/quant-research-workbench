# Structural Checkpoint Campaign v3

## Purpose and authority

Campaign v3 creates immutable Generic Structure algorithm v16 end-of-day
checkpoints with the same shared Rust streaming function used by historical and
live QMD. It changes scheduling, transport, restart, and persistence only; it
does not introduce a second level-book calculation.

The calculation consumes canonical compact quote/trade events in exact
per-ticker order. Bars or aggregates must never replace those events. Daily
bars may be read only to prioritize scarce worker slots. Split events are part
of the shared source plan and are applied causally before the affected session.
The output authority is `q_live.qmd_structure_daily_checkpoint_v1`.

## Universe and ordering

The automatic universe is:

1. current active primary USD listings; then
2. tickers with canonical events on the requested cold-start date that are not
   already current listings.

Current listings are ordered by bounded dollar volume for the configured
liquidity period. Explicit `--priority-ticker` values precede that automatic
order, so operationally important symbols such as SUGP or JUNS can become
usable first without embedding ticker-specific behavior. Optional ticker files
append extra symbols and never remove automatic ones.

## Per-ticker temporal ownership

Each worker owns one ticker through the entire requested period. It loads the
latest compatible checkpoint once, keeps one live book in memory, processes
completed sessions sequentially, persists the book at every session end, and
then releases the book before taking another ticker. Different tickers may run
concurrently; days for one ticker may not. The standalone campaign accepts
between 1 and 64 workers; this is an operator-selected capacity ceiling, not a
claim that every ClickHouse deployment scales efficiently to 64 concurrent
streams.

`--start-date` is the true cold authority start. With no compatible seed, the
first checkpoint begins from an empty book at 04:00 America/New_York on that
date. With a compatible seed, the worker verifies the complete source revision
from that same authority start through the seed session and resumes at the
first later session. Thus a ticker persisted through August 20 automatically
starts at August 21 when the requested end is later; it does not replay January
through August 20.

Canonical events are fetched in bounded causal windows. The checkpoint is
carried between windows and days without database reload. A day is persisted
only after source completeness and before/after revision equality are proven.
Transient transport/capacity failures receive bounded exponential retries;
semantic or compatibility failures stop that ticker and mark later sessions
blocked.

## Destructive cold reset

`--purge-existing-checkpoints` truncates only
`q_live.qmd_structure_daily_checkpoint_v1`, verifies zero rows remain, and then
starts the requested campaign cold. It does not delete compact events, bars,
split data, live snapshots, or other QMD tables. Do not use this option when a
compatible per-ticker resume is desired.

## Progress and restart

Progress schema v5 is atomically published to `campaign-status.json`. It
reports queued, active, completed, skipped/current, unavailable, retried,
failed, and dependency-blocked units; exact active ticker/day ownership; event
counts; the ordinal-summary total event estimate; recent completions; elapsed
time; and ETA. Every worker publishes batch-level progress into one global
event counter, including workers still inside a long ticker-day. Failed,
retried, or interrupted attempts roll back their uncommitted contribution. ETA
is the remaining estimated event count divided by this aggregate actual
processed-event rate over the latest fixed five-minute observation window. It
warms up until that window has at least 15 seconds of evidence. Interactive
output is a fixed-row dashboard with one initial clear and in-place updates, so
it does not flicker. Redirected output is plain text without ANSI sequences.

Ctrl+C writes `interrupted` and returns active units to the resumable queue.
Rerunning the same command verifies persisted seeds and resumes each ticker
independently.

## Validation gate

Before increasing concurrency, a bounded panel must prove exact checkpoint
JSON, cursor, source identity, split lineage, level geometry, scores,
probabilities, footprints, and timeframe state against the single-ticker
authority. Interrupt/resume output must be identical. Worker count should rise
only while measured events/second improves within ClickHouse memory, query, and
thread limits.
