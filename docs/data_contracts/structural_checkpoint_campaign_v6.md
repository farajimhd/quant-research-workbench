# Structural Checkpoint Campaign v6

## Authority

Campaign v6 retains the v5 certified, ticker-sharded, exact-ordinal execution
model and Generic Structure algorithm v16. The event transition function is
unchanged. This revision repairs derived hold evidence, separates the durable
supervisor from terminal presentation, and adds checkpoint-boundary stop
control.

The checkpoint stores causal state and raw counts. Strategy-facing scores are
deterministic projections of that state, not independent persisted truth and
not calibrated return forecasts.

## Execution-clock recovery

Historical structure consumes `market_sip_compact.events_YYYY` in SIP-arrival
order and joins the ingestion-owned
`q_live.historical_event_execution_clock_v1` sidecar. A completed coverage row
records exact event, trade, clock, and delayed-report counts for each
ticker/session. The delayed count is computed once by the canonical importer;
the campaign reads that compact audit and does not rescan raw events merely to
decide whether a checkpoint is reusable.

Recovery always writes a new checkpoint-set ID and preserves the source set.
For each ticker, it validates the complete predecessor chain from the campaign
start. A legacy checkpoint can be copied only while every preceding session is
valid and the sidecar proves zero delayed reports for that session. The new
schema-2 recovery certification binds the original set, original payload and
chain hashes, current execution-clock revision, and zero-delayed proof. The
first session containing a delayed report and every successor session are
replayed through function F. Already-correct execution-clock-v1 rows retain
their exact replay evidence but receive a successor-set chain binding.

Resume discovers the longest contiguous target chain whose source plan,
execution-clock token, checkpoint hash, and predecessor hashes all match. It
never trusts a valid-looking tail across a missing or invalid predecessor.
Campaign manifests bind the source commit, executable SHA-256, certification
revision, execution-clock tables, and optional recovery source set.

The canonical sidecar repair is owned by
`pipelines/market_sip/flatfiles/download_update_events.py
--execution-clock-only`. It requires explicit dates and tickers, does not
download or reopen flatfiles, and does not modify compact events, continuity,
indexes, or bars. It recomputes the coverage audit only when the already
persisted sidecar exactly matches archive trade counts; otherwise it fails
closed and requires the canonical importer to restore that source day.
Downstream QMD, campaign, chart, indicator, strategy, Replay, and Backtest paths
remain prohibited from reading flatfiles.

## Hold-evidence migration

Every unified level exposes:

- `hold_count` and `break_count`, the raw causal lifecycle outcomes;
- `hold_rate`, the unsmoothed observed frequency;
- `hold_probability`, the Beta(2, 2) posterior mean retained for compatibility;
- `hold_observation_count`, the exact number of outcomes;
- `hold_evidence_reliability`, `n / (n + 8)`, as evidence-depth metadata;
- `hold_quality_score`, the one-sided 90% Wilson lower bound over the Beta(2, 2)
  pseudo-observations; and
- `hold_score_revision = beta22-wilson90-v1`.

Loading a v16 checkpoint always recomputes all derived fields from the raw
counts before any event is applied. This is the migration step at the beginning
of historical advancement, QMD History materialization, and live restoration.
It repairs missing or stale score fields without deleting or replaying valid
event state. The next daily checkpoint persistence writes the repaired fields
under the same algorithm version because function F and its state transitions
did not change.

Consumers rank levels by `hold_quality_score` and retain the count and revision
in audit evidence. A single absolute threshold is not treated as a comparable
probability across tickers. Any explicitly configured legacy
`minimum_hold_probability` remains a compatibility gate; new canonical
profiles use relative top-N selection with at least one observed outcome.

## Process and terminal ownership

For multi-process runs the Python launcher starts a detached supervisor. The
supervisor owns planning, registry transitions, worker lifecycle, aggregate
status, and final sealing. Closing the launching terminal or its Rich monitor
does not terminate the supervisor or workers.

`--monitor-existing` is strictly read-only. It reconstructs the display from
durable per-worker status files and never writes campaign status or registry
state. This permits multiple observers without writer contention.

Status publication uses a unique temporary file per write and bounded retries
for transient Windows sharing violations. The terminal uses one Rich Live
surface and cursor-stable refreshes; it does not clear the screen per sample.

## Stop and resume

`--stop-existing graceful` writes a checkpoint-set-scoped control request.
Workers finish the active daily session, persist and certify it, then stop
before starting another session. `--stop-existing fast` stops at the next
ordinal-chunk boundary and rolls the incomplete day back in memory. It never
persists a partial daily checkpoint.

The supervisor marks a controlled stop `interrupted`, not `failed`. Re-running
the identical immutable campaign loads the latest certified daily checkpoint
for each ticker and resumes forward. The control file is cleared only by the
new supervisor before worker launch.

## Certification and sealing

The v5 certification contract remains mandatory: exact source count, ordinal
range, SIP-time order, source revision, split lineage, checkpoint hash, and
predecessor chain are verified before persistence. Only the supervisor may
transition the checkpoint set registry to `sealed`, and only when every latest
logical row is certified.
