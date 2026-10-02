import { useEffect, useState, type Dispatch, type SetStateAction } from "react";

// Keep the Research workspace across route navigation; reload requires preflight.
const researchState = new Map<string, unknown>();
export function useResearchState<T,>(key: string, initial: T, durable = false): [T, Dispatch<SetStateAction<T>>] {
  const storageKey = `research.v6.workspace.v1:${key}`;
  const [value, setValue] = useState<T>(() => {
    if (researchState.has(key)) return researchState.get(key) as T;
    if (durable) {
      try { const saved = localStorage.getItem(storageKey); if (saved) return JSON.parse(saved) as T; }
      catch { /* Unavailable storage must not prevent the audit from opening. */ }
    }
    return initial;
  });
  useEffect(() => {
    researchState.set(key, value);
    if (durable) {
      try { localStorage.setItem(storageKey, JSON.stringify(value)); }
      catch { /* Retain route-session persistence when browser storage is full. */ }
    }
  }, [key, value, durable, storageKey]);
  return [value, setValue];
}
