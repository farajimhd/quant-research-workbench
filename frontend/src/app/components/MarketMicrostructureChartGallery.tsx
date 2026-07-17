import { useMemo, useState, type ReactNode } from "react";

type Direction = "buy" | "mid" | "sell";
export type MicroQuote = { ask: number; askSize: number; bid: number; bidSize: number; timestampUs: number };
export type MicroTrade = { conditionTokens: number[]; direction: Direction; price: number; size: number; timestampUs: number };

type ChartChoice = {
  bearish: string;
  bullish: string;
  caution: string;
  chart: ReactNode;
  id: string;
  label: string;
  question: string;
  read: string;
  value?: string;
};

const WIDTH = 640;
const HEIGHT = 116;
const PAD_X = 48;
const PAD_Y = 12;

export function QuoteChartGallery({ quotes }: { quotes: MicroQuote[] }) {
  const charts = useMemo(() => quoteCharts(quotes), [quotes]);
  return <ChartGallery charts={charts} defaultId="price" empty="Quote updates are required to compare chart views." kind="Quote" />;
}

export function TapeChartGallery({ trades }: { trades: MicroTrade[] }) {
  const charts = useMemo(() => tapeCharts(trades), [trades]);
  return <ChartGallery charts={charts} defaultId="profile" empty="Trade prints are required to compare chart views." kind="Tape" />;
}

function ChartGallery({ charts, defaultId, empty, kind }: { charts: ChartChoice[]; defaultId: string; empty: string; kind: string }) {
  const [selectedId, setSelectedId] = useState(defaultId);
  const selected = charts.find((chart) => chart.id === selectedId) ?? charts[0];
  return <details className="microstructure-visual micro-chart-gallery" open>
    <summary><span><strong>{kind} chart comparison</strong><small>Select a view and read its guide; collapse this panel whenever you need more table rows.</small></span><b>{charts.length} views</b></summary>
    {selected ? <div className="micro-chart-lab">
      <div aria-label={`${kind} chart choices`} className="micro-chart-tabs" role="tablist">{charts.map((chart) => <button aria-selected={chart.id === selected.id} key={chart.id} onClick={() => setSelectedId(chart.id)} role="tab" type="button">{chart.label}</button>)}</div>
      <section className="micro-chart-guide" aria-label={`How to read ${selected.label}`}>
        <header><span><strong>{selected.label}</strong><small>{selected.question}</small></span>{selected.value ? <b>{selected.value}</b> : null}</header>
        <div><GuidePoint label="Read" text={selected.read} tone="mid" /><GuidePoint label="Bullish evidence" text={selected.bullish} tone="buy" /><GuidePoint label="Bearish evidence" text={selected.bearish} tone="sell" /><GuidePoint label="Do not overread" text={selected.caution} tone="warning" /></div>
      </section>
      <div className="micro-chart-stage">{selected.chart}</div>
    </div> : <span className="visual-empty">{empty}</span>}
  </details>;
}

function GuidePoint({ label, text, tone }: { label: string; text: string; tone: "buy" | "mid" | "sell" | "warning" }) {
  return <article data-tone={tone}><strong>{label}</strong><span>{text}</span></article>;
}

