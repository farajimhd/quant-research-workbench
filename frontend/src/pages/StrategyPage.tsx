import { Activity, Banknote, CircleHelp, Database, Gauge, ListChecks, Percent, Play, Shield, SlidersHorizontal, StopCircle, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";

import { api, query } from "../api/client";
import { ChartPanel, type ChartPayload } from "../app/components/ChartPanel";
import { DataTable } from "../app/components/DataTable";
import { Modal } from "../app/components/Modal";
import { PageIntro } from "../app/components/PageIntro";
import { ProgressMeter } from "../app/components/Progress";
import { SemanticBadge, toneForStatus } from "../app/components/SemanticBadge";
import { Tabs } from "../app/components/Tabs";
import { formatMoney, formatNumber, formatPct } from "../app/format";

type Strategy = {
  name: string;
  display_name: string;
  description: string;
  versions?: string[];
  default_version?: string;
};

type RunRow = {
  run_id: string;
  run_name: string;
  strategy_name: string;
  strategy_version: string;
  status: string;
  created_at: string;
  date_range: string;
  return_pct: number;
  total_pnl: number;
  trade_count: number;
};

type StrategyConfig = {
  strategy_name: string;
  strategy_version: string;
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
type NewRunMetricTone = "danger" | "info" | "neutral" | "success" | "warning";
type NewRunMetric = {
  detail: string;
  icon: ReactNode;
  label: string;
  tone?: NewRunMetricTone;
  value: string;
};

const tabs = ["Backtest", "Runs", "Strategy README"];
const defaultStrategyName = "orb_5m_momentum";

type StrategySelection = {
  strategyName: string;
  version: string;
};

const RUN_PARAMETER_HELP: Record<string, string> = {
  run_name: "A readable label for this backtest run. It is used in saved run folders and run history.",
  strategy_version: "Version of the strategy implementation used for this run. It is saved with the run metadata.",
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
  tema_exit_atr_buffer: "TEMA exit buffer expressed relative to ATR.",
  min_opening_volume: "Minimum opening-box share volume required before a ticker can be considered.",
  min_opening_dollar_volume: "Minimum opening-box dollar volume required before a ticker can be considered.",
  opening_volume_score_full: "Opening-box volume level that earns the full volume score.",
  min_box_range_pct: "Minimum opening-box range as a fraction of the opening price.",
  max_box_range_pct: "Maximum opening-box range as a fraction of the opening price.",
  min_box_dollar_range: "Minimum opening-box dollar range required for setup eligibility.",
  max_entry_extension_pct: "Maximum allowed close extension beyond the entry trigger.",
  tema_entry_buffer_pct: "TEMA entry buffer as a fraction of opening-box close.",
  tema_exit_buffer_pct: "TEMA exit buffer as a fraction of opening-box close."
};

const STRATEGY_PARAMETER_GROUPS = [
  {
    title: "Universe & Liquidity",
    description: "Filters that decide which symbols are allowed into the setup scan.",
    keys: [
      "min_price",
      "max_price",
      "min_avg_daily_volume",
      "min_atr",
      "relative_volume_daily_share",
      "min_opening_relative_volume",
      "min_opening_volume",
      "min_opening_dollar_volume",
      "opening_volume_score_full"
    ]
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
    keys: [
      "entry_buffer_pct",
      "entry_stage_proximity_pct",
      "max_entry_extension_pct",
      "stop_box_pullback_fraction",
      "min_risk_pct",
      "max_risk_pct",
      "max_capital_per_trade_pct",
      "cash_reserve_pct"
    ]
  },
  {
    title: "Pattern Quality",
    description: "Gap, candle quality, opening-range size, and trend buffer requirements.",
    keys: [
      "min_gap_up_pct",
      "min_close_location",
      "min_body_to_range",
      "min_orb_range_atr_fraction",
      "max_orb_range_atr_fraction",
      "min_box_range_pct",
      "max_box_range_pct",
      "min_box_dollar_range",
      "tema_entry_atr_buffer",
      "tema_exit_atr_buffer",
      "tema_entry_buffer_pct",
      "tema_exit_buffer_pct"
    ]
  }
] satisfies Array<{ title: string; description: string; keys: string[] }>;

const IMPORTANT_STRATEGY_PARAMETER_KEYS = [
  "max_active_positions",
  "max_capital_per_trade_pct",
  "cash_reserve_pct",
  "min_setup_score",
  "min_live_score",
  "entry_cutoff_minute",
  "min_risk_pct",
  "max_risk_pct"
];

export function StrategyPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [draftSelection, setDraftSelection] = useState<StrategySelection | null>(null);
  const [activeSelection, setActiveSelection] = useState<StrategySelection | null>(null);
  const [activeTab, setActiveTab] = useState(tabs[0]);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [readme, setReadme] = useState("");
  const [config, setConfig] = useState<StrategyConfig | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const activeStrategy = activeSelection ? strategies.find((item) => item.name === activeSelection.strategyName) ?? null : null;
  const draftStrategy = draftSelection ? strategies.find((item) => item.name === draftSelection.strategyName) ?? null : null;

  useEffect(() => {
    api<{ strategies: Strategy[] }>("/api/strategies").then((payload) => {
      const nextStrategies = payload.strategies;
      setStrategies(nextStrategies);
      const defaultStrategy = nextStrategies.find((item) => item.name === defaultStrategyName) ?? nextStrategies[0];
      if (!defaultStrategy) return;
      const version = defaultStrategy.default_version ?? defaultStrategy.versions?.[0] ?? "v1";
      setDraftSelection((current) => current ?? { strategyName: defaultStrategy.name, version });
    });
  }, []);

  useEffect(() => {
    if (!activeSelection) return;
    api<StrategyConfig>(`/api/strategies/${activeSelection.strategyName}/default-config${query({ version: activeSelection.version })}`).then((payload) => {
      setConfig({ ...payload, run_name: submittedRunName(payload) });
    });
    api<{ content: string }>(`/api/strategies/${activeSelection.strategyName}/readme${query({ version: activeSelection.version })}`).then((payload) => setReadme(payload.content));
  }, [activeSelection?.strategyName, activeSelection?.version]);

  useEffect(() => {
    if (!config || !activeSelection) return;
    loadRuns(config.output_root, activeSelection.strategyName, activeSelection.version);
  }, [config?.output_root, activeSelection?.strategyName, activeSelection?.version]);

  async function loadRuns(outputRoot: string, strategyName: string, version: string) {
    const payload = await api<{ runs: RunRow[] }>(`/api/backtests/runs${query({ output_root: outputRoot, strategy_name: strategyName, strategy_version: version })}`);
    setRuns(payload.runs);
  }

  function updateDraftStrategy(strategyName: string) {
    const strategy = strategies.find((item) => item.name === strategyName);
    const version = strategy?.default_version ?? strategy?.versions?.[0] ?? "v1";
    setDraftSelection({ strategyName, version });
  }

  function updateDraftVersion(version: string) {
    if (!draftSelection) return;
    setDraftSelection({ ...draftSelection, version });
  }

  function openBacktestWorkspace() {
    if (!draftSelection) return;
    setConfig(null);
    setRuns([]);
    setReadme("");
    setSelectedRun(null);
    setActiveTab(tabs[0]);
    setActiveSelection(draftSelection);
  }

  function openSavedRun(runId: string) {
    setSelectedRun(runId);
    setActiveTab(tabs[0]);
  }

  function changeSelection() {
    if (activeSelection) setDraftSelection(activeSelection);
    setActiveSelection(null);
    setActiveTab(tabs[0]);
    setConfig(null);
    setRuns([]);
    setReadme("");
    setSelectedRun(null);
  }

  if (!activeSelection) {
    return (
      <>
        <PageIntro
          groupLabel="Backtest"
          title="Backtest"
          description="Select the strategy and version before opening the run workspace."
        />
        <BacktestSelectionPanel
          draftSelection={draftSelection}
          onOpen={openBacktestWorkspace}
          onStrategyChange={updateDraftStrategy}
          onVersionChange={updateDraftVersion}
          strategies={strategies}
          strategy={draftStrategy}
        />
      </>
    );
  }

  return (
    <>
      <PageIntro
        actions={
          <button className="button secondary" onClick={changeSelection} type="button">
            <SlidersHorizontal size={15} /> Change Strategy
          </button>
        }
        groupLabel="Backtest"
        title={`${activeStrategy?.display_name ?? activeSelection.strategyName.replaceAll("_", " ")} ${activeSelection.version}`}
        description={activeStrategy?.description ?? "Opening range momentum research strategy."}
      />
      <Tabs tabs={tabs} active={activeTab} onChange={setActiveTab} />
      {activeTab === "Backtest" && config ? (
        selectedRun ? (
          <SavedRunPanel
            config={config}
            onBack={() => {
              setSelectedRun(null);
              setActiveTab("Runs");
            }}
            onNewBacktest={() => setSelectedRun(null)}
            outputRoot={config.output_root}
            runId={selectedRun}
          />
        ) : (
          <NewRunPanel
            config={config}
            key={config.strategy_version}
            onConfigChange={setConfig}
            onComplete={() => loadRuns(config.output_root, activeSelection.strategyName, activeSelection.version)}
            versions={activeStrategy?.versions ?? [activeSelection.version]}
          />
        )
      ) : null}
      {activeTab === "Runs" && config ? (
        <RunsPanel
          runs={runs}
          outputRoot={config.output_root}
          onOpen={openSavedRun}
          onDeleted={() => loadRuns(config.output_root, activeSelection.strategyName, activeSelection.version)}
        />
      ) : null}
      {activeTab === "Strategy README" ? (
        <div className="markdown-panel">
          <ReactMarkdown>{readme}</ReactMarkdown>
        </div>
      ) : null}
    </>
  );
}

function BacktestSelectionPanel({
  draftSelection,
  onOpen,
  onStrategyChange,
  onVersionChange,
  strategies,
  strategy
}: {
  draftSelection: StrategySelection | null;
  onOpen: () => void;
  onStrategyChange: (strategyName: string) => void;
  onVersionChange: (version: string) => void;
  strategies: Strategy[];
  strategy: Strategy | null;
}) {
  const versions = strategy?.versions?.length ? strategy.versions : draftSelection?.version ? [draftSelection.version] : [];

  return (
    <section className="panel backtest-selection-panel">
      <div className="backtest-selection-grid">
        <div className="field config-field">
          <label>Strategy</label>
          <select
            disabled={!strategies.length}
            onChange={(event) => onStrategyChange(event.target.value)}
            value={draftSelection?.strategyName ?? ""}
          >
            {strategies.map((item) => (
              <option key={item.name} value={item.name}>
                {item.display_name}
              </option>
            ))}
          </select>
        </div>
        <div className="field config-field">
          <label>Version</label>
          <select
            disabled={!versions.length}
            onChange={(event) => onVersionChange(event.target.value)}
            value={draftSelection?.version ?? ""}
          >
            {versions.map((version) => (
              <option key={version} value={version}>
                {version}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="backtest-selection-footer">
        <div>
          <h2>{strategy?.display_name ?? "Strategy"}</h2>
          <p>{strategy?.description ?? "Strategy catalog is loading."}</p>
        </div>
        <button className="button primary" disabled={!draftSelection} onClick={onOpen} type="button">
          <Play size={15} /> Open Backtest
        </button>
      </div>
    </section>
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
              <span className="meta-tag">{run.strategy_version || "v1"}</span>
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
  onComplete,
  versions
}: {
  config: StrategyConfig;
  onConfigChange: (config: StrategyConfig) => void;
  onComplete: () => void;
  versions: string[];
}) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [draftConfig, setDraftConfig] = useState(config);
  useEffect(() => {
    if (!jobId || !["running", "queued", "canceling"].includes(String(job?.status ?? "running"))) return;
    const timer = window.setInterval(() => {
      api<Record<string, unknown>>(`/api/backtests/jobs/${jobId}${query({ output_root: config.output_root })}`)
        .then((payload) => {
          setJob(payload);
          if (["complete", "cancelled"].includes(String(payload.status))) onComplete();
        })
        .catch((err) => {
          setError(err instanceof Error ? err.message : String(err));
        });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [jobId, job?.status, config.output_root]);

  async function startRun() {
    setError(null);
    const runConfig = { ...config, run_name: submittedRunName(config) };
    onConfigChange(runConfig);
    setDraftConfig(runConfig);
    try {
      const payload = await api<Record<string, unknown>>("/api/backtests/jobs", { method: "POST", body: JSON.stringify(runConfig) });
      setJob(payload);
      setJobId(String(payload.job_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function stopRun() {
    if (!jobId) return;
    setError(null);
    try {
      const payload = await api<Record<string, unknown>>(
        `/api/backtests/jobs/${jobId}/cancel${query({ output_root: config.output_root })}`,
        { method: "POST" }
      );
      setJob(payload);
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

  const jobStatus = String(job?.status ?? "").toLowerCase();
  const running = ["running", "queued", "canceling"].includes(jobStatus);
  const canStop = ["running", "queued"].includes(jobStatus);

  return (
    <section className="new-run-page">
      <div className="new-run-action-row">
        <button className="button primary" onClick={startRun} type="button" disabled={running}>
          <Play size={15} /> Start Backtest
        </button>
        <button className="button danger" onClick={stopRun} type="button" disabled={!canStop}>
          <StopCircle size={15} /> Stop Backtest
        </button>
        <button className="button secondary" onClick={() => openEditor("run")} type="button">
          <Database size={15} /> Update Backtest Parameters
        </button>
        <button className="button secondary" onClick={() => openEditor("strategy")} type="button">
          <SlidersHorizontal size={15} /> Update Strategy Parameters
        </button>
      </div>

      {error ? <div className="error-panel" style={{ marginTop: 12 }}>{error}</div> : null}
      <BacktestJobPanel config={config} job={job} outputRoot={config.output_root} />
      {editing === "run" ? (
        <Modal className="parameter-modal-panel" title="Update Backtest Parameters" onClose={() => setEditing(null)}>
          <BacktestParameterEditor config={draftConfig} onChange={setDraftConfig} versions={versions} />
          <div className="modal-actions">
            <button className="button" onClick={() => setEditing(null)} type="button">Cancel</button>
            <button className="button primary" onClick={applyDraft} type="button">Update Parameters</button>
          </div>
        </Modal>
      ) : null}
      {editing === "strategy" ? (
        <Modal className="parameter-modal-panel" title="Update Strategy Parameters" onClose={() => setEditing(null)}>
          <StrategyParameterEditor config={draftConfig} onChange={setDraftConfig} />
          <div className="modal-actions">
            <button className="button" onClick={() => setEditing(null)} type="button">Cancel</button>
            <button className="button primary" onClick={applyDraft} type="button">Update Parameters</button>
          </div>
        </Modal>
      ) : null}
    </section>
  );
}

function SavedRunPanel({
  config,
  onBack,
  onNewBacktest,
  outputRoot,
  runId
}: {
  config: StrategyConfig;
  onBack: () => void;
  onNewBacktest: () => void;
  outputRoot: string;
  runId: string;
}) {
  return (
    <section className="new-run-page">
      <div className="new-run-action-row">
        <button className="button secondary" onClick={onBack} type="button">
          Back to Runs
        </button>
        <button className="button primary" onClick={onNewBacktest} type="button">
          <Play size={15} /> New Backtest
        </button>
      </div>
      <BacktestJobPanel config={config} job={null} outputRoot={outputRoot} runId={runId} />
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

function BacktestParameterEditor({
  config,
  onChange,
  versions
}: {
  config: StrategyConfig;
  onChange: (config: StrategyConfig) => void;
  versions: string[];
}) {
  return (
    <ParameterEditorShell
      description="Define the run identity, date window, capital model, and artifact locations before submitting the backtest."
      icon={<Database size={18} />}
      meta={[
        { label: "Strategy", value: config.strategy_name.replaceAll("_", " ") },
        { label: "Version", value: versions.includes(config.strategy_version) ? config.strategy_version : config.strategy_version || "v1" },
        { label: "Range", value: `${config.start_date} to ${config.end_date}` }
      ]}
      title="Backtest Run Settings"
    >
      <EditSection
        description="Most runs are changed here: name, date range, starting capital, and synthetic fill cost."
        emphasis="primary"
        title="Important Settings"
      >
        <EditField help={RUN_PARAMETER_HELP.run_name} label="Run name" value={config.run_name} onChange={(value) => onChange({ ...config, run_name: value })} />
        <EditField help={RUN_PARAMETER_HELP.start_date} label="Start" type="date" value={config.start_date} onChange={(value) => onChange({ ...config, start_date: value })} />
        <EditField help={RUN_PARAMETER_HELP.end_date} label="End" type="date" value={config.end_date} onChange={(value) => onChange({ ...config, end_date: value })} />
        <EditNumberField help={RUN_PARAMETER_HELP.initial_cash} label="Initial cash" value={config.initial_cash} onChange={(value) => onChange({ ...config, initial_cash: value })} />
        <EditNumberField help={RUN_PARAMETER_HELP.slippage_bps} label="Slippage bps" value={config.slippage_bps} onChange={(value) => onChange({ ...config, slippage_bps: value })} />
      </EditSection>
      <EditSection description="Provider-built data is preferred for backtests; output root controls saved run artifacts." title="Data & Artifacts">
        <EditField help={RUN_PARAMETER_HELP.processed_data_root} label="Processed data root" value={config.processed_data_root} onChange={(value) => onChange({ ...config, processed_data_root: value })} />
        <EditField help={RUN_PARAMETER_HELP.data_root} label="Raw data root" value={config.data_root} onChange={(value) => onChange({ ...config, data_root: value })} />
        <EditField help={RUN_PARAMETER_HELP.output_root} label="Output root" value={config.output_root} onChange={(value) => onChange({ ...config, output_root: value })} />
        <EditBooleanField help={RUN_PARAMETER_HELP.save_symbol_bars} label="Save symbol bars" value={config.save_symbol_bars} onChange={(value) => onChange({ ...config, save_symbol_bars: value })} />
      </EditSection>
      <EditSection description="Strategy context is locked by the workspace selection so saved runs stay traceable." title="Strategy Context">
        <EditReadonlyField
          help={RUN_PARAMETER_HELP.strategy_version}
          label="Strategy version"
          value={versions.includes(config.strategy_version) ? config.strategy_version : config.strategy_version || "v1"}
        />
      </EditSection>
    </ParameterEditorShell>
  );
}

function EditReadonlyField({ help, label, value }: { help: string; label: string; value: string }) {
  return (
    <div className="field config-field">
      <FieldLabel help={help} label={label} />
      <input readOnly value={value} />
    </div>
  );
}

function StrategyParameterEditor({ config, onChange }: { config: StrategyConfig; onChange: (config: StrategyConfig) => void }) {
  const params = config.strategy_params;
  const knownKeys = new Set(STRATEGY_PARAMETER_GROUPS.flatMap((group) => group.keys));
  const importantKeys = IMPORTANT_STRATEGY_PARAMETER_KEYS.filter((key) => key in params);
  const importantKeySet = new Set(importantKeys);
  const remaining = Object.keys(params).filter((key) => !knownKeys.has(key));

  function updateParam(key: string, value: StrategyParamValue) {
    onChange({ ...config, strategy_params: { ...params, [key]: value } });
  }

  return (
    <ParameterEditorShell
      description="Tune the active strategy version. Capacity, scoring, timing, and risk controls are kept at the top because they change results the most."
      icon={<SlidersHorizontal size={18} />}
      meta={[
        { label: "Strategy", value: config.strategy_name.replaceAll("_", " ") },
        { label: "Version", value: config.strategy_version || "v1" },
        { label: "Parameters", value: formatNumber(Object.keys(params).length) }
      ]}
      title="Strategy Controls"
    >
      {importantKeys.length ? (
        <EditSection
          description="High-impact controls for breadth, capital allocation, score thresholds, entry timing, and risk bounds."
          emphasis="primary"
          title="Important Settings"
        >
          {importantKeys.map((key) => (
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
      {STRATEGY_PARAMETER_GROUPS.map((group) => {
        const keys = group.keys.filter((key) => key in params && !importantKeySet.has(key));
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
    </ParameterEditorShell>
  );
}

function ParameterEditorShell({
  children,
  description,
  icon,
  meta,
  title
}: {
  children: ReactNode;
  description: string;
  icon: ReactNode;
  meta: Array<{ label: string; value: string }>;
  title: string;
}) {
  return (
    <div className="parameter-editor-shell">
      <div className="parameter-editor-hero">
        <div className="parameter-editor-title-row">
          <span className="parameter-editor-icon">{icon}</span>
          <div>
            <h3>{title}</h3>
            <p>{description}</p>
          </div>
        </div>
        <div className="parameter-editor-summary">
          {meta.map((item) => (
            <div className="parameter-editor-summary-item" key={item.label}>
              <span>{item.label}</span>
              <b>{item.value}</b>
            </div>
          ))}
        </div>
      </div>
      <div className="parameter-edit-stack">{children}</div>
    </div>
  );
}

function EditSection({
  children,
  description,
  emphasis,
  title
}: {
  children: ReactNode;
  description: string;
  emphasis?: "primary";
  title: string;
}) {
  return (
    <section className="parameter-edit-section" data-emphasis={emphasis ?? "standard"}>
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
    <button aria-label={`Help for ${label}`} className="parameter-help-button" data-help={help} type="button">
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

function submittedRunName(config: StrategyConfig): string {
  const currentName = config.run_name.trim();
  const base = generatedRunName(config.strategy_name, config.strategy_version);
  if (!currentName || isDefaultRunName(currentName) || isGeneratedRunName(currentName, config.strategy_name, config.strategy_version)) {
    return base;
  }
  return `${base}_${slugRunToken(currentName)}`;
}

function generatedRunName(strategyName: string, strategyVersion: string, date = new Date()): string {
  const timestamp = [
    date.getFullYear(),
    padDatePart(date.getMonth() + 1),
    padDatePart(date.getDate()),
    "_",
    padDatePart(date.getHours()),
    padDatePart(date.getMinutes()),
    padDatePart(date.getSeconds()),
    "_",
    String(date.getMilliseconds()).padStart(3, "0")
  ].join("");
  return `${slugRunToken(strategyName)}_${slugRunToken(strategyVersion)}_${timestamp}`;
}

function isDefaultRunName(value: string): boolean {
  return ["react app run", "untitled run"].includes(value.trim().toLowerCase());
}

function isGeneratedRunName(value: string, strategyName: string, strategyVersion: string): boolean {
  const name = slugRunToken(value);
  const prefix = `${slugRunToken(strategyName)}_${slugRunToken(strategyVersion)}_`;
  return name.startsWith(prefix) && new RegExp(`^${escapeRegExp(prefix)}\\d{8}_\\d{6}_\\d{3}(?:_.+)?$`).test(name);
}

function slugRunToken(value: string): string {
  return value.replace(/[^a-zA-Z0-9]+/g, "_").replace(/^_+|_+$/g, "").toLowerCase() || "run";
}

function padDatePart(value: number): string {
  return String(value).padStart(2, "0");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function parseIsoDate(value: string): { date: Date; month: number; year: number } | null {
  const [yearText, monthText, dayText] = value.split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  if (![year, month, day].every(Number.isFinite)) return null;
  return { date: new Date(Date.UTC(year, month - 1, day)), month, year };
}

function BacktestJobPanel({
  config,
  job,
  outputRoot,
  runId
}: {
  config: StrategyConfig;
  job: Record<string, unknown> | null;
  outputRoot: string;
  runId?: string;
}) {
  const events = Array.isArray(job?.events) ? (job?.events as Record<string, unknown>[]) : [];
  const result = job?.result && typeof job.result === "object" ? job.result as Record<string, unknown> : null;
  const resultRunDir = String(result?.run_dir ?? "");
  const jobRunDir = String(job?.run_dir ?? "");
  const jobConfig = job?.config && typeof job.config === "object" ? job.config as Record<string, unknown> : null;
  const [detail, setDetail] = useState<RunDetailPayload | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [tab, setTab] = useState("Backtest Results");
  const shouldLoadTables = tab !== "Backtest Results";
  const isLiveRun = !runId && ["running", "queued"].includes(String(job?.status ?? "").toLowerCase());
  const metadataRunDir = String(detail?.metadata.run_dir ?? "");
  const latestRunDir = resultRunDir || jobRunDir || metadataRunDir || [...events].reverse().map((event) => String(event.run_dir ?? "")).find(Boolean) || "";
  const latestRunId = runId || (latestRunDir ? latestRunDir.split(/[\\/]/).filter(Boolean).at(-1) ?? "" : "");

  useEffect(() => {
    setDetail(null);
    setDetailError(null);
    setTab("Backtest Results");
  }, [latestRunId]);

  useEffect(() => {
    if (!latestRunId) {
      setDetail(null);
      setDetailError(null);
      return;
    }
    let canceled = false;
    const loadDetail = () => {
      fetchRunDetail(latestRunId, outputRoot, shouldLoadTables)
        .then((payload) => {
          if (canceled) return;
          setDetail((current) => (shouldLoadTables ? payload : mergeLiveRunDetail(current, payload)));
          setDetailError(null);
        })
        .catch((err) => {
          if (!canceled) setDetailError(err instanceof Error ? err.message : String(err));
        });
    };
    loadDetail();
    if (!isLiveRun || shouldLoadTables) {
      return () => {
        canceled = true;
      };
    }
    const timer = window.setInterval(loadDetail, 2500);
    return () => {
      canceled = true;
      window.clearInterval(timer);
    };
  }, [latestRunId, outputRoot, shouldLoadTables, isLiveRun, `${job?.status ?? "not-started"}-${events.length}`]);

  const progress = buildBacktestProgress(job, detail, config);
  const activeRunName = String(detail?.metadata.run_name ?? jobConfig?.run_name ?? config.run_name ?? "Backtest Results");
  const metrics = buildLiveBacktestMetrics(job, detail);

  return (
    <section className="panel backtest-results-panel" style={{ marginTop: 16 }}>
      <div className="toolbar" style={{ justifyContent: "space-between" }}>
        <h2 className="backtest-results-title">{activeRunName}</h2>
        <SemanticBadge tone={toneForStatus(progress.status)}>{progress.status}</SemanticBadge>
      </div>
      <Tabs tabs={["Backtest Results", "Daily", "Trades", "Orders", "Fills", "Positions", "Scanner", "Rejected", "Progress Events", "Logs"]} active={tab} onChange={setTab} />
      <div className="backtest-results-tab-content">
        {tab === "Backtest Results" ? (
          <>
            <ProgressMeter
              done={progress.done}
              elapsed_sec={0}
              label={progress.label}
              progress={progress.percent}
              status={progress.meterStatus}
              total={progress.total}
            />
            <NewRunMetricStrip metrics={metrics} />
            <div className="toolbar">
              <span className="meta-tag">{formatNumber(progress.done)}/{formatNumber(progress.total)} {progress.unitLabel}</span>
              {progress.currentSession ? <span className="meta-tag">{progress.currentSession}</span> : null}
              {latestRunDir ? <span className="meta-tag">{latestRunDir}</span> : null}
            </div>
            {detailError ? <div className="error-panel">{detailError}</div> : null}
            <PnlCandleChart payload={detail?.portfolio_candles} runName={activeRunName} title="Portfolio P/L Candles" />
          </>
        ) : null}
        {tab === "Daily" ? <DataTable rows={detail?.tables.daily.rows ?? []} /> : null}
        {tab === "Trades" ? <DataTable rows={detail?.tables.trades.rows ?? []} /> : null}
        {tab === "Orders" ? <DataTable rows={detail?.tables.orders.rows ?? []} /> : null}
        {tab === "Fills" ? <DataTable rows={detail?.tables.fills.rows ?? []} /> : null}
        {tab === "Positions" ? <DataTable rows={detail?.tables.positions.rows ?? []} /> : null}
        {tab === "Scanner" ? <DataTable rows={detail?.tables.scanner.rows ?? []} /> : null}
        {tab === "Rejected" ? <DataTable rows={detail?.tables.rejections.rows ?? []} /> : null}
        {tab === "Progress Events" ? <DataTable rows={events.map((event) => ({ session_date: event.session_date, status: event.status, run_dir: event.run_dir, ...((event.daily_summary as Record<string, unknown>) ?? {}) }))} /> : null}
        {tab === "Logs" ? <pre className="markdown-panel backtest-results-log">{detail?.logs || "No logs yet."}</pre> : null}
        {job?.error ? <div className="error-panel">{String(job.error)}</div> : null}
      </div>
    </section>
  );
}

function buildLiveBacktestMetrics(job: Record<string, unknown> | null, detail: RunDetailPayload | null): NewRunMetric[] {
  const summary = liveSummary(job, detail);
  const totalPnl = finiteNumber(summary.total_pnl);
  const returnPct = finiteNumber(summary.return_pct);
  const sharpe = finiteNumber(summary.sharpe_ratio);
  const maxDrawdownPct = finiteNumber(summary.max_drawdown_pct);
  const maxDrawdown = finiteNumber(summary.max_drawdown);
  const tradeCount = finiteNumber(summary.trade_count);
  const winRate = finiteNumber(summary.win_rate);
  const profitFactor = finiteNumber(summary.profit_factor);
  const unrealized = finiteNumber(summary.open_unrealized_pnl);
  const maxUnrealizedGain = finiteNumber(summary.max_open_unrealized_pnl);
  const maxUnrealizedLoss = finiteNumber(summary.max_open_unrealized_loss);

  return [
    {
      detail: "Mark-to-market net P/L",
      icon: <Banknote size={15} />,
      label: "P/L",
      tone: signedTone(totalPnl),
      value: formatMoney(totalPnl)
    },
    {
      detail: "Total return on equity",
      icon: <Percent size={15} />,
      label: "Return",
      tone: signedTone(returnPct),
      value: formatPct(returnPct)
    },
    {
      detail: "Annualized from live equity returns",
      icon: <Activity size={15} />,
      label: "Sharpe",
      tone: sharpeTone(sharpe),
      value: formatNumber(sharpe, 2)
    },
    {
      detail: `${formatMoney(maxDrawdown)} peak-to-trough`,
      icon: <Gauge size={15} />,
      label: "Max DD",
      tone: drawdownTone(maxDrawdownPct),
      value: formatPct(maxDrawdownPct)
    },
    {
      detail: "Closed trades",
      icon: <ListChecks size={15} />,
      label: "Trades",
      tone: countTone(tradeCount),
      value: formatNumber(tradeCount)
    },
    {
      detail: "Winning closed trades",
      icon: <Percent size={15} />,
      label: "Win Rate",
      tone: winRateTone(winRate, tradeCount),
      value: formatPct(winRate)
    },
    {
      detail: "Gross profit / gross loss",
      icon: <Shield size={15} />,
      label: "Profit Factor",
      tone: profitFactorTone(profitFactor, tradeCount),
      value: formatNumber(profitFactor, 2)
    },
    {
      detail: "Open mark-to-market P/L",
      icon: <Banknote size={15} />,
      label: "Unrealized",
      tone: signedTone(unrealized),
      value: formatMoney(unrealized)
    },
    {
      detail: "Worst open unrealized loss",
      icon: <Gauge size={15} />,
      label: "Max Unrlzd Loss",
      tone: unrealizedLossTone(maxUnrealizedLoss),
      value: formatMoney(maxUnrealizedLoss)
    },
    {
      detail: "Best open unrealized gain",
      icon: <Gauge size={15} />,
      label: "Max Unrlzd Gain",
      tone: signedTone(maxUnrealizedGain),
      value: formatMoney(maxUnrealizedGain)
    }
  ];
}

function liveSummary(job: Record<string, unknown> | null, detail: RunDetailPayload | null): Record<string, unknown> {
  const isLiveRun = ["running", "queued", "canceling"].includes(String(job?.status ?? "").toLowerCase());
  const jobSummary = job?.summary && typeof job.summary === "object" ? job.summary as Record<string, unknown> : null;
  if (isLiveRun && jobSummary) return jobSummary;
  if (detail?.summary && Object.keys(detail.summary).length > 0) return detail.summary;
  const metadataSummary = detail?.metadata?.summary;
  if (metadataSummary && typeof metadataSummary === "object") return metadataSummary as Record<string, unknown>;
  if (jobSummary) return jobSummary;
  return {};
}

function signedTone(value: number): NewRunMetricTone {
  if (value > 0) return "success";
  if (value < 0) return "danger";
  return "neutral";
}

function sharpeTone(value: number): NewRunMetricTone {
  if (value >= 1) return "success";
  if (value >= 0.3) return "warning";
  if (value < 0) return "danger";
  return "neutral";
}

function drawdownTone(value: number): NewRunMetricTone {
  if (value >= 0.1) return "danger";
  if (value > 0) return "warning";
  return "neutral";
}

function unrealizedLossTone(value: number): NewRunMetricTone {
  if (value < 0) return "danger";
  return "neutral";
}

function countTone(value: number): NewRunMetricTone {
  return value > 0 ? "info" : "neutral";
}

function winRateTone(value: number, tradeCount: number): NewRunMetricTone {
  if (tradeCount <= 0) return "neutral";
  if (value >= 0.5) return "success";
  if (value >= 0.4) return "warning";
  return "danger";
}

function profitFactorTone(value: number, tradeCount: number): NewRunMetricTone {
  if (tradeCount <= 0 || value <= 0) return "neutral";
  if (value >= 1.5) return "success";
  if (value >= 1) return "warning";
  return "danger";
}

function buildBacktestProgress(job: Record<string, unknown> | null, detail: RunDetailPayload | null, config: StrategyConfig) {
  const status = String(job?.status ?? detail?.metadata.status ?? "not started").replaceAll("_", " ");
  const normalizedStatus = status.toLowerCase();
  const barDone = finiteNumber(job?.processed_event_bars ?? detail?.metadata.processed_event_bars);
  const barTotal = finiteNumber(job?.total_event_bars ?? detail?.metadata.total_event_bars);
  if (barTotal > 0) {
    const done = Math.min(barTotal, Math.max(0, barDone));
    return {
      currentSession: String(job?.current_session ?? detail?.metadata.latest_session ?? ""),
      done,
      label: "Backtest bar progress",
      meterStatus: normalizedStatus === "not started" ? "queued" : status,
      percent: normalizedStatus.includes("complete") ? 100 : (done / barTotal) * 100,
      status,
      total: Math.max(1, barTotal),
      unitLabel: `${String(job?.progress_unit ?? detail?.metadata.progress_unit ?? "event")} bars`
    };
  }
  const eventCount = Array.isArray(job?.events) ? (job?.events as unknown[]).filter(Boolean).length : 0;
  const completed = finiteNumber(detail?.metadata.completed_sessions ?? eventCount);
  const totalFromMetadata = finiteNumber(detail?.metadata.total_sessions);
  const total = totalFromMetadata > 0 ? totalFromMetadata : estimateCalendarDays(config.start_date, config.end_date);
  const done = Math.min(total, Math.max(0, completed));
  const percent = normalizedStatus.includes("complete") ? 100 : total > 0 ? (done / total) * 100 : 0;
  return {
    currentSession: String(job?.current_session ?? detail?.metadata.latest_session ?? ""),
    done,
    label: "Backtest session progress",
    meterStatus: normalizedStatus === "not started" ? "queued" : status,
    percent,
    status,
    total: Math.max(1, total),
    unitLabel: "sessions"
  };
}

function finiteNumber(value: unknown): number {
  const numeric = Number(value ?? 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

function estimateCalendarDays(start: string, end: string) {
  const startParts = parseIsoDate(start);
  const endParts = parseIsoDate(end);
  if (!startParts || !endParts) return 1;
  return Math.max(1, Math.round((endParts.date.getTime() - startParts.date.getTime()) / 86_400_000) + 1);
}

function fetchRunDetail(runId: string, outputRoot: string, includeTables = true) {
  return api<RunDetailPayload>(
    `/api/backtests/runs/${runId}${query({ include_logs: includeTables, include_tables: includeTables, output_root: outputRoot })}`
  );
}

function mergeLiveRunDetail(current: RunDetailPayload | null, next: RunDetailPayload): RunDetailPayload {
  if (!current) return next;
  return {
    ...next,
    logs: next.logs || current.logs,
    tables: current.tables
  };
}

function PnlCandleChart({ payload, runName, title }: { payload?: PortfolioCandlePayload | null; runName: string; title: string }) {
  const availableTimeframes = useMemo(() => portfolioChartTimeframes(payload), [payload]);
  const defaultTimeframe = availableTimeframes.includes(payload?.default_timeframe ?? "") ? String(payload?.default_timeframe) : availableTimeframes[0] ?? "1h";
  const [timeframe, setTimeframe] = useState(defaultTimeframe);
  const periodBounds = useMemo(() => portfolioChartPeriodBounds(payload, availableTimeframes), [availableTimeframes, payload]);
  const [period, setPeriod] = useState({ end: periodBounds.end, start: periodBounds.start });

  useEffect(() => {
    setTimeframe(defaultTimeframe);
  }, [defaultTimeframe]);

  useEffect(() => {
    setPeriod({ end: periodBounds.end, start: periodBounds.start });
  }, [periodBounds.end, periodBounds.start]);

  const chartPayload = useMemo(
    () => portfolioChartPayload(payload, timeframe, period.start, period.end),
    [payload, period.end, period.start, timeframe]
  );

  function updatePeriod(start: string, end: string) {
    setPeriod(start <= end ? { start, end } : { start: end, end: start });
  }

  return (
    <section className="pnl-candle-chart">
      <div className="toolbar" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>{title}</h2>
      </div>
      <ChartPanel
        emptyMessage="No portfolio P/L candles have been written yet."
        featureOptions={[]}
        indicatorOptions={[]}
        normalizeTicker={false}
        onPeriodChange={updatePeriod}
        onTickerChange={() => undefined}
        onTimeframeChange={setTimeframe}
        onVisibleColumnsChange={() => undefined}
        onVisibleSupervisionGroupsChange={() => undefined}
        payload={chartPayload}
        periodEnd={period.end}
        periodMax={periodBounds.max}
        periodMin={periodBounds.min}
        periodStart={period.start}
        showIndicatorControls={false}
        showSupervisionControls={false}
        ticker={runName || "Backtest"}
        tickerInputWidth={180}
        tickerMaxLength={64}
        timeframe={timeframe}
        timeframes={availableTimeframes}
        visibleColumns={["portfolio_drawdown", "open_unrealized_pnl"]}
        visibleSupervisionGroups={[]}
      />
    </section>
  );
}

function portfolioChartTimeframes(payload?: PortfolioCandlePayload | null) {
  const allowed = ["1h", "2h", "4h", "1d"];
  const provided = payload?.timeframes?.length ? payload.timeframes.map(String) : allowed;
  const filtered = allowed.filter((timeframe) => provided.includes(timeframe));
  return filtered.length ? filtered : ["1h"];
}

function portfolioChartPeriodBounds(payload: PortfolioCandlePayload | null | undefined, timeframes: string[]) {
  const timestamps = timeframes.flatMap((timeframe) => (payload?.candles?.[timeframe] ?? []).map((candle) => Number(candle.time)).filter(Number.isFinite));
  if (!timestamps.length) return { end: "", max: "", min: "", start: "" };
  const dates = timestamps.map(dateStringFromTimestamp).filter(Boolean).sort();
  const min = dates[0] ?? "";
  const max = dates[dates.length - 1] ?? min;
  return { end: max, max, min, start: min };
}

function portfolioChartPayload(payload: PortfolioCandlePayload | null | undefined, timeframe: string, periodStart: string, periodEnd: string): ChartPayload | null {
  const sourceCandles = (payload?.candles?.[timeframe] ?? [])
    .filter((candle) => Number.isFinite(candle.time))
    .filter((candle) => candleInPeriod(candle, periodStart, periodEnd));
  const candles = sourceCandles.map((candle) => ({
    close: Number(candle.close ?? 0),
    high: Number(candle.high ?? 0),
    low: Number(candle.low ?? 0),
    open: Number(candle.open ?? 0),
    time: Number(candle.time)
  }));
  if (!candles.length) return null;
  return {
    candles,
    markers: [],
    oscillator_series: portfolioRiskSeries(sourceCandles),
    overlay_series: [],
    regions: [],
    volume: []
  };
}

function portfolioRiskSeries(candles: PortfolioCandle[]): ChartPayload["oscillator_series"] {
  const drawdownData = candles
    .map((candle) => ({
      color: "#dc2626",
      time: Number(candle.time),
      value: Number(candle.drawdown_close ?? 0)
    }))
    .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value));
  const unrealizedData = candles
    .map((candle) => ({
      time: Number(candle.time),
      value: Number(candle.open_unrealized_close ?? 0)
    }))
    .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value));
  return [
    {
      color: "#dc2626",
      column: "portfolio_drawdown",
      data: drawdownData,
      displayItemId: "portfolio_drawdown",
      label: "Drawdown",
      lineWidth: 2,
      paneKey: "portfolio_risk",
      style: "histogram"
    },
    {
      color: "#2563eb",
      column: "open_unrealized_pnl",
      data: unrealizedData,
      displayItemId: "open_unrealized_pnl",
      label: "Open Unrealized P/L",
      lineStyle: "solid",
      lineWidth: 2,
      paneKey: "portfolio_risk",
      style: "line"
    }
  ];
}

function candleInPeriod(candle: PortfolioCandle, periodStart: string, periodEnd: string) {
  if (!periodStart || !periodEnd) return true;
  const date = dateStringFromTimestamp(Number(candle.time));
  return Boolean(date && date >= periodStart && date <= periodEnd);
}

function dateStringFromTimestamp(timestamp: number) {
  if (!Number.isFinite(timestamp)) return "";
  return new Date(timestamp * 1000).toISOString().slice(0, 10);
}

type RunDetailPayload = {
  metadata: Record<string, unknown>;
  summary: Record<string, unknown>;
  tables: Record<string, { columns: string[]; rows: Record<string, unknown>[] }>;
  portfolio_candles?: PortfolioCandlePayload;
  logs: string;
};

type PortfolioCandle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  equity_open?: number;
  equity_high?: number;
  equity_low?: number;
  equity_close?: number;
  open_unrealized_open?: number;
  open_unrealized_high?: number;
  open_unrealized_low?: number;
  open_unrealized_close?: number;
  realized_pnl_open?: number;
  realized_pnl_high?: number;
  realized_pnl_low?: number;
  realized_pnl_close?: number;
  drawdown_open?: number;
  drawdown_high?: number;
  drawdown_low?: number;
  drawdown_close?: number;
  drawdown_pct_close?: number;
  gross_exposure?: number;
};

type PortfolioCandlePayload = {
  timeframes: string[];
  default_timeframe: string;
  candles: Record<string, PortfolioCandle[]>;
};
