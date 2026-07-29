import { CheckCircle2, CircleStop, Gauge, RefreshCcw, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { MarketStatusBadge, historicalMarketStatus } from "../app/components/MarketStatusBadge";

type HistoricalCheck = {
  evidence: string;
  id: string;
  label: string;
  required: boolean;
  status: "blocked" | "error" | "ready";
  summary: string;
};

type HistoricalPreflight = {
  automatic_strategy_count: number;
  checks: HistoricalCheck[];
  strategy_run_ready: boolean;
  window: {
    end: string;
    session_count: number;
    sessions: string[];
    start: string;
  };
};

export function HistoricalTradingPage({ mode }: { mode: "backtest" }) {
  const [anchorDate, setAnchorDate] = useState(previousWeekdayIsoDate);
  const [sessionCount, setSessionCount] = useState(20);
  const [preflight, setPreflight] = useState<HistoricalPreflight | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setChecking(true);
      setError("");
      api<HistoricalPreflight>("/api/trading/historical-preflight", {
        body: JSON.stringify({
          anchor_date: anchorDate,
          mode,
          session_count: sessionCount,
        }),
        method: "POST",
        timeoutMs: 60_000,
      })
        .then((payload) => {
          if (!cancelled) setPreflight(payload);
        })
        .catch((reason) => {
          if (!cancelled) {
            setPreflight(null);
            setError(reason instanceof Error ? reason.message : String(reason));
          }
        })
        .finally(() => {
          if (!cancelled) setChecking(false);
        });
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [anchorDate, mode, refreshKey, sessionCount]);

  return (
    <div className="historical-home">
      <header className="historical-goal-hero">
        <div className="historical-goal-copy">
          <h1>Backtest a strategy</h1>
          <p>Choose an exclusive anchor date and the prior exchange sessions to evaluate.</p>
        </div>
        <MarketStatusBadge value={historicalMarketStatus(anchorDate)} />
      </header>

      {error ? <div className="historical-error-banner"><TriangleAlert size={18} /><div><strong>Preflight failed</strong><span>{error}</span></div></div> : null}

      <div className="historical-home-grid">
        <main className="historical-primary-column">
          <section className="historical-run-card">
            <header>
              <div><span>Run definition</span><strong>Sessions before the anchor</strong></div>
              <button className="button secondary" disabled={checking} onClick={() => setRefreshKey((value) => value + 1)} type="button"><RefreshCcw size={16} /> Check again</button>
            </header>
            <div className="historical-large-fields">
              <label><span>Anchor date · exclusive</span><input onChange={(event) => setAnchorDate(event.target.value)} type="date" value={anchorDate} /><small>The selected date is never included in the result window.</small></label>
              <label><span>Prior exchange sessions</span><input max={260} min={1} onChange={(event) => setSessionCount(Math.max(1, Number(event.target.value) || 1))} type="number" value={sessionCount} /><small>Resolved backward from the exclusive anchor.</small></label>
            </div>
            <header className="historical-evidence-header"><div><span>Preflight</span><strong>Verified dependencies and data</strong></div>{checking ? <span className="historical-checking"><Gauge size={15} /> Checking</span> : null}</header>
            <div className="historical-check-list">
              {preflight?.checks.map((check) => <EvidenceCheck check={check} key={check.id} />)}
            </div>
          </section>
        </main>

        <aside className="historical-action-column">
          <section className="historical-primary-action blocked">
            <CircleStop size={24} />
            <div><strong>Backtest execution is not ready</strong><p>{preflight?.automatic_strategy_count
              ? "An automatic strategy exists, but the historical results workflow has not adopted the Replay run controller."
              : "There are no enabled automatic strategy revisions in the central trading authority."}</p></div>
            <button className="button primary" disabled type="button">Run backtest</button>
          </section>
        </aside>
      </div>
    </div>
  );
}

function EvidenceCheck({ check }: { check: HistoricalCheck }) {
  return <article data-status={check.status}><div className="historical-evidence-icon">{check.status === "ready" ? <CheckCircle2 size={20} /> : <TriangleAlert size={20} />}</div><div><header><strong>{check.label}</strong></header><p>{check.summary}</p><small>{check.evidence}</small></div></article>;
}

function previousWeekdayIsoDate() {
  const value = new Date();
  value.setDate(value.getDate() - 1);
  while (value.getDay() === 0 || value.getDay() === 6) value.setDate(value.getDate() - 1);
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}
