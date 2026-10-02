import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { PerformanceJournalReport } from "../../features/canvas/contracts";
import type { CanvasReplayRun } from "../replayRun";

type RunRow = Pick<CanvasReplayRun, "run_id" | "created_at" | "status" | "session_date" | "checkpoint" | "tickers"> & {
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

type Performance = { report?: PerformanceJournalReport; error?: string };
const numeric = (value: unknown): number | null => value == null || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);
const money = (value: number | null) => value == null ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
const percent = (value: number | null) => value == null ? "—" : new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 1 }).format(value);
const identity = (row: RunRow) => row.strategy_revision ? `Strategy ${row.strategy_revision}` : row.configuration_revision ? `Candidate ${row.configuration_revision}` : "Strategy unavailable";
// Unknown identities remain separate; a revision alone does not establish identical configuration.
const groupKey = (row: RunRow) => [row.strategy_id || identity(row), row.strategy_revision || row.configuration_revision || row.run_id,
  row.configuration_content_hash || row.configuration_revision_id || row.run_id, row.initial_cash ?? "unknown", [...(row.tickers || [])].sort().join(",")].join("|");
const terminal = (row: RunRow) => ["completed", "stopped", "failed"].includes(row.status);
const statusClass = (status: string) => status === "completed" ? "backtest-status-completed" : status === "failed" ? "backtest-status-failed" : ["running", "preparing"].includes(status) ? "backtest-status-active" : "backtest-status-incomplete";
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
  const [selectedGroup, setSelectedGroup] = useState("");
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
        setSelectedGroup("");
        setPerformance({});
      })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [refresh]);

  useEffect(() => {
    const controller = new AbortController();
    const pending = rows.filter(row => row.journal_backend === "arte_typed_journal_v4" && terminal(row));
    let cursor = 0;
    // Two readers bound full-prefix verification work; history and controls stay usable.
    async function read() {
      while (!controller.signal.aborted && cursor < pending.length) {
        const row = pending[cursor++];
        try {
          const result = await api<{ run_id: string; verified_sequence: number; report: PerformanceJournalReport }>(
            `/api/trading/backtest/runs/${encodeURIComponent(row.run_id)}/v4-performance?include_entry_context=false`,
            { signal: controller.signal, timeoutMs: 60_000 });
          if (result.run_id !== row.run_id || result.verified_sequence !== row.journal_sequence || !result.report?.summary) {
            throw new Error("Performance does not match this history snapshot. Refresh runs to retry.");
          }
          if (!controller.signal.aborted) setPerformance(current => ({ ...current, [row.run_id]: { report: result.report } }));
        } catch (reason) {
          if (!controller.signal.aborted) setPerformance(current => ({ ...current, [row.run_id]: { error: reason instanceof Error ? reason.message : String(reason) } }));
        }
      }
    }
    const timer = window.setTimeout(() => { void read(); void read(); }, 0);
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
  const visibleRows = selectedGroup ? groups.get(selectedGroup) || [] : rows;
  const pages = Math.max(1, Math.ceil(visibleRows.length / PAGE_SIZE));
  const verified = rows.filter(row => performance[row.run_id]?.report).length;
  return <section className="backtest-run-history" aria-labelledby="backtest-history-heading" aria-busy={loading}>
    <header><div><h2 id="backtest-history-heading">Recent backtests</h2><p>Compare saved strategy results across sessions · Times in ET.</p></div>
      <button className="button secondary compact" type="button" disabled={loading || Boolean(busy)} onClick={() => setRefresh(value => value + 1)}>{loading ? "Loading…" : "Refresh runs"}</button></header>
    {error ? <p role="alert">Could not refresh backtests: {error}{rows.length ? " Showing the previously loaded list." : ""}</p> : null}
    {!rows.length ? <p role="status">{loading ? "Loading recent backtests…" : error ? "Use Refresh runs to try again." : "No saved backtests yet."}</p> : <>
      <dl className="backtest-history-overview">
        <div><dt>Saved runs</dt><dd>{rows.length}</dd></div>
        <div><dt>Strategy / configuration groups</dt><dd>{groups.size}</dd></div>
        <div><dt>Market sessions</dt><dd>{new Set(rows.map(row => row.session_date).filter(Boolean)).size}</dd></div>
        <div><dt>Verified performance</dt><dd>{verified} / {rows.filter(row => row.journal_backend === "arte_typed_journal_v4" && terminal(row)).length}</dd></div>
      </dl>
      {Object.values(performance).some(result => result.error) ? <details className="backtest-performance-errors"><summary>Performance unavailable for {Object.values(performance).filter(result => result.error).length} {Object.values(performance).filter(result => result.error).length === 1 ? "run" : "runs"}</summary><ul>{rows.filter(row => performance[row.run_id]?.error).map(row => <li key={row.run_id}>{identity(row)} · {row.session_date} · {row.run_id.slice(0, 8)}: {performance[row.run_id].error}</li>)}</ul></details> : null}
      <h3>Strategy performance across sessions</h3>
      <p className="backtest-history-note">Loaded history only · Newest completed run per session and frozen configuration. Net P&amp;L covers closed trades after fees; sessions are independent, not a compounded portfolio. Partial runs are excluded.</p>
      <div className="backtest-history-scroll" role="region" aria-label="Strategy performance summaries" tabIndex={0}><table>
        <thead><tr><th scope="col">Strategy / configuration</th><th scope="col" className="numeric">Net P&amp;L · USD</th><th scope="col" className="numeric">Closed trades</th><th scope="col" className="numeric">Win rate</th><th scope="col">Completed coverage</th><th scope="col">Runs / sessions</th><th scope="col">Sessions</th></tr></thead>
        <tbody>{[...groups].map(([key, runs]) => {
          const sessions = new Map<string, RunRow>();
          runs.filter(row => row.status === "completed" && row.session_date).forEach(row => { if (!sessions.has(row.session_date)) sessions.set(row.session_date, row); });
          const reports = [...sessions.values()].map(row => performance[row.run_id]?.report).filter((report): report is PerformanceJournalReport => Boolean(report));
          const sum = (field: string) => reports.length && reports.every(report => numeric(report.summary[field]) != null) ? reports.reduce((total, report) => total + Number(report.summary[field]), 0) : null;
          const pnl = sum("net_pnl"), trades = sum("episode_count"), wins = sum("win_count");
          const first = runs[0];
          return <tr key={key} className={selectedGroup === key ? "backtest-group-selected" : ""}>
            <th scope="row">{identity(first)}<small title={first.configuration_content_hash || first.configuration_revision_id}>{first.configuration_content_hash ? `Config ${first.configuration_content_hash.slice(0, 8)}` : first.configuration_revision_id ? `Config ${first.configuration_revision_id.slice(0, 8)}` : "Unverified configuration · separate run"}</small><small>Initial cash {money(numeric(first.initial_cash))}</small></th>
            <td className={`numeric ${pnlClass(pnl)}`}>{money(pnl)}</td><td className="numeric">{trades == null ? "—" : trades.toLocaleString()}</td><td className="numeric">{percent(trades && wins != null ? wins / trades : null)}</td>
            <td className={reports.length < sessions.size ? "backtest-coverage-incomplete" : ""}>{reports.length} / {sessions.size} sessions<small>{reports.length < sessions.size ? "Partial coverage" : sessions.size ? "Verified" : "No completed sessions"}</small></td>
            <td>{runs.length} / {new Set(runs.map(row => row.session_date).filter(Boolean)).size}<small>{runs.filter(row => row.status !== "completed").length} incomplete</small></td>
            <td><button className="button secondary compact" type="button" aria-pressed={selectedGroup === key} onClick={() => { setSelectedGroup(selectedGroup === key ? "" : key); setPage(0); }}>View runs</button></td>
          </tr>;
        })}</tbody>
      </table></div>
      <div className="backtest-history-run-heading"><h3>{selectedGroup ? `${identity(visibleRows[0])} · session runs` : "Individual backtests"}</h3>{selectedGroup ? <button className="button secondary compact" type="button" onClick={() => { setSelectedGroup(""); setPage(0); }}>Show all runs</button> : null}<span>Newest created first · USD · Closed-trade results</span></div>
      <div className="backtest-history-scroll" role="region" aria-label="Recent backtests table" tabIndex={0}><table>
        <thead><tr><th scope="col">Market / session</th><th scope="col">Run / strategy</th><th scope="col" className="numeric">Net P&amp;L</th><th scope="col" className="numeric">Closed trades</th><th scope="col" className="numeric">Win rate</th><th scope="col" className="numeric">Fees</th><th scope="col">Status</th><th scope="col" aria-sort="descending">Created · ET ↓</th><th scope="col">Controls</th></tr></thead>
        <tbody>{visibleRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map(row => {
          const paused = row.status === "paused" && row.resident;
          const reviewable = row.v4_review_available || row.review_available !== false;
          const recordedV4 = row.journal_backend === "arte_typed_journal_v4";
          const resumeAttempt = recordedV4 && row.status === "running" && row.resident === false && row.resume_attempt_available === true;
          const resumable = paused || resumeAttempt || (row.status !== "completed" && (row.status === "stopped" || row.status === "failed" || row.resident === false) && row.checkpoint?.resume_supported);
          const identity = recordedV4
            ? row.strategy_revision ? `Strategy ${row.strategy_revision}` : "Strategy unavailable"
            : row.configuration_revision ? `Candidate ${row.configuration_revision}` : "Candidate unavailable";
          return <tr key={row.run_id}>
            <td>{row.tickers?.length ? row.tickers.join(", ") : "Configured universe"}<small>{row.session_date || "—"}</small></td>
            <td><strong title={row.run_id}>{row.run_id.slice(0, 8)}</strong><small>{identity}</small>{row.initial_cash != null && Number.isFinite(row.initial_cash) ? <small>Initial cash {new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(row.initial_cash)}</small> : null}{!recordedV4 && row.configuration_label ? <small>{row.configuration_label}</small> : null}</td>
            <td className={`numeric ${pnlClass(numeric(performance[row.run_id]?.report?.summary.net_pnl))}`}>{money(numeric(performance[row.run_id]?.report?.summary.net_pnl))}<small title={performance[row.run_id]?.error}>{performance[row.run_id]?.error ? "Unavailable · refresh to retry" : performance[row.run_id]?.report ? row.status === "completed" ? "Verified" : "Partial run" : recordedV4 && terminal(row) ? "Verifying…" : recordedV4 ? "Not terminal" : "Legacy report · open review"}</small></td>
            <td className="numeric">{numeric(performance[row.run_id]?.report?.summary.episode_count)?.toLocaleString() ?? "—"}</td>
            <td className="numeric">{numeric(performance[row.run_id]?.report?.summary.episode_count) ? percent(numeric(performance[row.run_id]?.report?.summary.win_rate)) : "—"}</td>
            <td className="numeric">{money(numeric(performance[row.run_id]?.report?.summary.total_fees))}</td>
            <td><span className={`backtest-status ${statusClass(row.status)}`}>{row.status.replaceAll("_", " ")}</span>{recordedV4 ? <small>{row.status === "running" && row.resident === false ? "Saved journal · no app runner attached" : "ClickHouse record · verified when opened"}</small> : row.resident === false && !["completed", "stopped", "failed"].includes(row.status) ? <small>Saved status · not active</small> : null}</td>
            <td><time dateTime={row.created_at}>{dateTime(row.created_at)}</time></td>
            <td><div className="backtest-history-actions"><button className="button secondary compact" type="button" disabled={!reviewable || Boolean(busy)} aria-label={`Review backtest ${row.run_id.slice(0, 8)}`} onClick={() => onReview(row.run_id)}>{recordedV4 ? "Review journal" : "Review"}</button>
              <button className="button secondary compact" type="button" disabled={!resumable || Boolean(busy)} aria-label={`Resume backtest ${row.run_id.slice(0, 8)}`} onClick={() => void resume(row)}>{busy === row.run_id ? "Resuming…" : "Resume"}</button></div>
              <small>{recordedV4 ? resumeAttempt ? "Keeper and checkpoint verified on click" : row.status === "completed" ? "Completed" : "No verified restart" : paused ? "Paused in memory" : resumable ? `Checkpoint: ${(row.checkpoint?.processed_events ?? 0).toLocaleString()} events` : row.status === "completed" ? "Completed" : ["stopped", "failed"].includes(row.status) || row.resident === false ? "No resumable checkpoint" : "Run is active"}</small>
              {actionError?.runId === row.run_id ? <p role="alert">{actionError.message} Refresh runs before retrying.</p> : null}
            </td>
          </tr>;
        })}</tbody>
      </table></div>
      <footer><span>{visibleRows.length.toLocaleString()} runs · Page {page + 1} of {pages}</span><div className="backtest-history-actions">
        <button className="button secondary compact" type="button" disabled={page === 0 || Boolean(busy)} onClick={() => setPage(value => value - 1)}>Newer</button>
        <button className="button secondary compact" type="button" disabled={page + 1 >= pages || Boolean(busy)} onClick={() => setPage(value => value + 1)}>Older</button>
      </div></footer>
    </>}
  </section>;
}