function quoteCharts(quotes: MicroQuote[]): ChartChoice[] {
  if (quotes.length < 2) return [];
  const visible = quotes.slice(-96);
  const frames = visible.map((quote) => quoteFrame(quote));
  const pressure = quotePressureSeries(visible);
  const midpoint = frames.map((frame) => frame.midpoint);
  const microprice = frames.map((frame) => frame.microprice);
  const imbalance = frames.map((frame) => frame.imbalance);
  const spread = frames.map((frame) => frame.spread * 100);
  const spreadMedian = percentile(spread, 0.5);
  const activity = quoteActivityBins(quotes.slice(-1024), 28);
  const eventMix = quoteEventMix(quotes.slice(-1024));
  const lastFrame = frames.at(-1)!;
  const firstMidpoint = midpoint[0];
  const midpointChange = (lastFrame.midpoint - firstMidpoint) * 100;
  return [
    {
      id: "price", label: "Price + microprice", question: "Is the NBBO repricing, and does displayed liquidity lean ahead of it?", value: `${signed(midpointChange, 2)}¢`,
      read: "Oldest is left and newest is right. Black is midpoint; blue is microprice. The right label is midpoint change over 96 updates.",
      bullish: "Both lines rise, or microprice stays above midpoint while midpoint begins rising.", bearish: "Both lines fall, or microprice stays below midpoint while midpoint begins falling.",
      caution: "A microprice lead without subsequent midpoint movement can be cancelled displayed size, not real demand.",
      chart: <MultiLineChart format={formatPrice} series={[{ className: "chart-midpoint", label: "Midpoint", values: midpoint }, { className: "chart-microprice", label: "Microprice", values: microprice }]} />,
    },
    {
      id: "pressure", label: "Pressure sequence", question: "Are price and displayed-size changes reinforcing in one direction?", value: signedPercent(pressure.at(-1) ?? 0),
      read: "Oldest is left, newest is right. The center line is neutral; green above is bid/up pressure and red below is ask/down pressure. Scale is fixed at ±100%.",
      bullish: "Several consecutive green bars grow or remain above zero, especially while midpoint also rises.", bearish: "Several consecutive red bars deepen or remain below zero, especially while midpoint also falls.",
      caution: "Small alternating bars mean noise. Pressure is a decaying diagnostic, not a probability or price target.",
      chart: <SignedBars values={pressure} />,
    },
    {
      id: "imbalance", label: "Size imbalance", question: "Which NBBO side currently shows more displayed shares?", value: signedPercent(lastFrame.imbalance),
      read: "Each bar is one quote update. Green above zero is bid-heavy; red below zero is ask-heavy. Oldest is left.",
      bullish: "Persistent positive imbalance that survives updates and is followed by bid improvement.", bearish: "Persistent negative imbalance that survives updates and is followed by ask-down or bid-fade events.",
      caution: "Displayed size can cancel instantly and excludes hidden liquidity and all depth away from the NBBO.",
      chart: <SignedBars values={imbalance} />,
    },
    {
      id: "spread", label: "Spread regime", question: "Is top-of-book liquidity becoming cheaper or more fragile?", value: `${spread.at(-1)?.toFixed(1) ?? "0.0"}¢`,
      read: "The line is spread in cents; the dashed line is the median of these 96 updates. Lower is tighter and usually easier to execute.",
      bullish: "A tightening spread combined with rising bid and midpoint can support an orderly upward move.", bearish: "A tightening spread combined with falling ask and midpoint can support an orderly downward move.",
      caution: "Widening predicts uncertainty and slippage, not direction. It can precede either a jump or a drop.",
      chart: <MultiLineChart format={(value) => `${value.toFixed(1)}¢`} reference={spreadMedian} series={[{ className: "chart-spread", label: "Spread", values: spread }]} />,
    },
    {
      id: "activity", label: "Quote activity", question: "When is NBBO information arriving fastest, and which way did midpoint move in each burst?", value: `${activity.reduce((sum, item) => sum + item.count, 0)} updates`,
      read: "Equal-time buckets run oldest to newest. Bar height is update count; green/red records the midpoint direction inside that bucket.",
      bullish: "Activity expands while successive buckets reprice midpoint upward.", bearish: "Activity expands while successive buckets reprice midpoint downward.",
      caution: "High update count with flat midpoint can be venue churn or quote flicker rather than directional conviction.",
      chart: <ActivityBars bins={activity} />,
    },
    {
      id: "events", label: "Event mix", question: "Which incremental liquidity actions dominate the 1,024-quote window?", value: `${eventMix.reduce((sum, item) => sum + item.buy + item.sell, 0)} events`,
      read: "Each row is an event family. Red extends left for bearish actions; green extends right for bullish actions. Counts use raw transitions, not the single table label.",
      bullish: "Bid improvements/additions and ask retreats/pulls dominate across multiple families.", bearish: "Bid fades/pulls and ask-down/additions dominate across multiple families.",
      caution: "Large counts can come from repeated cancellations. Confirm with midpoint movement and Tape execution.",
      chart: <DivergingCategories rows={eventMix} />,
    },
  ];
}

