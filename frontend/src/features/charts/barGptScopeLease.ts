import { api } from "../../api/client";

type ScopeLease = {
  deleteTimer: number | null;
  subscribers: number;
};

const CLIENT_INSTANCE_ID = typeof window !== "undefined" && typeof window.crypto?.randomUUID === "function"
  ? window.crypto.randomUUID()
  : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
const DELETE_GRACE_MS = 500;
const scopeLeases = new Map<string, ScopeLease>();

export function canvasBarGptScopeId(canvasId: string, instanceId: string) {
  return `canvas:${canvasId}:${CLIENT_INSTANCE_ID}:${instanceId}`;
}

export function acquireBarGptScope(scopeId: string) {
  const lease = scopeLeases.get(scopeId) ?? { deleteTimer: null, subscribers: 0 };
  if (lease.deleteTimer !== null) window.clearTimeout(lease.deleteTimer);
  lease.deleteTimer = null;
  lease.subscribers += 1;
  scopeLeases.set(scopeId, lease);
  let released = false;

  return () => {
    if (released) return;
    released = true;
    const current = scopeLeases.get(scopeId);
    if (!current) return;
    current.subscribers = Math.max(0, current.subscribers - 1);
    if (current.subscribers > 0) return;
    current.deleteTimer = window.setTimeout(() => {
      const latest = scopeLeases.get(scopeId);
      if (!latest || latest.subscribers > 0) return;
      scopeLeases.delete(scopeId);
      void api(`/api/bar-gpt/scopes/${encodeURIComponent(scopeId)}`, {
        method: "DELETE",
        timeoutMs: 1_000,
      }).catch(() => undefined);
    }, DELETE_GRACE_MS);
  };
}
