from __future__ import annotations

from contextvars import ContextVar
import re
from uuid import uuid4


CORRELATION_HEADER = "X-Correlation-ID"
CAUSATION_HEADER = "X-Causation-ID"
_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_correlation_id: ContextVar[str] = ContextVar("request_correlation_id", default="")
_causation_id: ContextVar[str] = ContextVar("request_causation_id", default="")


def normalize_request_identity(value: str | None) -> str:
    candidate = str(value or "").strip()
    return candidate if _IDENTITY_PATTERN.fullmatch(candidate) else ""


def begin_request_context(
    correlation_id: str | None,
    causation_id: str | None,
) -> tuple[object, object, str, str]:
    correlation = normalize_request_identity(correlation_id) or str(uuid4())
    causation = normalize_request_identity(causation_id) or correlation
    correlation_token = _correlation_id.set(correlation)
    causation_token = _causation_id.set(causation)
    return correlation_token, causation_token, correlation, causation


def end_request_context(correlation_token: object, causation_token: object) -> None:
    _causation_id.reset(causation_token)  # type: ignore[arg-type]
    _correlation_id.reset(correlation_token)  # type: ignore[arg-type]


def current_request_identity() -> dict[str, str]:
    correlation = _correlation_id.get()
    causation = _causation_id.get()
    result: dict[str, str] = {}
    if correlation:
        result["correlation_id"] = correlation
    if causation:
        result["causation_id"] = causation
    return result


def current_request_headers() -> dict[str, str]:
    identity = current_request_identity()
    result: dict[str, str] = {}
    if identity.get("correlation_id"):
        result[CORRELATION_HEADER] = identity["correlation_id"]
    if identity.get("causation_id"):
        result[CAUSATION_HEADER] = identity["causation_id"]
    return result


def current_request_query() -> dict[str, str]:
    return current_request_identity()