function tapeCharts(trades: MicroTrade[]): ChartChoice[] {
  if (!trades.length) return [];
  const recent = trades.slice(-256);
  const profile = tapeVolumeProfile(trades.slice(-1024));
  const cumulativeDelta = cumulativeTradeDelta(recent);
  const signedVolumes = recent.slice(-96).map((trade) => trade.direction === "buy" ? trade.size : trade.direction === "sell" ? -trade.size : 0);
  const prices = recent.slice(-96).map((trade) => trade.price);
  const mix = tradeMix(trades.slice(-1024));
  const sizeBuckets = tradeSizeBuckets(trades.slice(-1024));
  const activity = tradeActivityBins(trades.slice(-1024), 28);
  const response = deltaResponse(trades.slice(-512));
  const netDelta = cumulativeDelta.at(-1) ?? 0;
  const profileDelta = profile.reduce((sum, level) => sum + level.buy - level.sell, 0);
  return [
    {
      id: "profile", label: "Volume by price", question: "At which nearby prices did trading concentrate, and which aggressor side dominated?", value: `Δ ${signedCompact(profileDelta)}`,
      read: "Prices descend vertically. Red at-bid volume extends left; green at-ask volume extends right. Total shares appear at the far right.",
      bullish: "At-ask volume dominates as accepted prices migrate upward, or heavy at-bid volume fails to push price lower.", bearish: "At-bid volume dominates as accepted prices migrate downward, or heavy at-ask volume fails to lift price.",
      caution: "High volume marks acceptance or a battleground, not automatic support or resistance.", chart: <VolumeProfile levels={profile} />,
    },
    {
      id: "cvd", label: "Cumulative delta", question: "Is aggressive executed volume persistently buyer- or seller-led?", value: `Δ ${signedCompact(netDelta)}`,
      read: "Oldest is left. The line adds at-ask shares and subtracts at-bid shares across the latest 256 prints; zero is the starting point.",
      bullish: "A rising line with rising price confirms buyer aggression; a rising line while price stalls can signal seller absorption.", bearish: "A falling line with falling price confirms seller aggression; a falling line while price stalls can signal buyer absorption.",
      caution: "Between-market prints add zero, and late or special-condition prints can distort inferred aggressor flow.", chart: <SingleLineFromZero values={cumulativeDelta} format={compactNumber} />,
    },
    {
      id: "price-flow", label: "Price + signed flow", question: "Does each burst of aggressive volume actually move the trade price?", value: `${formatPrice(prices.at(-1) ?? 0)}`,
      read: "The black line is trade price. Green bars are at-ask size; red bars are at-bid size. Oldest is left, newest is right.",
      bullish: "Green bursts coincide with higher price highs, while red bursts fail to produce lower prices.", bearish: "Red bursts coincide with lower price lows, while green bursts fail to produce higher prices.",
      caution: "One large bar may be a negotiated or specially conditioned print. Check condition tags before interpreting it.", chart: <PriceFlowChart prices={prices} signedVolumes={signedVolumes} />,
    },
    {
      id: "mix", label: "Aggressor mix", question: "How is visible executed volume divided among at-ask, at-bid, and between-market prints?", value: `${Math.round(mix.buyShare * 100)}% buy`,
      read: "The full bar is all visible volume. Green is at ask, red is at bid, and gray is between market; labels show shares and percentage.",
      bullish: "At-ask share dominates and price responds upward rather than remaining absorbed.", bearish: "At-bid share dominates and price responds downward rather than remaining absorbed.",
      caution: "A dominant side with flat price is absorption evidence and can imply the opposite future direction.", chart: <AggressorMix mix={mix} />,
    },
    {
      id: "sizes", label: "Trade-size distribution", question: "Are small prints or larger participants driving each aggressor side?", value: `${trades.slice(-1024).length} prints`,
      read: "Rows are share-size ranges. Within each row red is at-bid count, green is at-ask count, and gray is between-market count.",
      bullish: "Larger-size buckets become increasingly green while price holds or rises.", bearish: "Larger-size buckets become increasingly red while price holds or falls.",
      caution: "Print count is not volume. Many odd lots can outnumber fewer large trades without carrying more shares.", chart: <GroupedCategories rows={sizeBuckets} />,
    },
    {
      id: "activity", label: "Trade activity", question: "When are executions clustering, and which aggressor owns each burst?", value: `${activity.reduce((sum, item) => sum + item.count, 0)} prints`,
      read: "Equal-time buckets run oldest to newest. Height is print count; color is the signed volume direction inside the bucket.",
      bullish: "Activity expands with positive delta and rising price.", bearish: "Activity expands with negative delta and falling price.",
      caution: "Fast prints without price response indicate churn or absorption, not necessarily continuation.", chart: <ActivityBars bins={activity} />,
    },
    {
      id: "response", label: "Delta vs price response", question: "Is aggressive flow moving price, or being absorbed?", value: `${response.length} windows`,
      read: "Each dot is a consecutive trade window. Right/left is buyer/seller volume delta; up/down is price return. Aligned quadrants confirm flow; opposing or flat response suggests absorption.",
      bullish: "Seller delta on the left fails to push price down, or buyer delta on the right produces positive returns.", bearish: "Buyer delta on the right fails to push price up, or seller delta on the left produces negative returns.",
      caution: "This diagnoses response inside the visible sample; it does not prove hidden orders or predict reversal timing.", chart: <DeltaResponseScatter points={response} />,
    },
  ];
}

