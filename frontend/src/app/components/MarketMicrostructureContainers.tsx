import { Activity, Clock3, Radio, WifiOff } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api, query } from "../../api/client";
import { TickerIdentity, useTickerPresentations } from "./TickerIdentity";

export type MarketEventSettings = { limit: number };

type CompactEvent = {
  arrival_sequence: number;
  event_meta: number;
  exchange_primary: number;
  exchange_secondary: number;
  ingest_ts: string;
  issue_flags: number;
  price_primary_int: number;
  price_secondary_int: number;
  sip_timestamp_us: number;
  size_primary: number;
  size_secondary: number;
  source_sequence: number;
  ticker: string;
};

type MarketEventsPayload = { events: CompactEvent[]; source: string; symbol: string };
type ConnectionState = "connecting" | "live" | "point-in-time" | "reconnecting";
const MARKET_EVENTS_UNAVAILABLE = "Live market events are unavailable. Start or reconnect QMD Gateway.";
const HISTORICAL_EVENTS_UNAVAILABLE = "Historical market events are unavailable. Start or reconnect QMD History.";
type QuoteUpdate = { ask: number; askExchange: number; askSize: number; bid: number; bidExchange: number; bidSize: number; id: number; issues: number; timestampUs: number };
type TapePrint = { direction: "buy" | "mid" | "sell"; exchange: number; id: number; issues: number; price: number; size: number; tape: number; timestampUs: number };

type MarketContainerProps = { end?: string; settings: MarketEventSettings; start?: string; symbol: string };

export function TapeContainer({ end, settings, start, symbol }: MarketContainerProps) {
  const { connected, error, events } = useMarketEvents(symbol, start, end);
  const decoded = useMemo(() => decodeMarketEvents(events), [events]);
  const prints = decoded.trades.slice(-settings.limit).reverse();
  const buyVolume = prints.reduce((sum, item) => sum + (item.direction === "buy" ? item.size : 0), 0);
  const sellVolume = prints.reduce((sum, item) => sum + (item.direction === "sell" ? item.size : 0), 0);
  const presentations = useTickerPresentations([symbol]);

  return <section aria-label={`${symbol} time and sales`} className="market-microstructure" data-market-state={connected}>
    <MicrostructureHeader connected={connected} detail={`${prints.length} recent prints`} logoUrl={presentations[symbol]?.logo_url} symbol={symbol} />
    <div className="tape-summary" aria-label="Visible tape summary">
      <Metric label="Buy vol" tone="buy" value={compactNumber(buyVolume)} />
      <Metric label="Sell vol" tone="sell" value={compactNumber(sellVolume)} />
      <Metric label="Delta" tone={buyVolume >= sellVolume ? "buy" : "sell"} value={signedCompact(buyVolume - sellVolume)} />
    </div>
    {error && !prints.length ? <MicrostructureEmpty message={error} /> : prints.length ? <div className="microstructure-scroll">
      <table className="tape-table">
        <thead><tr><th>Time ET</th><th>Price</th><th>Size</th><th>Venue</th><th>Tape</th></tr></thead>
        <tbody>{prints.map((print) => <tr data-direction={print.direction} key={print.id} title={print.issues ? `QMD issue flags: ${print.issues}` : undefined}>
          <td><time>{formatEventTime(print.timestampUs)}</time></td><td className="numeric price"><span aria-hidden="true" />{formatPrice(print.price)}</td><td className="numeric">{formatSize(print.size)}</td><td>{venue(print.exchange)}</td><td>{print.tape}</td>
        </tr>)}</tbody>
      </table>
    </div> : <MicrostructureEmpty message={connected === "point-in-time" ? "No trade prints were found before the Canvas clock." : connected === "live" ? "Waiting for the next eligible trade print." : "Connecting to the live tape…"} />}
  </section>;
}

