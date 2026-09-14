import { api, type ApiError } from "../api/client";

type RunStatus = { run_id: string; status: string; error?: string | null };

export class FailedBacktestError extends Error {
  constructor(run: RunStatus) {
    super(`Backtest ${run.run_id} failed. ${run.error || "No failure detail was saved."}`);
    this.name = "FailedBacktestError";
  }
}

function checkFailure<T extends RunStatus>(run: T): T {
  if (run.status === "failed") throw new FailedBacktestError(run);
  return run;
}

// A missing in-memory controller does not mean a saved run is reviewable.
// Consult durable status before requesting checkpoint rehydration.
export async function recoverBacktest<T extends RunStatus>(runId: string, signal?: AbortSignal, compact = true): Promise<T> {
  const path = `/api/trading/backtest/runs/${encodeURIComponent(runId)}`;
  try {
    return checkFailure(await api<T>(`${path}${compact ? "?compact=true" : ""}`, { signal, timeoutMs: 20_000 }));
  } catch (reason) {
    if ((reason as ApiError)?.status !== 404 || signal?.aborted) throw reason;
  }
  const saved = await api<{ rows: RunStatus[] }>("/api/trading/backtest/runs", { signal, timeoutMs: 20_000 });
  const row = saved.rows.find((run) => run.run_id === runId);
  if (row) checkFailure(row);
  return checkFailure(await api<T>(`${path}/review${compact ? "?compact=true" : ""}`, { method: "POST", signal, timeoutMs: 60_000 }));
}

export function openBacktestSetup() {
  const url = new URL(window.location.href);
  for (const key of ["backtest_run", "replay_run", "replay_focus", "historical_mode"]) url.searchParams.delete(key);
  url.hash = "backtest-trading";
  try { sessionStorage.removeItem("backtest.active-run.v1"); } catch { /* URL still clears the selection. */ }
  window.location.assign(url.toString());
}
