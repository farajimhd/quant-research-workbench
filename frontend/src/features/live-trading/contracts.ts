import type { ChartCatalogItem, ChartDisplayItem } from "../../app/components/ChartPanel";

export type Scope = {
  processed_root: string;
  raw_root: string;
  spread_root: string;
  start_date: string;
  end_date: string;
};

export type RecordRow = {
  columns: string[];
  exists: boolean;
  group: string;
  key: string;
  path: string;
  session_date: string;
  timeframe: string;
};

export type ReviewPayload = { records: RecordRow[] };

export type CatalogPayload = {
  columns: ChartCatalogItem[];
  displayItems?: ChartDisplayItem[];
};

export type ScannerSnapshot = {
  bar_time: string;
  columns: string[];
  feature_groups: string[];
  reason?: string;
  row_count: number;
  rows: Record<string, unknown>[];
  session_date: string;
  timeframe: string;
};

export type SignalRow = Record<string, unknown>;

export type ScannerSnapshotPayload = { snapshot: ScannerSnapshot };

export type RealLiveAccountKey = string;

export type RealLiveAccountConfig = {
  account_class: string;
  account_id: string;
  account_key: RealLiveAccountKey;
  configured: boolean;
  label: string;
  trading_mode: "paper" | "live" | string;
};

export type RealLiveAccountsPayload = { accounts: RealLiveAccountConfig[] };

export type RealLivePreflightCheck = {
  action?: { hash?: string; label?: string };
  details?: Record<string, unknown>;
  id: string;
  label: string;
  message?: string;
  required?: boolean;
  status: "ready" | "blocked" | string;
};

export type RealLivePreflightPayload = {
  account_id: string;
  account_type: string;
  accounts: RealLiveAccountConfig[];
  broker?: { base_url?: string; name?: string };
  checks: RealLivePreflightCheck[];
  data_provider?: { base_url?: string; name?: string };
  ready: boolean;
  selected_account_keys: string[];
  selected_accounts: RealLiveAccountConfig[];
};

export type RealLiveScannerPayload = {
  gateway_error?: string;
  market_row_count?: number;
  market_rows?: Record<string, unknown>[];
  market_time: string;
  provider: string;
  row_count: number;
  rows: Record<string, unknown>[];
  session_date: string;
  status?: Record<string, unknown>;
};

export type RealLiveProgressStep = {
  detail?: string;
  duration_ms?: number | null;
  id: string;
  label: string;
  status: string;
};

export type RealLiveUniversePreviewPayload = {
  can_query_universe: boolean;
  columns: Record<string, unknown>[];
  errors: Record<string, unknown>[];
  filters: Record<string, unknown>;
  joined_snapshot_row_count?: number;
  massive_snapshot_row_count?: number;
  persistence?: Record<string, unknown>;
  preview_columns: string[];
  progress_steps?: RealLiveProgressStep[];
  pulled_at_utc?: string;
  read_database: string;
  read_url: string;
  reference_columns?: string[];
  reference_row_count?: number;
  reference_rows?: Record<string, unknown>[];
  row_count: number;
  rows: Record<string, unknown>[];
  run_id?: string;
  scanner_row_count?: number;
  session_date?: string;
  snapshot_columns?: string[];
  snapshot_rows?: Record<string, unknown>[];
  startup_enrichment?: Record<string, unknown>;
  tables: Record<string, unknown>[];
  universe_query: string;
  write_database: string;
  write_url: string;
};

export type RealLiveSessionBaselineStatus = {
  enabled?: boolean;
  error?: string;
  errors?: Record<string, unknown>[];
  joined_snapshot_row_count?: number;
  massive_snapshot_row_count?: number;
  pulled_at_utc?: string;
  reference_row_count?: number;
  scanner_row_count?: number;
  scanner_rows_written?: number;
  started_at_utc?: string;
  status?: string;
  trading_session_id?: string;
};

export type RealLiveGatewayStatusPayload = {
  session_baseline?: RealLiveSessionBaselineStatus;
  trading_session_id?: string;
  [key: string]: unknown;
};

export type RealLivePortfolioPayload = {
  as_of?: string;
  account_id: string;
  account_type: string;
  accounts: RealLiveAccountConfig[];
  balances?: Record<string, unknown>[];
  connection?: Record<string, string>;
  errors?: Record<string, unknown>[];
  executions?: Record<string, unknown>[];
  ledger?: Record<string, unknown>;
  orders: Record<string, unknown>[];
  pnl?: Record<string, unknown>[];
  portfolios?: Record<string, unknown>[];
  positions: Record<string, unknown>[];
  selected_account_keys?: string[];
  source?: string;
  summary?: Record<string, unknown>;
};

