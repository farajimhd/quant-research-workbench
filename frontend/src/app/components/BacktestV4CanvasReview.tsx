import { useEffect, useState } from "react";
import { Clock3, Globe2, MapPin, PanelRightOpen, Maximize2, Minimize2 } from "lucide-react";
import { api } from "../../api/client";
import { TradingWorkspace } from "./TradingWorkspace";
import { BacktestV4SavedChart } from "./BacktestV4SavedChart";
import { ChartsQuotesMarketLayout, type SavedChartsQuote, type ChartsQuotesLayoutSettings } from "./MarketMicrostructureContainers";
import { MarketStatusBadge, historicalMarketStatus } from "./MarketStatusBadge";
import { StrategyActivityContainer } from "./MarketScreenerContainers";
import { dateInTimeZone } from "../timeZones";
import { normalizeTicker } from "../tickerNavigation";
import type { V4Page } from "./BacktestV4SavedReview";
import { canvasRuntimeWorkspaceStorageKey, readCanvasRegistry,
  readCanvasWorkspaceStateByStorageKey, type CanvasWorkspaceState } from "../canvasWorkspace";
import { TRADING_WORKSPACE_CONTAINERS, type WorkspaceContainerId } from "../tradingWorkspace";
import type { PerformanceJournalReport } from "../../features/canvas/contracts";
import { instanceSettings } from "../../features/canvas/settings";
import { ExecutionsPreview, PositionsPreview, TradingDataTable, TradingJournalPreview } from "../../features/canvas/tradingPresentation";
import { previewClockReadings, strategyReplayCanvasState } from "../../pages/CanvasConfigurationPage";

type TradePage = {
  schema_version: "strategy-one-v4-trade-history-page-v1";
  fills: Array<Record<string, unknown>>;
  commissions: Array<Record<string, unknown>>;
  next_fill_sequence: number;
  next_commission_sequence: number;
  complete: boolean;
};
type PerformancePage = {
  schema_version: "strategy-one-v4-performance-report-v1";
  report: PerformanceJournalReport;
  position_lifecycles: Array<Record<string, unknown>>;
  fill_count: number;
  fee_count: number;
};
type OrderPage = {
  schema_version: "strategy-one-v4-order-history-page-v1";
  commands: Array<Record<string, unknown>>;
  transitions: Array<Record<string, unknown>>;
  next_command_sequence: number;
  next_transition_sequence: number;
  complete: boolean;
};

const REVIEW_CONTAINERS: WorkspaceContainerId[] = [
  "performance_journal", "strategy_activity", "positions", "orders", "fills",
];
const AVAILABLE_CONTAINERS: WorkspaceContainerId[] = [...REVIEW_CONTAINERS, "chart", "portfolio"];
const DEFINITIONS = TRADING_WORKSPACE_CONTAINERS.filter(item => AVAILABLE_CONTAINERS.includes(item.id));
const EXCLUDED = TRADING_WORKSPACE_CONTAINERS.filter(item => !AVAILABLE_CONTAINERS.includes(item.id)).map(item => item.id);
const V4_LAYOUT_KEY = "quant-research-workbench.canvas.backtest.strategy-one-v4-v3";
const CERTIFIED_LAYOUT_KEY = canvasRuntimeWorkspaceStorageKey("backtest.strategy.main", "persistent-layout-v1", "main");

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
  return <header className="canvas-config-toolbar">
    <div className="canvas-clock-control" aria-label="Verified saved-run clock"><div className="canvas-clock-zones" aria-label="Preview time zones">
      {clocks.map((clock, index) => { const Icon = icons[index]; return <span key={clock.label}><Icon aria-hidden="true" size={15} /><span><small>{clock.label}</small><strong>{clock.value}</strong><em>{clock.detail}</em></span></span>; })}
    </div></div>
    <MarketStatusBadge value={instant ? historicalMarketStatus(sessionDate, etTime) : { asOfEt: "", label: "Unavailable", source: "et-clock", status: "unavailable" }} />
    <div className="canvas-mode-context-slot"><span title={`${title} · ${initialPage.status} · ${initialPage.verified_sequence.toLocaleString()} verified records`}>{title} · {initialPage.status} · {initialPage.verified_sequence.toLocaleString()} verified records</span><button className="button secondary compact" onClick={onClose} type="button">{backLabel}</button></div>
    {onManage ? <div className="canvas-toolbar-actions"><button aria-expanded={managementOpen} aria-label="Canvas management" className="button secondary compact canvas-management-toggle" onClick={onManage} type="button"><PanelRightOpen size={13} /> Manage</button></div> : null}
  </header>;
}