export function QuotesContainer({ end, settings, start, symbol }: MarketContainerProps) {
  const { connected, error, events } = useMarketEvents(symbol, start, end);
  const quotes = useMemo(() => decodeMarketEvents(events).quotes.slice(-settings.limit).reverse(), [events, settings.limit]);
  const current = quotes[0];
  const presentations = useTickerPresentations([symbol]);
  const spread = current ? Math.max(0, current.ask - current.bid) : 0;

  return <section aria-label={`${symbol} NBBO quote ladder`} className="market-microstructure quote-ladder" data-market-state={connected}>
    <MicrostructureHeader connected={connected} detail="Consolidated NBBO · not venue depth" logoUrl={presentations[symbol]?.logo_url} symbol={symbol} />
    <div className="quote-topline" aria-label="Current NBBO">
      <Metric label="Bid" tone="buy" value={current ? `${formatPrice(current.bid)} × ${formatSize(current.bidSize)}` : "—"} />
      <Metric label="Spread" value={current ? formatPrice(spread) : "—"} />
      <Metric label="Ask" tone="sell" value={current ? `${formatPrice(current.ask)} × ${formatSize(current.askSize)}` : "—"} />
    </div>
    {error && !quotes.length ? <MicrostructureEmpty message={error} /> : quotes.length ? <div className="microstructure-scroll">
      <table className="quote-ladder-table">
        <thead><tr><th>Time ET</th><th>Bid venue</th><th>Bid size</th><th>Bid</th><th>Ask</th><th>Ask size</th><th>Ask venue</th></tr></thead>
        <tbody>{quotes.map((quote, index) => <tr data-current={index === 0 ? "true" : "false"} key={quote.id} title={quote.issues ? `QMD issue flags: ${quote.issues}` : undefined}>
          <td><time>{formatEventTime(quote.timestampUs)}</time></td><td>{venue(quote.bidExchange)}</td><td className="numeric bid-size"><i style={{ width: sizeBarWidth(quote.bidSize, quotes.flatMap((row) => [row.bidSize, row.askSize])) }} />{formatSize(quote.bidSize)}</td><td className="numeric bid-price">{formatPrice(quote.bid)}</td><td className="numeric ask-price">{formatPrice(quote.ask)}</td><td className="numeric ask-size">{formatSize(quote.askSize)}<i style={{ width: sizeBarWidth(quote.askSize, quotes.flatMap((row) => [row.bidSize, row.askSize])) }} /></td><td>{venue(quote.askExchange)}</td>
        </tr>)}</tbody>
      </table>
    </div> : <MicrostructureEmpty message={connected === "point-in-time" ? "No NBBO updates were found before the Canvas clock." : connected === "live" ? "Waiting for the next NBBO update." : "Connecting to live NBBO…"} />}
  </section>;
}

function useMarketEvents(symbol: string, start?: string, end?: string) {
  const [events, setEvents] = useState<CompactEvent[]>([]);
  const [connected, setConnected] = useState<ConnectionState>("connecting");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    let socket: WebSocket | null = null;
    let retryTimer: number | undefined;
    let retryAttempt = 0;
    const ticker = symbol.trim().toUpperCase();
    setEvents([]);
    setConnected("connecting");
    setError("");

    const merge = (incoming: CompactEvent[]) => setEvents((current) => {
      const rows = new Map(current.map((event) => [event.arrival_sequence, event]));
      incoming.forEach((event) => { if (event.ticker === ticker) rows.set(event.arrival_sequence, event); });
      return [...rows.values()].sort(compareEvents).slice(-1000);
    });

    const historical = Boolean(start && end);
    api<MarketEventsPayload>(`/api/trading/canvas-market-events/${encodeURIComponent(ticker)}${query({ end, row_limit: 500, start })}`, { timeoutMs: historical ? 20000 : 10000 })
      .then((payload) => { if (active) { merge(payload.events); if (historical) setConnected("point-in-time"); } })
      .catch(() => { if (active) setError(historical ? HISTORICAL_EVENTS_UNAVAILABLE : MARKET_EVENTS_UNAVAILABLE); });

    if (historical) return () => { active = false; };

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const connect = () => {
      if (!active) return;
      socket = new WebSocket(`${protocol}//${window.location.host}/api/trading/canvas-market-events/stream/${encodeURIComponent(ticker)}`);
      socket.onopen = () => { if (active) setConnected("connecting"); };
      socket.onmessage = (message) => {
        if (!active) return;
        const payload = JSON.parse(String(message.data)) as CompactEvent & { error?: string; status?: string; warning?: string };
        if (payload.error) setError(MARKET_EVENTS_UNAVAILABLE);
        else if (payload.warning) setConnected("reconnecting");
        else if (payload.status === "connected") { retryAttempt = 0; setConnected("live"); setError(""); }
        else { retryAttempt = 0; setConnected("live"); setError(""); merge([payload]); }
      };
      socket.onerror = () => { if (active) { setConnected("reconnecting"); setError(MARKET_EVENTS_UNAVAILABLE); } };
      socket.onclose = () => {
        if (!active) return;
        setConnected("reconnecting");
        const delay = Math.min(5_000, 500 * 2 ** retryAttempt);
        retryAttempt += 1;
        retryTimer = window.setTimeout(connect, delay);
      };
    };
    connect();
    return () => { active = false; if (retryTimer) window.clearTimeout(retryTimer); socket?.close(); };
  }, [end, start, symbol]);

  return { connected, error, events };
}

