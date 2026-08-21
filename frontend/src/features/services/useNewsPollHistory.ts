import { useEffect, useState } from "react";

import type { ServiceStatusPayload } from "./contracts";
import type { NewsPollHistoryRow } from "./newsWorkContracts";
import {
  historiesEqual,
  mergeNewsPollHistory,
  newsPollHistoryRow,
  newsPollHistoryRowsFromLogs,
} from "./newsWorkPresentation";

export function useNewsPollHistory(service: ServiceStatusPayload) {
  const [history, setHistory] = useState<NewsPollHistoryRow[]>([]);
  useEffect(() => {
    if (service.registry.id !== "news") {
      setHistory([]);
      return;
    }
    const logRows = newsPollHistoryRowsFromLogs(service);
    const row = newsPollHistoryRow(service);
    const incoming = row ? [row, ...logRows] : logRows;
    if (!incoming.length) return;
    setHistory((current) => {
      const merged = mergeNewsPollHistory(incoming, current);
      return historiesEqual(merged, current) ? current : merged;
    });
  }, [service]);
  return history;
}
