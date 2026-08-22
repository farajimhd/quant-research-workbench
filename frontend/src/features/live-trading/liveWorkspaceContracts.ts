import type { BackendTableQuery } from "../../app/components/DataTable";
import type { WorkspaceWindowId, WorkspaceWindowLayout } from "../../app/components/WorkspaceCanvas";

export type ScannerQueryGroup = { id: string; name: string; query: BackendTableQuery };

export type ChartWindow = {
  id: WorkspaceWindowId;
  row: Record<string, unknown>;
  ticker: string;
};

export type SavedCanvasLayout = {
  chartWindows: ChartWindow[];
  layouts: Record<WorkspaceWindowId, WorkspaceWindowLayout>;
  layoutVersion?: number;
  name: string;
  windows: WorkspaceWindowId[];
};

export type LiveClockMode = "idle" | "loading_data" | "ready" | "seeking" | "running" | "paused" | "complete";
export type DecisionState = "approved" | "skipped" | "watching";