function quoteFrame(quote: MicroQuote) {
  const total = Math.max(1, quote.bidSize + quote.askSize);
  const midpoint = (quote.bid + quote.ask) / 2;
  return { imbalance: (quote.bidSize - quote.askSize) / total, microprice: (quote.ask * quote.bidSize + quote.bid * quote.askSize) / total, midpoint, spread: Math.max(0, quote.ask - quote.bid) };
}

function quotePressureSeries(quotes: MicroQuote[]) {
  let strength = 0;
  return quotes.map((quote, index) => {
    const previous = quotes[index - 1];
    if (!previous) return 0;
    const current = quoteFrame(quote);
    const prior = quoteFrame(previous);
    const referenceSpread = Math.max(0.0001, current.spread, prior.spread);
    const price = clamp((current.midpoint - prior.midpoint) / (referenceSpread / 2), -1, 1);
    const size = clamp(current.imbalance - prior.imbalance, -1, 1);
    strength = clamp(0.72 * strength + 0.55 * (0.7 * price + 0.3 * size), -1, 1);
    return strength;
  }).slice(1);
}

function quoteEventMix(quotes: MicroQuote[]) {
  const rows = [
    { buy: 0, label: "Bid price", sell: 0 }, { buy: 0, label: "Ask price", sell: 0 },
    { buy: 0, label: "Bid size", sell: 0 }, { buy: 0, label: "Ask size", sell: 0 },
  ];
  quotes.slice(1).forEach((quote, index) => {
    const previous = quotes[index];
    if (quote.bid > previous.bid) rows[0].buy += 1; else if (quote.bid < previous.bid) rows[0].sell += 1;
    if (quote.ask > previous.ask) rows[1].buy += 1; else if (quote.ask < previous.ask) rows[1].sell += 1;
    if (quote.bidSize > previous.bidSize) rows[2].buy += 1; else if (quote.bidSize < previous.bidSize) rows[2].sell += 1;
    if (quote.askSize < previous.askSize) rows[3].buy += 1; else if (quote.askSize > previous.askSize) rows[3].sell += 1;
  });
  return rows;
}

function quoteActivityBins(quotes: MicroQuote[], count: number) {
  return timeBins(quotes, count, (rows) => ({ count: rows.length, direction: Math.sign((rows.at(-1)!.bid + rows.at(-1)!.ask) - (rows[0].bid + rows[0].ask)) }));
}

function cumulativeTradeDelta(trades: MicroTrade[]) {
  let total = 0;
  return trades.map((trade) => total += trade.direction === "buy" ? trade.size : trade.direction === "sell" ? -trade.size : 0);
}

function tapeVolumeProfile(trades: MicroTrade[]) {
  const latestPrice = trades.at(-1)?.price;
  if (!latestPrice) return [];
  const tick = latestPrice >= 1 ? 0.01 : 0.0001;
  const levels = new Map<number, { buy: number; mid: number; price: number; sell: number; total: number }>();
  trades.forEach((trade) => {
    const price = Math.round(trade.price / tick) * tick;
    const level = levels.get(price) ?? { buy: 0, mid: 0, price, sell: 0, total: 0 };
    level[trade.direction] += trade.size; level.total += trade.size; levels.set(price, level);
  });
  return [...levels.values()].sort((a, b) => Math.abs(a.price - latestPrice) - Math.abs(b.price - latestPrice) || b.total - a.total).slice(0, 7).sort((a, b) => b.price - a.price);
}

