import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, CircleDollarSign, PauseCircle, Play, RefreshCw, ShieldAlert, Wifi } from "lucide-react";

import { api, query } from "../api/client";
import { DataTable } from "../app/components/DataTable";

type LiveAccountType = "paper" | "cash";

type PreflightCheck = {
  details?: Record<string, unknown>;
  id: string;
  label: string;
  message: string;
  status: "ready" | "blocked" | string;
};

type PreflightPayload = {
  account_id: string;
  account_type: LiveAccountType;
  checks: PreflightCheck[];
  ready: boolean;
};

type ScannerRow = {
  ask?: number | null;
  bid?: number | null;
  day_change_pct: number;
  day_notional: number;
  day_volume: number;
  last_price: number;
  live_priority: number;
  provider: string;
  spread_bps?: number | null;
  symbol: string;
  trade_count: number;
};

type ScannerPayload = {
  market_time: string;
  provider: string;
  row_count: number;
  rows: ScannerRow[];
  session_date: string;
};

type RealPosition = {
  avg_price: number;
  mark_price: number;
  market_value: number;
  quantity: number;
  realized_pnl: number;
  symbol: string;
  unrealized_pnl: number;
};

type RealOrder = {
  avg_fill_price: number | null;
  broker_order_id: string;
  client_order_id?: string;
  filled_quantity: number;
  last_fill_price: number | null;
  limit_price?: number | null;
  order_type: string;
  quantity: number;
  remaining_quantity: number;
  side: "BUY" | "SELL" | string;
  status: string;
  submitted_at: string;
  symbol: string;
  time_in_force?: string;
};

type PortfolioPayload = {
  account_id: string;
  account_type: LiveAccountType;
  orders: RealOrder[];
  positions: RealPosition[];
  summary: Record<string, unknown>;
};

type OrderTicket = {
  limit_price: string;
  order_type: "LMT" | "MKT";
  quantity: string;
  side: "BUY" | "SELL";
  symbol: string;
  time_in_force: "DAY" | "GTC";
};

type OrderSubmitPayload = {
  account_id: string;
  account_type: LiveAccountType;
  broker_response: unknown;
  preview: boolean;
  submitted_order: RealOrder;
};

const EMPTY_TICKET: OrderTicket = {
  limit_price: "",
  order_type: "LMT",
  quantity: "100",
  side: "BUY",
  symbol: "",
  time_in_force: "DAY",
};

