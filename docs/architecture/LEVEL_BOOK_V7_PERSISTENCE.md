# Level Book V7 persistence

## Decision

Historical V7 checkpoints are retrospective: each
session's full calculation becomes available only at its recorded session-end
`available_at`. Migration does not reinterpret its intraday segment timestamps
as causal confirmations and does not rerun historical V7. The original
`all-tradable-20250101-20260912-mle-v1` archive is not a valid migration source:
its producer admitted delayed-reported trades. It must be rebuilt from canonical
SIP after the versioned trade-reporting coverage is complete for every source
day. The stopped partial publication is not seed authority.

The permanent ClickHouse authority is in the existing `arte` database:

- `arte.structural_levels_v7` stores qualified and unqualified candidate closing states as
  half-open `[valid_from, valid_to)` intervals. `valid_from` is the checkpoint's
  session-end availability timestamp. A role, geometry, lifecycle or fit change
  closes the preceding interval and opens a successor. Disappearance only sets
  the preceding interval's `valid_to`; there is no removal row. Role-transition
  ancestry and mixture parent identity are retained explicitly.
- `arte.structural_level_observations_v7` stores typed observation-to-level
  assignments as half-open intervals. An observation identity is derived from
  its six source fields and duplicate occurrence index. Reassignment after a
  partition split closes the old assignment; unchanged observations are not
  republished each session. `resolved_at` is the causal confirmation time;
  assignment `valid_from` is the retrospective checkpoint availability time.
- `arte.structural_level_coverage_v7` is the publication fence and session audit.
  It is written only after both level and observation intervals have been
  acknowledged. Empty and missing coverage remain distinct. Historical daily
  files remain immutable migration evidence and producer restart authority.

All timestamps use `DateTime64(9, 'UTC')`. Existing V7 inputs have completed-bar
second resolution; nanosecond storage preserves exact values without claiming
subsecond observation precision. A future streaming producer may publish finer
timestamps under this schema only when its source and algorithm support them.

The table name carries the V7 contract identity. No `book_version` column exists.
The migration contract identity is `arte-structural-levels-v7-2`; changing the
physical or semantic mapping requires a new identity and compatible migration.
All tables explicitly use `live_market_ssd`; preflight validates the policy and
actual active-part placement before any insert.

## Historical and Backtest boundary

Historical rows are prior-session seed authority. They are not same-session
causal transitions. Backtest loads the latest published retrospective state
available before its session and advances current-session V7 causally. Replacing
that streaming advancement requires a separately built causal transition product;
this migration does not claim one.

Consumers must not expose a future `valid_to` as a strategy feature. The endpoint
is used only to select the interval active at the requested time.

## Migration

From the committed workstation checkout:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B scripts/migrate_level_book_v7_to_clickhouse.py preflight
python -B scripts/migrate_level_book_v7_to_clickhouse.py run
```

Defaults read the corrected campaign at
`<workstation-runtime>/level-book-v7/all-tradable-20250101-20260912-mle-reporting-v1`
and write operational results under
`<workstation-runtime>/level-book-v7/arte-migration-reporting-v1`. `--workers` is bounded
to 1..64; the default follows the current CPU/free-RAM budget. Inserts are
row- and byte-bounded.

The V7 producer excludes trades with the canonical delayed-report flag before
second-bar fitting and requires completed
`q_live.historical_trade_reporting_coverage_v1` records for every source day.
Migration preflight rejects older archives before table DDL or inserts. The
corrected archive needs a new plan and full checkpoint recomputation; copying
old books or resuming the old campaign cannot repair their geometry. Existing
partial V7 rows must be removed through the reviewed replacement procedure
after the stopped controller and its workers have exited.

One ticker is the durable work unit. Spawned worker processes read its immutable
daily gzip books, coalesce unchanged consecutive level and observation states,
insert compact intervals and publish coverage last. Unqualified candidates are
included so a prior-session streaming seed can be reconstructed.
Unchanged normalized states are compared before hashing so repeated full-book
checkpoints do not pay redundant JSON serialization and SHA-256 work. The
default process count is the maximum admitted by current CPU and free-RAM
budgets; `--workers` can lower it. `--insert-workers` separately bounds
ClickHouse publishers to four by default so CPU parallelism does not create an
unbounded insert/part load.

The runtime owns an exclusive `controller.lock`, preventing overlapping
migration controllers. Insert tokens and stable keys make retries deterministic.
Completed coverage with the same source-plan hash is skipped.
The migration rejects a different source-plan hash already present in these
tables; mixing filtered and unfiltered V7 campaigns would make as-of reads
ambiguous.
Ctrl+C stops new admission, drains active tickers, writes `result.json` and exits
130. The result includes the admitted worker budget and cumulative read/verify,
compaction, receipt and insert worker-seconds. Redirected output is line-oriented
JSON; interactive output uses a stable Rich status table.

## Maintenance

After a canonical SIP day is certified, a future incremental producer must
restore the latest compatible typed seed or the retained historical daily book,
advance ordered completed bars, publish changed level and observation intervals,
then publish coverage last. Backtest may only read these tables; it computes
intraday V7 in memory and may not write market products. Source, condition,
split, algorithm and numerical identities must remain compatible. A changed
historical source resumes from the last compatible predecessor rather than
relabeling the current checkpoint.
