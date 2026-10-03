import { api, type ApiError, type ApiRequestInit } from "../../api/client";

/** A cancelled HTTP read may still occupy an offline backend slot briefly. */
export async function researchApi<T>(path: string, init?: ApiRequestInit): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try { return await api<T>(path, init); }
    catch (reason) {
      const error = reason as ApiError;
      if (init?.signal?.aborted || error.code !== "backend_workload_capacity_exhausted" || !error.retryable || attempt >= 5) throw reason;
      await new Promise<void>((resolve, reject) => {
        const abort = () => { window.clearTimeout(timer); reject(init?.signal?.reason ?? new Error("Research request cancelled")); };
        const timer = window.setTimeout(() => { init?.signal?.removeEventListener("abort", abort); resolve(); }, 250 * (attempt + 1));
        init?.signal?.addEventListener("abort", abort, { once: true });
      });
    }
  }
}