function tradeMix(trades: MicroTrade[]) {
  const volume = { buy: 0, mid: 0, sell: 0 };
  trades.forEach((trade) => { volume[trade.direction] += trade.size; });
  const total = Math.max(1, volume.buy + volume.mid + volume.sell);
  return { ...volume, buyShare: volume.buy / Math.max(1, volume.buy + volume.sell), total };
}

function tradeSizeBuckets(trades: MicroTrade[]) {
  const definitions = [{ label: "<10", min: 0, max: 10 }, { label: "10–49", min: 10, max: 50 }, { label: "50–99", min: 50, max: 100 }, { label: "100–499", min: 100, max: 500 }, { label: "500+", min: 500, max: Infinity }];
  return definitions.map((definition) => {
    const counts = { buy: 0, mid: 0, sell: 0 };
    trades.filter((trade) => trade.size >= definition.min && trade.size < definition.max).forEach((trade) => { counts[trade.direction] += 1; });
    return { ...counts, label: definition.label };
  });
}

function tradeActivityBins(trades: MicroTrade[], count: number) {
  return timeBins(trades, count, (rows) => {
    const delta = rows.reduce((sum, trade) => sum + (trade.direction === "buy" ? trade.size : trade.direction === "sell" ? -trade.size : 0), 0);
    return { count: rows.length, direction: Math.sign(delta) };
  });
}

function deltaResponse(trades: MicroTrade[]) {
  const size = Math.max(8, Math.floor(trades.length / 16));
  const points: Array<{ delta: number; response: number }> = [];
  for (let index = 0; index + 1 < trades.length; index += size) {
    const rows = trades.slice(index, index + size);
    if (rows.length < 2 || rows[0].price <= 0) continue;
    points.push({ delta: rows.reduce((sum, trade) => sum + (trade.direction === "buy" ? trade.size : trade.direction === "sell" ? -trade.size : 0), 0), response: (rows.at(-1)!.price / rows[0].price - 1) * 10_000 });
  }
  return points;
}

function MultiLineChart({ format, reference, series }: { format: (value: number) => string; reference?: number; series: Array<{ className: string; label: string; values: number[] }> }) {
  const values = series.flatMap((row) => row.values);
  const [minimum, maximum] = paddedDomain(values);
  return <ChartSvg maximum={format(maximum)} minimum={format(minimum)}>
    {reference != null ? <line className="chart-reference" x1={PAD_X} x2={WIDTH - 8} y1={y(reference, minimum, maximum)} y2={y(reference, minimum, maximum)} /> : null}
    {series.map((row) => <polyline className={row.className} fill="none" key={row.label} points={linePoints(row.values, minimum, maximum)} />)}
    <foreignObject height="18" width="260" x={PAD_X + 4} y="2"><div className="micro-chart-legend">{series.map((row) => <span className={row.className} key={row.label}>{row.label}</span>)}</div></foreignObject>
  </ChartSvg>;
}

function SignedBars({ values }: { values: number[] }) {
  const step = (WIDTH - PAD_X - 8) / Math.max(1, values.length);
  const center = HEIGHT / 2;
  return <ChartSvg maximum="+100%" minimum="−100%" middle="0"><line className="chart-zero" x1={PAD_X} x2={WIDTH - 8} y1={center} y2={center} />{values.map((value, index) => {
    const magnitude = Math.abs(value) * (center - PAD_Y);
    return <rect className={value >= 0 ? "chart-buy-fill" : "chart-sell-fill"} height={Math.max(1, magnitude)} key={index} width={Math.max(1, step - 1)} x={PAD_X + index * step} y={value >= 0 ? center - magnitude : center} />;
  })}</ChartSvg>;
}

function SingleLineFromZero({ format, values }: { format: (value: number) => string; values: number[] }) {
  const [minimum, maximum] = paddedDomain([0, ...values]);
  return <ChartSvg maximum={format(maximum)} minimum={format(minimum)} middle={minimum < 0 && maximum > 0 ? "0" : undefined}><line className="chart-zero" x1={PAD_X} x2={WIDTH - 8} y1={y(0, minimum, maximum)} y2={y(0, minimum, maximum)} /><polyline className="chart-delta" fill="none" points={linePoints(values, minimum, maximum)} /></ChartSvg>;
}

