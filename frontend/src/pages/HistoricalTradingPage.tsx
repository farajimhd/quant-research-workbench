import {
  ArrowLeft,
  CalendarDays,
  CheckCircle2,
  CircleDollarSign,
  Clock3,
  Database,
  Gauge,
  Layers3,
  Play,
  RadioTower,
  ShieldCheck,
  SlidersHorizontal,
  TriangleAlert,
  WalletCards,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { api } from "../api/client";
import { TradingWorkspace } from "../app/components/TradingWorkspace";

type HistoricalMode = "backtest" | "replay";
type HistoricalView = "setup" | "workspace";

type TradingStrategy = {
  automatic: boolean;
  enabled: boolean;
  implementation: string;
  name: string;
  revision: number;
  strategy_id: string;
};

type StrategyPayload = {
  row_count: number;
  rows: TradingStrategy[];
};

type HistoricalGatewayPayload = {
  base_url: string;
  error?: string;
  online: boolean;
  ready: boolean;
  status: string;
};

type HistoricalWindowPayload = {
  anchor_date: string;
  anchor_semantics: "exclusive" | "inclusive";
  broker: string;
  end: string;
  mode: HistoricalMode;
  session_count: number;
  sessions: string[];
  source: string;
  source_url: string;
  start: string;
};

export function HistoricalTradingPage({ mode }: { mode: HistoricalMode }) {
  const [view, setView] = useState<HistoricalView>(() => readInitialView(mode));
  const [anchorDate, setAnchorDate] = useState(todayIsoDate);
  const [replayEndDate, setReplayEndDate] = useState(todayIsoDate);
  const [sessionCount, setSessionCount] = useState(20);
  const [strategies, setStrategies] = useState<TradingStrategy[]>([]);
  const [selectedStrategy, setSelectedStrategy] = useState("");
  const [gateway, setGateway] = useState<HistoricalGatewayPayload | null>(null);
  const [windowPreview, setWindowPreview] = useState<HistoricalWindowPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [error, setError] = useState("");
  const [initialCash, setInitialCash] = useState(100000);
  const [slippageBps, setSlippageBps] = useState(0);
  const [partialFills, setPartialFills] = useState(true);
  const [outsideRth, setOutsideRth] = useState(false);
  const [recentNewsLimit, setRecentNewsLimit] = useState(20);
  const [includeSec, setIncludeSec] = useState(true);
  const [includeXbrl, setIncludeXbrl] = useState(false);

  const eligibleStrategies = useMemo(
    () => strategies.filter((strategy) => strategy.enabled && (mode === "replay" || strategy.automatic)),
    [mode, strategies],
  );
  const selected = eligibleStrategies.find((strategy) => strategy.strategy_id === selectedStrategy);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api<StrategyPayload>("/api/trading/strategies", { timeoutMs: 10000 }),
      api<HistoricalGatewayPayload>("/api/trading/historical-gateway", { timeoutMs: 10000 }),
    ])
      .then(([strategyPayload, gatewayPayload]) => {
        if (cancelled) return;
        setStrategies(strategyPayload.rows);
        setGateway(gatewayPayload);
        const eligible = strategyPayload.rows.filter((strategy) => strategy.enabled && (mode === "replay" || strategy.automatic));
        setSelectedStrategy((current) => current || eligible[0]?.strategy_id || "");
        setError("");
      })
      .catch((exc) => {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [mode]);

  useEffect(() => {
    let cancelled = false;
    setPreviewLoading(true);
    api<HistoricalWindowPayload>("/api/trading/historical-window", {
      body: JSON.stringify({
        anchor_date: anchorDate,
        mode,
        replay_end_date: mode === "replay" ? replayEndDate : null,
        session_count: mode === "backtest" ? sessionCount : 1,
      }),
      method: "POST",
      timeoutMs: 10000,
    })
      .then((payload) => {
        if (!cancelled) {
          setWindowPreview(payload);
          setError("");
        }
      })
      .catch((exc) => {
        if (!cancelled) {
          setWindowPreview(null);
          setError(exc instanceof Error ? exc.message : String(exc));
        }
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });
    return () => { cancelled = true; };
  }, [anchorDate, mode, replayEndDate, sessionCount]);

  if (view === "workspace") {
    return (
      <div className="historical-page workspace-view">
        <header className="historical-workspace-header">
          <button className="button secondary compact" onClick={() => setView("setup")} type="button"><ArrowLeft size={14} /> Setup</button>
          <div>
            <span>{mode === "replay" ? "Replay workspace" : "Backtest workspace"}</span>
            <strong>{selected ? `${selected.name} · r${selected.revision}` : "No strategy revision selected"}</strong>
          </div>
          <div className="historical-workspace-header-status">
            <small>{windowPreview ? formatWindow(windowPreview) : "Historical window unresolved"}</small>
            <span data-status={gateway?.ready ? "ready" : "error"}>{gateway?.ready ? "Source ready" : "Source unavailable"}</span>
          </div>
        </header>
        <TradingWorkspace
          clockLabel={windowPreview ? formatClock(windowPreview.start) : "Run clock unavailable"}
          historicalSourceReady={Boolean(gateway?.ready)}
          mode={mode}
          runLabel={selected ? `${selected.name} r${selected.revision}` : `${mode === "replay" ? "Replay" : "Backtest"} not configured`}
          runStatus="idle"
        />
      </div>
    );
  }

  const blockers = [
    !gateway?.ready ? "QMD History must be running." : "",
    !windowPreview ? "Resolve a valid exchange-session window." : "",
    !selected ? "Persist and select a runtime-compatible strategy revision." : "",
    "The shared historical run controller API is not implemented yet.",
  ].filter(Boolean);

  return (
    <div className="historical-page setup-view">
      <header className="historical-page-header">
        <div className="historical-page-heading">
          <span className="historical-eyebrow">Historical trading</span>
          <h1>{mode === "replay" ? "Replay" : "Backtest"}</h1>
          <p>{mode === "replay"
            ? "Configure an inclusive historical session, then operate it through the same container workspace used by every trading mode."
            : "Evaluate an automatic strategy on sessions before the anchor date using canonical events and the IBKR-shaped simulated broker."}</p>
        </div>
        <div className="historical-page-header-actions">
          <button className="button secondary" onClick={() => setView("workspace")} type="button"><Layers3 size={15} /> Configure canvas</button>
          <button className="button primary" disabled title={blockers.join(" ")} type="button"><Play size={15} /> Start {mode === "replay" ? "replay" : "backtest"}</button>
        </div>
      </header>

      <section className="historical-readiness-strip" aria-label="Historical run readiness">
        <ReadinessItem icon={<RadioTower size={15} />} label="Historical source" value={gateway?.ready ? "Ready" : loading ? "Checking" : "Unavailable"} status={gateway?.ready ? "ready" : loading ? "loading" : "error"} detail={gateway?.base_url || "QMD History"} />
        <ReadinessItem icon={<CalendarDays size={15} />} label="Resolved sessions" value={previewLoading ? "Resolving" : `${windowPreview?.session_count ?? 0}`} status={windowPreview ? "ready" : previewLoading ? "loading" : "error"} detail={windowPreview ? `${windowPreview.anchor_semantics} anchor` : "No valid window"} />
        <ReadinessItem icon={<ShieldCheck size={15} />} label="Broker" value="Simulated IBKR" status="ready" detail="Deterministic account state" />
        <ReadinessItem icon={<Gauge size={15} />} label="Run controller" value="Not connected" status="warning" detail="UI will not fall back to legacy bars" />
      </section>

      {error ? <div className="historical-inline-error"><TriangleAlert size={16} /><span>{error}</span></div> : null}

      <div className="historical-setup-layout">
        <main className="historical-setup-main">
          <SetupSection icon={<CalendarDays size={16} />} step="01" title="Historical window" subtitle={mode === "replay" ? "The anchor session is included." : "The anchor date is excluded; sessions are selected backward."}>
            <div className="historical-field-grid">
              <label className="historical-field">
                <span>{mode === "replay" ? "Start date · inclusive" : "Anchor date · exclusive"}</span>
                <input type="date" value={anchorDate} onChange={(event) => setAnchorDate(event.target.value)} />
                <small>{mode === "replay" ? "Replay begins at 04:00 New York on this exchange session." : "No event on this date is included in the backtest."}</small>
              </label>
              {mode === "replay" ? (
                <label className="historical-field">
                  <span>End date · inclusive</span>
                  <input min={anchorDate} type="date" value={replayEndDate} onChange={(event) => setReplayEndDate(event.target.value)} />
                  <small>Use the same date for a single-session replay.</small>
                </label>
              ) : (
                <label className="historical-field">
                  <span>Prior exchange sessions</span>
                  <input max={260} min={1} type="number" value={sessionCount} onChange={(event) => setSessionCount(Math.max(1, Number(event.target.value) || 1))} />
                  <small>The backend calendar resolves the exact trading dates.</small>
                </label>
              )}
            </div>
            {windowPreview ? (
              <div className="historical-window-preview">
                <Clock3 size={15} />
                <div><span>Resolved event window</span><strong>{formatWindow(windowPreview)}</strong></div>
                <small>{windowPreview.sessions[0]} → {windowPreview.sessions.at(-1)}</small>
              </div>
            ) : null}
          </SetupSection>

          <SetupSection icon={<SlidersHorizontal size={16} />} step="02" title="Strategy revision" subtitle="Runs bind to an immutable persisted revision; backtests accept automatic strategies only.">
            <label className="historical-field full">
              <span>Strategy</span>
              <select value={selectedStrategy} onChange={(event) => setSelectedStrategy(event.target.value)}>
                <option value="">{eligibleStrategies.length ? "Select a strategy revision" : "No compatible persisted strategies"}</option>
                {eligibleStrategies.map((strategy) => <option key={`${strategy.strategy_id}-${strategy.revision}`} value={strategy.strategy_id}>{strategy.name} · revision {strategy.revision}{strategy.automatic ? " · automatic" : ""}</option>)}
              </select>
              <small>{selected ? `${selected.implementation} · ${selected.automatic ? "automatic" : "manual or semi-automatic"}` : "Create or migrate a strategy into the central trading strategy authority."}</small>
            </label>
          </SetupSection>

          <SetupSection icon={<WalletCards size={16} />} step="03" title="Account and execution" subtitle="The simulated broker preserves IBKR-shaped orders, lifecycle states, executions, positions, and portfolio resources.">
            <div className="historical-field-grid three">
              <label className="historical-field"><span>Simulated account</span><input readOnly value="SIM-PRIMARY" /><small>Independent state per account.</small></label>
              <label className="historical-field"><span>Starting cash · USD</span><input min={0} step={1000} type="number" value={initialCash} onChange={(event) => setInitialCash(Number(event.target.value) || 0)} /></label>
              <label className="historical-field"><span>Slippage · bps</span><input min={0} step={0.1} type="number" value={slippageBps} onChange={(event) => setSlippageBps(Number(event.target.value) || 0)} /></label>
            </div>
            <div className="historical-toggle-row">
              <Toggle checked={partialFills} label="Partial fills" onChange={setPartialFills} />
              <Toggle checked={outsideRth} label="Allow outside RTH" onChange={setOutsideRth} />
              <span className="historical-fee-model"><CircleDollarSign size={14} /> IBKR aligned fee model</span>
            </div>
          </SetupSection>

          <SetupSection icon={<Database size={16} />} step="04" title="Context containers" subtitle="Containers keep one rendering contract while their source binding changes by mode.">
            <div className="historical-field-grid three">
              <label className="historical-field"><span>Recent news items</span><input max={200} min={1} type="number" value={recentNewsLimit} onChange={(event) => setRecentNewsLimit(Math.max(1, Number(event.target.value) || 1))} /><small>Historical modes read only persisted point-in-time rows.</small></label>
              <Toggle checked={includeSec} label="SEC filing context" onChange={setIncludeSec} />
              <Toggle checked={includeXbrl} label="XBRL fact context" onChange={setIncludeXbrl} />
            </div>
          </SetupSection>
        </main>

        <aside className="historical-review-panel">
          <header><span>Review</span><strong>{mode === "replay" ? "Replay configuration" : "Backtest configuration"}</strong></header>
          <ReviewRow label="Mode" value={mode === "replay" ? "Replay" : "Backtest"} />
          <ReviewRow label="Source" value="QMD History · canonical events" />
          <ReviewRow label="Window" value={windowPreview ? `${windowPreview.session_count} session${windowPreview.session_count === 1 ? "" : "s"}` : "Unresolved"} />
          <ReviewRow label="Strategy" value={selected ? `${selected.name} r${selected.revision}` : "Not selected"} />
          <ReviewRow label="Account" value={`SIM-PRIMARY · ${formatCurrency(initialCash)}`} />
          <ReviewRow label="Execution" value={`${slippageBps} bps · ${partialFills ? "partial fills" : "full fills"}`} />
          <ReviewRow label="Context" value={`News ${recentNewsLimit} · SEC ${includeSec ? "on" : "off"} · XBRL ${includeXbrl ? "on" : "off"}`} />
          <div className="historical-blockers">
            <span>Start blockers</span>
            {blockers.map((blocker) => <p key={blocker}><TriangleAlert size={13} /> {blocker}</p>)}
          </div>
          <button className="button secondary" onClick={() => setView("workspace")} type="button"><Layers3 size={15} /> Configure workspace containers</button>
        </aside>
      </div>
    </div>
  );
}

