import { useEffect, useState } from "react";

import { api } from "../../api/client";
import { usePollingTask } from "../../app/hooks/usePollingTask";
import type { NewsTodayRowsPayload, NewsTodayRowsState, NewsTodaySort } from "./newsContracts";
import { newsTodayRowFromPayload, newsTodaySummaryFromPayload } from "./newsTodayPresentation";
import { isRecord } from "./workPresentation";

export function useNewsTodayRows(enabled: boolean, sort: NewsTodaySort): NewsTodayRowsState {
  const [payload, setPayload] = useState<NewsTodayRowsState>(() => defaultNewsTodayRowsState(sort));
  useEffect(() => {
    if (!enabled) setPayload(defaultNewsTodayRowsState(sort));
  }, [enabled, sort]);
  usePollingTask({
    enabled,
    initialDelayMs: 0,
    intervalMs: 30_000,
    restartKey: sort,
    task: async (signal) => {
      setPayload((current) => ({ ...current, loading: true }));
      try {
        const response = await api<NewsTodayRowsPayload>(`/api/services/news/today?limit=5000&sort=${sort}`, { signal });
        const rows = (response.rows || []).filter(isRecord).map(newsTodayRowFromPayload);
        setPayload({
          error: response.error || "",
          loading: false,
          rows,
          sort: response.sort === "asc" ? "asc" : "desc",
          summary: newsTodaySummaryFromPayload(response.summary, rows),
          windowEndUtc: response.window_end_utc || "",
          windowStartUtc: response.window_start_utc || "",
        });
      } catch (exc) {
        if (signal.aborted) return;
        setPayload((current) => ({ ...current, error: exc instanceof Error ? exc.message : String(exc), loading: false }));
      }
    },
  });
  return payload;
}

function defaultNewsTodayRowsState(sort: NewsTodaySort): NewsTodayRowsState {
  return {
    error: "",
    loading: false,
    rows: [],
    sort,
    summary: { externalText: 0, latest: "", loadedRows: 0, multiTickerRows: 0, noTickerRows: 0, oneTickerRows: 0, pdfRows: 0, totalRows: 0, withTicker: 0 },
    windowEndUtc: "",
    windowStartUtc: "",
  };
}
