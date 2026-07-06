"""Provider call result and retry helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderCallResult:
    provider: str
    endpoint: str
    ok: bool
    status_code: int = 0
    rows: int = 0
    seconds: float = 0.0
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderRetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    retry_status_codes: tuple[int, ...] = (408, 429, 500, 502, 503, 504)


def should_retry(result: ProviderCallResult, policy: ProviderRetryPolicy) -> bool:
    return (not result.ok) and (result.status_code in policy.retry_status_codes or result.status_code == 0)


def retry_delay_seconds(attempt: int, policy: ProviderRetryPolicy) -> float:
    return min(policy.max_delay_seconds, policy.base_delay_seconds * max(1, 2 ** max(0, attempt - 1)))
