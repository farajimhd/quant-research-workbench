import { Activity } from "lucide-react";
import { useEffect, useState } from "react";

import { api, query } from "../../api/client";
import { marketSessionDate } from "../canvas/chartData";
import type { CanonicalTradingPreview, LivePerformanceState, PerformanceMetric, PerformanceSnapshot, PerformanceSnapshotResponse } from "../canvas/contracts";
import { finiteNumber } from "../canvas/numbers";

const LIVE_ACCOUNT_KEYS_STORAGE_KEY = "quant-research-workbench.real-live-trading.account-keys";
const LIVE_PERFORMANCE_STORAGE_KEY = "quant-research-workbench.trading.performance-v3";

export function readLiveAccountKeys(): string[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_ACCOUNT_KEYS_STORAGE_KEY) || "null");
    if (Array.isArray(parsed)) return parsed.map((item) => String(item)).filter(Boolean);
  } catch {
    // A malformed preference must not prevent the trading workspace from loading.
  }
  return ["paper"];
}

function accountSignature(accountKeys: string[], mode: string) {
  return `${mode}:${[...accountKeys].map(String).filter(Boolean).sort().join(",")}`;
}

function readCachedPerformance(accountKeys: string[], mode: string): PerformanceSnapshot | null {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_PERFORMANCE_STORAGE_KEY) || "null") as { account_signature?: string; data?: PerformanceSnapshot } | null;
    if (parsed?.account_signature === accountSignature(accountKeys, mode) && parsed.data?.as_of) return parsed.data;
  } catch {
    // Cached presentation state is optional; canonical trading state remains authoritative.
  }
  return null;
}

function writeCachedPerformance(accountKeys: string[], mode: string, data: PerformanceSnapshot) {
  try {
    window.localStorage.setItem(LIVE_PERFORMANCE_STORAGE_KEY, JSON.stringify({ account_signature: accountSignature(accountKeys, mode), data }));
  } catch {
    // Storage restrictions must not interrupt live refreshes.
  }
}

function legacyMetrics(snapshot: PerformanceSnapshot): PerformanceMetric[] {
  return [
    metric("net_pnl_today", "Net P&L today", snapshot.net_pnl_today, "money", "signed", "Today's realized net P&L plus current unrealized P&L."),
    metric("max_unrealized_pnl", "Peak unrealized", snapshot.max_unrealized_pnl, "money", "favorable_high", "Sum of each open position's maximum favorable unrealized P&L observed during its current lifecycle."),
    metric("open_position_count", "Open positions", snapshot.open_position_count, "count", "neutral", "Current non-zero positions across the selected accounts."),
    metric("realized_pnl_today", "Realized today", snapshot.realized_pnl_today, "money", "signed", "Net P&L from flat-to-flat episodes closed on the New York market date."),
    metric("available_cash", "Available cash", snapshot.available_cash, "money", "neutral", "Broker available funds, with total cash used only when unavailable."),
  ];
}

function metric(id: string, label: string, value: string | number | null, format: string, interpretation: string, description: string): PerformanceMetric {
  return { available: value !== null && value !== undefined, description, format, id, interpretation, label, value };
}

export function normalizePerformanceSnapshot(payload: CanonicalTradingPreview): PerformanceSnapshot | null {
  if (payload.performance_snapshot) return { ...payload.performance_snapshot, source: "performance_snapshot" };
  const metrics = payload.portfolio?.metrics;
  if (!metrics || !payload.as_of) return null;
  const sessionDate = marketSessionDate(payload.as_of);
  const realizedToday = (payload.performance_journal?.episodes || []).reduce((total, row) => {
    const closedAt = String(row.closed_at || "");
    return marketSessionDate(closedAt) === sessionDate ? total + finiteNumber(row.net_pnl) : total;
  }, 0);
  const unrealized = finiteNumber(metrics.unrealized_pnl);
  const maxUnrealized = payload.positions.reduce(
    (total, row) => total + Math.max(0, finiteNumber(row.max_unrealized_pnl ?? row.unrealized_pnl)),
    0,
  );
  const hasAvailableFunds = payload.account_values.some((row) => String(row.key || "").toLowerCase() === "availablefunds" && String(row.segment || "base").toLowerCase() === "base")
    || payload.ledger.some((row) => row.is_base && row.values && typeof row.values === "object" && Object.keys(row.values as Record<string, unknown>).some((key) => key.toLowerCase() === "availablefunds"));
  return {
    as_of: payload.as_of,
    available_cash: hasAvailableFunds ? finiteNumber(metrics.available_funds) : finiteNumber(metrics.total_cash),
    available_cash_basis: hasAvailableFunds ? "available_funds" : "total_cash",
    net_pnl_today: realizedToday + unrealized,
    max_unrealized_pnl: maxUnrealized,
    max_unrealized_pnl_basis: "sum_of_open_position_maxima",
    open_position_count: payload.positions.filter((row) => finiteNumber(row.quantity) !== 0).length,
    realized_pnl_today: realizedToday,
    session_date: sessionDate,
    source: "canonical_state_v2",
    unrealized_pnl: unrealized,
  };
}

