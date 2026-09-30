import { useEffect, useRef, useState } from "react";
import { Clock3, Globe2, MapPin, PanelRightOpen, Maximize2, Minimize2 } from "lucide-react";
import { api, type ApiError } from "../../api/client";
import { TradingWorkspace } from "./TradingWorkspace";
import { BacktestV4SavedChart, SAVED_CHART_FRAMES, type ChartPage } from "./BacktestV4SavedChart";
import { ChartsQuotesMarketLayout, type SavedChartsQuote, type ChartsQuotesLayoutSettings } from "./MarketMicrostructureContainers";
import { MarketStatusBadge, historicalMarketStatus } from "./MarketStatusBadge";
import { SavedJournalQuery } from "./SavedJournalQuery";
import { dateInTimeZone } from "../timeZones";
import { strategyActionLabel, strategyExitReason } from "../strategyPresentationContract";
import { normalizeTicker } from "../tickerNavigation";
import type { V4Page } from "./BacktestV4SavedReview";
import { canvasRuntimeWorkspaceStorageKey, readCanvasRegistry,
  readCanvasWorkspaceStateByStorageKey, type CanvasWorkspaceState } from "../canvasWorkspace";
import { TRADING_WORKSPACE_CONTAINERS, type WorkspaceContainerId } from "../tradingWorkspace";
import type { PerformanceJournalReport } from "../../features/canvas/contracts";
import { instanceSettings } from "../../features/canvas/settings";
import { PositionsPreview, TradingDataTable, TradingJournalPreview } from "../../features/canvas/tradingPresentation";
import { previewClockReadings, strategyReplayCanvasState } from "../../pages/CanvasConfigurationPage";
import type { ChartPayload } from "./ChartPanel";

type PerformancePage = {
  schema_version: "strategy-one-v4-performance-report-v1";
  report: PerformanceJournalReport;
  position_lifecycles: Array<Record<string, unknown>>;
  position_executions?: Array<Record<string, unknown>>;
  fill_count: number;
  fee_count: number;
};
type ChartTradesPage = {
  schema_version: "strategy-one-v4-chart-trades-v1";
  run_id: string;
  ticker: string;
  verified_sequence: number;
  position_lifecycles: Array<Record<string, unknown>>;
  issued_intents: Array<Record<string, unknown>>;
};
type ContextPair = { schema_version: "strategy-one-v4-chart-context-pair-v1";
  run_id: string; ticker: string; daily: ChartPage; monthly: ChartPage };

async function savedReviewPage<T>(path: string, signal: AbortSignal): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try { return await api<T>(path, { signal, timeoutMs: 60_000 }); }
    catch (error) {
      const response = error as ApiError;
      if (signal.aborted || response.status !== 429 || response.retryable !== true || attempt >= 3) throw error;
      // The saved run is immutable. Bounded retry is safe after the backend's
      // read-workload budget rejects concurrent Canvas projections.
      await new Promise<void>((resolve, reject) => {
        const timer = window.setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 750 * (attempt + 1));
        const abort = () => { window.clearTimeout(timer); reject(signal.reason ?? new Error("Saved review canceled")); };
        signal.addEventListener("abort", abort, { once: true });
        if (signal.aborted) abort();
      });
    }
  }
}

const REVIEW_CONTAINERS: WorkspaceContainerId[] = [
  "performance_journal", "strategy_activity", "positions", "orders", "fills",
];
const AVAILABLE_CONTAINERS: WorkspaceContainerId[] = [...REVIEW_CONTAINERS, "chart", "portfolio"];
const DEFINITIONS = TRADING_WORKSPACE_CONTAINERS.filter(item => AVAILABLE_CONTAINERS.includes(item.id));
const EXCLUDED = TRADING_WORKSPACE_CONTAINERS.filter(item => !AVAILABLE_CONTAINERS.includes(item.id)).map(item => item.id);
const V4_LAYOUT_KEY = "quant-research-workbench.canvas.backtest.strategy-one-v4-v3";
const CERTIFIED_LAYOUT_KEY = canvasRuntimeWorkspaceStorageKey("backtest.strategy.main", "persistent-layout-v1", "main");

