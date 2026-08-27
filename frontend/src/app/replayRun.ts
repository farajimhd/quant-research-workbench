import { useEffect, useRef } from "react";

import { apiWebSocketUrl } from "../api/client";
import type { CanvasRegistry } from "./canvasWorkspace";

export type CanvasReplayRun = {
  account_ids: string[];
  canvas_revision: string;
  canvas_profile: CanvasRegistry;
  checkpoint?: {
    cursor: string;
    event_time: string | null;
    interval_events: number;
    processed_events: number;
    resume_supported: boolean;
    status: "available" | "pending";
    updated_at: string | null;
  };
  configuration_content_hash?: string;
  configuration_revision_id?: string;
  current_time: string;
  error: string;
  execution_mode?: "manual" | "strategy";
  mode?: "backtest" | "backtest_debug" | "replay";
  navigation_action?: {
    category: string;
    entity_id: string;
    entity_type: string;
    event_time: string;
    kind: "order_management" | "strategy_decision" | "watch_started";
    label: string;
    sequence: number;
    ticker: string;
  } | null;
  navigation_search?: {
    active: boolean;
    phase: "idle" | "preparing" | "scanning";
    scanned_events: number;
    start_event_time: string | null;
    started_at: string | null;
    targets: string[];
  };
  progress: number;
  run_id: string;
  session_date: string;
  session_end: string;
  session_start: string;
  requested_start: string;
  speed: number;
  status: string;
  runtime_ready?: boolean;
  strategy_debug_sources?: {
    signal_stream_ids: string[];
    watchlist_ids: string[];
  };
  updated_at: string;
  warmup_events?: number;
  processed_events?: number;
};

const REPLAY_UI_UPDATE_MS = 2_000;
const TERMINAL_REPLAY_STATUSES = new Set(["completed", "failed", "stopped"]);

export function isTerminalReplayStatus(status: string) {
  return TERMINAL_REPLAY_STATUSES.has(status);
}

export function latestReplayRun(current: CanvasReplayRun | null, update: CanvasReplayRun) {
  if (!current || current.run_id !== update.run_id) return update;
  return Date.parse(update.updated_at) >= Date.parse(current.updated_at) ? update : current;
}

export function useReplayRunEvents(
  runId: string | undefined,
  onUpdate: (update: CanvasReplayRun) => void,
  onError: (message: string) => void,
) {
  const onUpdateRef = useRef(onUpdate);
  const onErrorRef = useRef(onError);
  onUpdateRef.current = onUpdate;
  onErrorRef.current = onError;

  useEffect(() => {
    if (!runId) return;
    const streamUrl = apiWebSocketUrl(`/api/trading/replay/runs/${encodeURIComponent(runId)}/events`);
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let reconnectAttempt = 0;
    let disposed = false;
    let terminalReceived = false;
    let pending: CanvasReplayRun | null = null;
    let updateTimer: number | null = null;
    const flush = () => {
      updateTimer = null;
      if (!pending) return;
      const update = pending;
      pending = null;
      onUpdateRef.current(update);
    };
    const connect = () => {
      if (disposed || terminalReceived) return;
      socket = new WebSocket(streamUrl);
      socket.onopen = () => {
        reconnectAttempt = 0;
      };
      socket.onmessage = (event) => {
        try {
          const update = JSON.parse(String(event.data)) as CanvasReplayRun;
          if (isTerminalReplayStatus(update.status)) {
            terminalReceived = true;
            if (updateTimer !== null) window.clearTimeout(updateTimer);
            updateTimer = null;
            pending = null;
            onUpdateRef.current(update);
            socket?.close();
            return;
          }
          pending = latestReplayRun(pending, update);
          if (updateTimer === null) updateTimer = window.setTimeout(flush, REPLAY_UI_UPDATE_MS);
        } catch {
          onErrorRef.current("Replay returned an invalid runtime update.");
        }
      };
      socket.onclose = () => {
        socket = null;
        if (disposed || terminalReceived) return;
        reconnectAttempt += 1;
        const delay = Math.min(5_000, 500 * 2 ** Math.min(reconnectAttempt - 1, 4));
        reconnectTimer = window.setTimeout(connect, delay);
      };
      socket.onerror = () => socket?.close();
    };
    connect();
    return () => {
      if (updateTimer !== null) window.clearTimeout(updateTimer);
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      disposed = true;
      socket?.close();
    };
  }, [runId]);
}
