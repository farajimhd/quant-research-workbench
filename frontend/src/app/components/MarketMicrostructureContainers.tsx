import { Activity, CircleHelp, Clock3, Radio, ShieldAlert, WifiOff } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api, query } from "../../api/client";
import { TickerIdentity, useTickerPresentations } from "./TickerIdentity";

export type MarketEventSettings = { limit: number };

type CompactEvent = {
  arrival_sequence: number;
  condition_token_1: number;
  condition_token_2: number;
  condition_token_3: number;
  condition_token_4: number;
  condition_token_5: number;
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

type ExchangeReference = { acronym: string; mic: string; name: string; participant_id: string; type: string };
type ConditionReference = { name: string; sip_mapping: string; type: string; update_high_low: boolean; update_last: boolean; update_volume: boolean };
type MarketReferences = { conditions: Record<string, ConditionReference>; exchanges: Record<string, ExchangeReference> };
type MarketState = { active?: Array<{ event_type?: string; event_status?: string }>; as_of?: string; is_live_tradable?: boolean; is_tradable?: boolean; luld_active?: boolean; luld_distance_to_lower_pct?: number; luld_distance_to_upper_pct?: number; luld_lower_price?: number; luld_state?: string; luld_upper_price?: number; recent?: Array<{ event_type?: string; event_status?: string }>; trading_status?: string };
type MarketEventsPayload = { events: CompactEvent[]; market_state?: MarketState | null; market_state_error?: string; references: MarketReferences; source: string; symbol: string };
type ConnectionState = "connecting" | "live" | "point-in-time" | "reconnecting";
type Direction = "buy" | "mid" | "sell";
type QuoteUpdate = { ask: number; askExchange: number; askSize: number; bid: number; bidExchange: number; bidSize: number; id: number; issues: number; timestampUs: number };
type TapePrint = { conditionTokens: number[]; direction: Direction; exchange: number; id: number; issues: number; price: number; size: number; tape: number; timestampUs: number };
type QuoteSignal = { detail: string; quote: QuoteUpdate; tone: Direction };

type MarketContainerProps = { end?: string; settings: MarketEventSettings; start?: string; symbol: string };
const EMPTY_REFERENCES: MarketReferences = { conditions: {}, exchanges: {} };
const MARKET_EVENTS_UNAVAILABLE = "Live market events are unavailable. Start or reconnect QMD Gateway.";
const HISTORICAL_EVENTS_UNAVAILABLE = "Historical market events are unavailable. Start or reconnect QMD History.";

export function TapeContainer({ end, settings, start, symbol }: MarketContainerProps) {
  const { connected, error, events, marketState, references } = useMarketEvents(symbol, start, end);
  const decoded = useMemo(() => decodeMarketEvents(events), [events]);
  const chronological = decoded.trades.slice(-settings.limit);
  const prints = [...chronological].reverse();
  const last = prints[0];
  const buyVolume = chronological.reduce((sum, item) => sum + (item.direction === "buy" ? item.size : 0), 0);
  const sellVolume = chronological.reduce((sum, item) => sum + (item.direction === "sell" ? item.size : 0), 0);
  const directionalVolume = buyVolume + sellVolume;
  const buyShare = directionalVolume ? buyVolume / directionalVolume : 0.5;
  const largestPrint = chronological.reduce((largest, item) => Math.max(largest, item.size), 0);
  const pace = eventRate(chronological.map((item) => item.timestampUs));
  const presentations = useTickerPresentations([symbol]);

  return <section aria-label={`${symbol} time and sales`} className="market-microstructure tape-surface" data-market-state={connected}>
    <MicrostructureHeader connected={connected} detail={`Time & sales · ${prints.length} prints`} end={end} kind="tape" logoUrl={presentations[symbol]?.logo_url} marketState={marketState} symbol={symbol} />
    <div className="tape-overview" aria-label="Tape summary">
      <div className="last-print" data-direction={last?.direction ?? "mid"}>
        <MetricLabel help="The most recent eligible trade print at or before the displayed time." label="Last print" />
        <strong>{last ? formatPrice(last.price) : "—"}</strong>
        <span>{last ? `${directionLabel(last.direction)} · ${formatTradeSize(last.size)} sh` : "Waiting for a trade"}</span>
      </div>
      <div className="tape-flow">
        <SignalMetric help="At-ask volume divided by all directionally classified volume in the visible window." label="Buy share" tone={buyShare >= 0.5 ? "buy" : "sell"} value={`${Math.round(buyShare * 100)}%`} />
        <SignalMetric help="At-ask share volume minus at-bid share volume in the visible window." label="Net flow" tone={buyVolume >= sellVolume ? "buy" : "sell"} value={signedCompact(buyVolume - sellVolume)} />
        <SignalMetric help="Average prints per second across the visible tape window." label="Pace" value={`${pace.toFixed(pace >= 10 ? 0 : 1)}/s`} />
        <SignalMetric help="Largest single print size in the visible tape window." label="Largest" value={compactNumber(largestPrint)} />
      </div>
    </div>
    {error && !prints.length ? <MicrostructureEmpty message={error} /> : prints.length ? <div className="microstructure-scroll">
      <table className="tape-table">
        <thead><tr><th>Time ET</th><th>Price</th><th>Size</th><th>Exchange</th><th>Condition</th></tr></thead>
        <tbody>{prints.map((print) => {
          const exchange = venueReference(print.exchange, references);
          const condition = tradeCondition(print, references);
          return <tr data-condition-tone={condition.tone} data-direction={print.direction} key={print.id} title={print.issues ? `QMD issue flags: ${print.issues}` : directionLabel(print.direction)}>
            <td><time>{formatEventTime(print.timestampUs)}</time></td>
            <td className="numeric price">{formatPrice(print.price)}</td>
            <td className="numeric size">{formatTradeSize(print.size)}</td>
            <td><span className="venue-code" title={exchange.name}>{exchange.code}</span></td>
            <td><span className="condition-code" data-special={condition.special} title={condition.label}>{condition.code}</span><HelpTip label={`${condition.code}: ${condition.label}`} /></td>
          </tr>;
        })}</tbody>
      </table>
    </div> : <MicrostructureEmpty message={connected === "point-in-time" ? "No trade prints were found before the Canvas clock." : connected === "live" ? "Waiting for the next eligible trade print." : "Connecting to the live tape…"} />}
  </section>;
}

export function QuotesContainer({ end, settings, start, symbol }: MarketContainerProps) {
  const { connected, error, events, marketState, references } = useMarketEvents(symbol, start, end);
  const chronological = useMemo(() => decodeMarketEvents(events).quotes.slice(-settings.limit), [events, settings.limit]);
  const current = chronological.at(-1);
  const signals = useMemo(() => quoteSignals(chronological).reverse(), [chronological]);
  const presentations = useTickerPresentations([symbol]);
  const spread = current ? Math.max(0, current.ask - current.bid) : 0;
  const midpoint = current ? (current.ask + current.bid) / 2 : 0;
  const totalSize = current ? current.bidSize + current.askSize : 0;
  const imbalance = current && totalSize ? (current.bidSize - current.askSize) / totalSize : 0;
  const microprice = current && totalSize ? (current.ask * current.bidSize + current.bid * current.askSize) / totalSize : midpoint;
  const spreadState = classifySpread(spread, chronological);
  const bidVenue = venueReference(current?.bidExchange ?? 0, references);
  const askVenue = venueReference(current?.askExchange ?? 0, references);

  return <section aria-label={`${symbol} NBBO liquidity monitor`} className="market-microstructure quote-surface" data-market-state={connected}>
    <MicrostructureHeader connected={connected} detail="NBBO liquidity · consolidated top of book" end={end} kind="quotes" logoUrl={presentations[symbol]?.logo_url} marketState={marketState} symbol={symbol} />
    <div className="nbbo-overview" aria-label="Current NBBO and liquidity signals">
      <div className="nbbo-prices">
        <QuoteSide exchange={bidVenue} label="Bid" price={current?.bid} size={current?.bidSize} tone="buy" />
        <div className="spread-focus"><MetricLabel help="Current best ask minus current best bid." label="Spread" /><strong>{current ? formatPrice(spread) : "—"}</strong><span>{spreadState}</span></div>
        <QuoteSide exchange={askVenue} label="Ask" price={current?.ask} size={current?.askSize} tone="sell" />
      </div>
      <div className="liquidity-signals">
        <div className="imbalance-signal" data-tone={imbalance >= 0 ? "buy" : "sell"}>
          <span><MetricLabel help="(Bid size − ask size) ÷ total displayed NBBO size. Positive values are bid-heavy." label="Size imbalance" /><strong>{signedPercent(imbalance)}</strong></span>
          <i aria-hidden="true"><b style={{ width: `${Math.round((imbalance + 1) * 50)}%` }} /></i>
          <em>{imbalanceLabel(imbalance)}</em>
        </div>
        <SignalMetric help="Size-weighted NBBO price. It leans toward the side with less displayed liquidity." label="Microprice" tone={microprice >= midpoint ? "buy" : "sell"} value={current ? formatPrice(microprice) : "—"} />
        <SignalMetric help="Microprice minus the simple midpoint, shown in cents." label="Lean" tone={microprice >= midpoint ? "buy" : "sell"} value={current ? signedCents(microprice - midpoint) : "—"} />
        <SignalMetric help="Average NBBO updates per second across the visible quote window." label="Quote rate" value={`${eventRate(chronological.map((quote) => quote.timestampUs)).toFixed(1)}/s`} />
      </div>
    </div>
    {error && !signals.length ? <MicrostructureEmpty message={error} /> : signals.length ? <div className="microstructure-scroll">
      <table className="quote-signal-table">
        <thead><tr><th>Time ET</th><th>Liquidity event</th><th>Bid</th><th>Ask</th></tr></thead>
        <tbody>{signals.map(({ detail, quote, tone }, index) => {
          const rowBidVenue = venueReference(quote.bidExchange, references);
          const rowAskVenue = venueReference(quote.askExchange, references);
          return <tr data-current={index === 0 ? "true" : "false"} data-tone={tone} key={quote.id} title={quote.issues ? `QMD issue flags: ${quote.issues}` : undefined}>
            <td><time>{formatEventTime(quote.timestampUs)}</time></td>
            <td className="liquidity-event"><span>{detail}<HelpTip label={liquidityEventHelp(detail)} /></span><small>{imbalanceLabel((quote.bidSize - quote.askSize) / Math.max(1, quote.bidSize + quote.askSize))}</small></td>
            <td className="quote-cell bid"><strong>{formatPrice(quote.bid)}</strong><span>{formatSize(quote.bidSize)} · <abbr title={rowBidVenue.name}>{rowBidVenue.code}</abbr></span></td>
            <td className="quote-cell ask"><strong>{formatPrice(quote.ask)}</strong><span>{formatSize(quote.askSize)} · <abbr title={rowAskVenue.name}>{rowAskVenue.code}</abbr></span></td>
          </tr>;
        })}</tbody>
      </table>
    </div> : <MicrostructureEmpty message={connected === "point-in-time" ? "No NBBO updates were found before the Canvas clock." : connected === "live" ? "Waiting for the next NBBO update." : "Connecting to live NBBO…"} />}
  </section>;
}

function useMarketEvents(symbol: string, start?: string, end?: string) {
  const [events, setEvents] = useState<CompactEvent[]>([]);
  const [references, setReferences] = useState<MarketReferences>(EMPTY_REFERENCES);
  const [marketState, setMarketState] = useState<MarketState | null>(null);
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
    setMarketState(null);

    const merge = (incoming: CompactEvent[]) => setEvents((current) => {
      const rows = new Map(current.map((event) => [event.arrival_sequence, event]));
      incoming.forEach((event) => { if (event.ticker === ticker) rows.set(event.arrival_sequence, event); });
      return [...rows.values()].sort(compareEvents).slice(-1000);
    });

    const historical = Boolean(start && end);
    const loadMarketState = () => api<MarketState>(`/api/trading/canvas-market-state/${encodeURIComponent(ticker)}${query({ end, start })}`, { timeoutMs: historical ? 120000 : 10000 })
      .then((payload) => { if (active) setMarketState(payload); })
      .catch(() => { if (active) setMarketState(null); });
    void loadMarketState();
    api<MarketEventsPayload>(`/api/trading/canvas-market-events/${encodeURIComponent(ticker)}${query({ end, row_limit: 500, start })}`, { timeoutMs: historical ? 20000 : 10000 })
      .then((payload) => { if (active) { merge(payload.events); setReferences(payload.references ?? EMPTY_REFERENCES); if (historical) setConnected("point-in-time"); } })
      .catch(() => { if (active) setError(historical ? HISTORICAL_EVENTS_UNAVAILABLE : MARKET_EVENTS_UNAVAILABLE); });

    if (historical) return () => { active = false; };
    const marketStateTimer = window.setInterval(loadMarketState, 2_000);

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
    return () => { active = false; window.clearInterval(marketStateTimer); if (retryTimer) window.clearTimeout(retryTimer); socket?.close(); };
  }, [end, start, symbol]);

  return { connected, error, events, marketState, references };
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
      trades.push({
        conditionTokens: [event.condition_token_1, event.condition_token_2, event.condition_token_3, event.condition_token_4, event.condition_token_5].filter(Boolean),
        direction, exchange: event.exchange_primary, id: event.arrival_sequence, issues: event.issue_flags, price, size: event.size_primary,
        tape: ((event.event_meta >> 3) & 0x07) + 1, timestampUs: event.sip_timestamp_us,
      });
    }
  });
  return { quotes, trades };
}

