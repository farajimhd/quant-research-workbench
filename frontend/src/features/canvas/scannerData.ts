import { useCallback, useEffect, useRef, useState } from "react";

import { api, query } from "../../api/client";
import type { ScannerSnapshotMeta, WatchlistRuntimeResponse } from "../../app/components/MarketScreenerContainers";
import type { CanvasScannerSnapshot } from "./contracts";
import { dateInTimeZone } from "./time";

export function useCanvasScannerSnapshot({ cutoffMs, enabled, materializeDiscovery, technicalWindows }: { cutoffMs: number; enabled: boolean; materializeDiscovery: boolean; technicalWindows: string }) {
  const [snapshot, setSnapshot] = useState<CanvasScannerSnapshot | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState("");
  const targetRef = useRef({ asOf: "", enabled: false, key: "", technicalWindows: "" });
  const mountedRef = useRef(true);
  const inFlightRef = useRef(false);
  const loadedKeyRef = useRef("");
  const requestControllerRef = useRef<AbortController | null>(null);
  const retryTimerRef = useRef<number | null>(null);

  const pump = useCallback(() => {
    if (!mountedRef.current || inFlightRef.current || !targetRef.current.enabled) return;
    inFlightRef.current = true;
    void (async () => {
      try {
        while (mountedRef.current) {
          const target = targetRef.current;
          if (!target.enabled || target.key === loadedKeyRef.current) break;
          setLoading(true);
          setError("");
          const requestController = new AbortController();
          requestControllerRef.current = requestController;
          try {
            const corePayload = await api<CanvasScannerSnapshot>(`/api/trading/canvas-scanner${query({
              as_of: target.asOf,
              enrichment_scope: "core",
              lookback_minutes: 15,
              row_limit: 250,
              technical_windows: target.technicalWindows,
            })}`, { signal: requestController.signal, timeoutMs: 120_000 });
            if (!mountedRef.current) return;
            if (!targetRef.current.enabled) return;
            setSnapshot(corePayload);
            setError("");
            loadedKeyRef.current = target.key;
            let refreshStatus = corePayload.meta?.status === "building" || corePayload.meta?.status === "error"
              ? corePayload.meta.status
              : corePayload.meta?.refresh_status === "building" || corePayload.meta?.refresh_status === "error"
                ? corePayload.meta.refresh_status
                : undefined;
            if (corePayload.rows.length && targetRef.current.key === target.key) {
              try {
                const fullPayload = await api<CanvasScannerSnapshot>(`/api/trading/canvas-scanner${query({
                  as_of: target.asOf,
                  enrichment_scope: "full",
                  lookback_minutes: 15,
                  materialize_discovery: materializeDiscovery,
                  row_limit: 250,
                  technical_windows: target.technicalWindows,
                })}`, { signal: requestController.signal, timeoutMs: 180_000 });
                if (!mountedRef.current || !targetRef.current.enabled || targetRef.current.key !== target.key) return;
                setSnapshot(fullPayload);
                refreshStatus = fullPayload.meta?.status === "building" || fullPayload.meta?.status === "error"
                  ? fullPayload.meta.status
                  : fullPayload.meta?.refresh_status === "building" || fullPayload.meta?.refresh_status === "error"
                    ? fullPayload.meta.refresh_status
                    : fullPayload.meta?.qmd_derived_status === "building" || fullPayload.meta?.qmd_derived_status === "error"
                      ? fullPayload.meta.qmd_derived_status
                      : undefined;
              } catch (reason) {
                if (requestController.signal.aborted) throw reason;
                const message = reason instanceof Error ? reason.message : String(reason);
                setSnapshot((current) => current && targetRef.current.key === target.key
                  ? { ...current, errors: { ...current.errors, enrichment: message } }
                  : current);
              }
            }
            if (
              targetRef.current.key === target.key
              && (refreshStatus === "building" || refreshStatus === "error")
            ) {
              const retryDelay = refreshStatus === "building" ? 5_000 : 60_000;
              if (retryTimerRef.current !== null) window.clearTimeout(retryTimerRef.current);
              retryTimerRef.current = window.setTimeout(() => {
                retryTimerRef.current = null;
                loadedKeyRef.current = "";
                pump();
              }, retryDelay);
            }
          } catch (reason) {
            if (!mountedRef.current) return;
            if (requestController.signal.aborted) continue;
            loadedKeyRef.current = target.key;
            setError(reason instanceof Error ? reason.message : String(reason));
            break;
          } finally {
            if (requestControllerRef.current === requestController) requestControllerRef.current = null;
          }
          if (targetRef.current.key === target.key) break;
        }
      } finally {
        inFlightRef.current = false;
        if (!mountedRef.current) return;
        setLoading(false);
        if (targetRef.current.enabled && targetRef.current.key !== loadedKeyRef.current) {
          window.queueMicrotask(pump);
        }
      }
    })();
  }, [materializeDiscovery]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestControllerRef.current?.abort();
      if (retryTimerRef.current !== null) window.clearTimeout(retryTimerRef.current);
    };
  }, []);

  useEffect(() => {
    requestControllerRef.current?.abort();
    const asOf = new Date(cutoffMs).toISOString();
    const key = `${asOf}:${technicalWindows}:${materializeDiscovery ? "materialized" : "page"}`;
    targetRef.current = { asOf, enabled, key, technicalWindows };
    if (retryTimerRef.current !== null) {
      window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    if (!enabled) {
      loadedKeyRef.current = "";
      setSnapshot(null);
      setLoading(false);
      setError("");
      return;
    }
    pump();
  }, [cutoffMs, enabled, materializeDiscovery, pump, technicalWindows]);

  return { error, loading, snapshot };
}

