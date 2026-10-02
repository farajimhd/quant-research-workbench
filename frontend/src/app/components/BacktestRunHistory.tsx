import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { PerformanceJournalReport } from "../../features/canvas/contracts";
import type { CanvasReplayRun } from "../replayRun";

type RunRow = Pick<CanvasReplayRun, "run_id" | "created_at" | "status" | "session_date" | "checkpoint" | "tickers"> & {
  comparison_group_key?: string;
  code_fingerprint?: string;
  start_local_ms?: number;
  end_local_ms?: number;
  strategy_id?: string;
  configuration_content_hash?: string;
  configuration_revision_id?: string;
  current_time?: string | null;
  processed_events?: number | null;
  configuration_revision?: number;
  configuration_label?: string;
  strategy_revision?: number;
  initial_cash?: number;
  resident?: boolean;
  journal_backend?: string;
  journal_sequence?: number;
  review_available?: boolean;
  v4_review_available?: boolean;
  resume_attempt_available?: boolean;
};

const PAGE_SIZE = 10;
const createdTime = (value: string) => Date.parse(value) || 0;
const dateTime = (value: string) => Number.isFinite(Date.parse(value))
  ? new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "medium", timeZone: "America/New_York" }).format(new Date(value)) : "—";

type Performance = { run_id?: string; status?: "queued" | "verifying" | "deferred" | "available" | "unavailable"; verified_sequence?: number; report?: Pick<PerformanceJournalReport, "summary">; error?: string };
const numeric = (value: unknown): number | null => value == null || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);
const money = (value: number | null) => value == null ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
const percent = (value: number | null) => value == null ? "—" : new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 1 }).format(value);
const identity = (row: RunRow) => row.strategy_revision ? `Strategy ${row.strategy_revision}` : row.configuration_revision ? `Candidate ${row.configuration_revision}` : "Strategy unavailable";
// Unknown identities remain separate; a revision alone does not establish identical configuration.
const groupKey = (row: RunRow) => row.comparison_group_key || row.run_id;
const terminal = (row: RunRow) => ["completed", "stopped", "failed"].includes(row.status);
const pnlClass = (value: number | null) => value == null || value === 0 ? "" : value > 0 ? "backtest-positive" : "backtest-negative";

