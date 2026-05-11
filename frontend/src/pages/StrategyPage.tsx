import { Activity, Banknote, CalendarRange, CircleHelp, Database, Gauge, ListChecks, Pencil, Play, Shield, SlidersHorizontal, Trash2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";

import { api, query } from "../api/client";
import { DataTable } from "../app/components/DataTable";
import { MetricStrip } from "../app/components/MetricStrip";
import { Modal } from "../app/components/Modal";
import { PageIntro } from "../app/components/PageIntro";
import { SemanticBadge, toneForStatus } from "../app/components/SemanticBadge";
import { Tabs } from "../app/components/Tabs";
import { formatMoney, formatNumber, formatPct } from "../app/format";

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

type StrategyParamValue = number | string | boolean;
type EditTarget = "run" | "strategy";
type NewRunMetricTone = "info" | "neutral" | "success" | "warning";
type NewRunMetric = {
  detail: string;
  icon: ReactNode;
  label: string;
  tone?: NewRunMetricTone;
  value: string;
};

const tabs = ["Runs", "New Run", "Strategy README"];
const strategyName = "orb_5m_momentum";

const RUN_PARAMETER_HELP: Record<string, string> = {
  run_name: "A readable label for this backtest run. It is used in saved run folders and run history.",
  output_root: "Folder where backtest outputs, summaries, logs, and tables are saved.",
  start_date: "First market session included in the backtest range.",
  end_date: "Last market session included in the backtest range.",
  data_root: "Raw source data root used by older or fallback data readers.",
  processed_data_root: "Canonical provider-built market data root used by the backtest.",
  initial_cash: "Starting portfolio cash used to size positions and calculate return.",
  slippage_bps: "Per-fill slippage in basis points. One basis point is 0.01 percent.",
  save_symbol_bars: "When enabled, the run saves per-symbol bar snapshots for diagnostics."
};

const STRATEGY_PARAMETER_HELP: Record<string, string> = {
  min_price: "Minimum stock price allowed into the premarket candidate universe.",
  max_price: "Maximum stock price allowed into the premarket candidate universe.",
  min_avg_daily_volume: "Minimum average daily volume required before a ticker can be considered.",
  min_atr: "Minimum average true range required so tiny-range symbols are filtered out.",
  relative_volume_daily_share: "Minimum early relative-volume share versus normal daily volume.",
  min_opening_relative_volume: "Minimum opening relative volume required during setup scoring.",
  min_setup_score: "Minimum pre-entry setup score for a ticker to stay in the candidate list.",
  min_live_score: "Minimum live score required before the strategy can place an entry.",
  watchlist_size: "Maximum number of candidate tickers carried from scan into live evaluation.",
  max_active_positions: "Maximum number of simultaneous open positions.",
  replacement_score_buffer: "Score advantage required before a new candidate can replace a weaker active idea.",
  minimum_hold_minutes: "Minimum number of minutes a position must be held before normal exits can apply.",
  opening_box_start_minute: "Minute of day where the opening range measurement starts.",
  opening_box_end_minute: "Minute of day where the opening range measurement ends.",
  entry_cutoff_minute: "Last minute of day where new entries are allowed.",
  exit_minutes_before_close: "Minutes before the session close where positions should be flattened.",
  entry_buffer_pct: "Breakout buffer above the opening range high before an entry is valid.",
  entry_stage_proximity_pct: "Maximum distance from the staged entry level while the candidate remains actionable.",
  stop_box_pullback_fraction: "Opening-range pullback fraction used to place the initial stop.",
  min_risk_pct: "Minimum allowed trade risk as a fraction of entry price.",
  max_risk_pct: "Maximum allowed trade risk as a fraction of entry price.",
  max_capital_per_trade_pct: "Maximum portfolio capital allocated to one trade.",
  cash_reserve_pct: "Cash reserve kept outside new position sizing.",
  min_gap_up_pct: "Minimum gap-up from prior close required for setup eligibility.",
  min_close_location: "Minimum close location within the candle range required for quality scoring.",
  min_body_to_range: "Minimum candle body-to-range ratio required for quality scoring.",
  min_orb_range_atr_fraction: "Minimum opening-range size compared with ATR.",
  max_orb_range_atr_fraction: "Maximum opening-range size compared with ATR.",
  tema_entry_atr_buffer: "TEMA entry buffer expressed relative to ATR.",
  tema_exit_atr_buffer: "TEMA exit buffer expressed relative to ATR."
};

const STRATEGY_PARAMETER_GROUPS = [
  {
    title: "Universe & Liquidity",
    description: "Filters that decide which symbols are allowed into the setup scan.",
    keys: ["min_price", "max_price", "min_avg_daily_volume", "min_atr", "relative_volume_daily_share", "min_opening_relative_volume"]
  },
  {
    title: "Scoring & Capacity",
    description: "Controls candidate ranking, live confirmation, and portfolio breadth.",
    keys: ["min_setup_score", "min_live_score", "watchlist_size", "max_active_positions", "replacement_score_buffer"]
  },
  {
    title: "Session Timing",
    description: "Defines opening range, entry window, hold time, and closing behavior.",
    keys: ["opening_box_start_minute", "opening_box_end_minute", "entry_cutoff_minute", "exit_minutes_before_close", "minimum_hold_minutes"]
  },
  {
    title: "Entry & Risk",
    description: "Position sizing, entry buffers, stops, and portfolio capital constraints.",
    keys: ["entry_buffer_pct", "entry_stage_proximity_pct", "stop_box_pullback_fraction", "min_risk_pct", "max_risk_pct", "max_capital_per_trade_pct", "cash_reserve_pct"]
  },
  {
    title: "Pattern Quality",
    description: "Gap, candle quality, opening-range size, and trend buffer requirements.",
    keys: ["min_gap_up_pct", "min_close_location", "min_body_to_range", "min_orb_range_atr_fraction", "max_orb_range_atr_fraction", "tema_entry_atr_buffer", "tema_exit_atr_buffer"]
  }
] satisfies Array<{ title: string; description: string; keys: string[] }>;

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
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [draftConfig, setDraftConfig] = useState(config);
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

  function openEditor(target: EditTarget) {
    setDraftConfig(config);
    setEditing(target);
  }

  function applyDraft() {
    onConfigChange(draftConfig);
    setEditing(null);
  }

  const topMetrics = buildNewRunMetrics(config, params, job);

  return (
    <section className="new-run-page">
      <div className="new-run-action-row">
        <button className="button primary" onClick={startRun} type="button" disabled={["running", "queued"].includes(String(job?.status ?? ""))}>
          <Play size={15} /> Start Backtest
        </button>
      </div>
      <NewRunMetricStrip metrics={topMetrics} />

      <div className="run-config-grid">
        <ParameterCard
          description="Backtest identity, date range, data roots, and execution assumptions."
          icon={<Database size={18} />}
          onEdit={() => openEditor("run")}
          title="Backtest Parameters"
        >
          <ParameterSection title="Identity & Range">
            <ParameterItem help={RUN_PARAMETER_HELP.run_name} label="Run name" value={config.run_name} />
            <ParameterItem help={RUN_PARAMETER_HELP.start_date} label="Start" value={config.start_date} />
            <ParameterItem help={RUN_PARAMETER_HELP.end_date} label="End" value={config.end_date} />
            <ParameterItem help={RUN_PARAMETER_HELP.save_symbol_bars} label="Save symbol bars" value={config.save_symbol_bars ? "Enabled" : "Disabled"} />
          </ParameterSection>
          <ParameterSection title="Data & Storage">
            <ParameterItem help={RUN_PARAMETER_HELP.processed_data_root} label="Processed data root" mono value={config.processed_data_root} />
            <ParameterItem help={RUN_PARAMETER_HELP.data_root} label="Raw data root" mono value={config.data_root} />
            <ParameterItem help={RUN_PARAMETER_HELP.output_root} label="Output root" mono value={config.output_root} />
          </ParameterSection>
          <ParameterSection title="Capital & Fill Model">
            <ParameterItem help={RUN_PARAMETER_HELP.initial_cash} label="Initial cash" value={formatMoney(config.initial_cash)} />
            <ParameterItem help={RUN_PARAMETER_HELP.slippage_bps} label="Slippage" value={`${formatNumber(config.slippage_bps, 2)} bps`} />
          </ParameterSection>
        </ParameterCard>

        <ParameterCard
          description="Grouped ORB momentum thresholds used by the strategy engine."
          icon={<SlidersHorizontal size={18} />}
          onEdit={() => openEditor("strategy")}
          title="Strategy Parameters"
        >
          <StrategyParameterDisplay params={params} />
        </ParameterCard>
      </div>

      {error ? <div className="error-panel" style={{ marginTop: 12 }}>{error}</div> : null}
      {job ? <BacktestJobPanel job={job} /> : null}
      {editing === "run" ? (
        <Modal title="Edit Backtest Parameters" onClose={() => setEditing(null)}>
          <BacktestParameterEditor config={draftConfig} onChange={setDraftConfig} />
          <div className="modal-actions">
            <button className="button" onClick={() => setEditing(null)} type="button">Cancel</button>
            <button className="button primary" onClick={applyDraft} type="button">Apply</button>
          </div>
        </Modal>
      ) : null}
      {editing === "strategy" ? (
        <Modal title="Edit Strategy Parameters" onClose={() => setEditing(null)}>
          <StrategyParameterEditor config={draftConfig} onChange={setDraftConfig} />
          <div className="modal-actions">
            <button className="button" onClick={() => setEditing(null)} type="button">Cancel</button>
            <button className="button primary" onClick={applyDraft} type="button">Apply</button>
          </div>
        </Modal>
      ) : null}
    </section>
  );
}