export function useCanvasLiveScannerSnapshot(enabled: boolean) {
  const [snapshot, setSnapshot] = useState<CanvasScannerSnapshot | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled) {
      setSnapshot(null);
      setLoading(false);
      setError("");
      return;
    }
    let cancelled = false;
    let controller: AbortController | null = null;
    let timer: number | null = null;
    const load = async () => {
      if (cancelled || controller) return;
      const request = new AbortController();
      controller = request;
      let retryMs = 15_000;
      try {
        const payload = await api<{ composition_status?: string; core_population_count?: number; market_time?: string; provider?: string; rows?: Record<string, unknown>[]; session_date?: string; signal_rows?: Record<string, unknown>[]; watchlist_runtime?: WatchlistRuntimeResponse }>("/api/real-live-trading/scanner?row_limit=500", { signal: request.signal, timeoutMs: 45_000 });
        if (cancelled || request.signal.aborted) return;
        const rows = payload.rows ?? [];
        const compositionStatus = payload.composition_status === "building" ? "building" : payload.composition_status === "refreshing" ? "refreshing" : "ready";
        retryMs = compositionStatus === "building" ? 1_000 : 15_000;
        const asOfContext = payload.session_date && payload.market_time
          ? dateInTimeZone(payload.session_date, payload.market_time, "America/New_York")
          : new Date();
        setSnapshot({
          as_of: asOfContext.toISOString(),
          errors: {},
          meta: {
            // "refreshing" serves the last complete vectorized population while
            // QMD computes its successor.  Completeness describes the evaluated
            // source universe, not whether a newer projection is in flight or
            // how many ranked rows the Canvas requested for presentation.
            complete_universe: Number(payload.core_population_count ?? 0) > 0,
            row_count: Number(payload.core_population_count ?? rows.length),
            source: payload.provider || "qmd-gateway",
            status: compositionStatus,
          } as ScannerSnapshotMeta,
          rows,
          signal_rows: payload.signal_rows ?? [],
          watchlist_runtime: payload.watchlist_runtime,
        });
        setError("");
        setLoading(compositionStatus === "building" && rows.length === 0);
      } catch (reason) {
        if (!cancelled && !request.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (controller === request) controller = null;
        if (!cancelled) {
          if (retryMs !== 1_000) setLoading(false);
          timer = window.setTimeout(load, retryMs);
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
      controller?.abort();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [enabled]);
  return { error, loading, snapshot };
}