/** Keep the same certified container layout during execution. A committed
 * journal prefix is not a terminal P&L report; unavailable domains remain
 * explicit until their normalized projection is verified. */
export function BacktestV4RunningWorkspace({ boundaries, committedRows, liveCounts }: {
  boundaries: number; committedRows: number;
  liveCounts?: { signals: number; intents: number; commands: number; fills: number } | null;
}) {
  const [savedLayout] = useState<CanvasWorkspaceState | null>(() =>
    readCanvasWorkspaceStateByStorageKey(V4_LAYOUT_KEY)
    ?? strategyReplayCanvasState(readCanvasWorkspaceStateByStorageKey(CERTIFIED_LAYOUT_KEY)));
  return <TradingWorkspace clockLabel="" commandBarVisible={false} compact
    definitionsOverride={DEFINITIONS} defaultOpenIds={REVIEW_CONTAINERS}
    excludedContainerIds={EXCLUDED} initialStateOverride={savedLayout}
    layoutPreset="focus" historicalSourceReady mode="backtest" runLabel="Strategy 1"
    runStatus="running" sourceLabel="ARTE typed journal" showHealth={false}
    metaForContainer={() => ({ sourceLabel: "ARTE V4", status: "connecting", freshness: "At run clock" })}
    storageKeyOverride={V4_LAYOUT_KEY} persistState={false}
    renderContainer={definition => <section className="trading-preview" role="status">
      <p className="trading-disclosure">{definition.id === "strategy_activity"
        ? `${boundaries.toLocaleString()} boundaries · ${liveCounts?.signals.toLocaleString() ?? "—"} signals · ${liveCounts?.intents.toLocaleString() ?? "—"} intents (provisional).`
        : definition.id === "orders" ? `${liveCounts?.commands.toLocaleString() ?? "—"} order commands observed (provisional).`
        : definition.id === "fills" ? `${liveCounts?.fills.toLocaleString() ?? "—"} fills observed (provisional).`
        : `${definition.title} awaits verified account and position projections. No intraday P&L is inferred from market bars.`}</p>
      <p className="trading-disclosure">{committedRows.toLocaleString()} normalized records committed; final financial results follow journal verification.</p>
    </section>} />;
}

function display(value: unknown): string {
  return value == null || value === "" ? "—" : String(value);
}

function money(value: unknown, currency: string): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency,
    maximumFractionDigits: 2 }).format(Number(value));
}

function utcJournalTime(value: unknown): string {
  const raw = String(value ?? "");
  return /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?$/.test(raw)
    ? `${raw.replace(" ", "T")}Z` : raw;
}

export function SavedV4CanvasHeader({ initialPage, onClose, managementOpen, onManage, title, backLabel = "Return to setup" }: {
  initialPage: V4Page; onClose: () => void; managementOpen?: boolean;
  onManage?: () => void; title: string; backLabel?: string;
}) {
  const sessionDate = initialPage.run.session_date || initialPage.market_cursor?.session_date || "";
  const boundary = Number(initialPage.market_cursor?.boundary_ms ?? 0);
  const instant = /^\d{4}-\d\d-\d\d$/.test(sessionDate)
    ? new Date(dateInTimeZone(sessionDate, "04:00", "America/New_York").getTime() + boundary) : null;
  const etTime = instant ? new Intl.DateTimeFormat("en-US", { hour: "2-digit", hourCycle: "h23", minute: "2-digit", second: "2-digit", timeZone: "America/New_York" }).format(instant) : "";
  const clocks = instant ? previewClockReadings({ sessionDate, previewTime: etTime }, instant) : [
    { label: "ET", value: "—", detail: "Saved clock unavailable" },
    { label: "Local", value: "—", detail: "" },
    { label: "UTC", value: "—", detail: "" },
  ];
  const icons = [Clock3, MapPin, Globe2];
  const completed = initialPage.status === "completed";
  const initialCash = initialPage.run.initial_cash;
  return <header className="canvas-config-toolbar">
    <div className="canvas-clock-control" aria-label="Verified saved-run clock"><div className="canvas-clock-zones" aria-label="Preview time zones">
      {clocks.map((clock, index) => { const Icon = icons[index]; return <span key={clock.label}><Icon aria-hidden="true" size={15} /><span><small>{clock.label}</small><strong>{clock.value}</strong><em>{clock.detail}</em></span></span>; })}
    </div></div>
    <MarketStatusBadge value={instant ? historicalMarketStatus(sessionDate, etTime) : { asOfEt: "", label: "Unavailable", source: "et-clock", status: "unavailable" }} />
    <div className="canvas-mode-context-slot"><div className="historical-canvas-run-state historical-backtest-progress saved-v4-focus-progress">
      <div className="historical-backtest-progress-heading"><strong>{title}{typeof initialCash === "number" && Number.isFinite(initialCash) ? ` · ${money(initialCash, "USD")} initial` : ""} · {initialPage.run.run_id.slice(0, 8)} · {initialPage.status} · {initialPage.verified_sequence.toLocaleString()} verified records</strong><b>{completed ? "100%" : initialPage.status}</b></div>
      <div aria-label="Backtest progress" aria-valuemax={100} aria-valuemin={0} aria-valuenow={completed ? 100 : undefined} className="historical-backtest-progress-track" role="progressbar"><span style={{ width: completed ? "100%" : "0%" }} /></div>
      <div className="historical-backtest-progress-actions"><button className="button secondary compact" onClick={onClose} type="button">{backLabel}</button></div>
    </div></div>
    {onManage ? <div className="canvas-toolbar-actions"><button aria-expanded={managementOpen} aria-label="Canvas management" className="button secondary compact canvas-management-toggle" onClick={onManage} type="button"><PanelRightOpen size={13} /> Manage</button></div> : null}
  </header>;
}