export function BacktestRunHistory({ onReview, onResumed }: {
  onReview: (runId: string) => void;
  onResumed: (run: CanvasReplayRun) => void;
}) {
  const [rows, setRows] = useState<RunRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState<{ runId: string; message: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [performance, setPerformance] = useState<Record<string, Performance>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    // Defer dispatch past React's development mount/cleanup probe. History
    // remains independent of the setup form's readiness and warmup requests.
    const timer = window.setTimeout(() => {
      void api<{ rows: RunRow[] }>("/api/trading/backtest/runs?strategy_one_only=true", { signal: controller.signal, timeoutMs: 60_000 })
      .then(result => {
        if (controller.signal.aborted) return;
        setRows([...result.rows].sort((a, b) => createdTime(b.created_at) - createdTime(a.created_at) || b.run_id.localeCompare(a.run_id)));
        setPage(0);
        setExpanded(new Set());
        setPerformance({});
      })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [refresh]);

  useEffect(() => {
    const controller = new AbortController();
    const pending = rows.filter(row => row.journal_backend === "arte_typed_journal_v4" && terminal(row) && (row.journal_sequence ?? 0) > 0);
    let timer = 0;
    async function poll(first = false) {
      if (!pending.length || controller.signal.aborted) return;
      try {
        const results: Performance[] = [];
        for (let offset = 0; offset < pending.length; offset += 32) {
          const batch = pending.slice(offset, offset + 32);
          const query = new URLSearchParams({ run_ids: batch.map(row => row.run_id).join(","),
            sequences: batch.map(row => String(row.journal_sequence)).join(","), ...(first ? { retry_failed: "true" } : {}) });
          const result = await api<{ rows: Performance[] }>(`/api/trading/backtest/history-performance?${query}`, { signal: controller.signal, timeoutMs: 30_000 });
          results.push(...result.rows);
        }
        if (controller.signal.aborted) return;
        const next: Record<string, Performance> = {};
        for (const row of pending) {
          const value = results.find(item => item.run_id === row.run_id);
          next[row.run_id] = !value ? { status: "unavailable", error: "Missing performance status; refresh runs." }
            : value.report && value.verified_sequence !== row.journal_sequence
              ? { status: "unavailable", error: "Performance head changed; refresh runs." } : value;
        }
        setPerformance(next);
        if (Object.values(next).some(value => ["queued", "verifying", "deferred"].includes(value.status || ""))) {
          timer = window.setTimeout(() => void poll(), 3000);
        }
      } catch (reason) {
        if (!controller.signal.aborted) {
          setPerformance(Object.fromEntries(pending.map(row => [row.run_id, { status: "unavailable", error: reason instanceof Error ? reason.message : String(reason) }])));
        }
      }
    }
    timer = window.setTimeout(() => void poll(true), 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [rows]);

  async function resume(row: RunRow) {
    setBusy(row.run_id);
    setActionError(null);
    try {
      const paused = row.status === "paused" && row.resident;
      const run = await api<CanvasReplayRun>(`/api/trading/backtest/runs/${encodeURIComponent(row.run_id)}/${paused ? "commands" : "resume"}`, {
        method: "POST", ...(paused ? { body: JSON.stringify({ command: "play" }) } : {}), timeoutMs: 180_000,
      });
      onResumed(run);
    } catch (reason) {
      setActionError({ runId: row.run_id, message: reason instanceof Error ? reason.message : String(reason) });
    } finally { setBusy(""); }
  }

  const groups = new Map<string, RunRow[]>();
  rows.forEach(row => { const key = groupKey(row); groups.set(key, [...(groups.get(key) || []), row]); });
  const filteredGroups = [...groups].filter(([, runs]) => String(runs[0].strategy_revision ?? runs[0].configuration_revision ?? "").includes(search.trim()));
  const pages = Math.max(1, Math.ceil(filteredGroups.length / PAGE_SIZE));
  const performanceErrors = new Map<string, number>();
  Object.values(performance).forEach(result => {
    if (result.error) performanceErrors.set(result.error, (performanceErrors.get(result.error) || 0) + 1);
  });
  const unavailableCount = [...performanceErrors.values()].reduce((total, count) => total + count, 0);
  const pendingCount = rows.filter(row => row.journal_backend === "arte_typed_journal_v4" && terminal(row) && !performance[row.run_id]?.report && !performance[row.run_id]?.error).length;
  function toggle(key: string) {
    setExpanded(current => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  }
  return <section className="backtest-run-history" aria-labelledby="backtest-history-heading" aria-busy={loading}>
    <header><div><h2 id="backtest-history-heading">Recent backtests</h2><p>{groups.size} strategy groups · {rows.length} runs</p></div>
      <button className="button secondary compact" type="button" disabled={loading || Boolean(busy)} onClick={() => setRefresh(value => value + 1)}>{loading ? "Loading…" : "Refresh runs"}</button></header>
    {error ? <p role="alert">Could not refresh backtests: {error}{rows.length ? " Showing the previously loaded list." : ""}</p> : null}
    {!rows.length ? <p role="status">{loading ? "Loading recent backtests…" : error ? "Use Refresh runs to try again." : "No saved backtests yet."}</p> : <>
      <div className="backtest-history-toolbar">
        <label className="backtest-strategy-search"><span>Strategy number</span><input type="search" inputMode="numeric" aria-label="Search strategy numbers" placeholder="e.g. 34" value={search} onChange={event => { setSearch(event.target.value); setPage(0); }} /></label>
        <nav className="backtest-history-pagination" aria-label="Backtest strategy pages">
          <button className="button secondary compact" type="button" aria-label="First page" disabled={page === 0 || Boolean(busy)} onClick={() => setPage(0)}><ChevronsLeft aria-hidden="true" />First</button>
          <button className="button secondary compact" type="button" aria-label="Previous page" disabled={page === 0 || Boolean(busy)} onClick={() => setPage(value => value - 1)}><ChevronLeft aria-hidden="true" />Previous</button>
          <span role="status">{filteredGroups.length ? `Page ${page + 1} of ${pages}` : "No matching strategies"}</span>
          <button className="button secondary compact" type="button" aria-label="Next page" disabled={page + 1 >= pages || Boolean(busy)} onClick={() => setPage(value => value + 1)}>Next<ChevronRight aria-hidden="true" /></button>
          <button className="button secondary compact" type="button" aria-label="Last page" disabled={page + 1 >= pages || Boolean(busy)} onClick={() => setPage(pages - 1)}>Last<ChevronsRight aria-hidden="true" /></button>
        </nav>
      </div>
      <div className="backtest-history-scroll" role="region" aria-label="Recent backtests table" tabIndex={0}><table className="market-list-table backtest-history-table">
        <thead><tr><th scope="col">Strategy / run</th><th scope="col">Date / time · ET</th><th scope="col" className="numeric">Initial cash · USD</th><th scope="col" className="numeric" title="Closed-trade P&L after final commissions in USD. Group totals use the newest completed run per session.">Net P&amp;L · USD</th><th scope="col" className="numeric">Closed trades</th><th scope="col" className="numeric">Win rate</th><th scope="col" className="numeric">Fees · USD</th><th scope="col">Status / coverage</th><th scope="col">Controls</th></tr></thead>
        {filteredGroups.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map(([key, runs]) => {
          const sessions = new Map<string, RunRow>();
          runs.filter(row => row.status === "completed" && row.session_date).forEach(row => { if (!sessions.has(row.session_date)) sessions.set(row.session_date, row); });
          const reports = [...sessions.values()].map(row => performance[row.run_id]?.report).filter((report): report is Pick<PerformanceJournalReport, "summary"> => Boolean(report));
          const sum = (field: string) => reports.length && reports.every(report => numeric(report.summary[field]) != null) ? reports.reduce((total, report) => total + Number(report.summary[field]), 0) : null;
          const pnl = sum("net_pnl"), trades = sum("episode_count"), wins = sum("win_count"), first = runs[0];
          const sessionDates = [...new Set(runs.map(row => row.session_date).filter(Boolean))].sort();
          const open = expanded.has(key), id = `backtest-sessions-${key}`;
          return <Fragment key={key}>
            <tbody><tr className={`backtest-strategy-group ${open ? "backtest-group-selected" : ""}`} tabIndex={0} aria-expanded={open} aria-controls={id} aria-label={`${identity(first)}, ${sessionDates.length} sessions`} onClick={() => toggle(key)} onKeyDown={event => {
              if (event.target === event.currentTarget && ["Enter", " "].includes(event.key)) { event.preventDefault(); toggle(key); }
            }}>
              <th scope="row"><button className="backtest-group-toggle" type="button" aria-expanded={open} aria-controls={id} onClick={event => { event.stopPropagation(); toggle(key); }}><span aria-hidden="true">{open ? "▾" : "▸"}</span> {identity(first)}</button>
                <small title={`Configuration ${first.configuration_content_hash || "unknown"}; source ${first.code_fingerprint || "unknown"}`}>Variant {key.slice(0, 8)}</small>
              </th>
              <td className="backtest-history-datetime">{sessionDates[0] || "—"}{sessionDates.length > 1 ? <small>to {sessionDates.at(-1)}</small> : null}</td>
              <td className="numeric">{money(numeric(first.initial_cash))}</td>
              <td className={`numeric ${pnlClass(pnl)}`}>{money(pnl)}</td><td className="numeric">{trades == null ? "—" : trades.toLocaleString()}</td><td className="numeric">{percent(trades && wins != null ? wins / trades : null)}</td><td className="numeric">{money(sum("total_fees"))}</td>
              <td className={reports.length < sessions.size ? "backtest-coverage-incomplete" : ""}><span className="table-category-badge" data-tone={reports.length === sessions.size && sessions.size > 0 ? "positive" : "warning"} data-emphasis="medium">{reports.length} / {sessions.size} sessions</span>{runs.some(row => row.status !== "completed") ? <small>{runs.filter(row => row.status !== "completed").length} incomplete runs</small> : null}</td>
              <td className="backtest-group-count">{sessionDates.length} {sessionDates.length === 1 ? "session" : "sessions"}</td>
            </tr></tbody>
            <tbody id={id} hidden={!open} className="backtest-session-rows">{runs.map(row => {
              const recordedV4 = row.journal_backend === "arte_typed_journal_v4";
              const paused = row.status === "paused" && row.resident;
              const reviewable = row.v4_review_available || row.review_available !== false;
              const resumeAttempt = recordedV4 && row.status === "running" && row.resident === false && row.resume_attempt_available === true;
              const resumable = paused || resumeAttempt || (row.status !== "completed" && (row.status === "stopped" || row.status === "failed" || row.resident === false) && row.checkpoint?.resume_supported);
              const summary = performance[row.run_id]?.report?.summary;
              const pnl = numeric(summary?.net_pnl), count = numeric(summary?.episode_count);
              return <tr key={row.run_id}>
                <th scope="row"><span className="backtest-session-branch" aria-hidden="true">↳</span><strong title={row.run_id}>{row.run_id.slice(0, 8)}</strong><small>{row.tickers?.length ? row.tickers.join(", ") : "Configured universe"}</small></th>
                <td className="backtest-history-datetime">{row.session_date || "—"}<small><time dateTime={row.created_at} title="Run created at">{dateTime(row.created_at)}</time></small></td>
                <td className="numeric">{money(numeric(row.initial_cash))}</td>
                <td className={`numeric ${pnlClass(pnl)}`}>{money(pnl)}{performance[row.run_id]?.error ? <small title={performance[row.run_id].error}>Unavailable</small> : !summary ? <small>{recordedV4 && terminal(row) ? performance[row.run_id]?.status === "verifying" ? "Verifying…" : "Queued" : recordedV4 ? "Not terminal" : "Open review"}</small> : row.status !== "completed" ? <small>Partial run</small> : null}</td>
                <td className="numeric">{count?.toLocaleString() ?? "—"}</td><td className="numeric">{count ? percent(numeric(summary?.win_rate)) : "—"}</td><td className="numeric">{money(numeric(summary?.total_fees))}</td>
                <td><span className="table-category-badge" data-emphasis="strong" data-tone={row.status === "completed" ? "positive" : row.status === "failed" ? "negative" : ["running", "preparing"].includes(row.status) ? "info" : "warning"}><span>{row.status.replaceAll("_", " ")}</span></span>{row.status === "running" && row.resident === false ? <small>No app runner attached</small> : null}</td>
                <td><div className="backtest-history-actions"><button className="button secondary compact" type="button" disabled={!reviewable || Boolean(busy)} aria-label={`Review backtest ${row.run_id.slice(0, 8)}`} onClick={() => onReview(row.run_id)}>Review</button>
                  <button className="button secondary compact" type="button" disabled={!resumable || Boolean(busy)} aria-label={`Resume backtest ${row.run_id.slice(0, 8)}`} onClick={() => void resume(row)}>{busy === row.run_id ? "Resuming…" : "Resume"}</button></div>
                  {actionError?.runId === row.run_id ? <p role="alert">{actionError.message} Refresh runs before retrying.</p> : null}
                </td>
              </tr>;
            })}</tbody>
          </Fragment>;
        })}
        {!filteredGroups.length ? <tbody><tr><td colSpan={9}>No strategies match “{search}”.</td></tr></tbody> : null}
      </table></div>
      <footer><span>{pendingCount ? `Verifying ${pendingCount} runs` : ""}</span>
        {unavailableCount ? <details className="backtest-performance-errors"><summary>Performance issues · {unavailableCount} {unavailableCount === 1 ? "run" : "runs"}</summary><ul>{[...performanceErrors].map(([message, count]) => <li key={message}>{message} <span>({count} {count === 1 ? "run" : "runs"})</span></li>)}</ul></details> : null}
      </footer>
    </>}
  </section>;
}
