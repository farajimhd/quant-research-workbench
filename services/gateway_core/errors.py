from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from services.gateway_core.types import ErrorSummary


RETRYABLE_CATEGORIES = {"dependency", "provider_rate_limit", "provider_transient", "artifact_io", "resource_pressure"}
FAIL_FAST_CATEGORIES = {"schema_contract", "data_integrity"}


@dataclass(frozen=True, slots=True)
class GatewayErrorRecord:
    error_id: str
    service: str
    phase: str
    task: str
    category: str
    severity: str
    retryable: bool
    status: str
    message: str
    first_seen_utc: str
    last_seen_utc: str
    provider: str = ""
    table: str = ""
    item_id: str = ""
    attempt: int = 0
    max_attempts: int = 0
    next_retry_at_utc: str = ""
    resolved_at_utc: str = ""
    safe_detail: str = ""
    log_ref: str = ""

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_exception(exc: BaseException | str, *, service: str, phase: str = "", task: str = "") -> GatewayErrorRecord:
    message = str(exc)
    lowered = message.lower()
    category = "operator_action_required"
    retryable = False
    severity = "error"
    status = "active"
    if "429" in lowered or "rate limit" in lowered or "too many requests" in lowered:
        category = "provider_rate_limit"
        retryable = True
        status = "retrying"
    elif "timeout" in lowered or "connection reset" in lowered or "temporar" in lowered:
        category = "provider_transient"
        retryable = True
        status = "retrying"
    elif "schema" in lowered or "syntax error" in lowered or "missing column" in lowered:
        category = "schema_contract"
        severity = "critical"
    elif "clickhouse" in lowered or "database" in lowered or "db::exception" in lowered:
        category = "database_write"
        retryable = any(token in lowered for token in ("timeout", "500", "503", "connection"))
        status = "retrying" if retryable else "active"
    elif "not found" in lowered or "404" in lowered:
        category = "provider_not_found_required"
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return GatewayErrorRecord(
        error_id=stable_error_id(service, phase, task, category, message),
        service=service,
        phase=phase,
        task=task,
        category=category,
        severity=severity,
        retryable=retryable,
        status=status,
        message=safe_message(message),
        first_seen_utc=now,
        last_seen_utc=now,
    )


def error_summary_from_metrics(metrics: dict[str, Any], *, service: str) -> dict[str, Any]:
    summary = ErrorSummary()
    last_error = str(metrics.get("last_error") or "").strip()
    phase = str(metrics.get("current_phase") or "").lower()
    failed_phase = phase == "failed"
    provider_cooldown = float(metrics.get("sec_request_cooldown_remaining_seconds") or metrics.get("provider_cooldown_remaining_seconds") or 0.0)
    provider_cooldown_reason = str(metrics.get("sec_request_cooldown_reason") or metrics.get("provider_cooldown_reason") or "").lower()
    poll_failures = int(metrics.get("poll_failures") or 0)
    failed_rows = int(metrics.get("failed_rows") or metrics.get("failed_filings") or 0)
    if last_error:
        record = classify_exception(last_error, service=service, phase=str(metrics.get("current_phase") or ""), task="runtime")
        if failed_phase:
            summary.active_critical_count = 1
            record = GatewayErrorRecord(**{**record.public_dict(), "severity": "critical"})
            summary.latest_active_errors.append(record.public_dict())
        elif record.retryable and (provider_cooldown > 0 or phase == "provider_cooldown"):
            summary.retrying_count = 1
            if provider_cooldown_reason in {"sec_http_403", "sec_http_429"}:
                summary.active_error_count = 1
            summary.latest_active_errors.append(record.public_dict())
        elif record.retryable:
            summary.resolved_this_run_count = 1
            summary.latest_resolved_errors.append(
                GatewayErrorRecord(
                    **{
                        **record.public_dict(),
                        "status": "resolved",
                        "resolved_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                    }
                ).public_dict()
            )
        elif poll_failures or failed_rows:
            summary.active_error_count = 1
            summary.latest_active_errors.append(record.public_dict())
        else:
            summary.active_warning_count = 1
            summary.latest_active_errors.append(record.public_dict())
    return asdict(summary)


def stable_error_id(service: str, phase: str, task: str, category: str, message: str) -> str:
    digest = hashlib.sha1(f"{service}|{phase}|{task}|{category}|{message[:300]}".encode("utf-8")).hexdigest()[:12]
    return f"{service}_{category}_{digest}"


def safe_message(message: str, *, limit: int = 500) -> str:
    text = str(message or "").replace("\r", " ").replace("\n", " ").strip()
    for marker in ("apiKey=", "api_key=", "password=", "token="):
        index = text.lower().find(marker.lower())
        if index >= 0:
            end = text.find(" ", index)
            if end < 0:
                end = len(text)
            text = text[: index + len(marker)] + "<redacted>" + text[end:]
    return text[:limit]