function quoteSignals(quotes: QuoteUpdate[]): QuoteSignal[] {
  return quotes.map((quote, index) => {
    const previous = quotes[index - 1];
    if (!previous) return { detail: "Opening snapshot", quote, tone: "mid" };
    if (quote.bid > previous.bid) return { detail: `Bid improved ${signedCents(quote.bid - previous.bid)}`, quote, tone: "buy" };
    if (quote.ask < previous.ask) return { detail: `Ask moved down ${signedCents(quote.ask - previous.ask)}`, quote, tone: "sell" };
    if (quote.bid < previous.bid) return { detail: `Bid faded ${signedCents(quote.bid - previous.bid)}`, quote, tone: "sell" };
    if (quote.ask > previous.ask) return { detail: `Ask moved up ${signedCents(quote.ask - previous.ask)}`, quote, tone: "buy" };
    const bidDelta = quote.bidSize - previous.bidSize;
    const askDelta = quote.askSize - previous.askSize;
    if (Math.abs(bidDelta) >= Math.abs(askDelta) && bidDelta !== 0) return { detail: `Bid ${bidDelta > 0 ? "added" : "pulled"} ${signedShares(bidDelta)}`, quote, tone: bidDelta > 0 ? "buy" : "sell" };
    if (askDelta !== 0) return { detail: `Ask ${askDelta > 0 ? "added" : "pulled"} ${signedShares(askDelta)}`, quote, tone: askDelta > 0 ? "sell" : "buy" };
    return { detail: "Venue changed", quote, tone: "mid" };
  });
}