function EvidenceTable({ rows, columns, empty, onSymbolSelect }: {
  rows: Array<Record<string, unknown>>; columns: Array<[string, string]>; empty: string;
  onSymbolSelect?: (symbol: string) => void;
}) {
  return rows.length ? <TradingDataTable columns={columns.map(([key]) => key)}
    defaultSort={columns[0][0]} onSymbolSelect={onSymbolSelect} rows={rows} searchPlaceholder="Search verified evidence…" />
    : <p className="trading-disclosure">{empty}</p>;
}

/** Saved Backtest uses the certified Canvas container system, but never its
 * legacy SQLite preview readers. Every rendered fact is from a verified V4
 * ClickHouse prefix; unprojected legacy domains remain explicitly unavailable.
 */
export function BacktestV4CanvasReview({ runId, initialPage, onClose }: {
  runId: string; initialPage: V4Page; onClose: () => void;
}) {
  const [events, setEvents] = useState(initialPage.events);
  const [nextSequence, setNextSequence] = useState(initialPage.next_sequence);
  const [eventsComplete, setEventsComplete] = useState(initialPage.complete);
  const [trade, setTrade] = useState<TradePage | null>(null);
  const [fillRows, setFillRows] = useState<TradePage["fills"]>([]);
  const [commissionRows, setCommissionRows] = useState<TradePage["commissions"]>([]);
  const [tradeError, setTradeError] = useState("");
  const [performance, setPerformance] = useState<PerformancePage | null>(null);
  const [performanceError, setPerformanceError] = useState("");
  const [orders, setOrders] = useState<OrderPage | null>(null);
  const [orderError, setOrderError] = useState("");
  const [orderCommands, setOrderCommands] = useState<OrderPage["commands"]>([]);
  const [orderTransitions, setOrderTransitions] = useState<OrderPage["transitions"]>([]);
  const [loadingOrders, setLoadingOrders] = useState(false);
  const [eventError, setEventError] = useState("");
  const [loadingTrades, setLoadingTrades] = useState(false);
  const [loadingEvents, setLoadingEvents] = useState(false);
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
  const [activitySettings, setActivitySettings] = useState(presentationSettings.activity);

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
    void api<PerformancePage>(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-performance`, {
      signal: controller.signal, timeoutMs: 60_000,
    }).then(page => {
      if (!controller.signal.aborted) setPerformance(page);
    }).catch(error => {
      if (!controller.signal.aborted) setPerformanceError(error instanceof Error ? error.message : String(error));
    });
    return () => controller.abort();
  }, [runId]);

  useEffect(() => {
    const controller = new AbortController();
    setLoadingOrders(true);
    void api<OrderPage>(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-order-history?limit=500`, {
      signal: controller.signal, timeoutMs: 60_000,
    }).then(page => {
      if (controller.signal.aborted) return;
      setOrders(page);
      setOrderCommands(page.commands);
      setOrderTransitions(page.transitions);
    }).catch(error => {
      if (!controller.signal.aborted) setOrderError(error instanceof Error ? error.message : String(error));
    }).finally(() => { if (!controller.signal.aborted) setLoadingOrders(false); });
    return () => controller.abort();
  }, [runId]);

  useEffect(() => {
    const controller = new AbortController();
    setLoadingTrades(true);
    void api<TradePage>(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-trade-history?limit=500`, {
      signal: controller.signal, timeoutMs: 60_000,
    }).then(page => {
      if (controller.signal.aborted) return;
      setTrade(page);
      setFillRows(page.fills);
      setCommissionRows(page.commissions);
    }).catch(error => {
      if (!controller.signal.aborted) setTradeError(error instanceof Error ? error.message : String(error));
    }).finally(() => { if (!controller.signal.aborted) setLoadingTrades(false); });
    return () => controller.abort();
  }, [runId]);

  async function loadMoreTrades() {
    if (!trade || trade.complete || loadingTrades) return;
    setLoadingTrades(true);
    setTradeError("");
    try {
      const page = await api<TradePage>(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-trade-history?after_fill_sequence=${trade.next_fill_sequence}&after_commission_sequence=${trade.next_commission_sequence}&limit=500`, { timeoutMs: 60_000 });
      setFillRows(current => [...current, ...page.fills]);
      setCommissionRows(current => [...current, ...page.commissions]);
      setTrade(page);
    } catch (error) { setTradeError(error instanceof Error ? error.message : String(error)); }
    finally { setLoadingTrades(false); }
  }

  async function loadMoreOrders() {
    if (!orders || orders.complete || loadingOrders) return;
    setLoadingOrders(true);
    setOrderError("");
    try {
      const page = await api<OrderPage>(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-order-history?after_command_sequence=${orders.next_command_sequence}&after_transition_sequence=${orders.next_transition_sequence}&limit=500`, { timeoutMs: 60_000 });
      setOrderCommands(current => [...current, ...page.commands]);
      setOrderTransitions(current => [...current, ...page.transitions]);
      setOrders(page);
    } catch (error) { setOrderError(error instanceof Error ? error.message : String(error)); }
    finally { setLoadingOrders(false); }
  }

  async function loadMoreEvents() {
    if (eventsComplete || loadingEvents) return;
    setLoadingEvents(true);
    setEventError("");
    try {
      const page = await api<V4Page>(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-terminal-page?after_sequence=${nextSequence}&limit=500`, { timeoutMs: 60_000 });
      setEvents(current => [...current, ...page.events]);
      setNextSequence(page.next_sequence);
      setEventsComplete(page.complete);
    } catch (error) { setEventError(error instanceof Error ? error.message : String(error)); }
    finally { setLoadingEvents(false); }
  }

  const accounts = Object.entries(initialPage.financial_accounts).map(([account_id, account]) => ({ account_id, ...account }));
  const financialAccounts = accounts.map(account => ({
    account_id: account.account_id, currency: account.currency,
    net_liquidation: money(account.net_liquidation, account.currency),
    total_cash_value: money(account.total_cash_value, account.currency),
    gross_position_value: money(account.gross_position_value, account.currency),
    buying_power: money(account.buying_power, account.currency),
    expected_position_count: account.expected_position_count,
  }));
  const feeByExecution = new Map(commissionRows.map(row => [display(row.execution_id), row]));
  const fills: Array<Record<string, unknown>> = fillRows.map(row => ({ ...row, commission: feeByExecution.get(display(row.execution_id))?.commission ?? null }));
  const canonicalFills = fills.map(row => ({ ...row,
    source_event_time: utcJournalTime(row.source_event_time),
    instrument: { instrument_id: Number(row.conid) ? `conid:${row.conid}` : `ticker:${row.ticker}`,
      conid: row.conid, symbol: row.ticker },
    side: row.side === "B" ? "BUY" : row.side === "S" ? "SELL" : row.side,
    commission_status: feeByExecution.get(display(row.execution_id))?.status ?? "pending",
  }));
  const activityCategories = new Set(["strategy", "portfolio_management", "protection", "order_management", "execution", "broker", "command"]);
  const activityRows = events.filter(({ event }) => activityCategories.has(event.category)).map(({ event, detail_family, detail }) => ({
    run_id: runId, sequence: event.sequence, record_id: "",
    event_time: utcJournalTime(event.event_time), recorded_at: utcJournalTime(event.recorded_at),
    event_type: event.category === "strategy" ? "decision" : event.category,
    ticker: detail?.ticker ?? "", action: detail?.action ?? event.entity_type,
    state: detail?.status ?? detail?.state ?? "", reason: detail?.reason ?? "",
    reason_code: detail?.reason_code ?? "", reference_price: detail?.reference_price ?? null,
    strategy_id: initialPage.run.strategy_id ?? "",
    strategy_revision: initialPage.run.strategy_revision ?? null,
    source: detail_family ?? "", entity_id: event.entity_id,
    event_evidence: { ...(detail ?? {}), journal_record_id: event.record_id ?? "", detail_family },
  }));
  const commandRows = orderCommands.map(row => ({ ...row, created_at: utcJournalTime(row.created_at) }));
  const chartTicker = display(fillRows[0]?.ticker ?? events.find(item => typeof item.detail?.ticker === "string")?.detail?.ticker).replace("—", "");
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
  const positionTrading = performance && trade?.complete && accounts.length > 0 && accounts.every(account => account.expected_position_count === 0) ? {
    mode: "backtest", provider: "arte_typed_journal_v4", complete: false, stale: true,
    stale_reason: "Verified flat-to-flat lifecycles and fills; order state and intratrade marks unavailable.",
    as_of: accounts[0] ? new Date(accounts[0].source_timestamp_ms).toISOString() : "",
    position_lifecycles: performance.position_lifecycles,
    positions: [], orders: [], executions: canonicalFills,
    strategy_activity: [], activity: [],
  } : undefined;
  return <div className="canvas-config-page canvas-focus-page backtest-v4-canvas-review">
    <SavedV4CanvasHeader initialPage={initialPage} onClose={onClose} title="Backtest Canvas · Strategy 1"
      managementOpen={managementOpen} onManage={() => setManagementOpen(value => !value)} />
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
        switch (definition.id) {
          case "performance_journal": return <>{performanceError ? <p role="alert">Performance unavailable: {performanceError}</p> : null}
            <TradingJournalPreview data={performanceTrading} onSymbolSelect={openV4Ticker} readOnly settings={presentationSettings.journal} />
          </>;
          case "strategy_activity": return <section className="trading-preview"><StrategyActivityContainer
            asOf={new Date(dateInTimeZone(initialPage.market_cursor?.session_date || initialPage.run.session_date || "1970-01-01", "04:00", "America/New_York").getTime() + Number(initialPage.market_cursor?.boundary_ms ?? 0)).toISOString()}
            historicalRows={activityRows} historicalPage={{ complete: true }}
            onSettingsChange={patch => setActivitySettings(current => ({ ...current, ...patch }))}
            onTickerSelect={openV4Ticker} runId={runId} settings={activitySettings} />
            {eventError ? <p role="alert">Journal page unavailable: {eventError}</p> : null}
            {!eventsComplete ? <button className="button secondary compact" disabled={loadingEvents} onClick={() => void loadMoreEvents()} type="button">Load more events</button> : null}
          </section>;
          case "positions": return positionTrading ? <PositionsPreview data={positionTrading}
            onSymbolSelect={openV4Ticker} orderEvidenceComplete={false} settings={presentationSettings.positions} />
            : <div className="trading-disclosure" role="status">{performanceError || tradeError || (accounts.some(account => account.expected_position_count > 0)
              ? "Open positions require marks not retained by this saved review; lifecycle presentation is unavailable."
              : "Loading complete verified position lifecycles…")}</div>;
          case "orders": return <section className="trading-preview trading-order-manager"><p className="trading-disclosure">{orders ? `${orderCommands.length.toLocaleString()} verified order commands. ` : "Loading verified order commands. "}{orderTransitions.length ? `${orderTransitions.length.toLocaleString()} state transitions are retained.` : "Lifecycle status is unavailable; commands are not assumed filled or working."}</p>
            <EvidenceTable rows={commandRows} onSymbolSelect={openV4Ticker} columns={[["created_at", "Submitted"], ["ticker", "Ticker"], ["side", "Side"], ["order_type", "Type"], ["quantity", "Quantity"], ["limit_price", "Limit"], ["client_order_id", "Client order"]]} empty={loadingOrders ? "Loading verified order commands…" : "No verified order command."} />
            {orderError ? <p role="alert">Orders unavailable: {orderError}</p> : null}
            {orders && !orders.complete ? <button className="button secondary compact" disabled={loadingOrders} onClick={() => void loadMoreOrders()} type="button">Load more orders</button> : null}
          </section>;
          case "fills": return trade?.complete ? <ExecutionsPreview data={{ executions: canonicalFills,
            stale: true, complete: false, provider: "arte_typed_journal_v4", mode: "backtest",
            as_of: accounts[0] ? new Date(accounts[0].source_timestamp_ms).toISOString() : "",
            stale_reason: "Complete verified fills and final fees; other broker domains are not projected." }} onSymbolSelect={openV4Ticker} settings={presentationSettings.fills} />
            : <section className="trading-preview"><p className="trading-disclosure">{loadingTrades ? "Loading complete verified fill history…" : "Complete fill history is not loaded yet."}</p>
              {tradeError ? <p role="alert">Fills unavailable: {tradeError}</p> : null}
              {trade && !trade.complete ? <button className="button secondary compact" disabled={loadingTrades} onClick={() => void loadMoreTrades()} type="button">Load more fills</button> : null}</section>;
          case "chart": return chartTicker ? <BacktestV4SavedChart embedded runId={runId} ticker={chartTicker} />
            : <div className="trading-disclosure">Chart ticker will be selected from the first verified fill or journal signal.</div>;
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
export function BacktestV4ChartFocus({ runId, ticker, initialPage, onClose }: {
  runId: string; ticker: string; initialPage: V4Page; onClose: () => void;
}) {
  const [maximized, setMaximized] = useState(true);
  const [layout, setLayout] = useState<ChartsQuotesLayoutSettings>({ lowerRowPercent: 33, monthColumnPercent: 40, reservedColumnPercent: 20, tapeColumnPercent: 20 });
  const [quote, setQuote] = useState<SavedChartsQuote | null>(null);
  const sessionDate = initialPage.market_cursor?.session_date || initialPage.run.session_date;
  const savedAsOf = sessionDate && /^\d{4}-\d\d-\d\d$/.test(sessionDate)
    ? new Date(dateInTimeZone(sessionDate, "04:00", "America/New_York").getTime() + Number(initialPage.market_cursor?.boundary_ms ?? 0)).toISOString()
    : undefined;
  const definition = TRADING_WORKSPACE_CONTAINERS.filter(item => item.id === "charts_quotes");
  return <div className="canvas-config-page canvas-focus-page backtest-v4-canvas-review backtest-v4-chart-focus">
    <SavedV4CanvasHeader initialPage={initialPage} onClose={onClose} backLabel="Return to journal" title={`${ticker} · Charts & Quotes`} />
    <TradingWorkspace clockLabel="" commandBarVisible={false} compact
      definitionsOverride={definition} defaultOpenIds={["charts_quotes"]}
      excludedContainerIds={TRADING_WORKSPACE_CONTAINERS.filter(item => item.id !== "charts_quotes").map(item => item.id)}
      historicalSourceReady layoutPreset="focus" mode="backtest" persistState={false}
      runLabel={`${ticker} · Charts & Quotes`} runStatus="completed" sourceLabel="ARTE saved market"
      showHealth={false} metaForContainer={() => ({ sourceLabel: "ARTE verified V4", status: "ready", freshness: "Saved run" })}
      renderContainer={() => <ChartsQuotesMarketLayout symbol={ticker} end={savedAsOf} savedQuote={quote} layout={layout} onLayoutChange={setLayout}
        mainChartMaximized={maximized}
        mainChart={<BacktestV4SavedChart embedded runId={runId} ticker={ticker} onQuoteChange={value => setQuote(value ?? null)}
          toolbarAction={<button aria-label={maximized ? "Restore chart panels" : "Maximize main chart"} className="toolbar-button" onClick={() => setMaximized(value => !value)} title={maximized ? "Restore right column and bottom row" : "Maximize main chart: hide right column and bottom row"} type="button">{maximized ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>} />}
        monthChart={<BacktestV4SavedChart embedded enabled={!maximized} initialFrame="5s" panelLabel="5s context · certified ARTE" runId={runId} ticker={ticker} />}
        dailyChart={<BacktestV4SavedChart embedded enabled={!maximized} initialFrame="30s" panelLabel="30s context · certified ARTE" runId={runId} ticker={ticker} />}
        reservedPanel={<div className="trading-disclosure">Saved Backtest review · order entry disabled</div>} />} />
  </div>;
}
