import { useEffect, useState } from "react";

import { api } from "../../api/client";
import { usePollingTask } from "../../app/hooks/usePollingTask";
import type { NewsDailyHistogramState, NewsHistogramPayload } from "./newsContracts";
import { defaultNewsHistogramWindow, elapsedNewsHistogramRows } from "./newsHistogramPresentation";

export function useNewsDailyHistogram(enabled: boolean) {
  const [payload, setPayload] = useState<NewsDailyHistogramState>(() => defaultNewsHistogramWindow(900));
  useEffect(() => {
    if (!enabled) setPayload(defaultNewsHistogramWindow(900));
  }, [enabled]);
  usePollingTask({
    enabled,
    initialDelayMs: 0,
    intervalMs: 30_000,
    task: async (signal) => {
      try {
        const response = await api<NewsHistogramPayload>("/api/services/news/histogram", { signal });
        const binSeconds = Number(response.bin_seconds || 900);
        const defaultWindow = defaultNewsHistogramWindow(binSeconds);
        const windowStartUtc = response.window_start_utc || defaultWindow.windowStartUtc;
        const windowEndUtc = response.window_end_utc || defaultWindow.windowEndUtc;
        setPayload({
          binSeconds,
          error: response.error || "",
          rows: elapsedNewsHistogramRows(
            (response.rows || [])
              .map((row) => ({
                broadOrNoneRows: Number(row.broad_or_none_rows || 0),
                bucketUtc: String(row.bucket_utc || ""),
                singleTickerRows: Number(row.single_ticker_rows || 0),
                totalRows: Number(row.total_rows || 0),
              }))
              .filter((row) => row.bucketUtc),
            windowStartUtc,
            windowEndUtc,
            binSeconds,
          ),
          windowEndUtc,
          windowStartUtc,
        });
      } catch (exc) {
        if (signal.aborted) return;
        setPayload({ ...defaultNewsHistogramWindow(900), error: exc instanceof Error ? exc.message : String(exc) });
      }
    },
  });
  return payload;
}
