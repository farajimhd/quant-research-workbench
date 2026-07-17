import { Activity, BookOpen, ChevronRight, CircleHelp, Clock3, Radio, ShieldAlert, WifiOff } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api, query } from "../../api/client";
import { QuoteChartGallery, TapeChartGallery } from "./MarketMicrostructureChartGallery";
import { Modal } from "./Modal";
import { TickerIdentityWithChange, useTickerPresentations } from "./TickerIdentity";

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
type QuoteSignalGroup = { id: number; quote: QuoteUpdate; signals: QuoteSignal[]; tone: Direction };

type MarketContainerProps = { end?: string; settings: MarketEventSettings; start?: string; symbol: string };
const EMPTY_REFERENCES: MarketReferences = { conditions: {}, exchanges: {} };
const MARKET_EVENT_HISTORY_LIMIT = 1024;
const MARKET_EVENT_SOURCE_LIMIT = 5000;
const MARKET_EVENTS_UNAVAILABLE = "Live market events are unavailable. Start or reconnect QMD Gateway.";
const HISTORICAL_EVENTS_UNAVAILABLE = "Historical market events are unavailable. Start or reconnect QMD History.";
const QUOTE_EVENT_GUIDE = [
  ["Opening snapshot", "Neutral", "First quote in the window; no preceding quote is available."],
  ["Bid improved", "Bullish", "Best bid moved higher. Buyers are willing to pay more."],
  ["Ask moved down", "Bearish", "Best ask moved lower. Sellers accepted a lower price, although the spread may tighten."],
  ["Bid faded", "Bearish", "Best bid moved lower and displayed buying support retreated."],
  ["Ask moved up", "Bullish", "Best ask moved higher as the cheaper offer retreated; the spread may also widen."],
  ["Bid added", "Bullish", "Displayed bid size increased without a price change. Support may be fleeting."],
  ["Bid pulled", "Bearish", "Displayed bid size decreased and visible support was withdrawn."],
  ["Ask added", "Bearish", "Displayed ask size increased, adding visible supply."],
  ["Ask pulled", "Bullish", "Displayed ask size decreased, leaving less visible supply above the market."],
  ["Venue changed", "Neutral", "No price or size transition was detected; the posting venue may have changed."],
];
const TAPE_METRIC_GUIDE = [
  ["Last print", "Latest eligible trade, size, and inferred side.", "Immediate execution state; one print has little forecasting value."],
  ["Buy share", "At-ask volume divided by at-ask plus at-bid volume.", "Sustained values above 50% indicate buyer aggression; mid-market trades are excluded."],
  ["Net flow", "At-ask share volume minus at-bid share volume.", "Positive is aggressive buying; negative is aggressive selling."],
  ["Pace", "Average prints per second.", "Measures urgency and information arrival, not direction."],
  ["Largest print", "Largest single trade in the visible window.", "Shows unusually large participation; direction and conditions determine meaning."],
  ["Aggressor streak", "Consecutive latest prints on one aggressor side.", "A long streak suggests short-lived flow persistence."],
  ["Price drift", "First-to-last trade return in basis points.", "Shows whether aggression produced price movement; it is realized response, not a forecast."],
  ["Large-print share", "Volume in prints at or above the window's 90th-percentile size.", "High values indicate concentrated large-trade participation."],
  ["Size acceleration", "Recent-half average size divided by earlier-half average size.", "Above 1× means prints are getting larger and urgency may be increasing."],
  ["Absorption", "One-sided aggressive flow with less than 1.5 bp price drift.", "Suggests passive or hidden liquidity may be absorbing the flow."],
];

