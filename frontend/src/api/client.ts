export type ApiError = Error & { status?: number };
export type ApiRequestInit = RequestInit & { timeoutMs?: number };

export async function api<T>(path: string, init?: ApiRequestInit): Promise<T> {
  const { timeoutMs, ...requestInit } = init ?? {};
  const controller = timeoutMs ? new AbortController() : null;
  const timeout = controller ? window.setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const response = await fetch(path, {
      headers: {
        "Content-Type": "application/json",
        ...(requestInit.headers ?? {})
      },
      ...requestInit,
      signal: requestInit.signal ?? controller?.signal
    });
    const text = await response.text();
    const payload = parseJsonPayload(text);
    if (!response.ok) {
      const detail = payload === undefined ? response.statusText : formatApiErrorDetail(payload);
      const error = new Error(detail) as ApiError;
      error.status = response.status;
      throw error;
    }
    if (payload === undefined) {
      throw new Error(formatNonJsonApiResponse(path, text));
    }
    return payload as T;
  } finally {
    if (timeout !== null) window.clearTimeout(timeout);
  }
}

function parseJsonPayload(text: string): unknown | undefined {
  if (!text.trim()) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function formatNonJsonApiResponse(path: string, text: string): string {
  const preview = text.trim().slice(0, 80);
  if (/^<!doctype\s+html/i.test(preview) || /^<html[\s>]/i.test(preview)) {
    return `API route ${path} returned the frontend HTML page instead of JSON. Restart the backend and refresh the page; if it continues, the API route is missing.`;
  }
  return `API route ${path} returned a non-JSON response${preview ? `: ${preview}` : "."}`;
}

function formatApiErrorDetail(payload: unknown): string {
  if (typeof payload === "string") return payload;
  if (!payload || typeof payload !== "object") return String(payload);
  const detail = "detail" in payload ? (payload as { detail?: unknown }).detail : payload;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map(formatValidationIssue).filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  if (detail && typeof detail === "object") {
    if ("msg" in detail && typeof (detail as { msg?: unknown }).msg === "string") {
      return String((detail as { msg: string }).msg);
    }
    return JSON.stringify(detail);
  }
  return JSON.stringify(payload);
}

function formatValidationIssue(issue: unknown): string {
  if (!issue || typeof issue !== "object") return String(issue ?? "");
  const record = issue as { loc?: unknown; msg?: unknown; type?: unknown };
  const location = Array.isArray(record.loc) ? record.loc.filter((part) => part !== "query" && part !== "body").join(".") : "";
  const message = typeof record.msg === "string" ? record.msg : typeof record.type === "string" ? record.type : JSON.stringify(issue);
  return location ? `${location}: ${message}` : message;
}

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  });
  const text = search.toString();
  return text ? `?${text}` : "";
}
