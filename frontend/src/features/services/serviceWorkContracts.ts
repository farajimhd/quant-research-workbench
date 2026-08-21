export type ServiceWorkRow = {
  detail: string;
  kind: string;
  lastAt: string;
  lastAtMs?: number;
  name: string;
  progress: string;
  reportKind: "live" | "setup";
  rows: string;
  schedule: string;
  status: string;
};

export type ServiceWorkGroup = {
  activeCount: number;
  completedCount: number;
  description: string;
  id: string;
  lastAt: string;
  rows: ServiceWorkRow[];
  status: string;
  title: string;
  warningCount: number;
};

export type ServiceResponsibilitySpec = {
  description: string;
  id: string;
  match: RegExp[];
  title: string;
};

export type WorkPlanSummaryMetric = {
  label: string;
  title?: string;
  tone?: string;
  value: string;
};