export function TapeContainer({ end, start, symbol }: MarketContainerProps) {
  const { connected, error, events, marketState, references } = useMarketEvents(symbol, start, end);
  const decoded = useMemo(() => decodeMarketEvents(events), [events]);
  const chronological = decoded.trades.slice(-MARKET_EVENT_HISTORY_LIMIT);
  const prints = [...chronological].reverse();
  const last = prints[0];
  const buyVolume = chronological.reduce((sum, item) => sum + (item.direction === "buy" ? item.size : 0), 0);
  const sellVolume = chronological.reduce((sum, item) => sum + (item.direction === "sell" ? item.size : 0), 0);
  const directionalVolume = buyVolume + sellVolume;
  const buyShare = directionalVolume ? buyVolume / directionalVolume : 0.5;
  const largestPrint = chronological.reduce((largest, item) => Math.max(largest, item.size), 0);
  const pace = eventRate(chronological.map((item) => item.timestampUs));
  const priceDriftBps = chronological.length > 1 && chronological[0].price > 0 ? (chronological.at(-1)!.price / chronological[0].price - 1) * 10_000 : 0;
  const largeThreshold = percentile(chronological.map((item) => item.size), 0.9);
  const totalVolume = chronological.reduce((sum, item) => sum + item.size, 0);
  const largeShare = totalVolume ? chronological.reduce((sum, item) => sum + (item.size >= largeThreshold ? item.size : 0), 0) / totalVolume : 0;
  const streak = aggressorStreak(chronological);
  const sizeTrend = halfWindowRatio(chronological.map((item) => item.size));
  const absorption = directionalVolume > 0 && Math.abs(buyShare - 0.5) >= 0.18 && Math.abs(priceDriftBps) < 1.5;
  const presentations = useTickerPresentations([symbol]);

  return <section aria-label={`${symbol} time and sales`} className="market-microstructure tape-surface" data-market-state={connected}>
    <MicrostructureHeader connected={connected} end={end} kind="tape" logoUrl={presentations[symbol]?.logo_url} marketState={marketState} references={references} symbol={symbol} />
    <div className="tape-overview" aria-label="Tape summary">
      <div className="last-print" data-direction={last?.direction ?? "mid"}>
        <MetricLabel help="The most recent eligible trade print at or before the displayed time." label="Last print" />
        <strong>{last ? formatPrice(last.price) : "—"}</strong>
        <span>{last ? `${directionLabel(last.direction)} · ${formatTradeSize(last.size)} sh` : "Waiting for a trade"}</span>
      </div>
      <div className="tape-flow">
        <SignalMetric help="At-ask volume divided by all directionally classified volume in the visible window." label="Buy share" tone={buyShare >= 0.5 ? "buy" : "sell"} value={`${Math.round(buyShare * 100)}%`} />
        <SignalMetric help="At-ask share volume minus at-bid share volume in the visible window." label="Net flow" tone={buyVolume >= sellVolume ? "buy" : "sell"} value={signedCompact(buyVolume - sellVolume)} />
        <SignalMetric help="Average prints per second across the visible tape window." label="Pace" tone="mid" value={`${pace.toFixed(pace >= 10 ? 0 : 1)}/s`} />
        <SignalMetric help="Largest single print size in the visible tape window." label="Largest" tone="mid" value={compactNumber(largestPrint)} />
      </div>
      <div className="tape-diagnostics" aria-label="Tape diagnostic signals">
        <SignalMetric help="Consecutive most-recent prints classified on the same aggressor side. Longer streaks indicate short-lived order-flow persistence." label="Aggressor streak" tone={streak.direction === "buy" ? "buy" : streak.direction === "sell" ? "sell" : undefined} value={streak.count ? `${streak.count} ${directionLabel(streak.direction)}` : "Mixed"} />
        <SignalMetric help="Last print minus first print across the visible tape window, measured in basis points." label="Price drift" tone={priceDriftBps > 0 ? "buy" : priceDriftBps < 0 ? "sell" : undefined} value={`${priceDriftBps > 0 ? "+" : ""}${priceDriftBps.toFixed(1)} bp`} />
        <SignalMetric help="Share of visible volume executed in prints at or above the window's 90th-percentile size." label="Large-print share" tone="mid" value={`${Math.round(largeShare * 100)}%`} />
        <SignalMetric help="Recent-half average trade size divided by the earlier-half average. Above 1× means prints are getting larger." label="Size acceleration" tone="mid" value={`${sizeTrend.toFixed(2)}×`} />
        <SignalMetric help="Possible absorption appears when aggressive flow is one-sided but price barely moves. It is a diagnostic, not proof of hidden liquidity." label="Absorption" tone="mid" value={absorption ? "Possible" : "Not detected"} />
      </div>
    </div>
    <TapeChartGallery trades={chronological} />
    {error && !prints.length ? <MicrostructureEmpty message={error} /> : prints.length ? <div className="microstructure-scroll">
      <table className="tape-table">
        <thead><tr><th>Time ET</th><th>Price</th><th>Size</th><th>Exchange</th><th>Condition</th></tr></thead>
        <tbody>{prints.map((print) => {
          const exchange = venueReference(print.exchange, references);
          const condition = tradeCondition(print, references);
          const conditions = tradeConditionItems(print, references);
          return <tr data-condition-tone={condition.tone} data-direction={print.direction} key={print.id} title={print.issues ? `QMD issue flags: ${print.issues}` : directionLabel(print.direction)}>
            <td><time>{formatEventTime(print.timestampUs)}</time></td>
            <td className="numeric price">{formatPrice(print.price)}</td>
            <td className="numeric size">{formatTradeSize(print.size)}</td>
            <td><span className="venue-code" title={exchange.name}>{exchange.code}</span></td>
            <td><span className="trade-condition-list">{conditions.length ? conditions.map((item) => <span className="condition-code" data-condition-tone={conditionTone(item.name)} data-special={item.special} key={`${item.token}-${item.slot}`}><small>C{item.slot}</small>{item.label}</span>) : <span className="condition-empty">—</span>}</span></td>
          </tr>;
        })}</tbody>
      </table>
    </div> : <MicrostructureEmpty message={connected === "point-in-time" ? "No trade prints were found before the Canvas clock." : connected === "live" ? "Waiting for the next eligible trade print." : "Connecting to the live tape…"} />}
  </section>;
}

