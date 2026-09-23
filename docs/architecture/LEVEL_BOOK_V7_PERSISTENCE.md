# Level Book V7 persistence

## Decision

The existing historical V7 campaign is retained. It is retrospective: each
session's full calculation becomes available only at its recorded session-end
`available_at`. Migration does not reinterpret its intraday segment timestamps
as causal confirmations and does not rerun historical V7.

The permanent ClickHouse authority is in the existing `arte` database:

- `arte.structural_levels_v7` stores coalesced retrospective closing states as
  half-open `[valid_from, valid_to)` intervals. `valid_from` is the checkpoint's
  session-end availability timestamp. A role, geometry, lifecycle or fit change
  closes the preceding interval and opens a successor. Disappearance only sets
  the preceding interval's `valid_to`; there is no removal row. Role-transition
  ancestry and mixture parent identity are retained explicitly.
- `arte.structural_level_coverage_v7` is the publication fence and session audit.
  It is written only after a ticker's level intervals and terminal checkpoint
  have been acknowledged. Empty and missing coverage remain distinct.
- `arte.structural_level_builder_checkpoint_v7` retains the latest complete V7
  engine checkpoint per ticker so certified later source days can advance without
  rebuilding history. Historical daily files remain immutable migration evidence.

All timestamps use `DateTime64(9, 'UTC')`. Existing V7 inputs have completed-bar
second resolution; nanosecond storage preserves exact values without claiming
subsecond observation precision. A future streaming producer may publish finer
timestamps under this schema only when its source and algorithm support them.

The table name carries the V7 contract identity. No `book_version` column exists.
The migration contract identity is `arte-structural-levels-v7-1`; changing the
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

Defaults read the frozen campaign at
`<workstation-runtime>/level-book-v7/all-tradable-20250101-20260912-mle-v1`
and write operational results under
`<workstation-runtime>/level-book-v7/arte-migration-v1`. `--workers` is bounded
to 1..64; the default is at most 16. Inserts are row- and byte-bounded.

One ticker is the durable work unit. Workers read its immutable daily gzip books,
coalesce unchanged consecutive states, insert compact intervals, persist the
terminal builder checkpoint and publish coverage last. Insert tokens and stable
keys make retries deterministic. Completed terminal checkpoints with the same
source-plan hash are skipped. Ctrl+C stops new admission, drains active tickers,
writes `result.json` and exits 130. Redirected output is line-oriented JSON;
interactive output uses a stable Rich status table.

## Maintenance

After a canonical SIP day is certified, the updater must restore each ticker's
latest compatible builder checkpoint, advance ordered completed bars, publish
changed level intervals, replace the terminal checkpoint and publish coverage
last. Source, condition, split, algorithm and numerical identities must remain
compatible. A changed historical source resumes from the last compatible
predecessor rather than relabeling the current checkpoint.