export function normalizePreflightPayload(value: unknown): RealLivePreflightPayload {
  const payload = objectValue(value);
  const normalizeAccounts = (candidate: unknown): RealLiveAccountConfig[] => recordValues(candidate).map((account) => ({
    account_class: stringField(account, "account_class"),
    account_id: stringField(account, "account_id"),
    account_key: stringField(account, "account_key"),
    configured: account.configured === true,
    label: stringField(account, "label"),
    trading_mode: stringField(account, "trading_mode"),
  })).filter((account) => account.account_key.length > 0);
  const normalizeService = (candidate: unknown) => {
    const service = optionalRecord(candidate);
    return service ? { base_url: stringField(service, "base_url") || undefined, name: stringField(service, "name") || undefined } : undefined;
  };
  return {
    account_id: stringField(payload, "account_id"),
    account_type: stringField(payload, "account_type"),
    accounts: normalizeAccounts(payload.accounts),
    broker: normalizeService(payload.broker),
    checks: recordValues(payload.checks).map((check) => ({
      action: optionalRecord(check.action) ? {
        hash: stringField(objectValue(check.action), "hash") || undefined,
        label: stringField(objectValue(check.action), "label") || undefined,
      } : undefined,
      details: optionalRecord(check.details),
      id: stringField(check, "id") || stringField(check, "name"),
      label: stringField(check, "label") || stringField(check, "name").replaceAll("_", " "),
      message: stringField(check, "message") || stringField(check, "detail") || undefined,
      required: check.required !== false,
      status: stringField(check, "status") || "blocked",
    })).filter((check) => check.id.length > 0),
    data_provider: normalizeService(payload.data_provider),
    ready: payload.ready === true,
    selected_account_keys: stringValues(payload.selected_account_keys),
    selected_accounts: normalizeAccounts(payload.selected_accounts),
  };
}

export function normalizeUniversePreviewPayload(value: unknown): RealLiveUniversePreviewPayload {
  const payload = objectValue(value);
  const optionalNumber = (key: string) => {
    const candidate = payload[key];
    return typeof candidate === "number" && Number.isFinite(candidate) ? candidate : undefined;
  };
  const optionalString = (key: string) => typeof payload[key] === "string" ? payload[key] as string : undefined;
  return {
    can_query_universe: payload.can_query_universe === true,
    columns: recordValues(payload.columns),
    errors: recordValues(payload.errors),
    filters: objectValue(payload.filters),
    joined_snapshot_row_count: optionalNumber("joined_snapshot_row_count"),
    massive_snapshot_row_count: optionalNumber("massive_snapshot_row_count"),
    persistence: optionalRecord(payload.persistence),
    preview_columns: stringValues(payload.preview_columns),
    progress_steps: recordValues(payload.progress_steps).map((step) => ({
      detail: typeof step.detail === "string" ? step.detail : undefined,
      duration_ms: typeof step.duration_ms === "number" && Number.isFinite(step.duration_ms) ? step.duration_ms : null,
      id: typeof step.id === "string" ? step.id : "",
      label: typeof step.label === "string" ? step.label : "",
      status: typeof step.status === "string" ? step.status : "waiting",
    })).filter((step) => step.id.length > 0),
    pulled_at_utc: optionalString("pulled_at_utc"),
    read_database: stringField(payload, "read_database"),
    read_url: stringField(payload, "read_url"),
    reference_columns: stringValues(payload.reference_columns),
    reference_row_count: optionalNumber("reference_row_count"),
    reference_rows: recordValues(payload.reference_rows),
    row_count: numberField(payload, "row_count"),
    rows: recordValues(payload.rows),
    run_id: optionalString("run_id"),
    scanner_row_count: optionalNumber("scanner_row_count"),
    session_date: optionalString("session_date"),
    snapshot_columns: stringValues(payload.snapshot_columns),
    snapshot_rows: recordValues(payload.snapshot_rows),
    startup_enrichment: optionalRecord(payload.startup_enrichment),
    tables: recordValues(payload.tables),
    universe_query: stringField(payload, "universe_query"),
    write_database: stringField(payload, "write_database"),
    write_url: stringField(payload, "write_url"),
  };
}

export function objectValue(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function optionalRecord(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

export function recordValues(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(objectValue).filter((item) => Object.keys(item).length > 0) : [];
}

export function stringValues(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function stringField(row: Record<string, unknown>, key: string) {
  const value = row[key];
  return value === null || value === undefined ? "" : String(value);
}

function numberField(row: Record<string, unknown>, key: string) {
  const value = Number(row[key]);
  return Number.isFinite(value) ? value : 0;
}
