export type ApiError = Error & { status?: number };

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    ...init
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = formatApiErrorDetail(payload);
    } catch {
      detail = response.statusText;
    }
    const error = new Error(detail) as ApiError;
    error.status = response.status;
    throw error;
  }
  return response.json() as Promise<T>;
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
