import { api, type ApiError } from "../api/client";

type RunStatus = { run_id: string; status: string; error?: string | null };

// Review eligibility is validated against saved evidence by the backend.
export async function recoverBacktest<T extends RunStatus>(runId: string, signal?: AbortSignal, compact = true): Promise<T> {
  const path = `/api/trading/backtest/runs/${encodeURIComponent(runId)}`;
  try {
    return await api<T>(`${path}${compact ? "?compact=true" : ""}`, { signal, timeoutMs: 20_000 });
  } catch (reason) {
    if ((reason as ApiError)?.status !== 404 || signal?.aborted) throw reason;
  }
  return await api<T>(`${path}/review${compact ? "?compact=true" : ""}`, { method: "POST", signal, timeoutMs: 60_000 });
}

export function openBacktestSetup() {
  const url = new URL(window.location.href);
  for (const key of ["backtest_run", "replay_run", "replay_focus", "historical_mode"]) url.searchParams.delete(key);
  url.hash = "backtest-trading";
  try { sessionStorage.removeItem("backtest.active-run.v1"); } catch { /* URL still clears the selection. */ }
  window.location.assign(url.toString());
}
