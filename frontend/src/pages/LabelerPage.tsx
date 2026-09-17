import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ensureLabelerCanvas, type CanvasRecord } from "../app/canvasWorkspace";
import { LoadingState } from "../app/components/LoadingState";
import { CanvasWorkspaceSurface } from "./CanvasConfigurationPage";

export function LabelerPage() {
  const [canvas, setCanvas] = useState<CanvasRecord | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    api<{ rows: { container_id: string; status: string }[] }>("/api/registries/containers", { signal: controller.signal, timeoutMs: 10000 })
      .then(registry => {
        if (controller.signal.aborted) return;
        if (!registry.rows.some(row => row.container_id === "labeler" && row.status === "implemented")) throw new Error("The running backend has not loaded the Labeler container update. Restart Backend after active trading runs are safely stopped, then retry.");
        setCanvas(ensureLabelerCanvas());
      })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [attempt]);
  if (error) return <div className="canvas-inline-error" role="alert">{error}<button className="button secondary compact" onClick={() => setAttempt(value => value + 1)}>Retry Labeler</button></div>;
  if (!canvas) return <LoadingState fill label="Opening Labeler Canvas" />;
  return <CanvasWorkspaceSurface canvasId={canvas.id} manager={false} />;
}
