import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { api, query } from "../api/client";
import { DataTable } from "../app/components/DataTable";
import { InlineNotice } from "../app/components/InlineNotice";
import { MetricStrip } from "../app/components/MetricStrip";
import { Modal } from "../app/components/Modal";
import { PageIntro } from "../app/components/PageIntro";
import { ProgressMeter, SessionProgressColumn, type SessionCard, StageRow, type Stage } from "../app/components/Progress";
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
    status: string;
  };
  phases: Stage[];
  active_sessions: SessionCard[];
  completed_sessions: SessionCard[];
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
    rows_written?: number;
  };
  progress?: BuildProgress;
  result?: Record<string, unknown>;
  started_at?: string | null;
  updated_at?: string;
  error?: string;
  traceback?: string;
};

const tabs = ["Build", "Build Timings", "Artifacts", "Plan", "Processed Store", "Manifest"];

export function MarketDataBuildPage() {
  const [scope, setScope] = useState<Scope | null>(null);
  const [draft, setDraft] = useState<Scope | null>(null);
  const [job, setJob] = useState<BuildJob | null>(null);
  const [jobs, setJobs] = useState<BuildJob[]>([]);
  const [activeTab, setActiveTab] = useState(tabs[0]);
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
    if (!scope || !jobs.some((item) => ["queued", "running", "canceling"].includes(item.status))) return;
    const timer = window.setInterval(() => loadJobs(scope), 3000);
    return () => window.clearInterval(timer);
  }, [scope, jobs]);

  useEffect(() => {
    if (!scope || !job || !["queued", "running", "canceling"].includes(job.status)) return;
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
        body: JSON.stringify({ ...scope, max_workers: 4, polars_threads: 6 })
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
    await api(`/api/market-data/build/jobs/${job.job_id}/cancel${query({ processed_root: scope.processed_root })}`, { method: "POST" });
    await loadJob(scope, job.job_id);
    await loadJobs(scope);
  }

  function applyScope() {
    if (!draft) return;
    setScope(draft);
    setEditingScope(false);
    setJob(null);
    setJobs([]);
  }

  const running = Boolean(job && ["queued", "running", "canceling"].includes(job.status));
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
          <button className="button" onClick={() => setEditingScope(true)} type="button">
            Edit scope
          </button>
        </div>
      ) : null}
      {!job ? (
        <BuildStartPage
          error={error}
          jobs={jobs}
          onEditScope={() => setEditingScope(true)}
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
          {metrics ? (
            <MetricStrip
              items={[
                { label: "Raw", value: metrics.raw, kind: "number" },
                { label: "Exp", value: metrics.expected, kind: "number" },
                { label: "Miss", value: metrics.missing, kind: "number" },
                { label: "Closed", value: metrics.closed, kind: "number" },
                { label: "Rows", value: metrics.rows, kind: "number" },
                { label: "Written", value: metrics.written_bytes, kind: "bytes" },
                { label: "Elapsed", value: metrics.elapsed_sec, kind: "duration" },
                { label: "Status", value: metrics.status, kind: "status" }
              ]}
            />
          ) : (
            <MetricStrip
              items={[
                { label: "Raw", value: scope?.raw_file_count ?? 0, kind: "number" },
                { label: "Exp", value: "-", kind: "status" },
                { label: "Miss", value: "-", kind: "status" },
                { label: "Closed", value: "-", kind: "status" },
                { label: "Rows", value: "-", kind: "status" },
                { label: "Written", value: "-", kind: "status" },
                { label: "Elapsed", value: "-", kind: "status" },
                { label: "Status", value: "ready", kind: "status" }
              ]}
            />
          )}
          {missing.length ? (
            <InlineNotice tone="warning" icon={<AlertTriangle size={16} />} title="Missing raw market sessions">
              <span>
                {missing.map((row) => String(row.session_date)).slice(0, 16).join(", ")}
                {missing.length > 16 ? " ..." : ""}
              </span>
            </InlineNotice>
          ) : null}
          <PhasePanel elapsedSec={metrics?.elapsed_sec ?? 0} phases={progress?.phases ?? []} status={metrics?.status ?? job?.status} />
          <div className="build-board">
            <SessionProgressColumn title="Active Queue" cards={progress?.active_sessions ?? []} />
            <SessionProgressColumn title="Completed Files" cards={progress?.completed_sessions ?? []} />
          </div>
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
          <DataTable rows={progress?.plan ?? []} />
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
    </>
  );
}

function BuildStartPage({
  error,
  jobs,
  onEditScope,
  onOpenJob,
  onStartBuild,
  scope,
}: {
  error: string | null;
  jobs: BuildJob[];
  onEditScope: () => void;
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
      {jobs.length ? (
        <div className="runs-grid">
          {jobs.map((item) => (
            <button className="run-card build-run-card clickable" key={item.job_id} onClick={() => onOpenJob(item.job_id)} type="button">
              <div className="run-card-header">
                <div>
                  <div className="run-card-title">{buildDisplayName(item)}</div>
                  <div className="muted">{buildDateRange(item)} | {formatTimestamp(item.created_at)}</div>
                </div>
                <SemanticBadge tone={toneForStatus(item.status)}>{item.status}</SemanticBadge>
              </div>
              <div className="run-card-metrics">
                <span>{formatNumber(item.summary?.artifact_count ?? 0)} artifacts</span>
                <span>{formatNumber(item.summary?.rows_written ?? 0)} rows</span>
                <span>{formatBytes(item.summary?.bytes_written ?? 0)}</span>
                <span>{formatDuration(item.summary?.duration_sec ?? 0)}</span>
              </div>
            </button>
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
  return (
    <section className="panel">
      <div className="section-heading-row">
        <div>
          <h2>{buildDisplayName(job)}</h2>
          <p>{buildDateRange(job)} | {String(request.processed_root ?? "-")}</p>
        </div>
        <SemanticBadge tone={toneForStatus(job.status)}>{job.status}</SemanticBadge>
      </div>
      <div className="split-row">
        <div>
          <ScopeItem label="Build id" value={job.job_id} />
          <ScopeItem label="Created" value={formatTimestamp(job.created_at)} />
          <ScopeItem label="Started" value={formatTimestamp(job.started_at)} />
          <ScopeItem label="Finished" value={formatTimestamp(job.finished_at)} />
        </div>
        <div>
          <ScopeItem label="Raw root" value={String(request.raw_root ?? "-")} />
          <ScopeItem label="Timeframes" value={asListText(request.timeframes)} />
          <ScopeItem label="Feature groups" value={asListText(request.feature_groups)} />
          <ScopeItem label="Resources" value={`workers=${job.resources?.max_workers ?? "-"}, polars=${job.resources?.polars_threads ?? "-"}`} />
        </div>
      </div>
    </section>
  );
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
          <div className="phase-grid">
            {phases.map((phase) => (
              <StageRow key={phase.label} stage={phase} />
            ))}
          </div>
        </>
      ) : (
        <div className="empty-state">Progress will appear after the build starts.</div>
      )}
    </section>
  );
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