function PriceFlowChart({ prices, signedVolumes }: { prices: number[]; signedVolumes: number[] }) {
  const [minimum, maximum] = paddedDomain(prices);
  const maxVolume = Math.max(1, ...signedVolumes.map(Math.abs));
  const step = (WIDTH - PAD_X - 8) / Math.max(1, prices.length);
  const base = HEIGHT - PAD_Y;
  return <ChartSvg maximum={formatPrice(maximum)} minimum={formatPrice(minimum)}>{signedVolumes.map((value, index) => <rect className={value >= 0 ? "chart-buy-fill" : "chart-sell-fill"} height={Math.max(1, Math.abs(value) / maxVolume * 28)} key={index} opacity=".45" width={Math.max(1, step - 1)} x={PAD_X + index * step} y={base - Math.abs(value) / maxVolume * 28} />)}<polyline className="chart-price" fill="none" points={linePoints(prices, minimum, maximum)} /></ChartSvg>;
}

function ActivityBars({ bins }: { bins: Array<{ count: number; direction: number }> }) {
  const maximum = Math.max(1, ...bins.map((bin) => bin.count));
  const step = (WIDTH - PAD_X - 8) / Math.max(1, bins.length);
  return <ChartSvg maximum={`${maximum}`} minimum="0">{bins.map((bin, index) => {
    const height = bin.count / maximum * (HEIGHT - PAD_Y * 2);
    return <rect className={bin.direction > 0 ? "chart-buy-fill" : bin.direction < 0 ? "chart-sell-fill" : "chart-mid-fill"} height={Math.max(1, height)} key={index} width={Math.max(2, step - 2)} x={PAD_X + index * step} y={HEIGHT - PAD_Y - height} />;
  })}</ChartSvg>;
}

function VolumeProfile({ levels }: { levels: Array<{ buy: number; mid: number; price: number; sell: number; total: number }> }) {
  const maximum = Math.max(1, ...levels.flatMap((level) => [level.buy, level.sell]));
  return <div className="chart-category-list volume-profile-chart">{levels.map((level) => <div className="chart-diverging-row" key={level.price}><span>{formatPrice(level.price)}</span><i className="sell"><b style={{ width: `${level.sell / maximum * 100}%` }} /></i><i className="buy"><b style={{ width: `${level.buy / maximum * 100}%` }} /></i><strong>{compactNumber(level.total)}</strong></div>)}</div>;
}

function DivergingCategories({ rows }: { rows: Array<{ buy: number; label: string; sell: number }> }) {
  const maximum = Math.max(1, ...rows.flatMap((row) => [row.buy, row.sell]));
  return <div className="chart-category-list">{rows.map((row) => <div className="chart-diverging-row" key={row.label}><span>{row.label}</span><i className="sell"><b style={{ width: `${row.sell / maximum * 100}%` }} /></i><i className="buy"><b style={{ width: `${row.buy / maximum * 100}%` }} /></i><strong>{row.sell} | {row.buy}</strong></div>)}</div>;
}

function GroupedCategories({ rows }: { rows: Array<{ buy: number; label: string; mid: number; sell: number }> }) {
  const maximum = Math.max(1, ...rows.map((row) => row.buy + row.mid + row.sell));
  return <div className="chart-category-list">{rows.map((row) => <div className="chart-grouped-row" key={row.label}><span>{row.label}</span><i><b className="sell" style={{ width: `${row.sell / maximum * 100}%` }} /><b className="mid" style={{ width: `${row.mid / maximum * 100}%` }} /><b className="buy" style={{ width: `${row.buy / maximum * 100}%` }} /></i><strong>{row.sell + row.mid + row.buy}</strong></div>)}</div>;
}

function AggressorMix({ mix }: { mix: { buy: number; mid: number; sell: number; total: number } }) {
  return <div className="aggressor-mix-chart"><div><i className="sell" style={{ width: `${mix.sell / mix.total * 100}%` }} /><i className="mid" style={{ width: `${mix.mid / mix.total * 100}%` }} /><i className="buy" style={{ width: `${mix.buy / mix.total * 100}%` }} /></div><section><span className="sell">At bid <b>{compactNumber(mix.sell)}</b></span><span className="mid">Between <b>{compactNumber(mix.mid)}</b></span><span className="buy">At ask <b>{compactNumber(mix.buy)}</b></span></section></div>;
}

