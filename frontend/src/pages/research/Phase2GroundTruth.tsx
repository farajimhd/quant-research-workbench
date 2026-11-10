import { useEffect, useMemo, useRef, useState } from "react";
import { ChartPanel, type ChartPanelHandle, type ChartPayload } from "../../app/components/ChartPanel";
import { HISTORICAL_TIMEFRAMES } from "../../features/canvas/configuration";
import { query } from "../../api/client";
import { researchApi } from "./researchApi";

type Candle = { start_us: number; end_us: number; open: number; high: number; low: number; close: number; volume: number };
type Page = { bars: Candle[]; has_more: boolean; next_before_us: number | null; source: string };

/** Fresh canonical events, fixed session split basis, bounded backwards pagination. */
export function Phase2GroundTruth({ stream, unit, ticker, timeframe, origin, selectedEnd, selectedIndex, selectionFrame, onTimeframeChange }: {
  stream: string; unit: number; ticker: string; timeframe: string; origin: number;
  selectedEnd?: number; selectedIndex: number; selectionFrame: string;
  onTimeframeChange: (frame: string) => void;
}) {
  const [data, setData] = useState<Page | null>(null);
  const [loading, setLoading] = useState(false);
  const [earlier, setEarlier] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [fullSession, setFullSession] = useState(false);
  const request = useRef<AbortController | null>(null);
  const pending = useRef(false);
  const navigated = useRef(false);
  const generation = useRef(0);
  const chart = useRef<ChartPanelHandle>(null);
  const loadPage = useRef<(before?: number) => void>(() => {});

  useEffect(() => {
    const epoch = ++generation.current;
    setData(null); setError(""); navigated.current = false;
    async function load(before?: number, append = false) {
      if (append && pending.current) return;
      request.current?.abort();
      const abort = new AbortController(); request.current = abort; pending.current = true;
      setLoading(!append); setEarlier(append); setError("");
      try {
        const result = await researchApi<Page>("/api/research/models/bargpt/phase2/events" + query({ stream, unit, timeframe, before_us: before, limit: fullSession ? 50000 : 4000 }), { signal: abort.signal, timeoutMs: 45000 });
        if (abort.signal.aborted || epoch !== generation.current) return;
        setData(previous => {
          const merged = new Map<number, Candle>();
          result.bars.forEach(bar => merged.set(bar.start_us, bar));
          if (append) previous?.bars.forEach(bar => merged.set(bar.start_us, bar));
          return { ...result, bars: [...merged.values()].sort((a, b) => a.start_us - b.start_us) };
        });
      } catch (e) { if (!abort.signal.aborted && epoch === generation.current) setError((e as Error).message); }
      finally { if (epoch === generation.current) { pending.current = false; setLoading(false); setEarlier(false); } }
    }
    loadPage.current = before => { void load(before, true); };
    void load(fullSession ? undefined : origin);
    return () => { request.current?.abort(); pending.current = false; };
  }, [stream, unit, timeframe, origin, fullSession, revision]);

  const payload = useMemo<ChartPayload | null>(() => data ? ({
    timeframe, candles: data.bars.map(bar => ({ time: bar.start_us / 1e6, endTime: bar.end_us / 1e6, open: bar.open, high: bar.high, low: bar.low, close: bar.close })),
    volume: [], overlay_series: [], oscillator_series: [], markers: [], regions: [],
  }) : null, [data, timeframe]);
  function older() {
    if (data?.has_more && data.next_before_us && !pending.current) { navigated.current = false; loadPage.current(data.next_before_us); }
  }
  const selectedCandle = timeframe === selectionFrame ? data?.bars.find(bar => bar.end_us === selectedEnd) : undefined;
  // Fetch complete older pages until the selected saved close is covered. Never
  // mark a nearby candle as a match when the exact clock is still unavailable.
  useEffect(() => {
    if (timeframe !== selectionFrame || !selectedEnd || !data || loading || earlier || error || selectedCandle) return;
    if ((!data.bars.length || selectedEnd <= data.bars[0].start_us) && data.has_more) older();
  }, [selectedEnd, data, loading, earlier, error, selectedCandle, timeframe, selectionFrame]);
  useEffect(() => {
    if (!selectedCandle) return;
    const timer = window.setTimeout(() => chart.current?.fitRecent(), 0);
    return () => window.clearTimeout(timer);
  }, [selectedEnd, timeframe, selectedCandle?.start_us]);
  return <section className="phase2-ground-truth" aria-label="Canonical event ground truth"
    data-selected-close={selectedCandle?.end_us ?? ""}
    onWheelCapture={() => { navigated.current = true; }}
    onPointerMoveCapture={event => { if (event.buttons) navigated.current = true; }}>
    <div className="phase2-chart-heading"><h2>Ground truth · trades</h2><span className="research-muted">NY time · axis: candle open · saved bars: candle close</span></div>
    <ChartPanel ref={chart} ticker={ticker} timeframe={timeframe} timeframes={HISTORICAL_TIMEFRAMES} payload={payload}
      featureOptions={[]} indicatorOptions={[]} visibleColumns={[]} onTickerChange={() => {}}
      onTimeframeChange={onTimeframeChange} onVisibleColumnsChange={() => {}} tickerEditable={false}
      toolbarVariant="compact" showIndicatorControls={false} showSupervisionControls={false} persistedOnly
      settingsStorageKey="research.phase2.events.chart.v1" baseHeight={380}
      appearanceDefaults={{ legendGutterVisible: false, rightLegendGutterVisible: false }}
      loading={loading} loadingEarlier={earlier} errorMessage={error}
      emptyMessage="No eligible trades in this interval. Load earlier history."
      canLoadEarlier={Boolean(data?.has_more)} onLoadEarlier={() => { if (navigated.current) older(); }}
      reference={selectedCandle ? { time: selectedCandle.start_us / 1e6, label: `Bar ${selectedIndex + 1} · close ${new Date(selectedCandle.end_us / 1000).toLocaleTimeString("en-GB", { timeZone: "America/New_York", hour12: false })} NY` } : null}
      toolbarActions={<><label className="phase2-session-toggle"><input type="checkbox" checked={fullSession} onChange={e => setFullSession(e.target.checked)} />Full session</label><button className="button secondary compact" disabled={loading || earlier || !data?.has_more} onClick={older}>{earlier ? "Loading…" : "Earlier history"}</button>{error && <button className="button secondary compact" onClick={() => setRevision(v => v + 1)}>Retry events</button>}</>}
    />
  </section>;
}
