import { useEffect, useRef, useState, type ComponentProps } from "react";
import { api } from "../../api/client";
import { StrategyActivityContainer } from "./MarketScreenerContainers";
import { LoadingState } from "./LoadingState";
import { usePanelVisibility } from "./VisibleBacktestPanel";
import type { ScreenerRow } from "./MarketScreenerContainers";

type Props = ComponentProps<typeof StrategyActivityContainer> & { throughSequence?: number };
type Page = { rows: ScreenerRow[]; complete: boolean; next_offset?: number | null; as_of: string; presentation_sequence?: number };

export function LazyBacktestActivity(props: Props) {
  const visible = usePanelVisibility();
  const { asOf, runId, settings } = props;
  const key = JSON.stringify([runId, asOf, props.throughSequence, settings.strategyId, settings.ticker, settings.eventType]);
  const [cursor, setCursor] = useState({ key, offset: 0 });
  const offset = cursor.key === key ? cursor.offset : 0;
  const [page, setPage] = useState<Page | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const cache = useRef(new Map<string, Page>());
  const fence = useRef<{ scope: string; sequence?: number }>({ scope: "" });
  useEffect(() => {
    if (!visible || !runId) return;
    const scope = `${runId}:${asOf}:${props.throughSequence}`;
    if (fence.current.scope !== scope) { fence.current = { scope, sequence: props.throughSequence }; cache.current.clear(); }
    const cacheKey = `${key}:${offset}`;
    const cached = cache.current.get(cacheKey);
    if (cached) { setPage(cached); setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true);
    const timer = window.setTimeout(() => {
      const query = new URLSearchParams({ run_id: runId, as_of: asOf, limit: "200", offset: String(offset),
        include_decision_evidence: "false", strategy_id: settings.strategyId, ticker: settings.ticker, event_type: settings.eventType });
      if (fence.current.sequence !== undefined) query.set("through_sequence", String(fence.current.sequence));
      api<Page>(`/api/trading/strategy-activity?${query}`, { signal: controller.signal, timeoutMs: 30000 })
        .then(value => { if (!controller.signal.aborted) {
          fence.current.sequence = value.presentation_sequence;
          cache.current.set(cacheKey, value);
          if (cache.current.size > 6) cache.current.delete(cache.current.keys().next().value!);
          setPage(value); setError("");
        } })
        .catch(reason => { if (!controller.signal.aborted) setError(String(reason.message ?? reason)); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 150);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [key, offset, visible, retry]);
  return <div className="backtest-lazy-activity" aria-busy={loading}>
    <div className="backtest-view-controls" aria-label="Activity history pages">
      <span>{loading ? "Loading page…" : `${page?.rows.length ?? 0} events · offset ${offset.toLocaleString()}`}</span>
      <button className="button secondary compact" disabled={loading || offset === 0} onClick={() => setCursor({ key, offset: Math.max(0, offset - 200) })}>Newer events</button>
      <button className="button secondary compact" disabled={loading || !page || page.complete} onClick={() => setCursor({ key, offset: page?.next_offset ?? offset + 200 })}>Older events</button>
    </div>
    {error ? <div role="alert" className="canvas-inline-error">{error} <button onClick={() => setRetry(n => n + 1)}>Retry</button></div> : null}
    {!page ? <LoadingState label="Loading activity" /> : <StrategyActivityContainer {...props} asOf={page.as_of || asOf} loadAllHistory={false} historicalRows={page.rows} historicalPage={{ complete: true, next_offset: null }} />}
  </div>;
}