function decodeMarketEvents(events: CompactEvent[]) {
  const quotes: QuoteUpdate[] = [];
  const trades: TapePrint[] = [];
  let nbbo: QuoteUpdate | null = null;
  events.forEach((event) => {
    const primaryScale = event.event_meta & 0x02 ? 10_000 : 100;
    const secondaryScale = event.event_meta & 0x04 ? 10_000 : 100;
    if ((event.event_meta & 0x01) === 0) {
      const ask = event.price_primary_int / primaryScale;
      const bid = event.price_secondary_int / secondaryScale;
      if (bid <= 0 || ask <= 0 || ask < bid) return;
      nbbo = {
        ask, askExchange: event.exchange_primary, askSize: event.size_primary,
        bid, bidExchange: event.exchange_secondary, bidSize: event.size_secondary,
        id: event.arrival_sequence, issues: event.issue_flags, timestampUs: event.sip_timestamp_us,
      };
      quotes.push(nbbo);
    } else {
      const price = event.price_primary_int / primaryScale;
      if (price <= 0 || event.size_primary <= 0) return;
      const direction = nbbo && nbbo.ask > 0 && price >= nbbo.ask ? "buy" : nbbo && nbbo.bid > 0 && price <= nbbo.bid ? "sell" : "mid";
      trades.push({ direction, exchange: event.exchange_primary, id: event.arrival_sequence, issues: event.issue_flags, price, size: event.size_primary, tape: ((event.event_meta >> 3) & 0x07) + 1, timestampUs: event.sip_timestamp_us });
    }
  });
  return { quotes, trades };
}

function MicrostructureHeader({ connected, detail, logoUrl, symbol }: { connected: ConnectionState; detail: string; logoUrl?: string; symbol: string }) {
  return <header className="microstructure-header"><div><TickerIdentity logoUrl={logoUrl} ticker={symbol} /><small>{detail}</small></div><span data-state={connected}>{connected === "live" ? <Radio size={11} /> : connected === "point-in-time" ? <Clock3 size={11} /> : connected === "connecting" ? <Activity size={11} /> : <WifiOff size={11} />}{connected}</span></header>;
}
function Metric({ label, tone, value }: { label: string; tone?: "buy" | "sell"; value: string }) { return <div data-tone={tone}><small>{label}</small><strong>{value}</strong></div>; }
function MicrostructureEmpty({ message }: { message: string }) { return <div className="microstructure-empty"><Activity size={18} /><span>{message}</span></div>; }
function compareEvents(left: CompactEvent, right: CompactEvent) { return left.sip_timestamp_us - right.sip_timestamp_us || left.source_sequence - right.source_sequence || left.arrival_sequence - right.arrival_sequence; }
function formatPrice(value: number) { return value >= 100 ? value.toFixed(2) : value.toFixed(4).replace(/0+$/, "").replace(/\.$/, ""); }
function formatSize(value: number) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value); }
function compactNumber(value: number) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, notation: "compact" }).format(value); }
function signedCompact(value: number) { return `${value > 0 ? "+" : ""}${compactNumber(value)}`; }
function venue(value: number) { return value > 0 ? `X${value}` : "—"; }
function formatEventTime(timestampUs: number) { return new Intl.DateTimeFormat("en-US", { fractionalSecondDigits: 3, hour: "2-digit", hour12: false, minute: "2-digit", second: "2-digit", timeZone: "America/New_York" }).format(new Date(timestampUs / 1000)); }
function sizeBarWidth(value: number, sizes: number[]) { const maximum = Math.max(1, ...sizes); return `${Math.max(4, Math.round(value / maximum * 100))}%`; }