function MicrostructureHeader({ connected, detail, end, kind, logoUrl, marketState, symbol }: { connected: ConnectionState; detail: string; end?: string; kind: "quotes" | "tape"; logoUrl?: string; marketState: MarketState | null; symbol: string }) {
  const status = marketStatusPresentation(marketState);
  const context = connected === "live" ? "Live" : connected === "point-in-time" ? `Historical · ${formatContextTime(end)}` : connected;
  return <header className="microstructure-header">
    <div className="microstructure-identity"><TickerIdentity logoUrl={logoUrl} ticker={symbol} /><small>{detail}</small></div>
    <div className="microstructure-header-actions">
      <span className="market-status-badge" data-status={status.tone} title={status.help}>{status.tone === "halted" ? <ShieldAlert size={13} /> : <Radio size={12} />}{status.label}</span>
      <span className="luld-status-badge" data-status={status.luldTone} title={status.luldHelp}>LULD {status.luldLabel}<HelpTip label={status.luldHelp} /></span>
      <MicrostructureGuide kind={kind} />
      <span className="market-context-badge" data-state={connected} title={connected === "point-in-time" ? "A historical QMD snapshot ending at this Canvas time; it is not a live feed." : "Current QMD connection state."}>{connected === "live" ? <Radio size={11} /> : connected === "point-in-time" ? <Clock3 size={11} /> : connected === "connecting" ? <Activity size={11} /> : <WifiOff size={11} />}{context}</span>
    </div>
  </header>;
}
function SignalMetric({ help, label, tone, value }: { help: string; label: string; tone?: "buy" | "sell"; value: string }) { return <div className="signal-metric" data-tone={tone}><MetricLabel help={help} label={label} /><strong>{value}</strong></div>; }
function MetricLabel({ help, label }: { help: string; label: string }) { return <small className="metric-label">{label}<HelpTip label={help} /></small>; }
function HelpTip({ label }: { label: string }) { return <span aria-label={label} className="micro-help-tip" role="img" tabIndex={0} title={label}><CircleHelp size={11} /></span>; }
function MicrostructureGuide({ kind }: { kind: "quotes" | "tape" }) { return <details className="microstructure-guide"><summary><CircleHelp size={13} /> Help</summary><div className="microstructure-guide-popover">{kind === "tape" ? <><strong>Tape color key</strong><p><b className="key-ask">At ask</b> means the print executed at or above the current ask. <b className="key-bid">At bid</b> means at or below the bid. Cyan, violet, or amber identify between-market or special-condition prints.</p><p>Condition labels come from the SIP/QMD reference contract. Hover or focus a row's help icon for its full description.</p></> : <><strong>Liquidity event guide</strong><p>Bid improvement and ask pulls are buyer-supportive; bid fades and ask additions are seller-supportive. These are NBBO changes, not venue-depth orders.</p><p>Microprice and imbalance use displayed best-bid/best-ask size only. Hover or focus any metric help icon for its formula.</p></>}</div></details>; }
function QuoteSide({ exchange, label, price, size, tone }: { exchange: { code: string; name: string }; label: string; price?: number; size?: number; tone: "buy" | "sell" }) { return <div className="quote-side" data-tone={tone}><span><MetricLabel help={`${label} is the current consolidated national best ${label.toLowerCase()}; ${exchange.name} is posting it.`} label={label} /><abbr title={exchange.name}>{exchange.code}</abbr></span><strong>{price ? formatPrice(price) : "—"}</strong><em>{size != null ? `${formatSize(size)} shares` : "No quote"}</em></div>; }
function MicrostructureEmpty({ message }: { message: string }) { return <div className="microstructure-empty"><Activity size={18} /><span>{message}</span></div>; }
function compareEvents(left: CompactEvent, right: CompactEvent) { return left.sip_timestamp_us - right.sip_timestamp_us || left.source_sequence - right.source_sequence || left.arrival_sequence - right.arrival_sequence; }
function formatPrice(value: number) { return value >= 100 ? value.toFixed(2) : value.toFixed(4).replace(/0+$/, "").replace(/\.$/, ""); }
function formatSize(value: number) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value); }
function formatTradeSize(value: number) { return value > 0 && value < 1 ? value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "") : formatSize(value); }
function compactNumber(value: number) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, notation: "compact" }).format(value); }
function signedCompact(value: number) { return `${value > 0 ? "+" : ""}${compactNumber(value)}`; }
function signedPercent(value: number) { return `${value > 0 ? "+" : ""}${Math.round(value * 100)}%`; }
function signedCents(value: number) { const cents = value * 100; return `${cents > 0 ? "+" : ""}${cents.toFixed(Math.abs(cents) < 0.1 ? 2 : 1)}¢`; }
function signedShares(value: number) { return `${value > 0 ? "+" : ""}${formatSize(value)}`; }
function directionLabel(direction: Direction) { return direction === "buy" ? "At ask" : direction === "sell" ? "At bid" : "Between market"; }
function imbalanceLabel(value: number) { return value >= 0.25 ? "Bid-heavy" : value <= -0.25 ? "Ask-heavy" : "Balanced"; }
function eventRate(timestamps: number[]) { if (timestamps.length < 2) return 0; const seconds = Math.max(0.001, (timestamps.at(-1)! - timestamps[0]) / 1_000_000); return (timestamps.length - 1) / seconds; }
function formatEventTime(timestampUs: number) { return new Intl.DateTimeFormat("en-US", { fractionalSecondDigits: 3, hour: "2-digit", hour12: false, minute: "2-digit", second: "2-digit", timeZone: "America/New_York" }).format(new Date(timestampUs / 1000)); }
function classifySpread(current: number, quotes: QuoteUpdate[]) { const spreads = quotes.map((quote) => Math.max(0, quote.ask - quote.bid)).sort((a, b) => a - b); const median = spreads[Math.floor(spreads.length / 2)] ?? current; return current < median - 0.00001 ? "Tighter" : current > median + 0.00001 ? "Wider" : "Typical"; }
function venueReference(value: number, references: MarketReferences) { const reference = references.exchanges[String(value)]; return { code: reference?.acronym || reference?.mic || reference?.participant_id || (value ? `ID ${value}` : "—"), name: reference?.name || (value ? `Unresolved exchange ID ${value}` : "Unknown venue") }; }
function tradeCondition(print: TapePrint, references: MarketReferences) {
  const rows = print.conditionTokens.map((token) => references.conditions[String(token)]).filter((row): row is ConditionReference => Boolean(row));
  if (!rows.length) return { code: "REG", label: "Regular sale", special: false, tone: "regular" };
  const specialRows = rows.filter((row) => row.name.toLowerCase() !== "regular sale");
  if (!specialRows.length) return { code: "REG", label: rows.map((row) => row.name).join(" · "), special: false, tone: "regular" };
  const label = rows.map((row) => row.name).join(" · ");
  const normalized = label.toLowerCase();
  const tone = normalized.includes("out of sequence") || normalized.includes("late") ? "warning" : normalized.includes("odd lot") ? "odd" : normalized.includes("intermarket") || normalized.includes("exempt") ? "iso" : "special";
  return { code: specialRows.slice(0, 2).map((row) => shortConditionName(row)).join(" · "), label, special: true, tone };
}
function shortConditionName(row: ConditionReference) {
  const known: Record<string, string> = { "Derivatively Priced": "Derivative", "Intermarket Sweep": "ISO", "Odd Lot Trade": "Odd lot", "Sold (Out Of Sequence)": "Out of seq", "Trade Thru Exempt": "Exempt" };
  return known[row.name] || row.sip_mapping || row.name;
}

