import {
  ArrowLeft,
  BarChart3,
  CheckCircle2,
  CircleStop,
  Database,
  Gauge,
  Pause,
  Play,
  RefreshCcw,
  ShieldAlert,
  SkipForward,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { ChartPanel, type ChartPayload } from "../app/components/ChartPanel";
import { MarketStatusBadge, historicalMarketStatus } from "../app/components/MarketStatusBadge";

type HistoricalMode = "backtest" | "replay";
type HistoricalView = "home" | "replay";
type CheckStatus = "blocked" | "error" | "ready";

type HistoricalCheck = {
  evidence: string;
  id: string;
  label: string;
  required: boolean;
  status: CheckStatus;
  summary: string;
};

type HistoricalBar = {
  bar_start: string;
  close: number;
  high: number;
  low: number;
  open: number;
  quote_count: number;
  spread_bps_mean: number;
  tape_imbalance: number;
  trade_count: number;
  volume: number;
  vwap: number;
};

type HistoricalWindow = {
  end: string;
  session_count: number;
  sessions: string[];
  start: string;
};

type HistoricalPreflight = {
  automatic_strategy_count: number;
  checks: HistoricalCheck[];
  coverage: { event_count?: number; ticker_count?: number };
  market_ready: boolean;
  mode: HistoricalMode;
  strategy_run_ready: boolean;
  window: HistoricalWindow;
};

type HistoricalBarChunk = {
  bar_count: number;
  bars: HistoricalBar[];
  complete: boolean;
  next_offset_minutes: number;
  offset_minutes: number;
};

const TIMEFRAMES = ["1m", "5m"];
const PLAYBACK_SPEEDS = [1, 5, 15];

export function HistoricalTradingPage({ mode }: { mode: HistoricalMode }) {
  const [view, setView] = useState<HistoricalView>("home");
  const [anchorDate, setAnchorDate] = useState(previousWeekdayIsoDate);
  const [sessionCount, setSessionCount] = useState(20);
  const [preflight, setPreflight] = useState<HistoricalPreflight | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    setView("home");
  }, [mode]);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setChecking(true);
      setError("");
      api<HistoricalPreflight>("/api/trading/historical-preflight", {
        body: JSON.stringify({
          anchor_date: anchorDate,
          mode,
          session_count: mode === "replay" ? 1 : sessionCount,
        }),
        method: "POST",
        timeoutMs: 60000,
      })
        .then((payload) => {
          if (!cancelled) setPreflight(payload);
        })
        .catch((exc) => {
          if (!cancelled) {
            setPreflight(null);
            setError(exc instanceof Error ? exc.message : String(exc));
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

  if (mode === "replay" && view === "replay" && preflight) {
    return <ReplayPlayer onBack={() => setView("home")} preflight={preflight} sessionDate={anchorDate} />;
  }

  return (
    <div className="historical-home">
      <header className="historical-goal-hero">
        <div className="historical-goal-copy">
          <h1>{mode === "replay" ? "Replay a trading day" : "Backtest a strategy"}</h1>
          <p>{mode === "replay" ? "Choose one exchange day; symbols and intervals are selected inside replay containers." : "Choose an exclusive anchor date and the prior sessions to evaluate."}</p>
        </div>
        <MarketStatusBadge value={historicalMarketStatus(anchorDate)} />
      </header>

      {error ? <div className="historical-error-banner"><TriangleAlert size={18} /><div><strong>Preflight failed</strong><span>{error}</span></div></div> : null}

      <div className="historical-home-grid">
        <main className="historical-primary-column">
          <section className="historical-run-card">
            <header>
              <div><span>Run definition</span><strong>{mode === "replay" ? "Exactly one exchange day" : "Sessions before the anchor"}</strong></div>
              <button className="button secondary" disabled={checking} onClick={() => setRefreshKey((value) => value + 1)} type="button"><RefreshCcw size={16} /> Check again</button>
            </header>
            <div className="historical-large-fields">
              <label><span>{mode === "replay" ? "Replay date · inclusive" : "Anchor date · exclusive"}</span><input onChange={(event) => setAnchorDate(event.target.value)} type="date" value={anchorDate} /><small>{mode === "replay" ? "The backend enforces this as one 04:00-20:00 New York session; there is no end date." : "The selected date is never included in the result window."}</small></label>
              {mode === "backtest" ? <label><span>Prior exchange sessions</span><input max={260} min={1} onChange={(event) => setSessionCount(Math.max(1, Number(event.target.value) || 1))} type="number" value={sessionCount} /><small>Resolved backward from the exclusive anchor.</small></label> : null}
            </div>
            <header className="historical-evidence-header"><div><span>Preflight</span><strong>Verified dependencies and data</strong></div>{checking ? <span className="historical-checking"><Gauge size={15} /> Checking</span> : null}</header>
            <div className="historical-check-list">
              {preflight?.checks.filter((check) => mode === "backtest" || check.required).map((check) => <EvidenceCheck check={check} key={check.id} />)}
              {!preflight && checking ? <EvidenceSkeleton /> : null}
            </div>
          </section>
        </main>

        <aside className="historical-action-column">
          {mode === "replay" ? (
            <section className="historical-primary-action" data-ready={preflight?.market_ready ? "true" : "false"}>
              <Sparkles size={24} />
              <div><strong>{anchorDate}</strong><p>{preflight ? formatWindow(preflight.window) : "Checking the exchange session"}</p></div>
              <button className="button primary" disabled={checking || !preflight?.market_ready} onClick={() => setView("replay")} type="button"><Play size={18} /> Open one-day replay</button>
              <small>{new Intl.NumberFormat("en-US").format(preflight?.coverage.event_count ?? 0)} canonical events verified for the day.</small>
            </section>
          ) : (
            <section className="historical-primary-action blocked">
              <CircleStop size={24} />
              <div><strong>Backtest execution is not ready</strong><p>{preflight?.automatic_strategy_count
                ? "An automatic strategy exists, but the shared run-controller API is still missing."
                : "There are no enabled automatic strategy revisions in the central trading authority."}</p></div>
              <button className="button primary" disabled type="button"><Play size={18} /> Run backtest</button>
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}

function ReplayPlayer({ onBack, preflight, sessionDate }: { onBack: () => void; preflight: HistoricalPreflight; sessionDate: string }) {
  const [ticker, setTicker] = useState("AAPL");
  const [timeframe, setTimeframe] = useState("1m");
  const [bars, setBars] = useState<HistoricalBar[]>([]);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(5);
  const [nextOffset, setNextOffset] = useState(0);
  const [complete, setComplete] = useState(false);
  const [loadingChunk, setLoadingChunk] = useState(false);
  const [loadError, setLoadError] = useState("");

  const shouldPrefetch = !complete && !loadingChunk && (bars.length < 15 || cursor >= bars.length - 5);
  useEffect(() => {
    if (!shouldPrefetch) return;
    let cancelled = false;
    setLoadingChunk(true);
    setLoadError("");
    api<HistoricalBarChunk>("/api/trading/historical-bars", {
      body: JSON.stringify({
        offset_minutes: nextOffset,
        session_date: sessionDate,
        ticker,
        timeframe,
        window_minutes: 15,
      }),
      method: "POST",
      timeoutMs: 60000,
    })
      .then((payload) => {
        if (cancelled) return;
        setBars((current) => {
          const merged = mergeBars(current, payload.bars);
          if (merged.length) setCursor((value) => value || 1);
          return merged;
        });
        setNextOffset(payload.next_offset_minutes);
        setComplete(payload.complete || payload.next_offset_minutes >= 960);
      })
      .catch((exc) => {
        if (!cancelled) setLoadError(exc instanceof Error ? exc.message : String(exc));
      })
      .finally(() => {
        if (!cancelled) setLoadingChunk(false);
      });
    return () => { cancelled = true; };
  }, [bars.length, complete, cursor, nextOffset, sessionDate, ticker, timeframe]);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setCursor((current) => {
        const next = Math.min(bars.length, current + speed);
        if (next >= bars.length && complete) setPlaying(false);
        return next;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [bars.length, complete, playing, speed]);

  const visibleBars = bars.slice(0, Math.max(1, cursor));
  const current = visibleBars.at(-1);
  const marketStatus = useMemo(() => historicalMarketStatus(sessionDate, current ? marketTimeText(current.bar_start) : "04:00:00"), [current, sessionDate]);
  const chartPayload = useMemo(() => barsToChartPayload(visibleBars), [visibleBars]);
  const loadedEnd = bars.at(-1)?.bar_start;
  const progress = bars.length ? Math.max(1, Math.round((cursor / bars.length) * 100)) : 0;

  function resetMarketSelection(nextTicker: string, nextTimeframe: string) {
    setPlaying(false);
    setTicker(nextTicker);
    setTimeframe(nextTimeframe);
    setBars([]);
    setCursor(0);
    setNextOffset(0);
    setComplete(false);
    setLoadError("");
  }

  return (
    <div className="historical-replay-player">
      <header className="replay-player-header">
        <button className="button secondary" onClick={onBack} type="button"><ArrowLeft size={17} /> Run setup</button>
        <div><span>One-day market replay</span><strong>{sessionDate}</strong></div>
        <div className="replay-source-proof"><CheckCircle2 size={17} /><div><span>QMD History verified</span><strong>{new Intl.NumberFormat("en-US").format(preflight.coverage.event_count ?? 0)} canonical events</strong></div></div>
        <MarketStatusBadge value={marketStatus} />
      </header>

      <section className="replay-control-deck">
        <button aria-label={playing ? "Pause replay" : "Play replay"} className="replay-play-button" onClick={() => setPlaying((value) => !value)} type="button">{playing ? <Pause size={24} /> : <Play size={24} />}</button>
        <button aria-label="Advance one bar" className="button secondary replay-step-button" disabled={cursor >= bars.length && complete} onClick={() => setCursor((value) => Math.min(bars.length, value + 1))} type="button"><SkipForward size={18} /> Step</button>
        <div className="replay-clock"><span>Replay clock · New York</span><strong>{current ? formatMarketTime(current.bar_start) : "Waiting for first bar"}</strong></div>
        <div className="replay-speed" aria-label="Playback speed">{PLAYBACK_SPEEDS.map((value) => <button className={speed === value ? "active" : ""} key={value} onClick={() => setSpeed(value)} type="button">{value} bars/s</button>)}</div>
        <div className="replay-buffer" data-loading={loadingChunk ? "true" : "false"}><Database size={17} /><div><span>{loadingChunk ? "Loading next event window" : complete ? "Full day loaded" : "Progressive day buffer"}</span><strong>{bars.length} bars · through {loadedEnd ? formatMarketTime(loadedEnd) : "—"}</strong></div></div>
      </section>

      <div className="replay-progress-track"><span style={{ width: `${progress}%` }} /></div>
      {loadError ? <div className="historical-error-banner"><TriangleAlert size={18} /><div><strong>Replay buffer failed</strong><span>{loadError}</span></div></div> : null}

      <div className="replay-workspace-grid">
        <section className="replay-chart-surface">
          <ChartPanel
            emptyMessage="Loading the first event-derived bars for this trading day."
            enableFullscreen
            featureOptions={[]}
            indicatorOptions={[]}
            initialFitMode="recent"
            loading={loadingChunk && !bars.length}
            onTickerChange={(value) => resetMarketSelection(value.toUpperCase(), timeframe)}
            onTimeframeChange={(value) => resetMarketSelection(ticker, value)}
            onVisibleColumnsChange={() => undefined}
            payload={chartPayload}
            periodEnd={sessionDate}
            periodStart={sessionDate}
            showIndicatorControls={false}
            ticker={ticker}
            timeframe={timeframe}
            timeframes={TIMEFRAMES}
            visibleColumns={[]}
          />
        </section>
        <aside className="replay-inspector">
          <header><BarChart3 size={19} /><div><span>Current bar</span><strong>{current ? formatMarketTime(current.bar_start) : "Not started"}</strong></div></header>
          <Metric label="Open" value={formatPrice(current?.open)} />
          <Metric label="High / Low" value={current ? `${formatPrice(current.high)} / ${formatPrice(current.low)}` : "—"} />
          <Metric label="Close" value={formatPrice(current?.close)} />
          <Metric label="VWAP" value={formatPrice(current?.vwap)} />
          <Metric label="Volume" value={formatNumber(current?.volume)} />
          <Metric label="Trades / Quotes" value={current ? `${formatNumber(current.trade_count)} / ${formatNumber(current.quote_count)}` : "—"} />
          <Metric label="Mean spread" value={current ? `${current.spread_bps_mean.toFixed(2)} bps` : "—"} />
          <Metric label="Tape imbalance" value={current ? current.tape_imbalance.toFixed(3) : "—"} />
          <div className="replay-capability-note"><ShieldAlert size={17} /><div><strong>Market playback only</strong><span>Order entry, portfolio, fills, and strategy execution stay hidden until the shared simulated-broker run controller is connected.</span></div></div>
        </aside>
      </div>
    </div>
  );
}

function EvidenceCheck({ check }: { check: HistoricalCheck }) {
  return <article data-status={check.status}><div className="historical-evidence-icon">{check.status === "ready" ? <CheckCircle2 size={20} /> : <TriangleAlert size={20} />}</div><div><header><strong>{check.label}</strong></header><p>{check.summary}</p><small>{check.evidence}</small></div></article>;
}

function EvidenceSkeleton() {
  return <article className="historical-evidence-skeleton"><Gauge size={20} /><div><strong>Checking the selected run</strong><span>Resolving exchange sessions, canonical events, event-derived bars, and strategy authority.</span></div></article>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="replay-metric"><span>{label}</span><strong>{value}</strong></div>;
}

function mergeBars(current: HistoricalBar[], incoming: HistoricalBar[]) {
  const byTime = new Map(current.map((bar) => [bar.bar_start, bar]));
  incoming.forEach((bar) => byTime.set(bar.bar_start, bar));
  return [...byTime.values()].sort((left, right) => left.bar_start.localeCompare(right.bar_start));
}

function barsToChartPayload(bars: HistoricalBar[]): ChartPayload {
  const success = themeToken("--success");
  const danger = themeToken("--danger");
  return {
    candles: bars.map((bar) => ({ close: bar.close, high: bar.high, low: bar.low, open: bar.open, time: Date.parse(bar.bar_start) / 1000 })),
    markers: [],
    oscillator_series: [],
    overlay_series: [],
    regions: [],
    volume: bars.map((bar) => ({ color: bar.close >= bar.open ? success : danger, time: Date.parse(bar.bar_start) / 1000, value: bar.volume })),
  };
}

function themeToken(name: string) {
  if (typeof document === "undefined") return "";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function previousWeekdayIsoDate() {
  const value = new Date();
  value.setDate(value.getDate() - 1);
  while (value.getDay() === 0 || value.getDay() === 6) value.setDate(value.getDate() - 1);
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

function formatWindow(window: HistoricalWindow) {
  if (window.session_count === 1) return `${formatMarketTime(window.start)} → ${formatMarketTime(window.end)}`;
  return `${window.session_count} sessions · ${window.sessions[0]} → ${window.sessions.at(-1)}`;
}

function formatMarketTime(value: string) {
  return new Intl.DateTimeFormat("en-CA", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }).format(new Date(value));
}

function marketTimeText(value: string) {
  return new Intl.DateTimeFormat("en-CA", { hour: "2-digit", hour12: false, minute: "2-digit", second: "2-digit", timeZone: "America/New_York" }).format(new Date(value));
}

function formatPrice(value?: number) {
  return value === undefined ? "—" : new Intl.NumberFormat("en-US", { maximumFractionDigits: 4, minimumFractionDigits: 2 }).format(value);
}

function formatNumber(value?: number) {
  return value === undefined ? "—" : new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
}
