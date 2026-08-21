import type { ServiceId } from "../../app/routes";

export type ServiceRegistry = {
  base_url: string;
  description: string;
  id: ServiceId;
  kind: string;
  label: string;
};

export type ServiceStatusTone = "active" | "error" | "idle" | "ok" | "waiting" | "warn";

export type ServiceReadinessDimension = {
  evidence: string;
  source: string;
  status: string;
};

export type ServiceStatusPayload = {
  checked_at_utc: string;
  current_operation: Record<string, unknown>;
  database_tables?: ServiceDatabaseTablePayload;
  errors: Record<string, unknown>;
  header: Record<string, unknown>;
  health: Record<string, unknown>;
  logs?: ServiceLogPayload;
  metrics: Record<string, unknown>;
  operations?: Record<string, unknown>;
  online: boolean;
  readiness?: {
    schema_version: number;
    liveness: ServiceReadinessDimension;
    dependencies: ServiceReadinessDimension;
    data: ServiceReadinessDimension;
    execution: ServiceReadinessDimension;
  };
  recent: unknown;
  registry: ServiceRegistry;
  snapshot: Record<string, unknown>;
  status: string;
};

export type ServicesStatusPayload = {
  checked_at_utc: string;
  services: ServiceStatusPayload[];
};

export type WorkloadBudgetPayload = {
  lanes: Record<string, {
    active: number;
    available: number;
    completed: number;
    limit: number;
    rejected: number;
    total_wait_seconds: number;
  }>;
  schema_version: number;
  wait_timeout_seconds: number;
};

export type ServiceDatabaseTablePayload = {
  error?: string;
  rows?: ServiceDatabaseTableRow[];
};

export type ServiceDatabaseTableRow = {
  bytes?: string;
  database?: string;
  detail?: string;
  engine?: string;
  latest_update?: string;
  role?: string;
  rows?: string;
  rows_last_month?: string;
  rows_last_week?: string;
  rows_today?: string;
  status?: string;
  table?: string;
  time_column?: string;
  [key: string]: string | undefined;
};

export type ServiceTablePreviewPayload = {
  database: string;
  limit: number;
  order_by?: string;
  rows: Record<string, unknown>[];
  table: string;
};

export type ServiceLogPayload = {
  error?: string;
  path?: string;
  rows?: ServiceRuntimeLogRow[];
};

export type ServiceRuntimeLogRow = {
  detail?: string;
  event?: string;
  fields?: Record<string, unknown>;
  level?: string;
  line?: number;
  source?: string;
  title?: string;
  ts_utc?: string;
};