export function QuotesContainer({ end, start, symbol }: MarketContainerProps) {
  const { connected, error, events, marketState, references } = useMarketEvents(symbol, start, end);
  const chronological = useMemo(() => decodeMarketEvents(events).quotes.slice(-MARKET_EVENT_HISTORY_LIMIT), [events]);
  const current = chronological.at(-1);
  const signals = useMemo(() => quoteSignals(chronological).reverse(), [chronological]);
  const groups = useMemo(() => groupQuoteSignals(signals), [signals]);
  const [expandedGroups, setExpandedGroups] = useState<Set<number>>(() => new Set());
  const pressure = useMemo(() => quotePressureDimensions(chronological), [chronological]);
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
    <MicrostructureHeader connected={connected} end={end} kind="quotes" logoUrl={presentations[symbol]?.logo_url} marketState={marketState} references={references} symbol={symbol} />
    <div className="nbbo-overview" aria-label="Current NBBO and liquidity signals">
      <div className="nbbo-prices">
        <QuoteSide exchange={bidVenue} label="Bid" price={current?.bid} size={current?.bidSize} tone="buy" />
        <div className="spread-focus" data-tone={spreadState === "Tighter" ? "buy" : spreadState === "Wider" ? "sell" : "mid"}><MetricLabel help="Current best ask minus current best bid. Tight relative to this window is favorable for execution; wide is costly." label="Spread" /><strong>{current ? formatPrice(spread) : "—"}</strong><span>{spreadState}</span></div>
        <QuoteSide exchange={askVenue} label="Ask" price={current?.ask} size={current?.askSize} tone="sell" />
      </div>
      <div className="liquidity-signals">
        <div className="imbalance-signal" data-tone={imbalance >= 0 ? "buy" : "sell"}>
          <span><MetricLabel help="(Bid size − ask size) ÷ total displayed NBBO size. Positive values are bid-heavy." label="Size imbalance" /><strong>{signedPercent(imbalance)}</strong></span>
          <i aria-hidden="true"><b style={{ left: `${Math.round((imbalance + 1) * 50)}%` }} /></i>
          <em>{imbalanceLabel(imbalance)}</em>
        </div>
        <SignalMetric help="Size-weighted NBBO price. It leans toward the side with less displayed liquidity." label="Microprice" tone={microprice >= midpoint ? "buy" : "sell"} value={current ? formatPrice(microprice) : "—"} />
        <SignalMetric help="Microprice minus the simple midpoint, shown in cents." label="Lean" tone={microprice >= midpoint ? "buy" : "sell"} value={current ? signedCents(microprice - midpoint) : "—"} />
        <SignalMetric help="Average NBBO updates per second across the visible quote window." label="Quote rate" tone="mid" value={`${eventRate(chronological.map((quote) => quote.timestampUs)).toFixed(1)}/s`} />
      </div>
      <QuotePressurePanel dimensions={pressure} />
    </div>
    <QuoteChartGallery quotes={chronological} />
    {error && !groups.length ? <MicrostructureEmpty message={error} /> : groups.length ? <div className="microstructure-scroll">
      <table className="quote-signal-table">
        <thead><tr><th>Time ET</th><th>Quote burst / liquidity event</th><th>Bid</th><th>Ask</th></tr></thead>
        <tbody>{groups.flatMap((group, index) => {
          const summary = group.signals[0];
          const grouped = group.signals.length > 1;
          const expanded = expandedGroups.has(group.id);
          const rows = [<QuoteSignalRow current={index === 0} detail={grouped ? `${group.signals.length} updates · ${summarizeQuoteGroup(group)}` : summary.detail} expanded={expanded} grouped={grouped} key={`group-${group.id}`} onToggle={() => setExpandedGroups((currentGroups) => {
            const next = new Set(currentGroups);
            if (next.has(group.id)) next.delete(group.id); else next.add(group.id);
            return next;
          })} quote={group.quote} references={references} tone={group.tone} />];
          if (grouped && expanded) rows.push(...group.signals.map((signal) => <QuoteSignalRow child detail={signal.detail} key={`signal-${signal.quote.id}`} quote={signal.quote} references={references} tone={signal.tone} />));
          return rows;
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
      return [...rows.values()].sort(compareEvents).slice(-MARKET_EVENT_SOURCE_LIMIT);
    });

    const historical = Boolean(start && end);
    const loadMarketState = () => api<MarketState>(`/api/trading/canvas-market-state/${encodeURIComponent(ticker)}${query({ end, start })}`, { timeoutMs: historical ? 120000 : 10000 })
      .then((payload) => { if (active) setMarketState(payload); })
      .catch(() => { if (active) setMarketState(null); });
    void loadMarketState();
    api<MarketEventsPayload>(`/api/trading/canvas-market-events/${encodeURIComponent(ticker)}${query({ end, row_limit: MARKET_EVENT_SOURCE_LIMIT, start })}`, { timeoutMs: historical ? 20000 : 10000 })
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

function groupQuoteSignals(signals: QuoteSignal[]): QuoteSignalGroup[] {
  const groups: QuoteSignalGroup[] = [];
  signals.forEach((signal) => {
    const timestampMillisecond = Math.floor(signal.quote.timestampUs / 1_000);
    const current = groups.at(-1);
    if (current?.id === timestampMillisecond) {
      current.signals.push(signal);
      const score = current.signals.reduce((sum, item) => sum + (item.tone === "buy" ? 1 : item.tone === "sell" ? -1 : 0), 0);
      current.tone = score > 0 ? "buy" : score < 0 ? "sell" : "mid";
    } else groups.push({ id: timestampMillisecond, quote: signal.quote, signals: [signal], tone: signal.tone });
  });
  return groups;
}

function summarizeQuoteGroup(group: QuoteSignalGroup) {
  const unique = [...new Set(group.signals.map((signal) => signal.detail.replace(/ [+-][\d,.]+(?:Â¢)?$/, "")))];
  return unique.slice(0, 2).join(" + ");
}

function QuoteSignalRow({ child = false, current = false, detail, expanded = false, grouped = false, onToggle, quote, references, tone }: { child?: boolean; current?: boolean; detail: string; expanded?: boolean; grouped?: boolean; onToggle?: () => void; quote: QuoteUpdate; references: MarketReferences; tone: Direction }) {
  const bidVenue = venueReference(quote.bidExchange, references);
  const askVenue = venueReference(quote.askExchange, references);
  return <tr data-child={child ? "true" : "false"} data-current={current ? "true" : "false"} data-tone={tone} title={quote.issues ? `QMD issue flags: ${quote.issues}` : undefined}>
    <td>{child ? null : <time>{formatEventTime(quote.timestampUs)}</time>}</td>
    <td className="liquidity-event">{grouped ? <><button aria-expanded={expanded} className="quote-group-toggle" onClick={onToggle} type="button"><ChevronRight size={12} /><span>{detail}</span></button><small>{expanded ? "Collapse updates" : "Show every update in this timestamp"}</small></> : <><span>{detail}</span><small>{imbalanceLabel((quote.bidSize - quote.askSize) / Math.max(1, quote.bidSize + quote.askSize))}</small></>}</td>
    <td className="quote-cell bid"><strong>{formatPrice(quote.bid)}</strong><span>{formatSize(quote.bidSize)} · <abbr title={bidVenue.name}>{bidVenue.code}</abbr></span></td>
    <td className="quote-cell ask"><strong>{formatPrice(quote.ask)}</strong><span>{formatSize(quote.askSize)} · <abbr title={askVenue.name}>{askVenue.code}</abbr></span></td>
  </tr>;
}

type PressureDimension = { help: string; label: string; value: number };
function QuotePressurePanel({ dimensions }: { dimensions: PressureDimension[] }) {
  const composite = dimensions.reduce((sum, item) => sum + item.value, 0) / Math.max(1, dimensions.length);
  return <div className="quote-pressure-panel" aria-label="Quote pressure dimensions"><header><MetricLabel help="Four normalized quote-only signals from −100% ask/down pressure to +100% bid/up pressure. The center marker is neutral; these are diagnostics, not a forecast." label="Liquidity pressure" /><strong data-tone={composite > 0.08 ? "buy" : composite < -0.08 ? "sell" : "mid"}>{pressureLabel(composite)}</strong></header><div>{dimensions.map((item) => <div className="pressure-dimension" key={item.label}><MetricLabel help={item.help} label={item.label} /><i aria-hidden="true"><b data-tone={item.value > 0.08 ? "buy" : item.value < -0.08 ? "sell" : "mid"} style={{ left: `${(clamp(item.value, -1, 1) + 1) * 50}%` }} /></i><strong data-tone={item.value > 0.08 ? "buy" : item.value < -0.08 ? "sell" : "mid"}>{item.value > 0 ? "+" : ""}{Math.round(item.value * 100)}%</strong></div>)}</div></div>;
}

function quotePressureDimensions(quotes: QuoteUpdate[]): PressureDimension[] {
  const signals = quoteSignals(quotes);
  const directional = signals.filter((signal) => signal.tone !== "mid");
  const priceSignals = directional.filter((signal) => /Bid improved|Ask moved down|Bid faded|Ask moved up/.test(signal.detail));
  const score = (rows: QuoteSignal[]) => rows.length ? clamp(rows.reduce((sum, item) => sum + (item.tone === "buy" ? 1 : -1), 0) / rows.length, -1, 1) : 0;
  const current = quotes.at(-1);
  const sizeImbalance = current ? (current.bidSize - current.askSize) / Math.max(1, current.bidSize + current.askSize) : 0;
  const spread = current ? Math.max(0, current.ask - current.bid) : 0;
  const midpoint = current ? (current.ask + current.bid) / 2 : 0;
  const microprice = current ? (current.ask * current.bidSize + current.bid * current.askSize) / Math.max(1, current.bidSize + current.askSize) : midpoint;
  const micropriceLean = spread > 0 ? clamp((microprice - midpoint) / (spread / 2), -1, 1) : 0;
  return [
    { help: "Directional score of bid/ask price improvements and fades. Persistent positive values indicate upward NBBO repricing; negative values indicate downward repricing.", label: "Price", value: score(priceSignals) },
    { help: "Current displayed NBBO size imbalance. Positive means more size at the bid; negative means more at the ask.", label: "Displayed size", value: clamp(sizeImbalance, -1, 1) },
    { help: "Microprice displacement inside the spread. It estimates which side is thinner and therefore easier to move through.", label: "Microprice", value: micropriceLean },
    { help: "Directional agreement across the 12 most recent quote events. Persistence is more informative than a single update but can still reverse quickly.", label: "Persistence", value: score(directional.slice(-12)) },
  ];
}

function MicrostructureHeader({ connected, end, kind, logoUrl, marketState, references, symbol }: { connected: ConnectionState; end?: string; kind: "quotes" | "tape"; logoUrl?: string; marketState: MarketState | null; references: MarketReferences; symbol: string }) {
  const [liveAsOf] = useState(() => new Date().toISOString());
  const changeAsOf = end || liveAsOf;
  const status = marketStatusPresentation(marketState);
  const context = connected === "live" ? "Live" : connected === "point-in-time" ? `Historical · ${formatContextTime(end)}` : connected;
  return <header className="microstructure-header">
    <div className="microstructure-identity"><TickerIdentityWithChange asOf={changeAsOf} logoUrl={logoUrl} ticker={symbol} /></div>
    <div className="microstructure-header-actions">
      <span className="market-status-badge" data-status={status.tone} title={status.help}>{status.tone === "halted" ? <ShieldAlert size={13} /> : <Radio size={12} />}{status.label}</span>
      <span className="luld-status-badge" data-status={status.luldTone} title={status.luldHelp}>LULD {status.luldLabel}<HelpTip label={status.luldHelp} /></span>
      <MicrostructureGuide kind={kind} references={references} />
      <span className="market-context-badge" data-state={connected} title={connected === "point-in-time" ? "A historical QMD snapshot ending at this Canvas time; it is not a live feed." : "Current QMD connection state."}>{connected === "live" ? <Radio size={11} /> : connected === "point-in-time" ? <Clock3 size={11} /> : connected === "connecting" ? <Activity size={11} /> : <WifiOff size={11} />}{context}</span>
    </div>
  </header>;
}
function SignalMetric({ help, label, tone = "mid", value }: { help: string; label: string; tone?: Direction; value: string }) { return <div className="signal-metric" data-tone={tone}><MetricLabel help={help} label={label} /><strong>{value}</strong></div>; }
function MetricLabel({ help, label }: { help: string; label: string }) { return <small className="metric-label">{label}<HelpTip label={help} /></small>; }
function HelpTip({ label }: { label: string }) { return <span aria-label={label} className="micro-help-tip" role="img" tabIndex={0} title={label}><CircleHelp size={11} /></span>; }
function MicrostructureGuide({ kind, references }: { kind: "quotes" | "tape"; references: MarketReferences }) {
  const [open, setOpen] = useState(false);
  return <><button className="microstructure-guide-button" onClick={() => setOpen(true)} type="button"><BookOpen size={13} /> Guide</button>{open ? <Modal className="microstructure-guide-modal" onClose={() => setOpen(false)} title={kind === "tape" ? "Tape conditions and signal guide" : "NBBO liquidity analysis guide"}>{kind === "tape" ? <TapeGuide references={references} /> : <QuoteGuide />}</Modal> : null}</>;
}

function TapeGuide({ references }: { references: MarketReferences }) {
  const [search, setSearch] = useState("");
  const rows = Object.entries(references.conditions).filter(([, row]) => /trade_conditions|trade_corrections|held_trade/.test(row.type)).map(([token, row]) => ({ row, token })).filter(({ row }) => !search || `${row.name} ${row.sip_mapping} ${row.type}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="microstructure-guide-content"><section className="guide-intro"><h3>How to read the tape</h3><p><b className="key-ask">Green rows</b> executed at the ask, <b className="key-bid">red rows</b> at the bid, and colored between-market rows use the condition family below. Direction is inferred against the preceding NBBO.</p><div className="guide-signal-grid"><GuideSignal label="Flow" text="Buy share, net flow and aggressor streak measure who is crossing the spread." /><GuideSignal label="Response" text="Price drift shows whether that aggression is actually moving price." /><GuideSignal label="Participation" text="Pace, large-print share and size acceleration describe urgency and trade-size regime." /><GuideSignal label="Absorption" text="One-sided aggression with little price response can suggest passive liquidity absorbing flow." /></div></section><section><h3>Tape metrics</h3><GuideTable headings={["Metric", "What it measures", "Typical implication"]} rows={TAPE_METRIC_GUIDE} /></section><section><h3>Executed-volume profile</h3><p>The profile aggregates the visible 1,024 prints at tradable price increments. At-bid volume extends left and at-ask volume extends right. Repeated volume at a price shows auction acceptance or a battleground—not automatic support or resistance. Compare its directional delta with price drift and absorption.</p></section><section><div className="guide-search"><h3>Trade condition dictionary</h3><label><span>Find condition</span><input onChange={(event) => setSearch(event.target.value)} placeholder="Odd lot, ISO, out of sequence…" value={search} /></label></div><div className="condition-dictionary">{rows.map(({ row, token }) => <article data-condition-tone={conditionTone(row.name)} key={token}><header><strong>{shortConditionName(row)}</strong><span>{row.sip_mapping || `Token ${token}`}</span></header><p>{conditionDescription(row)}</p><footer><span>{row.update_last ? "Updates last" : "Does not update last"}</span><span>{row.update_volume ? "Counts volume" : "Excluded from volume"}</span><span>{row.update_high_low ? "Updates high/low" : "No high/low update"}</span></footer></article>)}</div></section></div>;
}

function QuoteGuide() { return <div className="microstructure-guide-content"><section className="guide-intro"><h3>From updates to an organized signal</h3><p>Quote bursts group all NBBO changes with the same SIP timestamp. The collapsed row preserves screen space; opening it reveals each price, size, or venue transition.</p><div className="guide-signal-grid"><GuideSignal label="Price pressure" text="Direction of bid and ask repricing. This is usually the strongest quote-only short-horizon feature." /><GuideSignal label="Displayed size" text="Best-level size imbalance. Useful, but vulnerable to cancellations and hidden liquidity." /><GuideSignal label="Microprice" text="Size-weighted location inside the spread. It measures which side is thinner and easier to consume." /><GuideSignal label="Persistence" text="Agreement across recent events. Repeated pressure is usually more useful than one isolated update." /></div></section><section><h3>Incremental quote-event classes</h3><GuideTable headings={["Class", "Tone", "Meaning and likely implication"]} rows={QUOTE_EVENT_GUIDE} /></section><section><h3>How quote strength accumulates</h3><p>The pressure path analyzes every raw transition rather than relying on the single display label. Each update combines 70% normalized midpoint movement with 30% change in displayed-size imbalance. The prior score retains 72% of its value and the new impulse contributes 55%, clamped to ±100%. Subsequent updates in the same direction therefore reinforce one another; opposite updates cancel the sequence. Price remains dominant because displayed size can disappear without trading.</p></section><section><h3>Forecasting interpretation</h3><p>The pressure map and path are feature summaries, not forecasts. For short-horizon models, use signed values, their changes, persistence, spread regime, update rate, venue churn and interaction with tape aggression. Validate separately by symbol, session phase and forecast horizon because impact decays quickly and reverses around news, halts and liquidity shocks.</p></section></div>; }
function GuideSignal({ label, text }: { label: string; text: string }) { return <article><strong>{label}</strong><p>{text}</p></article>; }
function GuideTable({ headings, rows }: { headings: string[]; rows: string[][] }) { return <div className="guide-table-scroll"><table className="guide-table"><thead><tr>{headings.map((heading) => <th key={heading}>{heading}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={row[0]}>{row.map((cell, index) => <td data-tone={index === 1 && (cell === "Bullish" || cell === "Bearish") ? (cell === "Bullish" ? "buy" : "sell") : undefined} key={`${row[0]}-${index}`}>{cell}</td>)}</tr>)}</tbody></table></div>; }
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
function tradeConditionItems(print: TapePrint, references: MarketReferences) {
  return print.conditionTokens.map((token, index) => {
    const row = references.conditions[String(token)];
    const name = row?.name || `Unknown condition ${token}`;
    return { label: row ? shortConditionName(row) : `Token ${token}`, name, slot: index + 1, special: name.toLowerCase() !== "regular sale", token };
  }).filter((item) => item.name.toLowerCase() !== "regular sale");
}
function shortConditionName(row: ConditionReference) {
  const known: Record<string, string> = { "Derivatively Priced": "Derivative", "Intermarket Sweep": "ISO", "Odd Lot Trade": "Odd lot", "Sold (Out Of Sequence)": "Out of seq", "Trade Thru Exempt": "Exempt" };
  return known[row.name] || row.name;
}

function conditionTone(name: string) {
  const normalized = name.toLowerCase();
  if (normalized.includes("out of sequence") || normalized.includes("late") || normalized.includes("sold")) return "warning";
  if (normalized.includes("odd lot")) return "odd";
  if (normalized.includes("intermarket") || normalized.includes("exempt") || normalized.includes("sweep")) return "iso";
  if (normalized.includes("regular")) return "regular";
  return "special";
}

function conditionDescription(row: ConditionReference) {
  const known: Record<string, string> = {
    "Derivatively Priced": "Price is derived from another instrument or pricing relationship, so it should not be read as ordinary price discovery.",
    "Intermarket Sweep": "An ISO indicates the sender is simultaneously routing to protected quotations elsewhere; it is commonly associated with urgent liquidity taking.",
    "Odd Lot Trade": "The trade size is below the standard round lot. It is real volume, but may not carry the same quoting or price-discovery significance as round lots.",
    "Regular Sale": "A standard eligible sale with no exceptional sequencing or pricing condition.",
    "Sold (Out Of Sequence)": "The print arrived or was reported out of normal sequence and should not be interpreted as the newest market price.",
    "Trade Thru Exempt": "The execution is exempt from the usual protected-quotation trade-through restriction.",
  };
  return known[row.name] || `${row.name} is a SIP trade qualifier. Its market-statistic behavior is shown below; interpret it together with execution side, size and sequence.`;
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

function percentile(values: number[], quantile: number) { if (!values.length) return 0; const sorted = [...values].sort((a, b) => a - b); return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * quantile))]; }
function halfWindowRatio(values: number[]) { if (values.length < 2) return 1; const split = Math.floor(values.length / 2); const average = (rows: number[]) => rows.reduce((sum, value) => sum + value, 0) / Math.max(1, rows.length); const earlier = average(values.slice(0, split)); return earlier > 0 ? average(values.slice(split)) / earlier : 1; }
function aggressorStreak(trades: TapePrint[]) { const latest = trades.at(-1); if (!latest || latest.direction === "mid") return { count: 0, direction: "mid" as Direction }; let count = 0; for (let index = trades.length - 1; index >= 0 && trades[index].direction === latest.direction; index -= 1) count += 1; return { count, direction: latest.direction }; }
function pressureLabel(value: number) { return value >= 0.35 ? "Strong bid pressure" : value >= 0.1 ? "Bid pressure" : value <= -0.35 ? "Strong ask pressure" : value <= -0.1 ? "Ask pressure" : "Mixed"; }
function clamp(value: number, minimum: number, maximum: number) { return Math.max(minimum, Math.min(maximum, value)); }
