import { Play, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";

import { api, query } from "../api/client";
import { DataTable } from "../app/components/DataTable";
import { MetricStrip } from "../app/components/MetricStrip";
import { Modal } from "../app/components/Modal";
import { PageIntro } from "../app/components/PageIntro";
import { SemanticBadge, toneForStatus } from "../app/components/SemanticBadge";
import { Tabs } from "../app/components/Tabs";
import { formatMoney, formatPct } from "../app/format";

type Strategy = {
  name: string;
  display_name: string;
  description: string;
};

type RunRow = {
  run_id: string;
  run_name: string;
  strategy_name: string;
  status: string;
  created_at: string;
  date_range: string;
  return_pct: number;
  total_pnl: number;
  trade_count: number;
};

type StrategyConfig = {
  strategy_name: string;
  run_name: string;
  start_date: string;
  end_date: string;
  data_root: string;
  processed_data_root: string;
  output_root: string;
  initial_cash: number;
  slippage_bps: number;
  save_symbol_bars: boolean;
  strategy_params: Record<string, number | string | boolean>;
};

const tabs = ["Runs", "New Run", "Strategy README"];
const strategyName = "orb_5m_momentum";

export function StrategyPage() {
  const [strategy, setStrategy] = useState<Strategy | null>(null);
  const [activeTab, setActiveTab] = useState(tabs[0]);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [readme, setReadme] = useState("");
  const [config, setConfig] = useState<StrategyConfig | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);

  useEffect(() => {
    api<{ strategies: Strategy[] }>("/api/strategies").then((payload) => setStrategy(payload.strategies.find((item) => item.name === strategyName) ?? payload.strategies[0]));
    api<{ content: string }>(`/api/strategies/${strategyName}/readme`).then((payload) => setReadme(payload.content));
    api<StrategyConfig>(`/api/strategies/${strategyName}/default-config`).then(setConfig);
  }, []);

  useEffect(() => {
    if (!config) return;
    loadRuns(config.output_root);
  }, [config?.output_root]);

  async function loadRuns(outputRoot: string) {
    const payload = await api<{ runs: RunRow[] }>(`/api/backtests/runs${query({ output_root: outputRoot, strategy_name: strategyName })}`);
    setRuns(payload.runs);
  }

  return (
    <>
      <PageIntro
        groupLabel="Strategies"
        title={strategy?.display_name ?? "ORB 5M Momentum"}
        description={strategy?.description ?? "Opening range momentum research strategy."}
      />
      <Tabs tabs={tabs} active={activeTab} onChange={setActiveTab} />
      {activeTab === "Runs" && config ? <RunsPanel runs={runs} outputRoot={config.output_root} onOpen={setSelectedRun} onDeleted={() => loadRuns(config.output_root)} /> : null}
      {activeTab === "New Run" && config ? <NewRunPanel config={config} onConfigChange={setConfig} onComplete={() => loadRuns(config.output_root)} /> : null}
      {activeTab === "Strategy README" ? (
        <div className="markdown-panel">
          <ReactMarkdown>{readme}</ReactMarkdown>
        </div>
      ) : null}
      {selectedRun && config ? <RunDetail runId={selectedRun} outputRoot={config.output_root} onClose={() => setSelectedRun(null)} /> : null}
    </>
  );
}

