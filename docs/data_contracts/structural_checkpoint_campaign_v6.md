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

The supervisor's `--resume-from-runtime` mode is the only supported way to
continue work from an older campaign revision. It does not mutate or append to
the source set. It copies the source campaign's immutable ticker/session plan
into a new target runtime, verifies the source universe hash, automatically
binds `--recovery-source-checkpoint-set-id`, and places SUGP and JUNS first.
The successor manifest additionally binds the SHA-256 of the source manifest
and its universe hash. Re-running the identical successor command resumes the
target set; it never replans from a potentially changed current universe.

Recovery requires certified execution-clock coverage for every non-empty
source session. A missing coverage row is not equivalent to zero delayed
reports and fails closed before a legacy checkpoint can be copied. This means
an interrupted legacy campaign remains preserved, but its work is reusable
only where the ingestion-owned sidecar proves compatibility.

Before launching any process shard, the supervisor runs one read-only
execution-clock preflight over the complete immutable ticker plan. If coverage
is incomplete, it exits before opening worker processes or changing the source
checkpoint set. Coverage failure is campaign-fatal inside a worker as a second
line of defense, so 80 shards cannot cascade the same authority error.

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

Campaign v6 also carries the shared engine's frozen per-session ticker-relative
quality baseline. At 04:00 New York, the engine snapshots separate inherited
support and resistance `hold_quality_score` distributions. QMD History, charts,
Replay, Backtest, and Live derive `ticker_relative_quality_score` as the
mid-rank empirical percentile of the level's current cumulative quality against
that immutable session baseline. Same-session levels are explicitly
`same_session_provisional` and fail open under relative-score filters. The
baseline revision and hash are checkpointed for exact restart equivalence;
per-level relative scores remain recomputable projections and are excluded from
checkpoint certification hashes.

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

Example successor recovery through the PowerShell entry point:

```powershell
.\scripts\run_structure_checkpoint_campaign.ps1 `
  -CheckpointSetId canonical-tradable-20250101-20260831-v16-clock-v2 `
  -RuntimeDir D:\TradingML\runtimes\qmd_gateway\structure-checkpoint-campaign-v6\canonical-tradable-20250101-20260831-v16-clock-v2 `
  -ResumeFromRuntime D:\TradingML\runtimes\qmd_gateway\structure-checkpoint-campaign-v5\canonical-tradable-20250101-20260831-v16-cert-v1 `
  -SourceCommit $env:QMD_STRUCTURE_CAMPAIGN_SOURCE_COMMIT `
  -Workers 80 `
  -Rebuild
```

The wrapper delegates only to the v6 process supervisor. The former v2
planning/build wrapper is retired and can no longer accidentally enforce a
32-worker ceiling or invoke the superseded daily HTTP builder. `-Rebuild`
forces a workstation-local Cargo build into the managed runtime target, even
when an older runtime executable exists; this avoids both stale binaries and
Windows trust failures caused by copying an executable from another machine.
Gitless workstation mirrors must pass `-SourceCommit` as the full committed
laptop revision; the value is propagated unchanged into the detached
supervisor and immutable campaign manifest.

## Certification and sealing

The v5 certification contract remains mandatory: exact source count, ordinal
range, SIP-time order, source revision, split lineage, checkpoint hash, and
predecessor chain are verified before persistence. Only the supervisor may
transition the checkpoint set registry to `sealed`, and only when every latest
logical row is certified.
