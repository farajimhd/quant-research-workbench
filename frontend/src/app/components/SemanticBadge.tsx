import type { ReactNode } from "react";

export type SemanticTone = "accent" | "danger" | "info" | "muted" | "neutral" | "success" | "warning";

export function toneForStatus(status: string | undefined): SemanticTone {
  const normalized = String(status ?? "").toLowerCase();
  if (["complete", "ready", "success", "done"].includes(normalized)) return "success";
  if (["failed", "error", "missing", "missing_raw"].includes(normalized)) return "danger";
  if (["running", "building", "processing", "in_progress"].includes(normalized)) return "info";
  if (["skipped", "partial", "warning", "canceling", "cancelled"].includes(normalized)) return "warning";
  if (["queued", "closed"].includes(normalized)) return "muted";
  return "neutral";
}

export function SemanticBadge({ tone, children }: { tone: SemanticTone; children: ReactNode }) {
  return (
    <span className="semantic-badge" data-tone={tone}>
      {children}
    </span>
  );
}