export function RealLiveTradingPage() {
  const [accountType, setAccountType] = useState<LiveAccountType>("paper");
  const [preflight, setPreflight] = useState<PreflightPayload | null>(null);
  const [scanner, setScanner] = useState<ScannerPayload | null>(null);
  const [positions, setPositions] = useState<RealPosition[]>([]);
  const [orders, setOrders] = useState<RealOrder[]>([]);
  const [ticket, setTicket] = useState<OrderTicket>(EMPTY_TICKET);
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const selectedQuote = useMemo(() => scanner?.rows.find((row) => row.symbol === ticket.symbol.toUpperCase()), [scanner?.rows, ticket.symbol]);
  const ready = Boolean(preflight?.ready);

  useEffect(() => {
    if (!running || !ready) return;
    const timer = window.setInterval(() => {
      void refreshLiveData();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [ready, running, accountType]);

  async function runPreflight() {
    setLoading(true);
    setError("");
    setMessage("Checking Massive and IBKR connections.");
    try {
      const payload = await api<PreflightPayload>(`/api/real-live-trading/preflight${query({ account_type: accountType })}`);
      setPreflight(payload);
      if (!payload.ready) {
        setRunning(false);
        setMessage("Live trading is blocked until every check is ready.");
        return;
      }
      setMessage("Connections are ready. Live data can start.");
      await refreshLiveData();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Preflight failed.");
    } finally {
      setLoading(false);
    }
  }

  async function refreshLiveData() {
    setLoading(true);
    setError("");
    try {
      const [scannerPayload, portfolioPayload] = await Promise.all([
        api<ScannerPayload>("/api/real-live-trading/scanner?row_limit=250"),
        api<PortfolioPayload>(`/api/real-live-trading/portfolio${query({ account_type: accountType })}`),
      ]);
      setScanner(scannerPayload);
      setPositions(portfolioPayload.positions ?? []);
      setOrders(portfolioPayload.orders ?? []);
      setMessage(`Updated ${scannerPayload.market_time} ET from ${scannerPayload.provider}.`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Live refresh failed.");
      setRunning(false);
    } finally {
      setLoading(false);
    }
  }

  async function submitOrder(preview: boolean) {
    setLoading(true);
    setError("");
    try {
      const payload = await api<OrderSubmitPayload>("/api/real-live-trading/orders", {
        method: "POST",
        body: JSON.stringify({
          account_type: accountType,
          preview,
          order: buildOrderRequest(ticket),
        }),
      });
      setOrders((current) => [payload.submitted_order, ...current]);
      setMessage(preview ? "IBKR order preview completed." : "Order submitted to IBKR.");
      if (!preview) await refreshLiveData();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Order request failed.");
    } finally {
      setLoading(false);
    }
  }

  function chooseSymbol(row: ScannerRow) {
    setTicket((current) => ({
      ...current,
      limit_price: row.ask ? String(row.ask) : row.last_price ? String(row.last_price) : current.limit_price,
      symbol: row.symbol,
    }));
  }

  return (
    <section className="real-live-page" aria-label="Real live trading">
      <header className="real-live-header">
        <div>
          <span>Real Live Trading</span>
          <h2>{accountType === "paper" ? "IBKR Paper" : "IBKR Cash"}</h2>
        </div>
        <div className="real-live-header-actions">
          <label>
            <span>Account</span>
            <select value={accountType} onChange={(event) => setAccountType(event.target.value === "cash" ? "cash" : "paper")}>
              <option value="paper">Paper</option>
              <option value="cash">Cash</option>
            </select>
          </label>
          <button className="button secondary" disabled={loading} onClick={runPreflight} type="button">
            <Wifi size={15} /> Check
          </button>
          <button className="button primary" disabled={!ready || loading} onClick={() => setRunning((value) => !value)} type="button">
            {running ? <PauseCircle size={15} /> : <Play size={15} />} {running ? "Pause" : "Start"}
          </button>
          <button className="button secondary" disabled={!ready || loading} onClick={() => void refreshLiveData()} type="button">
            <RefreshCw size={15} /> Refresh
          </button>
        </div>
      </header>

      {message ? <div className="real-live-message">{message}</div> : null}
      {error ? <div className="real-live-message error">{error}</div> : null}

      <section className="real-live-gate">
        {(preflight?.checks ?? []).map((check) => (
          <article className="real-live-check" data-status={check.status} key={check.id}>
            {check.status === "ready" ? <CheckCircle2 size={15} /> : <ShieldAlert size={15} />}
            <div>
              <strong>{check.label}</strong>
              <span>{check.message}</span>
            </div>
          </article>
        ))}
        {!preflight ? <article className="real-live-check"><Wifi size={15} /><div><strong>Connection gate</strong><span>Run checks before starting live trading.</span></div></article> : null}
      </section>

      <main className="real-live-grid">
        <section className="real-live-panel scanner">
          <div className="real-live-panel-title">
            <strong>Massive Scanner</strong>
            <span>{scanner ? `${scanner.row_count} rows` : "waiting"}</span>
          </div>
          <div className="real-live-scanner-table">
            <DataTable
              rows={(scanner?.rows ?? []).map((row) => ({
                ...row,
                day_change_pct: row.day_change_pct,
                spread_bps: row.spread_bps ?? "",
              }))}
              empty="No Massive scanner rows loaded."
              onRowClick={(row) => chooseSymbol(row as unknown as ScannerRow)}
            />
          </div>
        </section>

        <aside className="real-live-side">
          <section className="real-live-panel">
            <div className="real-live-panel-title">
              <strong>Order Ticket</strong>
              <span>{selectedQuote ? `${selectedQuote.bid ?? "-"} / ${selectedQuote.ask ?? "-"}` : "no quote"}</span>
            </div>
            <div className="real-live-ticket">
              <label><span>Symbol</span><input value={ticket.symbol} onChange={(event) => setTicket({ ...ticket, symbol: event.target.value.toUpperCase() })} /></label>
              <label><span>Side</span><select value={ticket.side} onChange={(event) => setTicket({ ...ticket, side: event.target.value === "SELL" ? "SELL" : "BUY" })}><option>BUY</option><option>SELL</option></select></label>
              <label><span>Type</span><select value={ticket.order_type} onChange={(event) => setTicket({ ...ticket, order_type: event.target.value === "MKT" ? "MKT" : "LMT" })}><option value="LMT">Limit</option><option value="MKT">Market</option></select></label>
              <label><span>Quantity</span><input type="number" value={ticket.quantity} onChange={(event) => setTicket({ ...ticket, quantity: event.target.value })} /></label>
              <label><span>Limit</span><input disabled={ticket.order_type === "MKT"} type="number" value={ticket.limit_price} onChange={(event) => setTicket({ ...ticket, limit_price: event.target.value })} /></label>
              <label><span>TIF</span><select value={ticket.time_in_force} onChange={(event) => setTicket({ ...ticket, time_in_force: event.target.value === "GTC" ? "GTC" : "DAY" })}><option>DAY</option><option>GTC</option></select></label>
              <button className="button secondary" disabled={!ready || loading} onClick={() => void submitOrder(true)} type="button">Preview</button>
              <button className="button primary" disabled={!ready || loading} onClick={() => void submitOrder(false)} type="button"><CircleDollarSign size={15} /> Submit</button>
            </div>
          </section>

          <section className="real-live-panel">
            <div className="real-live-panel-title"><strong>Positions</strong><span>{positions.length}</span></div>
            <DataTable rows={positions} empty="No live positions loaded." />
          </section>
        </aside>

        <section className="real-live-panel orders">
          <div className="real-live-panel-title"><strong>Live Orders</strong><span>fills tracked by broker schema</span></div>
          <DataTable rows={orders} empty="No live orders loaded." />
        </section>
      </main>
    </section>
  );
}

function buildOrderRequest(ticket: OrderTicket) {
  return {
    client_order_id: `live-${Date.now()}`,
    symbol: ticket.symbol.trim().toUpperCase(),
    side: ticket.side,
    order_type: ticket.order_type,
    quantity: Number(ticket.quantity),
    limit_price: ticket.order_type === "LMT" ? Number(ticket.limit_price) : null,
    time_in_force: ticket.time_in_force,
    outside_rth: true,
  };
}
