import { useEffect, useRef, useState } from "react";

import { api } from "../../api/client";
import { usePollingTask } from "../../app/hooks/usePollingTask";
import type { ServiceId } from "../../app/routes";
import type { ServicesStatusPayload, ServiceStatusPayload, WorkloadBudgetPayload } from "./contracts";

export function useServicesStatus(serviceId: ServiceId | null) {
  const [payload, setPayload] = useState<ServicesStatusPayload | null>(null);
  const [selectedPayload, setSelectedPayload] = useState<ServiceStatusPayload | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [workloadBudgets, setWorkloadBudgets] = useState<WorkloadBudgetPayload | null>(null);
  const [workloadBudgetError, setWorkloadBudgetError] = useState("");
  const payloadRef = useRef<ServicesStatusPayload | null>(null);

  useEffect(() => {
    payloadRef.current = payload;
  }, [payload]);

  usePollingTask({
    initialDelayMs: 0,
    intervalMs: 5_000,
    onError: (exc) => setWorkloadBudgetError(errorMessage(exc)),
    task: async (signal) => {
      const next = await api<WorkloadBudgetPayload>("/api/system/workload-budgets", { signal, timeoutMs: 5_000 });
      setWorkloadBudgets(next);
      setWorkloadBudgetError("");
    },
  });

  // Liveness paints first. Database inspection is enrichment and must not
  // hold the operational dashboard behind the slower full-status request.
  usePollingTask({
    initialDelayMs: 0,
    intervalMs: 5_000,
    onError: (exc) => {
      setError(errorMessage(exc));
      setLoading(false);
    },
    task: async (signal) => {
      setError("");
      const next = await api<ServicesStatusPayload>("/api/services/status?include_database_tables=false", { signal, timeoutMs: 15_000 });
      setPayload((current) => mergeServicesPayload(next, current, false));
      setLoading(false);
    },
  });
  usePollingTask({
    initialDelayMs: 0,
    intervalMs: 30_000,
    onError: (exc) => {
      setError(errorMessage(exc));
      setLoading(false);
    },
    task: async (signal) => {
      const next = await api<ServicesStatusPayload>("/api/services/status?include_database_tables=true", { signal, timeoutMs: 30_000 });
      setPayload((current) => mergeServicesPayload(next, current, true));
      setLoading(false);
    },
  });

  useEffect(() => {
    if (!serviceId) {
      setSelectedPayload(null);
      setDetailLoading(false);
      return;
    }
    setDetailLoading(true);
  }, [serviceId]);

  const setDetailError = (exc: unknown) => {
    if (!serviceId) return;
    const fallback = payloadRef.current?.services.find((service) => service.registry.id === serviceId) ?? null;
    setSelectedPayload(fallback ? { ...fallback, errors: { ...fallback.errors, detail: errorMessage(exc) } } : null);
  };

  usePollingTask({
    enabled: Boolean(serviceId),
    initialDelayMs: 5_000,
    intervalMs: 5_000,
    onError: setDetailError,
    restartKey: serviceId ?? "",
    task: async (signal) => {
      if (!serviceId) return;
      const next = await api<ServiceStatusPayload>(`/api/services/${serviceId}/status?include_database_tables=false&include_recent=false&include_logs=false`, { signal, timeoutMs: 10_000 });
      setSelectedPayload((current) => mergeServiceDetailPayload(next, current, false));
    },
  });
  usePollingTask({
    enabled: Boolean(serviceId),
    initialDelayMs: 0,
    intervalMs: 30_000,
    onError: (exc) => {
      setDetailError(exc);
      setDetailLoading(false);
    },
    restartKey: serviceId ?? "",
    task: async (signal) => {
      if (!serviceId) return;
      const next = await api<ServiceStatusPayload>(`/api/services/${serviceId}/status?include_database_tables=true&include_recent=true&include_logs=true`, { signal, timeoutMs: 30_000 });
      setSelectedPayload((current) => mergeServiceDetailPayload(next, current, true));
      setDetailLoading(false);
    },
  });

  return { detailLoading, error, loading, payload, selectedPayload, workloadBudgetError, workloadBudgets };
}

function mergeServiceDetailPayload(next: ServiceStatusPayload, current: ServiceStatusPayload | null, full: boolean): ServiceStatusPayload {
  if (full || current?.registry.id !== next.registry.id) return next;
  const readiness = next.readiness && current.readiness && next.readiness.data.status === "unknown"
    ? { ...next.readiness, data: current.readiness.data }
    : next.readiness;
  return {
    ...next,
    database_tables: current.database_tables ?? next.database_tables,
    logs: current.logs ?? next.logs,
    readiness,
    recent: current.recent ?? next.recent,
  };
}

function mergeServicesPayload(next: ServicesStatusPayload, current: ServicesStatusPayload | null, full: boolean): ServicesStatusPayload {
  if (full || !current) return next;
  const currentById = new Map(current.services.map((service) => [service.registry.id, service]));
  return {
    ...next,
    services: next.services.map((service) => mergeServiceDetailPayload(service, currentById.get(service.registry.id) ?? null, false)),
  };
}

function errorMessage(value: unknown): string {
  return value instanceof Error ? value.message : String(value);
}