function liquidityEventHelp(detail: string) {
  if (detail.startsWith("Bid improved")) return "The national best bid moved higher, a buyer-supportive price change.";
  if (detail.startsWith("Ask moved down")) return "The national best ask moved lower, a seller-aggressive price change.";
  if (detail.startsWith("Bid faded")) return "The national best bid moved lower, indicating weaker displayed support.";
  if (detail.startsWith("Ask moved up")) return "The national best ask moved higher, reducing immediate sell-side pressure.";
  if (detail.startsWith("Bid added")) return "Displayed size increased at the best bid.";
  if (detail.startsWith("Bid pulled")) return "Displayed size decreased at the best bid.";
  if (detail.startsWith("Ask added")) return "Displayed size increased at the best ask.";
  if (detail.startsWith("Ask pulled")) return "Displayed size decreased at the best ask.";
  if (detail === "Venue changed") return "The venue posting the NBBO changed without a price or size change.";
  return "The first quote establishes the comparison baseline for later liquidity events.";
}

function formatContextTime(value?: string) {
  if (!value) return "selected time";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "selected time" : `${new Intl.DateTimeFormat("en-US", { hour: "2-digit", hour12: false, minute: "2-digit", second: "2-digit", timeZone: "America/New_York" }).format(date)} ET`;
}

