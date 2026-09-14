import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { CanvasReplayRun } from "../replayRun";

type RunRow = Pick<CanvasReplayRun, "run_id" | "created_at" | "status" | "session_date" | "current_time" | "processed_events" | "checkpoint" | "tickers"> & {
  configuration_revision?: number;
  configuration_label?: string;
  resident?: boolean;
};

const PAGE_SIZE = 10;
const createdTime = (value: string) => Date.parse(value) || 0;
const dateTime = (value: string) => Number.isFinite(Date.parse(value))
  ? new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "medium", timeZone: "America/New_York" }).format(new Date(value)) : "—";

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
  const [page, setPage] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api<{ rows: RunRow[] }>("/api/trading/backtest/runs", { signal: controller.signal, timeoutMs: 60_000 })
      .then(result => {
        if (controller.signal.aborted) return;
        setRows([...result.rows].sort((a, b) => createdTime(b.created_at) - createdTime(a.created_at) || b.run_id.localeCompare(a.run_id)));
        setPage(0);
      })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);

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

  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  return <section className="backtest-run-history" aria-labelledby="backtest-history-heading" aria-busy={loading}>
    <header><div><h2 id="backtest-history-heading">Recent backtests</h2><p>Newest created first · Times in ET · Resume continues the saved run.</p></div>
      <button className="button secondary compact" type="button" disabled={loading || Boolean(busy)} onClick={() => setRefresh(value => value + 1)}>{loading ? "Loading…" : "Refresh runs"}</button></header>
    {error ? <p role="alert">Could not refresh backtests: {error}{rows.length ? " Showing the previously loaded list." : ""}</p> : null}
    {!rows.length ? <p role="status">{loading ? "Loading recent backtests…" : error ? "Use Refresh runs to try again." : "No saved backtests yet."}</p> : <>
      <div className="backtest-history-scroll" role="region" aria-label="Recent backtests table" tabIndex={0}><table>
        <thead><tr><th scope="col" aria-sort="descending">Created · ET ↓</th><th scope="col">Run / candidate</th><th scope="col">Market / session</th><th scope="col">Status</th><th scope="col">Progress</th><th scope="col">Controls</th></tr></thead>
        <tbody>{rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map(row => {
          const paused = row.status === "paused" && row.resident;
          const resumable = paused || (row.status !== "completed" && (row.status === "stopped" || row.status === "failed" || row.resident === false) && row.checkpoint?.resume_supported);
          return <tr key={row.run_id}>
            <td><time dateTime={row.created_at}>{dateTime(row.created_at)}</time></td>
            <td><strong title={row.run_id}>{row.run_id.slice(0, 8)}</strong><small>{row.configuration_revision ? `Candidate ${row.configuration_revision}` : "Candidate unavailable"}</small><small>{row.configuration_label}</small></td>
            <td>{row.tickers?.length ? row.tickers.join(", ") : "Configured universe"}<small>{row.session_date || "—"}</small></td>
            <td>{row.status.replaceAll("_", " ")}{row.resident === false && !["completed", "stopped", "failed"].includes(row.status) ? <small>Saved status · not active</small> : null}</td>
            <td>{(row.processed_events ?? 0).toLocaleString()} events<small>Through {dateTime(row.current_time)}</small></td>
            <td><div className="backtest-history-actions"><button className="button secondary compact" type="button" disabled={Boolean(busy)} aria-label={`Review backtest ${row.run_id.slice(0, 8)}`} onClick={() => onReview(row.run_id)}>Review</button>
              <button className="button secondary compact" type="button" disabled={!resumable || Boolean(busy)} aria-label={`Resume backtest ${row.run_id.slice(0, 8)}`} onClick={() => void resume(row)}>{busy === row.run_id ? "Resuming…" : "Resume"}</button></div>
              <small>{paused ? "Paused in memory" : resumable ? `Checkpoint: ${(row.checkpoint?.processed_events ?? 0).toLocaleString()} events` : row.status === "completed" ? "Completed" : ["stopped", "failed"].includes(row.status) || row.resident === false ? "No resumable checkpoint" : "Run is active"}</small>
              {actionError?.runId === row.run_id ? <p role="alert">{actionError.message} Refresh runs before retrying.</p> : null}
            </td>
          </tr>;
        })}</tbody>
      </table></div>
      <footer><span>{rows.length.toLocaleString()} runs · Page {page + 1} of {pages}</span><div className="backtest-history-actions">
        <button className="button secondary compact" type="button" disabled={page === 0 || Boolean(busy)} onClick={() => setPage(value => value - 1)}>Newer</button>
        <button className="button secondary compact" type="button" disabled={page + 1 >= pages || Boolean(busy)} onClick={() => setPage(value => value + 1)}>Older</button>
      </div></footer>
    </>}
  </section>;
}
