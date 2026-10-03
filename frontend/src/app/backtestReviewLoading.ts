import { api, type ApiError } from "../api/client";
import type { V4Page } from "./components/BacktestV4SavedReview";

function pause(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { window.clearTimeout(timer); reject(signal.reason ?? new Error("Saved review canceled")); };
    const timer = window.setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 1000);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

/** Poll one shared verification job; disconnecting never restarts its audit. */
export async function openSavedBacktestReview(runId: string, signal: AbortSignal): Promise<V4Page> {
  while (!signal.aborted) {
    try {
      const result = await api<{ status: "queued" | "verifying" | "ready"; page?: V4Page }>(
        `/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-review-ready`, { signal, timeoutMs: 15_000 });
      if (result.status === "ready") {
        const page = result.page;
        if (!page || page.schema_version !== "strategy-one-v4-terminal-review-page-v1" || page.run.run_id !== runId) {
          throw new Error("Saved numbered strategy review identity differs from the selected run.");
        }
        return page;
      }
      if (result.status !== "queued" && result.status !== "verifying") throw new Error("Invalid saved review loading state.");
    } catch (reason) {
      const error = reason as ApiError;
      if (signal.aborted || error.status !== 429 || !error.retryable) throw reason;
    }
    await pause(signal);
  }
  throw signal.reason ?? new Error("Saved review canceled");
}