function RunsPanel({
  runs,
  outputRoot,
  onOpen,
  onDeleted
}: {
  runs: RunRow[];
  outputRoot: string;
  onOpen: (runId: string) => void;
  onDeleted: () => void;
}) {
  async function deleteRun(runId: string) {
    await api(`/api/backtests/runs/${runId}${query({ output_root: outputRoot })}`, { method: "DELETE" });
    onDeleted();
  }
  if (!runs.length) return <div className="empty-state panel">No app-created runs exist for this strategy yet.</div>;
  return (
    <div className="progress-column-body">
      {runs.map((run) => (
        <article className="run-card" key={run.run_id}>
          <div>
            <div className="run-card-title">{run.run_name}</div>
            <div className="muted">{run.date_range} | {run.created_at}</div>
            <div className="toolbar" style={{ marginBottom: 0 }}>
              <SemanticBadge tone={toneForStatus(run.status)}>{run.status}</SemanticBadge>
              <span className="meta-tag">{formatPct(run.return_pct)}</span>
              <span className="meta-tag">{formatMoney(run.total_pnl)}</span>
              <span className="meta-tag">{run.trade_count} trades</span>
            </div>
          </div>
          <div className="toolbar" style={{ margin: 0 }}>
            <button className="button primary" onClick={() => onOpen(run.run_id)} type="button">Open</button>
            <button className="icon-button" onClick={() => deleteRun(run.run_id)} type="button" title="Delete run"><Trash2 size={15} /></button>
          </div>
        </article>
      ))}
    </div>
  );
}