export function useTradingPerformance({ enabled = true, requestedAccountKeys, mode = "paper" }: { enabled?: boolean; requestedAccountKeys?: string[]; mode?: string } = {}): LivePerformanceState {
  const requestedSignature = requestedAccountKeys?.join(",") ?? "";
  const [accountKeys, setAccountKeys] = useState(() => requestedAccountKeys?.length ? requestedAccountKeys : readLiveAccountKeys());
  const [state, setState] = useState<LivePerformanceState>(() => {
    const cached = readCachedPerformance(accountKeys, mode);
    return { data: cached, status: cached ? "stale" : "loading" };
  });

  useEffect(() => {
    if (!enabled) return;
    if (requestedAccountKeys?.length) {
      setAccountKeys(requestedAccountKeys);
      return;
    }
    const syncAccounts = (event: StorageEvent) => {
      if (event.key === LIVE_ACCOUNT_KEYS_STORAGE_KEY) setAccountKeys(readLiveAccountKeys());
    };
    window.addEventListener("storage", syncAccounts);
    return () => window.removeEventListener("storage", syncAccounts);
  }, [enabled, requestedSignature]);

  useEffect(() => {
    if (!enabled) {
      setState({ data: null, status: "loading" });
      return;
    }
    let cancelled = false;
    let controller: AbortController | null = null;
    let timer: number | null = null;
    const cached = readCachedPerformance(accountKeys, mode);
    setState({ data: cached, status: cached ? "stale" : "loading" });
    const schedule = () => { if (!cancelled) timer = window.setTimeout(load, 15_000); };
    const load = async () => {
      if (cancelled || controller) return;
      if (document.visibilityState === "hidden") { schedule(); return; }
      const request = new AbortController();
      controller = request;
      const parameters = { account_keys: accountKeys.join(","), account_type: accountKeys[0] || "paper", mode };
      try {
        let performance: PerformanceSnapshot;
        let stale = false;
        try {
          const compact = await api<PerformanceSnapshotResponse>(`/api/trading/performance-snapshot${query(parameters)}`, { signal: request.signal, timeoutMs: 45_000 });
          performance = { ...compact.performance_snapshot, source: "performance_snapshot" };
          stale = compact.stale;
        } catch (reason) {
          if ((reason as { status?: number })?.status !== 404) throw reason;
          const payload = await api<CanonicalTradingPreview>(`/api/trading/state${query(parameters)}`, { signal: request.signal, timeoutMs: 45_000 });
          const normalized = normalizePerformanceSnapshot(payload);
          if (!normalized) throw new Error("Canonical performance evidence is unavailable");
          performance = normalized;
          stale = payload.stale;
        }
        if (!cancelled) {
          writeCachedPerformance(accountKeys, mode, performance);
          setState({ data: performance, status: stale ? "stale" : "ready" });
        }
      } catch {
        if (!cancelled && !request.signal.aborted) setState((current) => ({ data: current.data, status: "error" }));
      } finally {
        if (controller === request) controller = null;
        schedule();
      }
    };
    void load();
    const refreshVisible = () => {
      if (document.visibilityState !== "visible" || controller) return;
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      void load();
    };
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      cancelled = true;
      controller?.abort();
      if (timer !== null) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [accountKeys.join(","), enabled, mode]);

  return state;
}

export function TradingPerformanceStrip({ state }: { state: LivePerformanceState }) {
  const snapshot = state.data;
  const rows = (snapshot?.metrics?.length ? snapshot.metrics : snapshot ? legacyMetrics(snapshot) : []).slice(0, 5);
  while (rows.length < 5) rows.push(metric(`loading-${rows.length}`, "Loading", null, "ratio", "neutral", "Waiting for the canonical trading snapshot."));
  const freshness = snapshot?.as_of ? new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit", timeZone: "America/New_York" }).format(new Date(snapshot.as_of)) : "";
  return <section aria-label="Trading performance" className="canvas-performance-strip" data-status={state.status} title={freshness ? `Canonical trading snapshot as of ${freshness} ET` : "Canonical trading snapshot is loading"}>
    <div className="canvas-performance-title"><Activity aria-hidden="true" size={13} /><span>Performance</span><i aria-hidden="true" /></div>
    {rows.map((row) => <div className="canvas-performance-metric" data-tone={metricTone(row)} key={row.id} title={row.description}>
      <span>{row.label}</span>
      <strong>{formatMetric(row)}</strong>
    </div>)}
  </section>;
}

function metricTone(metricValue: PerformanceMetric) {
  const value = Number(metricValue.value);
  if (!metricValue.available || !Number.isFinite(value) || value === 0) return "neutral";
  if (metricValue.interpretation === "signed") return value > 0 ? "positive" : "negative";
  if (metricValue.interpretation === "favorable_high") return value > 0 ? "positive" : "neutral";
  if (metricValue.interpretation === "adverse_high") return value > 0 ? "negative" : "neutral";
  return "neutral";
}

function formatMetric(metricValue: PerformanceMetric) {
  const value = Number(metricValue.value);
  if (!metricValue.available || !Number.isFinite(value)) return "—";
  if (metricValue.format === "money") return `${value > 0 && metricValue.interpretation === "signed" ? "+" : ""}${new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: Math.abs(value) >= 100_000 ? "compact" : "standard", maximumFractionDigits: Math.abs(value) >= 1000 ? 0 : 2 }).format(value)}`;
  if (metricValue.format === "percent") return new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 1 }).format(value);
  if (metricValue.format === "count") return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
}