/** Saved Backtest uses the certified Canvas container system, but never its
 * legacy SQLite preview readers. Every rendered fact is from a verified V4
 * ClickHouse prefix; unprojected legacy domains remain explicitly unavailable.
 */
function EvidenceTable({ rows, columns, empty }: { rows: Array<Record<string, unknown>>; columns: Array<[string, string]>; empty: string }) {
  return rows.length ? <TradingDataTable rows={rows} columns={columns.map(([key]) => key)} defaultSort={columns[0][0]} searchPlaceholder="Search verified evidence…" /> : <p>{empty}</p>;
}

export function BacktestV4CanvasReview({ runId, initialPage, onClose, timing }: {
  runId: string; initialPage: V4Page; onClose: () => void;
  timing?: { totalSeconds: number; executionSeconds?: number; finalizationSeconds?: number };
}) {
  const [performance, setPerformance] = useState<PerformancePage | null>(null);
  const [performanceError, setPerformanceError] = useState("");
  const [managementOpen, setManagementOpen] = useState(false);
  const [savedLayout] = useState<CanvasWorkspaceState | null>(() =>
    readCanvasWorkspaceStateByStorageKey(V4_LAYOUT_KEY)
    ?? strategyReplayCanvasState(readCanvasWorkspaceStateByStorageKey(CERTIFIED_LAYOUT_KEY)));
  const [presentationSettings] = useState(() => {
    const registry = readCanvasRegistry();
    return {
      journal: instanceSettings(registry, "performance_journal").performance_journal,
      fills: instanceSettings(registry, "fills").fills,
      positions: instanceSettings(registry, "positions").positions,
      activity: instanceSettings(registry, "strategy_activity").strategy_activity,
    };
  });
  const [activitySettings] = useState(presentationSettings.activity);

  function openV4Ticker(value: string) {
    const symbol = normalizeTicker(value);
    if (!symbol) return;
    const url = new URL(window.location.href);
    url.searchParams.set("backtest_run", runId);
    url.searchParams.set("backtest_ticker", symbol);
    url.hash = "canvas-focus";
    window.open(url, "_blank", "noopener,noreferrer");
  }

  useEffect(() => {
    const controller = new AbortController();
    void savedReviewPage<PerformancePage>(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-performance`, controller.signal).then(page => {
      if (!controller.signal.aborted) setPerformance(page);
    }).catch(error => {
      if (!controller.signal.aborted) setPerformanceError(error instanceof Error ? error.message : String(error));
    });
    return () => controller.abort();
  }, [runId]);

  const accounts = Object.entries(initialPage.financial_accounts).map(([account_id, account]) => ({ account_id, ...account }));
  const financialAccounts = accounts.map(account => ({
    account_id: account.account_id, currency: account.currency,
    net_liquidation: money(account.net_liquidation, account.currency),
    total_cash_value: money(account.total_cash_value, account.currency),
    gross_position_value: money(account.gross_position_value, account.currency),
    buying_power: money(account.buying_power, account.currency),
    expected_position_count: account.expected_position_count,
  }));
  const chartTicker = String((performance?.position_lifecycles[0]?.instrument as { symbol?: string } | undefined)?.symbol ?? "");
  // The certified journal is complete for fills/fees, not for every old
  // Canvas broker view. Keep the performance projection honest about that.
  const performanceTrading = performance ? {
    mode: "backtest", provider: "arte_typed_journal_v4",
    as_of: accounts[0] ? new Date(accounts[0].source_timestamp_ms).toISOString() : "",
    complete: false, stale: true,
    stale_reason: "Verified fills and final fees; other broker domains are not yet projected.",
    positions: [],
    position_lifecycles: performance.position_lifecycles,
    orders: [], executions: [],
    performance_journal: performance.report,
  } : undefined;
  const positionTrading = performance && accounts.length > 0 ? {
    mode: "backtest", provider: "arte_typed_journal_v4", complete: false, stale: true,
    stale_reason: "Verified fill-derived lifecycles; current marks, unrealized P&L, and order state are unavailable.",
    as_of: accounts[0] ? new Date(accounts[0].source_timestamp_ms).toISOString() : "",
    position_lifecycles: performance.position_lifecycles,
    positions: [], orders: [], executions: performance.position_executions ?? [],
    strategy_activity: [], activity: [],
  } : undefined;
  return <div className="canvas-config-page canvas-focus-page backtest-v4-canvas-review">
    <SavedV4CanvasHeader initialPage={initialPage} onClose={onClose} title="Backtest Canvas · Strategy 1"
      managementOpen={managementOpen} onManage={() => setManagementOpen(value => !value)} />
    {timing ? <div className="backtest-v4-completed-timing" aria-label="Completed Backtest timing">
      <strong>Total {timing.totalSeconds.toFixed(2)}s</strong>
      <span>Execution {timing.executionSeconds?.toFixed(2) ?? "—"}s</span>
      <span>Journal finalization {timing.finalizationSeconds?.toFixed(2) ?? "—"}s</span>
    </div> : null}
    <TradingWorkspace clockLabel="" commandBarVisible={false} compact
      definitionsOverride={DEFINITIONS} defaultOpenIds={REVIEW_CONTAINERS}
      excludedContainerIds={EXCLUDED}
      initialStateOverride={savedLayout} layoutPreset="focus"
      historicalSourceReady mode="backtest" runLabel="Strategy 1" runStatus="completed"
      sourceLabel="ARTE typed journal" showHealth={false}
      metaForContainer={() => ({ sourceLabel: "ARTE verified V4", status: "ready", freshness: "Saved run" })}
      managementOpen={managementOpen} onManagementClose={() => setManagementOpen(false)}
      storageKeyOverride={V4_LAYOUT_KEY}
      renderContainer={definition => {
        if (["strategy_activity", "orders", "fills"].includes(definition.id)) return <SavedJournalQuery
          runId={runId} domain={definition.id === "strategy_activity" ? "activity" : definition.id as "orders" | "fills"}
          strategyId={initialPage.run.strategy_id} strategyRevision={initialPage.run.strategy_revision}
          asOf={new Date(dateInTimeZone(initialPage.market_cursor?.session_date || initialPage.run.session_date || "1970-01-01", "04:00", "America/New_York").getTime() + Number(initialPage.market_cursor?.boundary_ms ?? 0)).toISOString()}
          onTickerSelect={openV4Ticker} activitySettings={activitySettings} fillSettings={presentationSettings.fills} />;
        switch (definition.id) {
          case "performance_journal": return <>{performanceError ? <p role="alert">Performance unavailable: {performanceError}</p> : null}
            <TradingJournalPreview data={performanceTrading} onSymbolSelect={openV4Ticker} readOnly settings={presentationSettings.journal} />
          </>;
          case "positions": return positionTrading ? <PositionsPreview data={positionTrading}
            onSymbolSelect={openV4Ticker} openRowsInChart orderEvidenceComplete={false} settings={presentationSettings.positions} />
            : <div className="trading-disclosure" role="status">{performanceError || "Loading complete verified position lifecycles…"}</div>;
          // The journal's Chart affordance must open the certified multi-panel
          // Charts & Quotes focus canvas, not introduce a second single-chart UI.
          case "chart": return chartTicker ? <div className="trading-preview">
            <button className="button secondary" onClick={() => openV4Ticker(chartTicker)} type="button">
              Open {chartTicker} Charts &amp; Quotes focus canvas
            </button>
          </div> : <div className="trading-disclosure">Chart ticker will be selected from the first verified fill or journal signal.</div>;
          case "portfolio": return <section className="trading-preview"><p className="trading-disclosure">Verified terminal account snapshots. Intraday account marks are not inferred.</p>
            <EvidenceTable rows={financialAccounts} columns={[["account_id", "Account"], ["net_liquidation", "Net liquidation"], ["total_cash_value", "Cash"], ["gross_position_value", "Gross positions"], ["buying_power", "Buying power"], ["expected_position_count", "Open positions"]]} empty="No verified terminal account snapshot." />
          </section>;
          default: return <div className="trading-disclosure">This container has no V4 projection.</div>;
        }
      }} />
  </div>;
}

/** A ticker drilldown is a new, isolated Canvas page. It never rewrites the
 * saved review layout or asks the legacy replay/chart reader for V4 data. */
export function BacktestV4ChartsQuotesContent({ runId, ticker, initialPage, layout, onLayoutChange, mainFrame }: {
  runId: string; ticker: string; initialPage: V4Page;
  layout: ChartsQuotesLayoutSettings;
  onLayoutChange: (layout: ChartsQuotesLayoutSettings) => void;
  mainFrame: string;
}) {
  const [maximized, setMaximized] = useState(true);
  // The certified Canvas owns the window, header, and saved layout. This
  // container supplies only the typed V4 market adapter to that window.
  const chartFrame = SAVED_CHART_FRAMES.find(frame => frame === mainFrame) ?? "10s";
  const [quote, setQuote] = useState<SavedChartsQuote | null>(null);
  const [mark, setMark] = useState<{ price: number; barEnd: string } | null>(null);
  const [tradeAnnotations, setTradeAnnotations] = useState<NonNullable<ChartPayload["trade_annotations"]>>([]);
  const [tradeError, setTradeError] = useState("");
  const [contextPair, setContextPair] = useState<ContextPair | null>(null);
  const [contextError, setContextError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void savedReviewPage<ChartTradesPage>(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-chart-trades?ticker=${encodeURIComponent(ticker)}`, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      if (value.schema_version !== "strategy-one-v4-chart-trades-v1"
          || value.run_id !== runId || value.ticker !== ticker.toUpperCase()
          || !Array.isArray(value.issued_intents)
          || !Number.isSafeInteger(value.verified_sequence) || value.verified_sequence < 1) {
        throw new Error("Saved position evidence contract mismatch");
      }
      // The saved chart uses the same lifecycle identity as the journal. Never
      // infer a trade from candles or synthesize fills at bar boundaries.
      const annotations: NonNullable<ChartPayload["trade_annotations"]> = [];
      const issued = value.issued_intents.map(intent => ({
        accountId: String(intent.account_id ?? ""),
        action: String(intent.action ?? ""),
        reason: String(intent.reason ?? ""),
        time: Date.parse(String(intent.event_time ?? "")) / 1000,
        price: Number(intent.reference_price),
        sequence: Number(intent.sequence),
      })).filter(intent => intent.accountId && Number.isFinite(intent.time)
        && Number.isFinite(intent.price) && intent.price > 0
        && Number.isSafeInteger(intent.sequence));
      for (const row of value.position_lifecycles) {
        const instrument = row.instrument as { symbol?: string } | undefined;
        if (instrument?.symbol?.toUpperCase() !== ticker.toUpperCase()) continue;
        const entryTime = Date.parse(String(row.opened_at ?? "")) / 1000;
        const entryPrice = Number(row.entry_price);
        if (!Number.isFinite(entryTime) || !Number.isFinite(entryPrice)) continue;
        const exitTime = row.closed_at ? Date.parse(String(row.closed_at)) / 1000 : undefined;
        const exitPrice = row.exit_price == null ? undefined : Number(row.exit_price);
        const side = row.side === "SHORT" ? "SHORT" : "LONG";
        const quantity = Number(row.quantity);
        const accountId = String(row.account_id ?? "");
        const requestedTime = Date.parse(String(row.requested_at ?? "")) / 1000;
        const previousClose = Math.max(Number.NEGATIVE_INFINITY,
          ...value.position_lifecycles.filter(other => other !== row
            && String(other.account_id ?? "") === accountId)
            .map(other => Date.parse(String(other.closed_at ?? "")) / 1000)
            .filter(time => Number.isFinite(time) && time <= entryTime));
        const entryIntent = issued.filter(intent => intent.accountId === accountId
          && intent.action === (side === "SHORT" ? "enter_short" : "enter_long")
          && intent.time <= entryTime && intent.time > previousClose
          && (!Number.isFinite(requestedTime) || intent.time <= requestedTime))
          .sort((left, right) => right.time - left.time || right.sequence - left.sequence)[0];
        const exitIntents = issued.filter(intent => intent.accountId === accountId
          && (side === "SHORT" ? ["exit", "reduce_short", "cover"] : ["exit", "reduce_long", "take_profit"]).includes(intent.action)
          && intent.time >= entryTime && intent.time <= (exitTime ?? Number.POSITIVE_INFINITY))
          .map(intent => ({ kind: "exit_intent" as "exit_intent" | "exit_trigger", time: intent.time,
            price: intent.price, side: side === "SHORT" ? "BUY" as const : "SELL" as const,
            labelParts: strategyActionLabel({ kind: "exit", side, reason: intent.reason }) }));
        const reasonCode = String(row.exit_reason || row.presentation_exit_reason || "");
        const exitReason = strategyExitReason(reasonCode);
        // Broker-held stops and targets have no later strategy exit intent.
        // Their verified trigger/fill boundary is the only causal exit marker;
        // never manufacture an earlier decision from the resting order.
        if (!exitIntents.length && exitTime !== undefined && Number.isFinite(exitTime)
            && exitPrice !== undefined && Number.isFinite(exitPrice) && exitReason) {
          exitIntents.push({ kind: "exit_trigger" as const, time: exitTime,
            price: exitPrice, side: side === "SHORT" ? "BUY" as const : "SELL" as const,
            labelParts: strategyActionLabel({ kind: "exit", side, reason: reasonCode }) });
        }
        const pnl = row.net_pnl == null ? undefined : Number(row.net_pnl);
        const protectionPath = Array.isArray(row.protection_timeline)
          ? (row.protection_timeline as Array<Record<string, unknown>>)
            .filter(event => event.phase === "effective" && (event.kind === "stop" || event.kind === "target")
              && Number.isFinite(Number(event.price)) && Number.isFinite(Date.parse(String(event.event_time))))
            .map(event => ({ time: Date.parse(String(event.event_time)) / 1000, sequence: Number(event.sequence),
              orderId: String(event.order_id), kind: event.kind as "stop" | "target",
              price: Number(event.price), active: event.active === true }))
            .sort((a, b) => a.time - b.time || a.sequence - b.sequence)
          : [];
        annotations.push({ id: String(row.lifecycle_id ?? row.episode_id), color: "var(--chart-strategy-entry)",
          entryTime, entryPrice,
          currentQuantity: row.status === "open" ? Number(row.current_quantity) : undefined,
          entryIntentTime: entryIntent?.time, entryIntentPrice: entryIntent?.price,
          entryLabelParts: entryIntent ? strategyActionLabel({ kind: "entry", side, quantity, price: entryIntent.price }) : [],
          exitIntents,
          positionSide: side,
          protectionPath,
          status: row.status === "closed" ? "closed" : "open",
          ...(exitTime !== undefined && Number.isFinite(exitTime) && exitPrice !== undefined && Number.isFinite(exitPrice)
            ? { exitTime, exitPrice, endTime: exitTime, exitFills: [{ kind: "exit_fill" as const,
              time: exitTime, price: exitPrice, side: side === "SHORT" ? "BUY" as const : "SELL" as const,
              // A missing cause must not suppress verified fill price or P&L.
              // The shared presenter omits an unverified reason on its own.
              labelParts: strategyActionLabel({ kind: "exit_fill", side,
                quantity, price: exitPrice,
                pnl: pnl !== undefined && Number.isFinite(pnl) ? pnl : undefined }) }] } : {}),
          pnl: pnl !== undefined && Number.isFinite(pnl) ? pnl : undefined });
      }
      setTradeAnnotations(annotations);
      setTradeError("");
    }).catch(() => { if (!controller.signal.aborted) { setTradeAnnotations([]); setTradeError("Saved position evidence unavailable"); } });
    return () => controller.abort();
  }, [runId, ticker]);
  useEffect(() => {
    if (maximized || contextPair) return;
    const controller = new AbortController();
    setContextError("");
    void api<ContextPair>(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-chart-context?ticker=${encodeURIComponent(ticker)}`, {
      signal: controller.signal, timeoutMs: 60_000,
    }).then(value => {
      if (controller.signal.aborted) return;
      if (value.schema_version !== "strategy-one-v4-chart-context-pair-v1"
          || value.run_id !== runId || value.ticker !== ticker) {
        throw new Error("Saved chart context identity differs from the selected run.");
      }
      setContextPair(value);
    }).catch(reason => {
      if (!controller.signal.aborted) setContextError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => controller.abort();
  }, [contextPair, maximized, runId, ticker]);
  const sessionDate = initialPage.market_cursor?.session_date || initialPage.run.session_date;
  const savedAsOf = sessionDate && /^\d{4}-\d\d-\d\d$/.test(sessionDate)
    ? new Date(dateInTimeZone(sessionDate, "04:00", "America/New_York").getTime() + Number(initialPage.market_cursor?.boundary_ms ?? 0)).toISOString()
    : undefined;
  return <div className="backtest-v4-chart-focus">
    <ChartsQuotesMarketLayout symbol={ticker} end={savedAsOf} savedQuote={quote} layout={layout} onLayoutChange={onLayoutChange}
        mainChartMaximized={maximized}
        mainChart={<BacktestV4SavedChart embedded initialFrame={chartFrame} runId={runId} ticker={ticker} tradeAnnotations={tradeAnnotations.map(annotation => annotation.status === "open" ? {
          ...annotation, openMarkPrice: quote?.fresh && quote.bid > 0 && quote.ask >= quote.bid
            ? (quote.bid + quote.ask) / 2 : mark?.price,
        } : annotation)} tradeError={tradeError} onQuoteChange={value => setQuote(value ?? null)} onMarkChange={setMark}
          toolbarAction={<button aria-label={maximized ? "Restore chart panels" : "Maximize main chart"} className="toolbar-button" onClick={() => setMaximized(value => !value)} title={maximized ? "Restore right column and bottom row" : "Maximize main chart: hide right column and bottom row"} type="button">{maximized ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>} />}
        monthChart={contextPair ? <BacktestV4SavedChart embedded enabled={!maximized} initialFrame="1mo" allowedFrames={["1mo"]} initialShowMacd={false} panelLabel="Monthly context · limited ARTE history" prefetchedPage={contextPair.monthly} runId={runId} ticker={ticker} /> : <div className="trading-disclosure" role={contextError ? "alert" : "status"}>{contextError || "Loading certified monthly context…"}</div>}
        dailyChart={contextPair ? <BacktestV4SavedChart embedded enabled={!maximized} initialFrame="1d" allowedFrames={["1d"]} initialShowMacd={false} panelLabel="Daily context · limited ARTE history" prefetchedPage={contextPair.daily} runId={runId} ticker={ticker} /> : <div className="trading-disclosure" role={contextError ? "alert" : "status"}>{contextError || "Loading certified daily context…"}</div>}
        reservedPanel={<div className="trading-disclosure">Saved Backtest review · order entry disabled</div>} />
  </div>;
}
