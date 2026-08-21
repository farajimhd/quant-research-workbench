import { useEffect, useRef } from "react";

type PollingTaskOptions = {
  enabled?: boolean;
  initialDelayMs?: number;
  intervalMs: number;
  onError?: (error: unknown) => void;
  pauseWhenHidden?: boolean;
  repeat?: boolean;
  restartKey?: string | number;
  task: (signal: AbortSignal) => Promise<void>;
};

/**
 * Owns the browser lifecycle for a single polling task. Domain state and
 * response merging deliberately remain with the feature that owns them.
 */
export function usePollingTask({
  enabled = true,
  initialDelayMs,
  intervalMs,
  onError,
  pauseWhenHidden = true,
  repeat = true,
  restartKey = "",
  task,
}: PollingTaskOptions) {
  const taskRef = useRef(task);
  const errorRef = useRef(onError);
  taskRef.current = task;
  errorRef.current = onError;

  useEffect(() => {
    if (!enabled) return;

    let activeController: AbortController | null = null;
    let completed = false;
    let stopped = false;
    let timer: number | null = null;

    const clearTimer = () => {
      if (timer === null) return;
      window.clearTimeout(timer);
      timer = null;
    };
    const schedule = (delayMs: number) => {
      clearTimer();
      if (!stopped && !(completed && !repeat)) timer = window.setTimeout(run, Math.max(0, delayMs));
    };
    const run = async () => {
      timer = null;
      if (stopped) return;
      if (pauseWhenHidden && document.visibilityState === "hidden") return;

      const controller = new AbortController();
      activeController = controller;
      try {
        await taskRef.current(controller.signal);
      } catch (error) {
        if (!controller.signal.aborted && !stopped) errorRef.current?.(error);
      } finally {
        if (activeController === controller) activeController = null;
        if (!repeat) completed = true;
        else if (!stopped) schedule(intervalMs);
      }
    };
    const handleVisibilityChange = () => {
      if (!pauseWhenHidden) return;
      if (document.visibilityState === "hidden") {
        clearTimer();
        activeController?.abort();
      } else if (!activeController && !(completed && !repeat)) {
        schedule(0);
      }
    };

    schedule(initialDelayMs ?? intervalMs);
    if (pauseWhenHidden) document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      stopped = true;
      clearTimer();
      activeController?.abort();
      if (pauseWhenHidden) document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [enabled, initialDelayMs, intervalMs, pauseWhenHidden, repeat, restartKey]);
}
