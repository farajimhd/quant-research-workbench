import { Activity, AlertTriangle, Archive, CheckCircle2, Clock3, FileStack, FolderInput, HardDrive, Rows3, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { api, query } from "../api/client";
import { DataTable } from "../app/components/DataTable";
import { InlineNotice } from "../app/components/InlineNotice";
import { Modal } from "../app/components/Modal";
import { PageIntro } from "../app/components/PageIntro";
import { ProgressMeter, type Stage } from "../app/components/Progress";
import { SemanticBadge, toneForStatus } from "../app/components/SemanticBadge";
import { Tabs } from "../app/components/Tabs";
import { formatBytes, formatDuration, formatNumber } from "../app/format";
import { useViewportFillPanel } from "../app/hooks/useViewportFillPanel";

type Scope = {
  raw_root: string;
  processed_root: string;
  start_date: string;
  end_date: string;
  raw_file_count: number;
  artifact_count: number;
};

type BuildProgress = {
  metrics: {
    raw: number;
    expected: number;
    missing: number;
    closed: number;
    rows: number;
    written_bytes: number;
    elapsed_sec: number;
    reference_sessions?: number;
    missing_reference_sessions?: number;
    output_sessions?: number;
    output_start_date?: string | null;
    warmup_sessions?: number;
    carryover_timeframes?: string[];
    status: string;
  };
  phases: Stage[];
  plan: Record<string, unknown>[];
  artifact_events: Record<string, unknown>[];
  phase_events: Record<string, unknown>[];
};

type BuildJob = {
  build_name?: string;
  created_at?: string;
  finished_at?: string | null;
  job_id: string;
  status: string;
  request?: Record<string, unknown>;
  resources?: Record<string, unknown>;
  summary?: {
    artifact_count?: number;
    bytes_written?: number;
    duration_sec?: number;
    event_count?: number;
    expected_sessions?: number;
    missing_sessions?: number;
    output_sessions?: number;
    reference_sessions?: number;
    rows_written?: number;
  };
  progress?: BuildProgress;
  result?: Record<string, unknown>;
  started_at?: string | null;
  updated_at?: string;
  error?: string;
  traceback?: string;
};

type DeleteBuildResponse = {
  deleted_data?: boolean;
  job_id?: string;
  orphaned_job?: boolean;
  status?: string;
};

type BuildMetric = {
  label: string;
  value: ReactNode;
  detail: string;
  icon: ReactNode;
  tone?: "danger" | "info" | "neutral" | "success" | "warning";
};

const tabs = ["Build", "Build Timings", "Artifacts", "Plan", "Processed Store", "Manifest"];
const activeBuildStatuses = new Set(["queued", "running", "canceling", "cancelling"]);
const resumableBuildStatuses = new Set(["cancelled", "canceled", "failed", "error"]);

export function MarketDataBuildPage() {
  const [scope, setScope] = useState<Scope | null>(null);
  const [draft, setDraft] = useState<Scope | null>(null);
  const [job, setJob] = useState<BuildJob | null>(null);
  const [jobs, setJobs] = useState<BuildJob[]>([]);
  const [activeTab, setActiveTab] = useState(tabs[0]);
  const [deleteTarget, setDeleteTarget] = useState<BuildJob | null>(null);
  const [deleteResult, setDeleteResult] = useState<string | null>(null);
  const [editingScope, setEditingScope] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadScope();
  }, []);

  useEffect(() => {
    if (!scope) return;
    loadJobs(scope);
  }, [scope]);

  useEffect(() => {
    if (!scope || !jobs.some((item) => activeBuildStatuses.has(String(item.status).toLowerCase()))) return;
    const timer = window.setInterval(() => loadJobs(scope), 3000);
    return () => window.clearInterval(timer);
  }, [scope, jobs]);

  useEffect(() => {
    if (!scope || !job || !activeBuildStatuses.has(String(job.status).toLowerCase())) return;
    const timer = window.setInterval(() => loadJob(scope, job.job_id), 1000);
    return () => window.clearInterval(timer);
  }, [scope, job?.job_id, job?.status]);

  async function loadScope() {
    const payload = await api<Scope>("/api/market-data/scope");
    setScope(payload);
    setDraft(payload);
  }

  async function loadJobs(currentScope: Scope) {
    const payload = await api<{ jobs: BuildJob[] }>(`/api/market-data/build/jobs${query({ processed_root: currentScope.processed_root })}`);
    setJobs(payload.jobs);
    if (job) {
      const current = payload.jobs.find((item) => item.job_id === job.job_id);
      if (current) setJob((value) => (value ? { ...value, ...current } : current));
    }
  }

  async function loadJob(currentScope: Scope, jobId: string) {
    const payload = await api<BuildJob>(
      `/api/market-data/build/jobs/${jobId}${query({ processed_root: currentScope.processed_root, raw_root: currentScope.raw_root })}`
    );
    setJob(payload);
  }

  async function openJob(currentScope: Scope, jobId: string) {
    setActiveTab(tabs[0]);
    await loadJob(currentScope, jobId);
  }

  async function startBuild() {
    if (!scope) return;
    setError(null);
    try {
      const payload = await api<BuildJob>("/api/market-data/build/jobs", {
        method: "POST",
        body: JSON.stringify(scope)
      });
      setJob(payload);
      await loadJobs(scope);
      await loadJob(scope, payload.job_id);
      setActiveTab(tabs[0]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function stopBuild() {
    if (!scope || !job) return;
    const payload = await api<BuildJob>(`/api/market-data/build/jobs/${job.job_id}/cancel${query({ processed_root: scope.processed_root })}`, { method: "POST" });
    setJob(payload);
    await loadJobs(scope);
  }

  async function resumeStatefulBuild() {
    if (!scope || !job) return;
    setError(null);
    try {
      const payload = await api<BuildJob>(`/api/market-data/build/jobs/${job.job_id}/resume-stateful${query({ processed_root: scope.processed_root })}`, { method: "POST" });
      setJob(payload);
      await loadJobs(scope);
      await loadJob(scope, payload.job_id);
      setActiveTab(tabs[0]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function requestDeleteBuild(target: BuildJob) {
    setError(null);
    setDeleteResult(null);
    setDeleteTarget(target);
  }

  async function deleteBuild() {
    if (!scope || !deleteTarget) return;
    setError(null);
    try {
      const result = await api<DeleteBuildResponse>(
        `/api/market-data/build/jobs/${deleteTarget.job_id}${query({ processed_root: scope.processed_root })}`,
        { method: "DELETE" }
      );
      setDeleteResult(deleteResultText(result));
      if (job?.job_id === deleteTarget.job_id) {
        setJob(null);
      }
      setDeleteTarget(null);
      await loadScope();
      await loadJobs(scope);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function applyScope() {
    if (!draft) return;
    setScope(draft);
    setEditingScope(false);
    setJob(null);
    setJobs([]);
  }

  const running = Boolean(job && activeBuildStatuses.has(String(job.status).toLowerCase()));
  const resumable = Boolean(job && !running && resumableBuildStatuses.has(String(job.status).toLowerCase()));
  const progress = job?.progress;
  const metrics = progress?.metrics;
  const missing = useMemo(() => (progress?.plan ?? []).filter((row) => row.expected_market_session && !row.exists), [progress?.plan]);
  const viewScope = scopeFromBuildJob(job, scope);

  return (
    <>
      <PageIntro
        groupLabel="Market Data"
        title="Build Data"
        description="Rebuild the canonical market-data store with every supported timeframe and feature group."
        actions={scope ? <ScopeCard scope={scope} /> : null}
      />
      {job ? (
        <div className="button-row">
          <button className="button" onClick={() => setJob(null)} type="button">
            Back to builds
          </button>
          <button className={running ? "button danger" : "button primary"} onClick={running ? stopBuild : startBuild} type="button">
            {running ? "Stop build" : "New build"}
          </button>
          {resumable ? (
            <button className="button primary" onClick={resumeStatefulBuild} type="button">
              Resume stateful
            </button>
          ) : null}
          <button className="button" onClick={() => setEditingScope(true)} type="button">
            Edit scope
          </button>
          <button className="button danger" disabled={running} onClick={() => requestDeleteBuild(job)} type="button">
            Delete build
          </button>
        </div>
      ) : null}
      {!job ? (
        <BuildStartPage
          error={error}
          deleteResult={deleteResult}
          jobs={jobs}
          onEditScope={() => setEditingScope(true)}
          onDeleteRequest={requestDeleteBuild}
          onOpenJob={(jobId) => scope && openJob(scope, jobId)}
          onStartBuild={startBuild}
          scope={scope}
        />
      ) : null}
      {job ? <Tabs tabs={tabs} active={activeTab} onChange={setActiveTab} /> : null}
      {activeTab === "Build" ? (
        job ? (
        <>
          {error ? <div className="error-panel">{error}</div> : null}
          <BuildRunHeader job={job} />
          <BuildMetricStrip metrics={buildMetricsForDisplay(metrics, progress, scope)} />
          {missing.length ? (
            <InlineNotice tone="warning" icon={<AlertTriangle size={16} />} title="Missing raw market sessions">
              <span>
                {missing.map((row) => String(row.session_date)).slice(0, 16).join(", ")}
                {missing.length > 16 ? " ..." : ""}
              </span>
            </InlineNotice>
          ) : null}
          {metrics && Number(metrics.output_sessions ?? 0) === 0 ? (
            <InlineNotice tone="warning" icon={<AlertTriangle size={16} />} title="Reference-only build range">
              <span>This range does not contain an output session after the {metrics.warmup_sessions ?? 13} trading-session warm-up window, so no artifacts will be written.</span>
            </InlineNotice>
          ) : null}
          {metrics && Number(metrics.missing_reference_sessions ?? 0) > 0 ? (
            <InlineNotice tone="warning" icon={<AlertTriangle size={16} />} title="Incomplete warm-up context">
              <span>{metrics.missing_reference_sessions} reference session raw file(s) are missing, so carry-over indicators may have a shorter warm-up.</span>
            </InlineNotice>
          ) : null}
          <PhasePanel elapsedSec={metrics?.elapsed_sec ?? 0} phases={progress?.phases ?? []} status={metrics?.status ?? job?.status} />
          {job?.status === "failed" ? <div className="error-panel" style={{ marginTop: 18 }}>{job.error ?? "Build failed."}</div> : null}
        </>
        ) : null
      ) : null}
      {job && activeTab === "Build Timings" ? (
        <BuildTablePanel trigger={`timings:${job?.job_id ?? ""}:${progress?.phase_events?.length ?? 0}`}>
          <DataTable rows={progress?.phase_events ?? []} />
        </BuildTablePanel>
      ) : null}
      {job && activeTab === "Artifacts" ? (
        <BuildTablePanel trigger={`artifacts:${job?.job_id ?? ""}:${progress?.artifact_events?.length ?? 0}`}>
          <DataTable rows={progress?.artifact_events ?? []} />
        </BuildTablePanel>
      ) : null}
      {job && activeTab === "Plan" ? (
        <BuildTablePanel trigger={`plan:${job?.job_id ?? ""}:${progress?.plan?.length ?? 0}`}>
          <DataTable
            rows={progress?.plan ?? []}
            columns={["session_date", "build_role", "status", "exists", "write_output", "reference_only", "reason", "path", "size_bytes", "modified_at"]}
          />
        </BuildTablePanel>
      ) : null}
      {job && activeTab === "Processed Store" ? <ProcessedStore scope={viewScope} /> : null}
      {job && activeTab === "Manifest" ? <ManifestCard scope={viewScope} job={job} /> : null}
      {editingScope && draft ? (
        <Modal title="Update Data Scope" onClose={() => setEditingScope(false)}>
          <div className="form-grid">
            <Field label="Raw root" value={draft.raw_root} onChange={(value) => setDraft({ ...draft, raw_root: value })} />
            <Field label="Processed root" value={draft.processed_root} onChange={(value) => setDraft({ ...draft, processed_root: value })} />
            <Field label="Start" type="date" value={draft.start_date} onChange={(value) => setDraft({ ...draft, start_date: value })} />
            <Field label="End" type="date" value={draft.end_date} onChange={(value) => setDraft({ ...draft, end_date: value })} />
          </div>
          <div className="modal-actions">
            <button className="button" onClick={() => setEditingScope(false)} type="button">Cancel</button>
            <button className="button primary" onClick={applyScope} type="button">Apply</button>
          </div>
        </Modal>
      ) : null}
      {deleteTarget ? (
        <Modal title="Delete Build" onClose={() => setDeleteTarget(null)}>
          <div className="delete-confirmation">
            <p>
              Delete <b>{buildDisplayName(deleteTarget)}</b> from the build list?
            </p>
            <p className="muted">
              This only removes the build job record and logs. Market data artifacts in the processed store remain on disk.
            </p>
          </div>
          <div className="modal-actions">
            <button className="button" onClick={() => setDeleteTarget(null)} type="button">Cancel</button>
            <button className="button danger" onClick={deleteBuild} type="button">Delete</button>
          </div>
        </Modal>
      ) : null}
    </>
  );
}

function BuildStartPage({
  deleteResult,
  error,
  jobs,
  onEditScope,
  onDeleteRequest,
  onOpenJob,
  onStartBuild,
  scope,
}: {
  deleteResult: string | null;
  error: string | null;
  jobs: BuildJob[];
  onEditScope: () => void;
  onDeleteRequest: (job: BuildJob) => void;
  onOpenJob: (jobId: string) => void;
  onStartBuild: () => void;
  scope: Scope | null;
}) {
  return (
    <section className="panel">
      <div className="section-heading-row">
        <div>
          <h2>Build Runs</h2>
          <p>Start a provider build or open an earlier build record. The market-data artifacts stay integrated in the shared processed store.</p>
        </div>
        <div className="button-row">
          <button className="button primary" disabled={!scope} onClick={onStartBuild} type="button">
            New build
          </button>
          <button className="button" disabled={!scope} onClick={onEditScope} type="button">
            Edit scope
          </button>
        </div>
      </div>
      {error ? <div className="error-panel">{error}</div> : null}
      {deleteResult ? <InlineNotice tone="success" icon={<CheckCircle2 size={16} />} title="Build deleted">{deleteResult}</InlineNotice> : null}
      {jobs.length ? (
        <div className="runs-grid">
          {jobs.map((item) => (
            <article
              className="run-card build-run-card clickable"
              key={item.job_id}
              onClick={() => onOpenJob(item.job_id)}
              onKeyDown={(event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                onOpenJob(item.job_id);
              }}
              role="button"
              tabIndex={0}
              title="Open build"
            >
              <div className="run-card-header">
                <div>
                  <div className="run-card-title">{buildDisplayName(item)}</div>
                  <div className="muted">{buildDateRange(item)} | {formatTimestamp(item.created_at)}</div>
                </div>
                <div className="toolbar" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()} style={{ margin: 0 }}>
                  <SemanticBadge tone={toneForStatus(item.status)}>{item.status}</SemanticBadge>
                  <button
                    className="icon-button"
                    disabled={activeBuildStatuses.has(String(item.status).toLowerCase())}
                    onClick={() => onDeleteRequest(item)}
                    title="Delete build"
                    type="button"
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
              <div className="run-card-metrics">
                <span>{formatNumber(item.summary?.artifact_count ?? 0)} artifacts</span>
                <span>{formatNumber(item.summary?.rows_written ?? 0)} rows</span>
                <span>{formatBytes(item.summary?.bytes_written ?? 0)}</span>
                <span>{formatDuration(item.summary?.duration_sec ?? 0)}</span>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="empty-state">No build runs have been recorded for this processed store.</div>
      )}
    </section>
  );
}

function BuildRunHeader({ job }: { job: BuildJob }) {
  const request = job.request ?? {};
  const metrics = job.progress?.metrics;
  return (
    <section className="panel build-summary-panel">
      <div className="build-summary-main">
        <div>
          <h2>{buildDisplayName(job)}</h2>
          <p title={String(request.processed_root ?? "-")}>{buildDateRange(job)} | {String(request.processed_root ?? "-")}</p>
        </div>
        <SemanticBadge tone={toneForStatus(job.status)}>{job.status}</SemanticBadge>
      </div>
      <div className="build-summary-facts">
        <BuildFact label="Build id" value={job.job_id} />
        <BuildFact label="Created" value={formatTimestamp(job.created_at)} />
        <BuildFact label="Session workers" value={`${job.resources?.session_workers ?? job.resources?.bar_workers ?? job.resources?.max_workers ?? "-"} workers`} />
        <BuildFact label="Polars" value={`${job.resources?.polars_threads ?? "-"} threads`} />
        <BuildFact label="Output starts" value={String(metrics?.output_start_date ?? "-")} />
        <BuildFact label="Warm-up" value={`${formatNumber(metrics?.warmup_sessions ?? 13)} sessions`} />
        <BuildFact label="Carry-over" value={asListText(metrics?.carryover_timeframes ?? ["1m", "5m", "15m", "30m"])} />
      </div>
    </section>
  );
}

function BuildFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="build-fact" title={value}>
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}

function BuildMetricStrip({ metrics }: { metrics: BuildMetric[] }) {
  return (
    <div aria-label="Build metrics" className="build-metric-strip" role="list">
      {metrics.map((metric) => (
        <article className="build-metric-card" data-tone={metric.tone ?? "neutral"} key={metric.label} role="listitem" title={metric.detail}>
          <div className="build-metric-icon">{metric.icon}</div>
          <span className="build-metric-label">{metric.label}</span>
          <strong className="build-metric-value">{metric.value}</strong>
        </article>
      ))}
    </div>
  );
}

function buildMetricsForDisplay(metrics: BuildProgress["metrics"] | undefined, progress: BuildProgress | undefined, scope: Scope | null): BuildMetric[] {
  const status = String(metrics?.status ?? "ready");
  return [
    {
      label: "Output raw files",
      value: formatNumber(metrics?.raw ?? scope?.raw_file_count ?? 0),
      detail: "Raw files that will produce output artifacts.",
      icon: <FolderInput size={16} />,
      tone: "info",
    },
    {
      label: "Reference files",
      value: formatNumber(metrics?.reference_sessions ?? 0),
      detail: "Warm-up files used as carry-over context.",
      icon: <Archive size={16} />,
    },
    {
      label: "Missing raw files",
      value: formatNumber(metrics?.missing ?? 0),
      detail: "Expected market-session raw files that were not found.",
      icon: <AlertTriangle size={16} />,
      tone: Number(metrics?.missing ?? 0) > 0 ? "warning" : "neutral",
    },
    {
      label: "Artifacts written",
      value: formatNumber(progress?.artifact_events?.length ?? 0),
      detail: "Bar and feature artifacts written by this build.",
      icon: <FileStack size={16} />,
      tone: "success",
    },
    {
      label: "Rows written",
      value: formatNumber(metrics?.rows ?? 0),
      detail: "Total rows written across completed artifacts.",
      icon: <Rows3 size={16} />,
    },
    {
      label: "Data written",
      value: formatBytes(metrics?.written_bytes ?? 0),
      detail: "Total bytes written to the processed store.",
      icon: <HardDrive size={16} />,
    },
    {
      label: "Elapsed time",
      value: formatDuration(metrics?.elapsed_sec ?? 0),
      detail: "Wall-clock time reported by the build job.",
      icon: <Clock3 size={16} />,
    },
    {
      label: "Build status",
      value: status,
      detail: "Current lifecycle status of this build.",
      icon: <Activity size={16} />,
      tone: statusTone(status),
    },
  ];
}

function ScopeCard({ scope }: { scope: Scope }) {
  return (
    <div className="scope-card">
      <div className="scope-card-header">
        <div className="scope-title">Data Scope</div>
        <span className="force-badge">
          <CheckCircle2 size={12} /> Force rebuild
        </span>
      </div>
      <div className="scope-card-grid">
        <div>
          <ScopeItem label="Start" value={scope.start_date} />
          <ScopeItem label="End" value={scope.end_date} />
        </div>
        <div>
          <ScopeItem label="Raw root" value={scope.raw_root} />
          <ScopeItem label="Processed root" value={scope.processed_root} />
        </div>
      </div>
    </div>
  );
}

function ScopeItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="scope-item">
      <span>{label}</span>
      <b title={value}>{value}</b>
    </div>
  );
}

function Field({ label, value, onChange, type = "text" }: { label: string; value: string; onChange: (value: string) => void; type?: string }) {
  return (
    <div className="field">
      <label>{label}</label>
      <input type={type} value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

function PhasePanel({ elapsedSec, phases, status }: { elapsedSec: number; phases: Stage[]; status?: string }) {
  const done = phases.reduce((total, phase) => total + Number(phase.done || 0), 0);
  const total = phases.reduce((sum, phase) => sum + Number(phase.total || 0), 0);
  const progress = total > 0 ? (done / total) * 100 : 0;
  return (
    <section className="panel phase-panel">
      <h2>Build Progress</h2>
      {phases.length ? (
        <>
          <ProgressMeter done={done} elapsed_sec={elapsedSec} label="Total build progress" progress={progress} status={status} total={total} />
          <div className="build-step-grid">
            {phases.map((phase) => (
              <BuildStepCard key={phase.phase ?? phase.label} stage={phase} />
            ))}
          </div>
        </>
      ) : (
        <div className="empty-state">Progress will appear after the build starts.</div>
      )}
    </section>
  );
}

function BuildStepCard({ stage }: { stage: Stage }) {
  const description = buildStageDescription(stage.phase);
  const isSkipped = Number(stage.total || 0) === 0;
  const active = stage.active_items ?? [];
  const activeCount = Number(stage.active_count ?? active.length);
  const mode = buildStageMode(stage.phase, activeCount, isSkipped, stage.progress);
  return (
    <article className="build-step-card" data-status={isSkipped ? "skipped" : progressStatus(stage.progress)}>
      <header className="build-step-card-header">
        <div>
          <div className="build-step-title-row">
            <h3>{stage.label}</h3>
            <span className="build-step-mode">{mode}</span>
          </div>
          <p>{description}</p>
        </div>
        <span className="build-step-percent">{isSkipped ? "Skipped" : `${Math.round(stage.progress)}%`}</span>
      </header>
      <ProgressMeter
        ariaLabel={`${stage.label} progress`}
        done={stage.done}
        elapsed_sec={stage.elapsed_sec}
        label=""
        progress={stage.progress}
        showLabel={false}
        status={isSkipped ? "queued" : progressStatus(stage.progress)}
        total={stage.total}
      />
      <div className="build-step-meta">
        <span>{formatNumber(stage.done, stage.done % 1 ? 1 : 0)} / {formatNumber(stage.total)} {stage.unit_label ?? "total"}</span>
        <span>{formatDuration(stage.elapsed_sec)} elapsed</span>
      </div>
      <div className="build-step-active">
        <span>Processing</span>
        {active.length ? (
          <div className="build-step-active-list">
            {active.map((item, index) => (
              <span className="build-step-file" key={`${item.label ?? "item"}-${index}`}>
                <span>{item.label ?? "processing"}</span>
                {item.detail ? <span className="build-step-file-detail">{String(item.detail)}</span> : null}
              </span>
            ))}
          </div>
        ) : (
          <div className="build-step-active-empty">{isSkipped ? "Not requested" : stage.progress >= 100 ? "Completed" : "Waiting"}</div>
        )}
      </div>
    </article>
  );
}

function buildStageDescription(phase: string | undefined): string {
  switch (phase) {
    case "scan_source":
      return "Checks raw files and classifies each market session.";
    case "reference_window":
      return "Marks warm-up sessions used only for carry-over context.";
    case "build_bars":
      return "Loads raw data, normalizes 1m bars, aggregates timeframes, and writes bar files.";
    case "build_features":
      return "Calculates feature artifacts that only need the current session table.";
    case "build_stateful":
      return "Calculates carry-over features after parallel session workers have drained.";
    case "finalize":
      return "Records final status and closes the build run.";
    default:
      return "Build pipeline step.";
  }
}

function buildStageMode(phase: string | undefined, activeCount: number, skipped: boolean, progress: number): string {
  if (skipped) return "Skipped";
  if (activeCount > 0) return `${formatNumber(activeCount)} Concurrent`;
  if (progress >= 100) return "Complete";
  if (phase === "build_bars" || phase === "build_features" || phase === "build_stateful") {
    return "Waiting";
  }
  if (phase === "reference_window") return "Reference";
  return "Sequential";
}

function progressStatus(progress: number): string {
  if (progress >= 100) return "complete";
  if (progress <= 0) return "queued";
  return "running";
}

function statusTone(status: string): BuildMetric["tone"] {
  const normalized = status.toLowerCase();
  if (["failed", "error", "canceled", "cancelled"].some((value) => normalized.includes(value))) return "danger";
  if (["running", "queued", "canceling"].some((value) => normalized.includes(value))) return "warning";
  if (["complete", "ready"].some((value) => normalized.includes(value))) return "success";
  return "neutral";
}

function BuildTablePanel({ children, trigger }: { children: ReactNode; trigger: unknown }) {
  const fillPanel = useViewportFillPanel<HTMLElement>(trigger);
  return (
    <section className="panel table-fill-panel" ref={fillPanel.ref} style={fillPanel.style}>
      {children}
    </section>
  );
}

function ProcessedStore({ scope }: { scope: Scope | null }) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const fillPanel = useViewportFillPanel<HTMLElement>(`${scope?.processed_root ?? ""}:${rows.length}`);

  useEffect(() => {
    if (!scope) return;
    api<{ records: Record<string, unknown>[] }>(`/api/market-data/review${query({ processed_root: scope.processed_root, start_date: scope.start_date, end_date: scope.end_date })}`).then((payload) =>
      setRows(payload.records)
    );
  }, [scope]);
  return (
    <section className="panel table-fill-panel" ref={fillPanel.ref} style={fillPanel.style}>
      <DataTable rows={rows} columns={["group", "timeframe", "session_date", "rows", "column_count", "size", "built_at", "build_name", "exists", "path"]} />
    </section>
  );
}

function ManifestCard({ job, scope }: { job: BuildJob | null; scope: Scope | null }) {
  const [card, setCard] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    if (!scope) {
      setCard(null);
      return;
    }
    setCard(null);
    api<{ card: Record<string, unknown> }>(`/api/market-data/manifest${query({ processed_root: scope.processed_root })}`).then((payload) => setCard(payload.card));
  }, [scope]);
  if (!card) return <div className="empty-state">No manifest loaded.</div>;
  return (
    <div className="panel">
      <h2>Manifest</h2>
      <div className="split-row">
        <div>
          <ScopeItem label="Updated" value={String(card.updated_at ?? "-")} />
          <ScopeItem label="Artifacts" value={formatNumber(card.artifact_count)} />
          <ScopeItem label="Schema version" value={String(card.schema_version ?? "-")} />
        </div>
        <div>
          <ScopeItem label="Feature version" value={String(card.feature_version ?? "-")} />
          <ScopeItem label="Supervision version" value={String(card.supervision_version ?? "-")} />
          <ScopeItem label="Processed root" value={String(card.processed_root ?? "-")} />
        </div>
      </div>
      {job ? (
        <div className="split-row" style={{ marginTop: 18 }}>
          <div>
            <ScopeItem label="Selected build" value={buildDisplayName(job)} />
            <ScopeItem label="Build id" value={job.job_id} />
          </div>
          <div>
            <ScopeItem label="Build artifacts" value={formatNumber(job.summary?.artifact_count ?? 0)} />
            <ScopeItem label="Build rows" value={formatNumber(job.summary?.rows_written ?? 0)} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function buildDisplayName(job: BuildJob): string {
  const request = job.request ?? {};
  const value = job.build_name ?? request.build_name ?? job.job_id;
  return String(value || job.job_id);
}

function buildDateRange(job: BuildJob): string {
  const request = job.request ?? {};
  const start = request.start_date ? String(request.start_date) : "-";
  const end = request.end_date ? String(request.end_date) : "-";
  return `${start} to ${end}`;
}

function formatTimestamp(value: unknown): string {
  if (!value) return "-";
  const parsed = new Date(String(value));
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function asListText(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.map(String).join(", ") : "-";
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

function deleteResultText(result: DeleteBuildResponse): string {
  return result.orphaned_job
    ? "Orphaned build record removed. Market data artifacts were left on disk."
    : "Build record removed. Market data artifacts were left on disk.";
}

function scopeFromBuildJob(job: BuildJob | null, fallback: Scope | null): Scope | null {
  if (!job?.request) return fallback;
  const request = job.request;
  return {
    raw_root: String(request.raw_root ?? fallback?.raw_root ?? ""),
    processed_root: String(request.processed_root ?? fallback?.processed_root ?? ""),
    start_date: String(request.start_date ?? fallback?.start_date ?? ""),
    end_date: String(request.end_date ?? fallback?.end_date ?? ""),
    raw_file_count: Number(fallback?.raw_file_count ?? 0),
    artifact_count: Number(fallback?.artifact_count ?? 0),
  };
}