function DeltaResponseScatter({ points }: { points: Array<{ delta: number; response: number }> }) {
  const maxDelta = Math.max(1, ...points.map((point) => Math.abs(point.delta)));
  const maxResponse = Math.max(0.01, ...points.map((point) => Math.abs(point.response)));
  return <ChartSvg maximum={`+${maxResponse.toFixed(1)} bp`} minimum={`−${maxResponse.toFixed(1)} bp`} middle="0" showTimeAxis={false}><line className="chart-zero" x1={PAD_X} x2={WIDTH - 8} y1={HEIGHT / 2} y2={HEIGHT / 2} /><line className="chart-zero" x1={WIDTH / 2} x2={WIDTH / 2} y1={PAD_Y} y2={HEIGHT - PAD_Y} />{points.map((point, index) => <circle className={point.delta >= 0 ? "chart-buy-dot" : "chart-sell-dot"} cx={WIDTH / 2 + point.delta / maxDelta * (WIDTH / 2 - PAD_X)} cy={HEIGHT / 2 - point.response / maxResponse * (HEIGHT / 2 - PAD_Y)} key={index} r="3.5" />)}<text className="chart-axis-note" x={PAD_X} y={HEIGHT - 2}>Seller delta ←</text><text className="chart-axis-note" textAnchor="end" x={WIDTH - 8} y={HEIGHT - 2}>→ Buyer delta</text></ChartSvg>;
}

function ChartSvg({ children, maximum, middle, minimum, showTimeAxis = true }: { children: ReactNode; maximum: string; middle?: string; minimum: string; showTimeAxis?: boolean }) {
  return <div className="micro-svg-chart"><svg preserveAspectRatio="none" role="img" viewBox={`0 0 ${WIDTH} ${HEIGHT}`}><line className="chart-grid" x1={PAD_X} x2={WIDTH - 8} y1={PAD_Y} y2={PAD_Y} /><line className="chart-grid" x1={PAD_X} x2={WIDTH - 8} y1={HEIGHT - PAD_Y} y2={HEIGHT - PAD_Y} /><text className="chart-axis-label" x="2" y={PAD_Y + 3}>{maximum}</text>{middle ? <text className="chart-axis-label" x="2" y={HEIGHT / 2 + 3}>{middle}</text> : null}<text className="chart-axis-label" x="2" y={HEIGHT - PAD_Y + 3}>{minimum}</text>{children}</svg>{showTimeAxis ? <div className="chart-time-axis"><span>Older</span><span>Time →</span><span>Newest</span></div> : null}</div>;
}

function timeBins<T extends { timestampUs: number }>(rows: T[], count: number, summarize: (rows: T[]) => { count: number; direction: number }) {
  if (!rows.length) return [];
  const start = rows[0].timestampUs;
  const duration = Math.max(1, rows.at(-1)!.timestampUs - start);
  const bins = Array.from({ length: count }, () => [] as T[]);
  rows.forEach((row) => bins[Math.min(count - 1, Math.floor((row.timestampUs - start) / duration * count))].push(row));
  return bins.map((bin) => bin.length ? summarize(bin) : { count: 0, direction: 0 });
}

function paddedDomain(values: number[]): [number, number] {
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const padding = Math.max((maximum - minimum) * 0.08, Math.abs(maximum || 1) * 0.00001);
  return [minimum - padding, maximum + padding];
}
function linePoints(values: number[], minimum: number, maximum: number) { const step = (WIDTH - PAD_X - 8) / Math.max(1, values.length - 1); return values.map((value, index) => `${PAD_X + index * step},${y(value, minimum, maximum)}`).join(" "); }
function y(value: number, minimum: number, maximum: number) { return PAD_Y + (maximum - value) / Math.max(0.0000001, maximum - minimum) * (HEIGHT - PAD_Y * 2); }
function percentile(values: number[], quantile: number) { const sorted = [...values].sort((a, b) => a - b); return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * quantile))] ?? 0; }
function clamp(value: number, minimum: number, maximum: number) { return Math.max(minimum, Math.min(maximum, value)); }
function formatPrice(value: number) { return value >= 100 ? value.toFixed(2) : value.toFixed(4).replace(/0+$/, "").replace(/\.$/, ""); }
function compactNumber(value: number) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, notation: "compact" }).format(value); }
function signed(value: number, digits = 1) { return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`; }
function signedPercent(value: number) { return `${value > 0 ? "+" : ""}${Math.round(value * 100)}%`; }
function signedCompact(value: number) { return `${value > 0 ? "+" : ""}${compactNumber(value)}`; }