function NewRunMetricStrip({ metrics }: { metrics: NewRunMetric[] }) {
  return (
    <div className="new-run-metric-strip">
      {metrics.map((metric) => (
        <article className="new-run-metric-card" data-tone={metric.tone ?? "neutral"} key={metric.label}>
          <div className="new-run-metric-icon">{metric.icon}</div>
          <span className="new-run-metric-label">{metric.label}</span>
          <strong className="new-run-metric-value">{metric.value}</strong>
          <span className="new-run-metric-detail">{metric.detail}</span>
        </article>
      ))}
    </div>
  );
}

function ParameterCard({
  children,
  description,
  icon,
  onEdit,
  title
}: {
  children: ReactNode;
  description: string;
  icon: ReactNode;
  onEdit: () => void;
  title: string;
}) {
  return (
    <section className="parameter-card">
      <div className="parameter-card-header">
        <div className="parameter-card-title-group">
          <span className="parameter-card-icon">{icon}</span>
          <div>
            <h2>{title}</h2>
            <p>{description}</p>
          </div>
        </div>
        <button className="icon-button parameter-edit-button" onClick={onEdit} type="button" aria-label={`Edit ${title}`}>
          <Pencil size={15} />
        </button>
      </div>
      <div className="parameter-card-body">{children}</div>
    </section>
  );
}

