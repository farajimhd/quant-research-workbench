import { useState, type CSSProperties } from "react";
import { Settings, ShieldAlert } from "lucide-react";

import { type OrderRow, type PositionRow, type StageOrderContext } from "./portfolio";
import { quoteFromRow } from "./scanner";
import { type TradingSession } from "./time";
import {
  LiveNewsDetailPopover,
  LiveNewsSection,
  liveNewsItems,
  newsTickerCount,
  type LiveNewsArticle,
} from "./LiveNewsPanel";
import { integer, money, numberValue, percent, stringValue } from "./liveTradingFormat";

export function ChartTradePanel({
  availableCash,
  draft,
  onDraftChange,
  onStage,
  orders,
  position,
  quote,
  row,
  selectedTicker,
  session,
}: {
  availableCash: number;
  draft: { limit: string; quantity: string; side: "BUY" | "SELL"; stop: string; type: string };
  onDraftChange: (draft: { limit: string; quantity: string; side: "BUY" | "SELL"; stop: string; type: string }) => void;
  onStage: (side?: "BUY" | "SELL", status?: string, context?: Partial<StageOrderContext>) => void;
  orders: OrderRow[];
  position?: PositionRow;
  quote: ReturnType<typeof quoteFromRow>;
  row: Record<string, unknown>;
  selectedTicker: string;
  session: TradingSession;
}) {
  const [strategy, setStrategy] = useState("Manual");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [strategySettings, setStrategySettings] = useState({ orderType: "LIMIT", sizeMode: "risk_pct", sizeValue: "10", stopBufferPct: "3" });
  const stopBufferRatio = Math.max(0, Number(strategySettings.stopBufferPct || 3) / 100);
  const bufferedStop = quote.bid * (1 - stopBufferRatio);
  const suggestedStop = numberValue(row, "suggested_stop");
  const vwapStop = numberValue(row, "last_vwap") || bufferedStop;
  const longStop = Math.max(0, Math.min(suggestedStop || vwapStop, bufferedStop, quote.ask || Number.POSITIVE_INFINITY));
  const entryQuantity = calculateLiveOrderQuantity({
    availableCash,
    entry: quote.ask,
    mode: strategySettings.sizeMode,
    side: "BUY",
    stop: longStop,
    value: strategySettings.sizeValue,
  });
  const openOrders = orders.filter((order) => order.symbol === selectedTicker && order.status === "STAGED").length;
  const spreadRatio = quote.ask > 0 ? quote.spread / quote.ask : 0;
  const spreadTone = spreadRatio >= 0.02 || quote.spread >= 0.05 ? "danger" : spreadRatio >= 0.01 || quote.spread >= 0.02 ? "warning" : "success";
  const transactionsTone = quote.transactions <= 0 ? "muted" : quote.transactions < 100 ? "warning" : quote.transactions >= 300 ? "success" : "info";
  const volumeTone = quote.volume <= 0 ? "muted" : quote.volume < 8000 ? "warning" : quote.volume >= 50000 ? "success" : "info";
  const liquidityStats = [
    {
      detail: quote.transactions >= 300 ? "strong" : quote.transactions >= 100 ? "ok" : quote.transactions > 0 ? "thin" : "none",
      label: "Transactions",
      strength: quote.transactionsMarketStrength,
      tone: transactionsTone,
      value: integer(quote.transactions),
      warning: transactionsTone === "warning",
    },
    {
      detail: quote.volume >= 50000 ? "strong" : quote.volume >= 8000 ? "ok" : quote.volume > 0 ? "light" : "none",
      label: "Volume",
      strength: quote.volumeMarketStrength,
      tone: volumeTone,
      value: integer(quote.volume),
      warning: volumeTone === "warning",
    },
  ];
  const spreadWarning = spreadTone === "warning" || spreadTone === "danger";
  const [selectedNewsItem, setSelectedNewsItem] = useState<LiveNewsArticle | null>(null);
  const newsItems = liveNewsItems(row, session);
  const companyNewsItems = newsItems.filter((item) => newsTickerCount(item) <= 1);
  const otherNewsItems = newsItems.filter((item) => newsTickerCount(item) > 1);
  const newsRecency = stringValue(row, "live_news_recency") || "none";
  const actions = buildStrategyTradeActions({
    entryQuantity,
    longStop,
    orderType: strategySettings.orderType,
    position,
    quote,
    selectedTicker,
    strategy,
  });

  function stageStrategyAction(action: LiveStrategyTradeAction) {
    if (action.disabled) return;
    const context = {
      limit: action.limit,
      mark: quote.bid,
      quantity: action.quantity,
      row,
      side: action.side,
      status: "STAGED",
      stop: action.stop,
      symbol: selectedTicker,
      type: action.type,
    };
    onStage(action.side, "STAGED", context);
    onDraftChange({ ...draft, limit: action.limit.toFixed(4), quantity: String(action.quantity), side: action.side, stop: action.stop.toFixed(4), type: action.type });
  }

  return (
    <aside className="live-chart-trade-panel">
      <div className="live-chart-trade-header">
        <strong>{selectedTicker}</strong>
        <div className={position ? "live-trade-status active" : "live-trade-status"}>
          {position ? "In Position" : "Flat"}
        </div>
      </div>
      <div className="live-market-panel">
        <div className="live-inside-market">
          <div className="live-quote-price bid">
            <span>Bid</span>
            <strong>{money(quote.bid)}</strong>
          </div>
          <div className={`live-spread-badge ${spreadTone}`}>
            <span>
              {spreadWarning ? <ShieldAlert size={12} aria-hidden="true" /> : null}
              Spread
            </span>
            <strong>{money(quote.spread)}</strong>
            <small>{spreadRatio > 0 ? percent(spreadRatio) : "n/a"}</small>
          </div>
          <div className="live-quote-price ask">
            <span>Ask</span>
            <strong>{money(quote.ask)}</strong>
          </div>
        </div>
        <div className="live-market-health-list" aria-label="Market quality">
          {liquidityStats.map((stat) => (
            <div
              key={stat.label}
              className={`live-market-row ${stat.tone} has-strength`}
              style={marketStrengthStyle(stat.strength)}
              title={`${stat.label} market percentile: ${percent(stat.strength)}`}
            >
              <span>
                {stat.label}
                {stat.warning ? <ShieldAlert size={12} aria-hidden="true" /> : null}
              </span>
              <strong>{stat.value}</strong>
              <em>{stat.detail}</em>
            </div>
          ))}
        </div>
      </div>
      <div className={`live-news-card ${newsRecency}`} aria-label="Ticker news">
        <div className="live-news-card-header">
          <div>
            <span>News</span>
            <strong>{newsItems.length ? `${newsItems.length} headlines` : "No headlines yet"}</strong>
          </div>
          <div className="live-news-summary-pills" aria-label="News summary">
            <em>Company {companyNewsItems.length}</em>
            <em>Other {otherNewsItems.length}</em>
          </div>
        </div>
        <div className="live-news-sections">
          <LiveNewsSection empty="No single-company headlines by this bar." items={companyNewsItems} title="Company News" onOpen={setSelectedNewsItem} />
          <LiveNewsSection
            collapsible
            defaultOpen={false}
            empty="No multi-ticker or analyst headlines by this bar."
            items={otherNewsItems}
            title="Other / Analyst News"
            onOpen={setSelectedNewsItem}
          />
        </div>
      </div>
      {selectedNewsItem ? <LiveNewsDetailPopover item={selectedNewsItem} onClose={() => setSelectedNewsItem(null)} /> : null}
      <div className="live-execution-panel">
        <div className="live-strategy-row">
          <LiveSelect label="Strategy" value={strategy} values={["Manual", "Momentum Assist"]} onChange={setStrategy} />
          <button className={settingsOpen ? "icon-button active" : "icon-button"} title="Strategy settings" type="button" onClick={() => setSettingsOpen((current) => !current)}>
            <Settings size={15} />
          </button>
        </div>
        {settingsOpen ? (
          <div className="live-strategy-settings">
            <LiveSelect label="Sizing" value={strategySettings.sizeMode} values={["risk_pct", "dollar", "cash_pct", "shares"]} onChange={(value) => setStrategySettings((current) => ({ ...current, sizeMode: value }))} />
            <LiveField label={sizeModeLabel(strategySettings.sizeMode)} type="number" value={strategySettings.sizeValue} onChange={(value) => setStrategySettings((current) => ({ ...current, sizeValue: value }))} />
            <LiveSelect label="Order Type" value={strategySettings.orderType} values={["LIMIT", "MARKET", "STOP"]} onChange={(value) => setStrategySettings((current) => ({ ...current, orderType: value }))} />
            <LiveField label="Stop Buffer %" type="number" value={strategySettings.stopBufferPct} onChange={(value) => setStrategySettings((current) => ({ ...current, stopBufferPct: value }))} />
          </div>
        ) : null}
        <div className={`live-action-panel count-${actions.length}${actions.length > 2 ? " many" : ""}`}>
          {actions.map((action) => (
            <button
              key={action.id}
              className={`live-strategy-action ${action.tone}`}
              disabled={action.disabled || !selectedTicker || action.quantity <= 0}
              title={action.description}
              type="button"
              onClick={() => stageStrategyAction(action)}
            >
              <span>{action.label}</span>
              <strong>{action.side === "BUY" ? "Buy" : action.label}</strong>
              <small>{integer(action.quantity)} sh</small>
              <em>{money(action.limit)}</em>
            </button>
          ))}
        </div>
        <dl className="live-execution-summary">
          <div><dt>Size</dt><dd>{integer(entryQuantity)} sh</dd></div>
          <div><dt>Risk</dt><dd>{money(Math.max(0, quote.ask - longStop) * entryQuantity)}</dd></div>
          <div><dt>Cash</dt><dd>{money(availableCash)}</dd></div>
          <div><dt>Staged</dt><dd>{integer(openOrders)}</dd></div>
        </dl>
        {position ? (
          <div className={(quote.bid - position.avg_price) * position.quantity >= 0 ? "live-chart-position-strip positive" : "live-chart-position-strip negative"}>
            <div>
              <span>{integer(position.quantity)} sh</span>
              <strong>{money(position.avg_price)}</strong>
            </div>
            <div>
              <span>P/L</span>
              <strong>{money((quote.bid - position.avg_price) * position.quantity)}</strong>
              <small>{percent(position.avg_price > 0 ? quote.bid / position.avg_price - 1 : 0)}</small>
            </div>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

type LiveStrategyTradeAction = {
  description: string;
  disabled?: boolean;
  id: string;
  label: string;
  limit: number;
  quantity: number;
  side: "BUY" | "SELL";
  stop: number;
  tone: "buy" | "sell" | "neutral";
  type: string;
};

function buildStrategyTradeActions({
  entryQuantity,
  longStop,
  orderType,
  position,
  quote,
  selectedTicker,
  strategy,
}: {
  entryQuantity: number;
  longStop: number;
  orderType: string;
  position?: PositionRow;
  quote: ReturnType<typeof quoteFromRow>;
  selectedTicker: string;
  strategy: string;
}): LiveStrategyTradeAction[] {
  const closeQuantity = Math.max(0, Math.floor(position?.quantity ?? 0));
  const commonClose = {
    description: position ? `Sell ${integer(closeQuantity)} shares at ${money(quote.bid)}` : "Requires an open position",
    disabled: !position || closeQuantity <= 0,
    id: `${strategy}-close`,
    label: "Close",
    limit: quote.bid,
    quantity: closeQuantity,
    side: "SELL" as const,
    stop: position?.stop ?? 0,
    tone: "sell" as const,
    type: "LIMIT",
  };

  if (strategy === "Momentum Assist") {
    return [
      {
        description: `Buy ${integer(entryQuantity)} shares at ${money(quote.ask)}`,
        disabled: !selectedTicker || entryQuantity <= 0,
        id: "momentum-enter",
        label: "Enter",
        limit: quote.ask,
        quantity: entryQuantity,
        side: "BUY",
        stop: longStop,
        tone: "buy",
        type: orderType,
      },
      {
        description: position ? `Sell ${integer(closeQuantity)} shares at ${money(quote.bid)}` : "Requires an open position",
        disabled: !position || closeQuantity <= 0,
        id: "momentum-pocket",
        label: "Pocket",
        limit: quote.bid,
        quantity: closeQuantity,
        side: "SELL",
        stop: position?.stop ?? 0,
        tone: "neutral",
        type: "LIMIT",
      },
      commonClose,
    ];
  }

  return [
    {
      description: `Buy ${integer(entryQuantity)} shares at ${money(quote.ask)}`,
      disabled: !selectedTicker || entryQuantity <= 0,
      id: "manual-buy",
      label: "Buy Ask",
      limit: quote.ask,
      quantity: entryQuantity,
      side: "BUY",
      stop: longStop,
      tone: "buy",
      type: orderType,
    },
    commonClose,
  ];
}

export function LiveField({
  label,
  onChange,
  step,
  type,
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  step?: string;
  type: string;
  value: string;
}) {
  return (
    <label className="live-field">
      <span>{label}</span>
      <input step={step} type={type} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

export function LiveSelect({ label, onChange, value, values }: { label: string; onChange: (value: string) => void; value: string; values: string[] }) {
  return (
    <label className="live-field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {values.length ? values.map((item) => <option key={item} value={item}>{item}</option>) : <option value={value}>{value || "-"}</option>}
      </select>
    </label>
  );
}

function marketStrengthStyle(value: number): CSSProperties {
  const strength = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  return { "--strength": `${Math.round(strength * 100)}%` } as CSSProperties;
}

function calculateLiveOrderQuantity({
  availableCash,
  entry,
  mode,
  side,
  stop,
  value,
}: {
  availableCash: number;
  entry: number;
  mode: string;
  side: "BUY" | "SELL";
  stop: number;
  value: string;
}) {
  const numeric = Math.max(0, Number(value) || 0);
  if (!entry || entry <= 0) return 0;
  if (mode === "shares") return Math.floor(numeric);
  const cashCapShares = Math.floor(availableCash / entry);
  if (mode === "dollar") return Math.max(0, Math.min(cashCapShares, Math.floor(numeric / entry)));
  if (mode === "cash_pct") return Math.max(0, Math.min(cashCapShares, Math.floor((availableCash * numeric / 100) / entry)));
  const riskPerShare = Math.max(0.0001, side === "SELL" ? stop - entry : entry - stop);
  return Math.max(0, Math.min(cashCapShares, Math.floor((availableCash * numeric / 100) / riskPerShare)));
}

function sizeModeLabel(mode: string) {
  if (mode === "dollar") return "Dollars";
  if (mode === "cash_pct") return "% Cash";
  if (mode === "shares") return "Shares";
  return "% Risk";
}
