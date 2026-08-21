import { AlertTriangle } from "lucide-react";

import { displayName, formatDuration } from "../../app/format";
import type { WorkloadBudgetPayload } from "./contracts";

export function ServicePageApiFailure({ message }: { message: string }) {
  return (
    <section className="service-page-api-failure">
      <div className="service-page-api-failure-icon"><AlertTriangle size={18} /></div>
      <div>
        <h2>Service status could not be loaded</h2>
        <p>{message}</p>
        <span>The dashboard will keep retrying in the background. Confirm the backend is running on port 8000 and refresh once it is healthy.</span>
      </div>
    </section>
  );
}

export function WorkloadBudgetPanel({ error, payload }: { error: string; payload: WorkloadBudgetPayload | null }) {
  const lanes = Object.entries(payload?.lanes ?? {});
  const rejected = lanes.reduce((total, [, lane]) => total + lane.rejected, 0);
  return (
    <section className={`service-workload-budget-panel${error ? " is-unavailable" : rejected ? " has-rejections" : ""}`} aria-label="Backend workload admission budgets">
      <header>
        <div><span className="page-kicker">Application backend</span><h2>Workload admission</h2></div>
        <span className={!payload && !error ? "inline-loading-message" : undefined}>
          {!payload && !error ? <span className="loading-spinner" aria-hidden="true" /> : null}
          {payload ? `${payload.wait_timeout_seconds}s admission wait` : error ? "Evidence unavailable" : "Loading limits…"}
        </span>
      </header>
      {lanes.length ? (
        <div className="service-workload-budget-grid">
          {lanes.map(([name, lane]) => {
            const pressure = lane.limit ? lane.active / lane.limit : 0;
            return (
              <article className={lane.rejected ? "has-rejections" : pressure >= 0.8 ? "is-pressured" : ""} key={name}>
                <div><strong>{displayName(name)}</strong><span>{lane.active} / {lane.limit} active</span></div>
                <progress aria-label={`${displayName(name)} workload usage`} max={lane.limit} value={lane.active} />
                <small>{lane.available} available · {lane.completed} completed · {lane.rejected} rejected · {formatDuration(lane.total_wait_seconds * 1000)} waiting</small>
              </article>
            );
          })}
        </div>
      ) : <p>{error || "Waiting for backend admission evidence."}</p>}
    </section>
  );
}