function ParameterSection({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section className="parameter-section">
      <h3>{title}</h3>
      <div className="parameter-grid">{children}</div>
    </section>
  );
}

function ParameterItem({ help, label, mono = false, value }: { help: string; label: string; mono?: boolean; value: string | number }) {
  return (
    <div className="parameter-item">
      <div className="parameter-label">
        <span>{label}</span>
        <HelpButton help={help} label={label} />
      </div>
      <div className={mono ? "parameter-value mono" : "parameter-value"} title={String(value)}>
        {value}
      </div>
    </div>
  );
}

function StrategyParameterDisplay({ params }: { params: Record<string, StrategyParamValue> }) {
  const rendered = new Set<string>();
  const groups = STRATEGY_PARAMETER_GROUPS.map((group) => ({
    ...group,
    keys: group.keys.filter((key) => key in params)
  })).filter((group) => group.keys.length > 0);
  const remaining = Object.keys(params).filter((key) => !STRATEGY_PARAMETER_GROUPS.some((group) => group.keys.includes(key)));

  return (
    <div className="strategy-parameter-groups">
      {groups.map((group) => {
        group.keys.forEach((key) => rendered.add(key));
        return (
          <section className="strategy-parameter-group" key={group.title}>
            <div className="strategy-group-heading">
              <h3>{group.title}</h3>
              <p>{group.description}</p>
            </div>
            <div className="parameter-grid dense">
              {group.keys.map((key) => (
                <ParameterItem
                  help={STRATEGY_PARAMETER_HELP[key] ?? `Controls ${formatParamLabel(key)} for this strategy run.`}
                  key={key}
                  label={formatParamLabel(key)}
                  value={formatStrategyParamValue(key, params[key])}
                />
              ))}
            </div>
          </section>
        );
      })}
      {remaining.filter((key) => !rendered.has(key)).length ? (
        <section className="strategy-parameter-group">
          <div className="strategy-group-heading">
            <h3>Other Parameters</h3>
            <p>Additional strategy settings declared by the backend configuration.</p>
          </div>
          <div className="parameter-grid dense">
            {remaining.filter((key) => !rendered.has(key)).map((key) => (
              <ParameterItem
                help={STRATEGY_PARAMETER_HELP[key] ?? `Controls ${formatParamLabel(key)} for this strategy run.`}
                key={key}
                label={formatParamLabel(key)}
                value={formatStrategyParamValue(key, params[key])}
              />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function BacktestParameterEditor({ config, onChange }: { config: StrategyConfig; onChange: (config: StrategyConfig) => void }) {
  return (
    <div className="parameter-edit-stack">
      <EditSection description="These values identify the run and choose the inclusive backtest date range." title="Identity & Range">
        <EditField help={RUN_PARAMETER_HELP.run_name} label="Run name" value={config.run_name} onChange={(value) => onChange({ ...config, run_name: value })} />
        <EditField help={RUN_PARAMETER_HELP.start_date} label="Start" type="date" value={config.start_date} onChange={(value) => onChange({ ...config, start_date: value })} />
        <EditField help={RUN_PARAMETER_HELP.end_date} label="End" type="date" value={config.end_date} onChange={(value) => onChange({ ...config, end_date: value })} />
        <EditBooleanField help={RUN_PARAMETER_HELP.save_symbol_bars} label="Save symbol bars" value={config.save_symbol_bars} onChange={(value) => onChange({ ...config, save_symbol_bars: value })} />
      </EditSection>
      <EditSection description="Provider-built data is preferred for backtests; output root controls saved run artifacts." title="Data & Storage">
        <EditField help={RUN_PARAMETER_HELP.processed_data_root} label="Processed data root" value={config.processed_data_root} onChange={(value) => onChange({ ...config, processed_data_root: value })} />
        <EditField help={RUN_PARAMETER_HELP.data_root} label="Raw data root" value={config.data_root} onChange={(value) => onChange({ ...config, data_root: value })} />
        <EditField help={RUN_PARAMETER_HELP.output_root} label="Output root" value={config.output_root} onChange={(value) => onChange({ ...config, output_root: value })} />
      </EditSection>
      <EditSection description="These values control portfolio starting capital and synthetic fill slippage." title="Capital & Fill Model">
        <EditNumberField help={RUN_PARAMETER_HELP.initial_cash} label="Initial cash" value={config.initial_cash} onChange={(value) => onChange({ ...config, initial_cash: value })} />
        <EditNumberField help={RUN_PARAMETER_HELP.slippage_bps} label="Slippage bps" value={config.slippage_bps} onChange={(value) => onChange({ ...config, slippage_bps: value })} />
      </EditSection>
    </div>
  );
}

function StrategyParameterEditor({ config, onChange }: { config: StrategyConfig; onChange: (config: StrategyConfig) => void }) {
  const params = config.strategy_params;
  const knownKeys = new Set(STRATEGY_PARAMETER_GROUPS.flatMap((group) => group.keys));
  const remaining = Object.keys(params).filter((key) => !knownKeys.has(key));

  function updateParam(key: string, value: StrategyParamValue) {
    onChange({ ...config, strategy_params: { ...params, [key]: value } });
  }

  return (
    <div className="parameter-edit-stack">
      {STRATEGY_PARAMETER_GROUPS.map((group) => {
        const keys = group.keys.filter((key) => key in params);
        if (!keys.length) return null;
        return (
          <EditSection description={group.description} key={group.title} title={group.title}>
            {keys.map((key) => (
              <EditStrategyParamField
                help={STRATEGY_PARAMETER_HELP[key] ?? `Controls ${formatParamLabel(key)} for this strategy run.`}
                key={key}
                name={key}
                onChange={(value) => updateParam(key, value)}
                value={params[key]}
              />
            ))}
          </EditSection>
        );
      })}
      {remaining.length ? (
        <EditSection description="Additional strategy settings declared by the backend configuration." title="Other Parameters">
          {remaining.map((key) => (
            <EditStrategyParamField
              help={STRATEGY_PARAMETER_HELP[key] ?? `Controls ${formatParamLabel(key)} for this strategy run.`}
              key={key}
              name={key}
              onChange={(value) => updateParam(key, value)}
              value={params[key]}
            />
          ))}
        </EditSection>
      ) : null}
    </div>
  );
}

function EditSection({ children, description, title }: { children: ReactNode; description: string; title: string }) {
  return (
    <section className="parameter-edit-section">
      <div className="parameter-edit-heading">
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
      <div className="parameter-edit-grid">{children}</div>
    </section>
  );
}

function EditStrategyParamField({ help, name, onChange, value }: { help: string; name: string; onChange: (value: StrategyParamValue) => void; value: StrategyParamValue }) {
  if (typeof value === "boolean") {
    return <EditBooleanField help={help} label={formatParamLabel(name)} value={value} onChange={onChange} />;
  }
  if (typeof value === "number") {
    return <EditNumberField help={help} label={formatParamLabel(name)} value={value} onChange={onChange} />;
  }
  return <EditField help={help} label={formatParamLabel(name)} value={String(value)} onChange={onChange} />;
}

function EditField({
  help,
  label,
  onChange,
  type = "text",
  value
}: {
  help: string;
  label: string;
  onChange: (value: string) => void;
  type?: string;
  value: string;
}) {
  return (
    <div className="field config-field">
      <FieldLabel help={help} label={label} />
      <input type={type} value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

function EditNumberField({ help, label, onChange, value }: { help: string; label: string; onChange: (value: number) => void; value: number }) {
  return (
    <div className="field config-field">
      <FieldLabel help={help} label={label} />
      <input type="number" value={value} onChange={(event) => onChange(Number(event.target.value))} />
    </div>
  );
}

function EditBooleanField({ help, label, onChange, value }: { help: string; label: string; onChange: (value: boolean) => void; value: boolean }) {
  return (
    <div className="config-checkbox-field">
      <div>
        <FieldLabel help={help} label={label} />
        <span>{value ? "Enabled" : "Disabled"}</span>
      </div>
      <input aria-label={label} checked={value} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
    </div>
  );
}

function FieldLabel({ help, label }: { help: string; label: string }) {
  return (
    <span className="parameter-label">
      <span>{label}</span>
      <HelpButton help={help} label={label} />
    </span>
  );
}

function HelpButton({ help, label }: { help: string; label: string }) {
  return (
    <button aria-label={`Help for ${label}`} className="parameter-help-button" data-help={help} title={help} type="button">
      <CircleHelp size={13} />
    </button>
  );
}

function formatParamLabel(key: string): string {
  const tokenOverrides: Record<string, string> = {
    atr: "ATR",
    avg: "Avg",
    box: "Box",
    gap: "Gap",
    max: "Max",
    min: "Min",
    orb: "ORB",
    pct: "%",
    per: "Per",
    tema: "TEMA",
    to: "to"
  };
  return key
    .split("_")
    .filter(Boolean)
    .map((part) => tokenOverrides[part.toLowerCase()] ?? part[0].toUpperCase() + part.slice(1))
    .join(" ");
}

function formatStrategyParamValue(key: string, value: StrategyParamValue): string {
  if (typeof value === "boolean") return value ? "Enabled" : "Disabled";
  if (typeof value !== "number") return String(value);
  if (key === "opening_box_start_minute" || key === "opening_box_end_minute" || key === "entry_cutoff_minute") return formatMinuteOfDay(value);
  if (key === "minimum_hold_minutes" || key === "exit_minutes_before_close") return `${formatNumber(value)} min`;
  if (key.includes("_pct") || key.includes("_fraction") || key.includes("_share") || key === "min_close_location" || key === "min_body_to_range") return formatPct(value);
  if (Math.abs(value) >= 10_000) return formatNumber(value);
  if (!Number.isInteger(value)) return formatNumber(value, 4);
  return formatNumber(value);
}

function formatMinuteOfDay(value: number): string {
  const hours = Math.floor(value / 60);
  const minutes = value % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function buildNewRunMetrics(config: StrategyConfig, params: Record<string, StrategyParamValue>, job: Record<string, unknown> | null): NewRunMetric[] {
  const status = String(job?.status ?? "draft");
  const eventCount = Array.isArray(job?.events) ? job.events.length : 0;
  return [
    {
      detail: eventCount ? `${formatNumber(eventCount)} sessions reported` : "Ready to submit",
      icon: <Activity size={15} />,
      label: "Status",
      tone: statusMetricTone(status),
      value: status
    },
    {
      detail: dateRangeDetail(config.start_date, config.end_date),
      icon: <CalendarRange size={15} />,
      label: "Range",
      value: compactDateRange(config.start_date, config.end_date)
    },
    {
      detail: "Starting portfolio cash",
      icon: <Banknote size={15} />,
      label: "Capital",
      value: formatMoney(config.initial_cash)
    },
    {
      detail: "Candidate watchlist",
      icon: <ListChecks size={15} />,
      label: "Universe",
      value: formatNumber(numberParam(params.watchlist_size))
    },
    {
      detail: "Max concurrent positions",
      icon: <Gauge size={15} />,
      label: "Capacity",
      value: formatNumber(numberParam(params.max_active_positions))
    },
    {
      detail: "Setup / live score",
      icon: <SlidersHorizontal size={15} />,
      label: "Entry Gate",
      value: `${formatNumber(numberParam(params.min_setup_score))} / ${formatNumber(numberParam(params.min_live_score))}`
    },
    {
      detail: "Max capital per trade",
      icon: <Shield size={15} />,
      label: "Risk Cap",
      value: formatPct(numberParam(params.max_capital_per_trade_pct))
    }
  ];
}

function numberParam(value: StrategyParamValue | undefined): number {
  const numeric = Number(value ?? 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

function statusMetricTone(status: string): NewRunMetricTone {
  const normalized = status.toLowerCase();
  if (normalized === "complete" || normalized === "ready") return "success";
  if (normalized === "running" || normalized === "queued") return "info";
  if (normalized === "failed" || normalized === "error") return "warning";
  return "neutral";
}

function compactDateRange(start: string, end: string): string {
  const startParts = parseIsoDate(start);
  const endParts = parseIsoDate(end);
  if (!startParts || !endParts) return `${start} to ${end}`;
  const monthDay = new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short", timeZone: "UTC" });
  const dayOnly = new Intl.DateTimeFormat("en-US", { day: "numeric", timeZone: "UTC" });
  if (startParts.year === endParts.year && startParts.month === endParts.month) {
    return `${monthDay.format(startParts.date)}-${dayOnly.format(endParts.date)}`;
  }
  return `${monthDay.format(startParts.date)}-${monthDay.format(endParts.date)}`;
}

function dateRangeDetail(start: string, end: string): string {
  const startParts = parseIsoDate(start);
  const endParts = parseIsoDate(end);
  if (!startParts || !endParts) return "Selected backtest dates";
  const days = Math.max(1, Math.round((endParts.date.getTime() - startParts.date.getTime()) / 86_400_000) + 1);
  const yearLabel = startParts.year === endParts.year ? String(startParts.year) : `${startParts.year}-${endParts.year}`;
  return `${yearLabel}, ${formatNumber(days)} calendar days`;
}

function parseIsoDate(value: string): { date: Date; month: number; year: number } | null {
  const [yearText, monthText, dayText] = value.split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  if (![year, month, day].every(Number.isFinite)) return null;
  return { date: new Date(Date.UTC(year, month - 1, day)), month, year };
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