function SetupSection({ children, icon, step, subtitle, title }: { children: ReactNode; icon: ReactNode; step: string; subtitle: string; title: string }) {
  return (
    <section className="historical-setup-section">
      <header><span>{step}</span><div className="historical-setup-section-icon">{icon}</div><div><strong>{title}</strong><small>{subtitle}</small></div></header>
      <div className="historical-setup-section-body">{children}</div>
    </section>
  );
}

function ReadinessItem({ detail, icon, label, status, value }: { detail: string; icon: ReactNode; label: string; status: string; value: string }) {
  return <article data-status={status}><div>{icon}<span>{label}</span></div><strong>{value}</strong><small>{detail}</small></article>;
}

function Toggle({ checked, label, onChange }: { checked: boolean; label: string; onChange: (checked: boolean) => void }) {
  return <label className="historical-toggle"><input checked={checked} onChange={(event) => onChange(event.target.checked)} type="checkbox" /><span><CheckCircle2 size={14} /> {label}</span></label>;
}

function ReviewRow({ label, value }: { label: string; value: string }) {
  return <div className="historical-review-row"><span>{label}</span><strong>{value}</strong></div>;
}

function readInitialView(mode: HistoricalMode): HistoricalView {
  const query = new URLSearchParams(window.location.search);
  return query.get("historicalWorkspace") === mode ? "workspace" : "setup";
}

function todayIsoDate() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

function formatWindow(windowPreview: HistoricalWindowPayload) {
  return `${formatClock(windowPreview.start)} → ${formatClock(windowPreview.end)}`;
}

function formatClock(value: string) {
  return new Intl.DateTimeFormat("en-CA", { dateStyle: "medium", timeStyle: "short", timeZone: "America/New_York" }).format(new Date(value));
}

function formatCurrency(value: number) {
  return new Intl.NumberFormat("en-US", { currency: "USD", maximumFractionDigits: 0, style: "currency" }).format(value);
}
