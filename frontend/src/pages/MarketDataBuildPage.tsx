import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api, query } from "../api/client";
import { DataTable } from "../app/components/DataTable";
import { InlineNotice } from "../app/components/InlineNotice";
import { MetricStrip } from "../app/components/MetricStrip";
import { Modal } from "../app/components/Modal";
import { PageIntro } from "../app/components/PageIntro";
import { ProgressMeter, SessionProgressColumn, type SessionCard, StageRow, type Stage } from "../app/components/Progress";
import { Tabs } from "../app/components/Tabs";
import { formatNumber } from "../app/format";

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
  job_id: string;
  status: string;
  request?: Record<string, unknown>;
  progress?: BuildProgress;
  error?: string;
  traceback?: string;
};

const tabs = ["Build", "Build Timings", "Artifacts", "Plan", "Processed Store", "Manifest"];

export function MarketDataBuildPage() {
  const [scope, setScope] = useState<Scope | null>(null);
  const [draft, setDraft] = useState<Scope | null>(null);
  const [job, setJob] = useState<BuildJob | null>(null);
  const [activeTab, setActiveTab] = useState(tabs[0]);
  const [editingScope, setEditingScope] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadScope();
  }, []);

  useEffect(() => {
    if (!scope) return;
    loadLatestJob(scope);
  }, [scope]);

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

  async function loadLatestJob(currentScope: Scope) {
    const payload = await api<{ jobs: BuildJob[] }>(`/api/market-data/build/jobs${query({ processed_root: currentScope.processed_root })}`);
    const latest = payload.jobs.find((item) => ["queued", "running", "canceling"].includes(item.status)) ?? payload.jobs[0];
    if (latest) {
      await loadJob(currentScope, latest.job_id);
    }
  }

  async function loadJob(currentScope: Scope, jobId: string) {
    const payload = await api<BuildJob>(
      `/api/market-data/build/jobs/${jobId}${query({ processed_root: currentScope.processed_root, raw_root: currentScope.raw_root })}`
    );
    setJob(payload);
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
      await loadJob(scope, payload.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function stopBuild() {
    if (!scope || !job) return;
    await api(`/api/market-data/build/jobs/${job.job_id}/cancel${query({ processed_root: scope.processed_root })}`, { method: "POST" });
    await loadJob(scope, job.job_id);
  }

  function applyScope() {
    if (!draft) return;
    setScope(draft);
    setEditingScope(false);
    setJob(null);
  }

  const running = Boolean(job && ["queued", "running", "canceling"].includes(job.status));
  const progress = job?.progress;
  const metrics = progress?.metrics;
  const missing = useMemo(() => (progress?.plan ?? []).filter((row) => row.expected_market_session && !row.exists), [progress?.plan]);

  return (
    <>
      <PageIntro
        groupLabel="Market Data"
        title="Build Data"
        description="Rebuild the canonical market-data store with every supported timeframe, feature group, and supervision label."
        actions={scope ? <ScopeCard scope={scope} /> : null}
      />
      <Tabs tabs={tabs} active={activeTab} onChange={setActiveTab} />
      {activeTab === "Build" ? (
        <>
          <div className="button-row">
            <button className={running ? "button danger" : "button primary"} onClick={running ? stopBuild : startBuild} type="button">
              {running ? "Stop build" : "Rebuild selected range"}
            </button>
            <button className="button" onClick={() => setEditingScope(true)} type="button">
              Edit scope
            </button>
          </div>
          {error ? <div className="error-panel">{error}</div> : null}
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
      ) : null}
      {activeTab === "Build Timings" ? <DataTable rows={progress?.phase_events ?? []} /> : null}
      {activeTab === "Artifacts" ? <DataTable rows={progress?.artifact_events ?? []} /> : null}
      {activeTab === "Plan" ? <DataTable rows={progress?.plan ?? []} /> : null}
      {activeTab === "Processed Store" ? <ProcessedStore scope={scope} /> : null}
      {activeTab === "Manifest" ? <ManifestCard scope={scope} /> : null}
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

function ProcessedStore({ scope }: { scope: Scope | null }) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  useEffect(() => {
    if (!scope) return;
    api<{ records: Record<string, unknown>[] }>(`/api/market-data/review${query({ processed_root: scope.processed_root, start_date: scope.start_date, end_date: scope.end_date })}`).then((payload) =>
      setRows(payload.records)
    );
  }, [scope]);
  return <DataTable rows={rows} columns={["group", "timeframe", "session_date", "rows", "column_count", "size", "built_at", "exists", "path"]} />;
}

function ManifestCard({ scope }: { scope: Scope | null }) {
  const [card, setCard] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    if (!scope) return;
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
    </div>
  );
}