function marketStatusPresentation(state: MarketState | null) {
  const active = state?.active ?? [];
  const halted = state?.trading_status === "halted" || state?.is_tradable === false || active.some((event) => event.event_type === "condition_halt");
  const blocked = !halted && state?.is_live_tradable === false;
  const resumed = state?.trading_status === "resumed" || (!halted && state?.recent?.[0]?.event_type === "condition_resume");
  const tradingLabel = halted ? "Halted" : blocked ? "Blocked" : resumed ? "Resumed" : state ? "Trading" : "Status unavailable";
  const luldEvent = active.find((event) => event.event_type?.startsWith("estimated_luld_"));
  const luldState = state?.luld_state ?? luldEvent?.event_type?.replace("estimated_luld_", "") ?? "unknown";
  const luldLabel = !state ? "unavailable" : state.luld_active === false ? "inactive" : luldState.replaceAll("_", " ");
  const bands = state?.luld_lower_price && state?.luld_upper_price ? ` Estimated QMD bands: ${formatPrice(state.luld_lower_price)}–${formatPrice(state.luld_upper_price)}.` : "";
  return {
    help: !state ? "QMD market status has not resolved for this timestamp." : halted ? "QMD reports a currently active halt/pause at this timestamp." : blocked ? "QMD currently blocks live tradability for an active market-state risk; inspect the LULD badge and QMD state details." : resumed ? "The latest QMD condition transition before this timestamp is a resumption." : "No active QMD halt condition is present at this timestamp.",
    label: tradingLabel,
    luldHelp: `QMD's locally estimated LULD proximity state; it is a risk estimate, not an official SIP band or halt declaration.${bands}`,
    luldLabel,
    luldTone: luldState.includes("above") || luldState.includes("below") ? "halted" : luldState.includes("near") ? "warning" : "normal",
    tone: halted || blocked ? "halted" : resumed ? "resumed" : state ? "normal" : "unknown",
  };
}
