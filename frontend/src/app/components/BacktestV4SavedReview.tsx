import { lazy, Suspense, useEffect, useState } from "react";
import { api } from "../../api/client";

const BacktestV4SavedChart = lazy(() => import("./BacktestV4SavedChart").then(module => ({ default: module.BacktestV4SavedChart })));

type Account = {
  currency: string;
  net_liquidation: number;
  total_cash_value: number;
  gross_position_value: number;
  buying_power: number;
  expected_position_count: number;
  source_timestamp_ms: number;
};

export type V4Page = {
  schema_version: "strategy-one-v4-terminal-review-page-v1";
  run: { run_id: string; initial_cash?: number; session_date?: string; strategy_id?: string; strategy_revision?: number };
  status: string;
  verified_sequence: number;
  market_cursor: { session_date: string; boundary_ms: number } | null;
  market_cursor_verified: boolean;
  limitations: string[];
  financial_accounts: Record<string, Account>;
  events: Array<{
    event: { sequence: number; event_time: string; category: string;
      entity_type: string; entity_id: string; account_id: string;
      record_id?: string; recorded_at?: string };
    detail_family: string | null;
    detail: Record<string, unknown> | null;
  }>;
  next_sequence: number;
  complete: boolean;
};

function et(value: string | number): string {
  // ClickHouse DateTime64 journal values are UTC even when JSONEachRow omits
  // the offset. Date.parse would otherwise reinterpret them in browser time.
  const normalized = typeof value === "string" && /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?$/.test(value)
    ? `${value.replace(" ", "T").replace(/(\.\d{3})\d+$/, "$1")}Z`
    : value;
  const parsed = new Date(normalized);
  return Number.isFinite(parsed.getTime())
    ? new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "medium", timeZone: "America/New_York" }).format(parsed)
    : "—";
}

function boundaryClock(boundaryMs: number): string {
  const total = 4 * 3_600_000 + boundaryMs;
  const hour = Math.floor(total / 3_600_000);
  const minute = Math.floor(total / 60_000) % 60;
  const second = Math.floor(total / 1_000) % 60;
  const millis = total % 1_000;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:${String(second).padStart(2, "0")}.${String(millis).padStart(3, "0")} ET`;
}

function amount(value: number, currency: string): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: currency || "USD", maximumFractionDigits: 2 }).format(value);
}

function scalar(value: unknown): string {
  return value == null ? "—" : typeof value === "object" ? "Structured child evidence" : String(value);
}

export function BacktestV4SavedReview({ runId, onClose, initialPage }: {
  runId: string;
  onClose: () => void;
  initialPage?: V4Page;
}) {
  const [cursors, setCursors] = useState([0]);
  const [index, setIndex] = useState(0);
  const [page, setPage] = useState<V4Page | null>(initialPage ?? null);
  const [loading, setLoading] = useState(!initialPage);
  const [error, setError] = useState("");
  const [chartTicker, setChartTicker] = useState("");
  const afterSequence = cursors[index];

  useEffect(() => {
    // A deep link has already cold-verified its first page. Reuse it rather
    // than repeating a full journal audit during the same navigation.
    if (initialPage && afterSequence === 0) {
      setPage(initialPage);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void api<V4Page>(`/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-terminal-page?after_sequence=${afterSequence}&limit=100`, {
      signal: controller.signal, timeoutMs: 60_000,
    }).then(value => {
      if (!controller.signal.aborted) setPage(value);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [runId, afterSequence, initialPage]);

  const accounts = Object.entries(page?.financial_accounts ?? {});
  return <section className="backtest-v4-review" aria-labelledby="backtest-v4-review-heading" aria-busy={loading}>
    <header><div><h3 id="backtest-v4-review-heading">Verified Strategy {page?.run.strategy_revision ?? "unknown"} journal · {runId.slice(0, 8)}</h3>
      <p>Read-only ClickHouse evidence. This view does not resume execution or reconstruct the legacy Canvas.</p></div>
      <button className="button secondary compact" type="button" onClick={onClose}>Close review</button></header>
    {loading ? <p role="status">{page ? "Loading journal page…" : "Verifying the complete journal and terminal account snapshots…"}</p> : null}
    {error ? <p role="alert">Could not verify this journal page: {error}</p> : null}
    {page ? <>
      <p><strong>{page.status}</strong> · {page.verified_sequence.toLocaleString()} verified journal records
        {page.market_cursor_verified && page.market_cursor ? ` · Last processed boundary ${page.market_cursor.session_date} ${boundaryClock(page.market_cursor.boundary_ms)}` : " · Last processed boundary unavailable"}</p>
      {page.limitations.map((message, i) => <p key={i} role="note">{message}</p>)}
      {chartTicker ? <Suspense fallback={<p role="status">Loading chart…</p>}><BacktestV4SavedChart runId={runId} ticker={chartTicker} onClose={() => setChartTicker("")} /></Suspense> : null}
      <div className="backtest-history-scroll" role="region" aria-label="Terminal account balances" tabIndex={0}><table>
        <thead><tr><th scope="col">Account</th><th scope="col">Net liquidation</th><th scope="col">Cash</th><th scope="col">Gross positions</th><th scope="col">Buying power</th><th scope="col">Open positions</th><th scope="col">Snapshot · ET</th></tr></thead>
        <tbody>{accounts.map(([id, account]) => <tr key={id}><td>{id}</td><td>{amount(account.net_liquidation, account.currency)}</td><td>{amount(account.total_cash_value, account.currency)}</td><td>{amount(account.gross_position_value, account.currency)}</td><td>{amount(account.buying_power, account.currency)}</td><td>{account.expected_position_count}</td><td>{et(account.source_timestamp_ms)}</td></tr>)}</tbody>
      </table></div>
      <h4>Normalized event journal</h4>
      <div className="backtest-history-scroll" role="region" aria-label="Verified journal events" tabIndex={0}><table>
        <thead><tr><th scope="col">Sequence</th><th scope="col">Time · ET</th><th scope="col">Category</th><th scope="col">Entity</th><th scope="col">Account</th><th scope="col">Evidence</th></tr></thead>
        <tbody>{page.events.map(({ event, detail, detail_family }) => <tr key={event.sequence}><td>{event.sequence.toLocaleString()}</td><td>{et(event.event_time)}</td><td>{event.category}</td><td>{event.entity_type}<small>{event.entity_id}</small></td><td>{event.account_id || "—"}</td><td>{detail && typeof detail.ticker === "string" && /^[A-Z0-9.-]{1,24}$/.test(detail.ticker) ? <button className="button secondary compact" type="button" onClick={() => setChartTicker(detail.ticker as string)}>Chart {detail.ticker}</button> : null}{detail ? <details><summary>{detail_family || "Typed detail"}</summary><dl>{Object.entries(detail).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{scalar(value)}</dd></div>)}</dl></details> : "—"}</td></tr>)}</tbody>
      </table></div>
      <footer><span>Records {afterSequence + 1}–{page.next_sequence.toLocaleString()} of {page.verified_sequence.toLocaleString()}</span><div className="backtest-history-actions">
        <button className="button secondary compact" type="button" disabled={loading || index === 0} onClick={() => setIndex(value => value - 1)}>Previous page</button>
        <button className="button secondary compact" type="button" disabled={loading || page.complete} onClick={() => {
          const next = page.next_sequence;
          setCursors(current => current[index + 1] === next ? current : [...current.slice(0, index + 1), next]);
          setIndex(value => value + 1);
        }}>Next page</button>
      </div></footer>
    </> : null}
  </section>;
}