function NewRunPanel({
  config,
  onConfigChange,
  onComplete
}: {
  config: StrategyConfig;
  onConfigChange: (config: StrategyConfig) => void;
  onComplete: () => void;
}) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const params = config.strategy_params;

  useEffect(() => {
    if (!jobId || !["running", "queued"].includes(String(job?.status ?? "running"))) return;
    const timer = window.setInterval(() => {
      api<Record<string, unknown>>(`/api/backtests/jobs/${jobId}${query({ output_root: config.output_root })}`).then((payload) => {
        setJob(payload);
        if (String(payload.status) === "complete") onComplete();
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [jobId, job?.status, config.output_root]);

  async function startRun() {
    setError(null);
    try {
      const payload = await api<Record<string, unknown>>("/api/backtests/jobs", { method: "POST", body: JSON.stringify(config) });
      setJob(payload);
      setJobId(String(payload.job_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <section>
      <MetricStrip
        items={[
          { label: "Initial Cash", value: config.initial_cash, kind: "number" },
          { label: "Max Positions", value: Number(params.max_active_positions ?? 0), kind: "number" },
          { label: "Watchlist", value: Number(params.watchlist_size ?? 0), kind: "number" },
          { label: "Min Setup", value: Number(params.min_setup_score ?? 0), kind: "number" },
          { label: "Min Live", value: Number(params.min_live_score ?? 0), kind: "number" },
          { label: "Status", value: String(job?.status ?? "draft"), kind: "status" },
          { label: "Sessions", value: Array.isArray(job?.events) ? job.events.length : 0, kind: "number" },
          { label: "Slippage", value: config.slippage_bps, kind: "number" }
        ]}
      />
      <div className="panel">
        <div className="form-grid">
          <Field label="Run name" value={config.run_name} onChange={(value) => onConfigChange({ ...config, run_name: value })} />
          <Field label="Output root" value={config.output_root} onChange={(value) => onConfigChange({ ...config, output_root: value })} />
          <Field label="Start" type="date" value={config.start_date} onChange={(value) => onConfigChange({ ...config, start_date: value })} />
          <Field label="End" type="date" value={config.end_date} onChange={(value) => onConfigChange({ ...config, end_date: value })} />
          <Field label="Data root" value={config.data_root} onChange={(value) => onConfigChange({ ...config, data_root: value })} />
          <Field label="Processed data root" value={config.processed_data_root} onChange={(value) => onConfigChange({ ...config, processed_data_root: value })} />
          <NumberField label="Initial cash" value={config.initial_cash} onChange={(value) => onConfigChange({ ...config, initial_cash: value })} />
          <NumberField label="Slippage bps" value={config.slippage_bps} onChange={(value) => onConfigChange({ ...config, slippage_bps: value })} />
        </div>
        <h2 style={{ marginTop: 20 }}>Strategy Parameters</h2>
        <div className="form-grid">
          {Object.entries(params).map(([key, value]) =>
            typeof value === "boolean" ? null : (
              <NumberField
                key={key}
                label={key.replaceAll("_", " ")}
                value={Number(value)}
                onChange={(next) => onConfigChange({ ...config, strategy_params: { ...params, [key]: next } })}
              />
            )
          )}
        </div>
        <div className="modal-actions">
          <button className="button primary" onClick={startRun} type="button" disabled={String(job?.status ?? "") === "running"}>
            <Play size={15} /> Start Backtest
          </button>
        </div>
      </div>
      {error ? <div className="error-panel" style={{ marginTop: 12 }}>{error}</div> : null}
      {job ? <BacktestJobPanel job={job} /> : null}
    </section>
  );
}

function BacktestJobPanel({ job }: { job: Record<string, unknown> }) {
  const events = Array.isArray(job.events) ? (job.events as Record<string, unknown>[]) : [];
  return (
    <section className="panel" style={{ marginTop: 16 }}>
      <h2>Backtest Progress</h2>
      <div className="toolbar">
        <SemanticBadge tone={toneForStatus(String(job.status))}>{String(job.status)}</SemanticBadge>
        {job.result && typeof job.result === "object" ? <span className="meta-tag">{String((job.result as Record<string, unknown>).run_dir ?? "")}</span> : null}
      </div>
      <DataTable rows={events.map((event) => ({ session_date: event.session_date, status: event.status, run_dir: event.run_dir, ...((event.daily_summary as Record<string, unknown>) ?? {}) }))} />
      {job.error ? <div className="error-panel">{String(job.error)}</div> : null}
    </section>
  );
}

function RunDetail({ runId, outputRoot, onClose }: { runId: string; outputRoot: string; onClose: () => void }) {
  const [detail, setDetail] = useState<RunDetailPayload | null>(null);
  const [tab, setTab] = useState("Overview");
  useEffect(() => {
    api<RunDetailPayload>(`/api/backtests/runs/${runId}${query({ output_root: outputRoot })}`).then(setDetail);
  }, [runId, outputRoot]);
  const summary = detail?.summary ?? {};
  return (
    <Modal title={runId} onClose={onClose}>
      <MetricStrip
        items={[
          { label: "Return", value: Number(summary.return_pct ?? 0) * 100, kind: "number" },
          { label: "Net P/L", value: Number(summary.total_pnl ?? 0), kind: "number" },
          { label: "Trades", value: Number(summary.trade_count ?? 0), kind: "number" },
          { label: "Status", value: String(detail?.metadata.status ?? "-"), kind: "status" }
        ]}
      />
      <Tabs tabs={["Overview", "Trades", "Orders", "Scanner", "Rejected", "Positions", "Logs"]} active={tab} onChange={setTab} />
      {tab === "Overview" ? <DataTable rows={detail?.tables.daily.rows ?? []} /> : null}
      {tab === "Trades" ? <DataTable rows={detail?.tables.trades.rows ?? []} /> : null}
      {tab === "Orders" ? <DataTable rows={detail?.tables.orders.rows ?? []} /> : null}
      {tab === "Scanner" ? <DataTable rows={detail?.tables.scanner.rows ?? []} /> : null}
      {tab === "Rejected" ? <DataTable rows={detail?.tables.rejections.rows ?? []} /> : null}
      {tab === "Positions" ? <DataTable rows={detail?.tables.positions.rows ?? []} /> : null}
      {tab === "Logs" ? <pre className="markdown-panel">{detail?.logs || "No logs."}</pre> : null}
    </Modal>
  );
}

type RunDetailPayload = {
  metadata: Record<string, unknown>;
  summary: Record<string, unknown>;
  tables: Record<string, { columns: string[]; rows: Record<string, unknown>[] }>;
  logs: string;
};

function Field({ label, value, onChange, type = "text" }: { label: string; value: string; onChange: (value: string) => void; type?: string }) {
  return (
    <div className="field">
      <label>{label}</label>
      <input type={type} value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

function NumberField({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <div className="field">
      <label>{label}</label>
      <input type="number" value={value} onChange={(event) => onChange(Number(event.target.value))} />
    </div>
  );
}
