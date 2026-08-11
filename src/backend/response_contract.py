from __future__ import annotations

from typing import Any

from src.request_context import current_request_identity


ERROR_SCHEMA_VERSION = 1
RESPONSE_ENVELOPE_HEADER = "X-Response-Envelope"
RESPONSE_ENVELOPE_VERSION = "1"


def success_response_envelope(
    data: Any,
    *,
    correlation_id: str,
    causation_id: str,
) -> dict[str, Any]:
    """Wrap one successful payload without changing the payload itself."""
    record = data if isinstance(data, dict) else {}
    warnings = record.get("warnings") if isinstance(record.get("warnings"), list) else []
    complete = bool(record.get("complete", True))
    return {
        "schema_version": 1,
        "complete": complete,
        "data": data,
        "warnings": warnings,
        "meta": {
            "correlation_id": correlation_id,
            "causation_id": causation_id,
        },
    }


def error_response_envelope(
    *,
    status_code: int,
    detail: Any,
    code: str | None = None,
) -> dict[str, Any]:
    """Return one typed error contract while retaining FastAPI ``detail``."""
    identity = current_request_identity()
    detail_record = detail if isinstance(detail, dict) else {}
    message = _error_message(detail)
    resolved_code = str(
        code
        or detail_record.get("code")
        or _status_code_name(status_code)
    )
    retryable = bool(
        detail_record.get("retryable")
        if "retryable" in detail_record
        else status_code in {408, 425, 429, 502, 503, 504}
    )
    error = {
        "code": resolved_code,
        "message": message,
        "retryable": retryable,
        "status": int(status_code),
        "correlation_id": identity.get("correlation_id", ""),
        "causation_id": identity.get("causation_id", ""),
    }
    if detail_record:
        error["details"] = dict(detail_record)
    return {
        "schema_version": ERROR_SCHEMA_VERSION,
        "complete": False,
        "data": None,
        "warnings": [],
        "detail": detail,
        "error": error,
    }


def _error_message(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        for key in ("message", "detail", "error", "msg"):
            value = detail.get(key)
            if isinstance(value, str) and value:
                return value
    if isinstance(detail, list):
        messages = [
            str(row.get("msg") or row.get("message") or row)
            if isinstance(row, dict)
            else str(row)
            for row in detail
        ]
        return "; ".join(value for value in messages if value)
    return str(detail or "Request failed")


def _status_code_name(status_code: int) -> str:
    return {
        400: "invalid_request",
        401: "authentication_required",
        403: "authority_denied",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        410: "expired",
        422: "validation_failed",
        429: "capacity_exhausted",
        500: "internal_error",
        502: "upstream_error",
        503: "temporarily_unavailable",
        504: "upstream_timeout",
    }.get(int(status_code), f"http_{int(status_code)}")
