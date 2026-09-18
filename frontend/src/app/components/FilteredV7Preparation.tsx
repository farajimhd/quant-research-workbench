import { useEffect, useState } from "react";
import type { FilteredV7Progress } from "../replayRun";

function duration(seconds: number) {
  if (seconds < 60) return `${Math.ceil(seconds)}s`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.ceil((seconds % 3600) / 60)}m`;
}

export function FilteredV7Preparation({ progress }: { progress: FilteredV7Progress }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  const age = (at?: string) => at && Number.isFinite(Date.parse(at)) ? `${duration(Math.max(0, (now - Date.parse(at)) / 1000))} ago` : "Awaiting update";
  return <section className="filtered-v7-preparation" aria-label="Filtered V7 preparation">
    <h3>Filtered V7 history · {progress.completed.toLocaleString()} / {progress.total.toLocaleString()} tickers</h3>
    <p>History before {progress.before}. Verified checkpoints are reused on later runs.</p>
    <dl>
      <div><dt>Active / queued</dt><dd>{progress.active} / {progress.queued}</dd></div>
      <div><dt>Throughput</dt><dd>{progress.tickers_per_minute.toFixed(1)} tickers/min</dd></div>
      <div><dt>Estimated remaining</dt><dd>{progress.failed || progress.workers.some(w => w.state === "cancelled") ? "Stopped" : progress.completed === progress.total ? "Complete" : progress.eta_seconds == null ? "Estimating…" : `~${duration(progress.eta_seconds)}`}</dd></div>
    </dl>
    <div className="backtest-exclusion-table filtered-v7-workers"><table>
      <caption>Fixed worker slots · controller updated {age(progress.updated_at)}</caption>
      <thead><tr><th>Worker / ticker</th><th>Stage / session</th><th>Sessions</th><th>Last update</th></tr></thead>
      <tbody>{progress.workers.map(worker => <tr key={worker.slot}>
        <th scope="row">{worker.slot} · {worker.ticker || "—"}</th>
        <td>{["failed", "cancelled", "complete", "idle"].includes(worker.state) ? worker.state : worker.stage || worker.state}<small>{worker.session || "—"}{worker.retried ? ` · ${worker.retried} retries` : ""}</small>{worker.error ? <small role="alert">{worker.error}</small> : null}</td>
        <td>{worker.total == null ? "—" : `${worker.completed ?? 0} / ${worker.total}`}<small>{worker.resumed ? `${worker.resumed} reused` : ""}</small></td>
        <td>{age(worker.updated_at)}</td>
      </tr>)}</tbody>
    </table></div>
    <p>{progress.built} built · {progress.reused} reused · {progress.unavailable} without coverage · {progress.failed} failed · {duration(progress.elapsed_seconds)} elapsed</p>
  </section>;
}
