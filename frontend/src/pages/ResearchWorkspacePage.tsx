import { Microscope, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { CanvasWorkspaceSurface, type ApprovedCanvasProfile } from "./CanvasConfigurationPage";

export function ResearchWorkspacePage() {
  const [approved, setApproved] = useState<ApprovedCanvasProfile | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api<ApprovedCanvasProfile>("/api/trading/configuration/canvas-profile", { timeoutMs: 20_000 })
      .then((payload) => {
        if (cancelled) return;
        if (!payload.available || !payload.profile) throw new Error("Publish an approved configuration with a Canvas profile before opening Research.");
        setApproved(payload);
      })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { cancelled = true; };
  }, []);

  if (error) return <div className="canvas-config-page canvas-focus-page"><div aria-live="assertive" className="canvas-inline-error replay-runtime-error"><TriangleAlert aria-hidden="true" size={16} /><div><strong>Research unavailable</strong><span>The published Canvas profile could not be resolved: {error}</span></div></div></div>;
  if (!approved) return <div className="canvas-config-page canvas-focus-page"><div className="canvas-empty-state"><strong>Loading Research workspace</strong><span>Resolving the published Canvas default and your separate Research overlay.</span></div></div>;
  return <CanvasWorkspaceSurface
    approvedCanvas={approved}
    canvasId="main"
    manager={false}
    modeControls={<div className="historical-canvas-run-state"><strong><Microscope size={14} /> Research</strong><span>Published default / private overlay</span></div>}
    runtimeMode="research"
    runtimeWorkspaceId="main"
  />;
}
