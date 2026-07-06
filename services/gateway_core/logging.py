"""Shared JSONL log utilities for gateway status events."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.gateway_core.types import utc_now_text


SECRET_TOKENS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


@dataclass(frozen=True)
class LogEvent:
    event: str
    service_name: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts_utc: str = field(default_factory=utc_now_text)


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).upper()
            if any(token in key_text for token in SECRET_TOKENS):
                redacted[str(key)] = "present" if item else "missing"
            else:
                redacted[str(key)] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]
    return value


def write_jsonl_event(path: Path, event: LogEvent) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts_utc": event.ts_utc,
        "service_name": event.service_name,
        "event": event.event,
        "payload": redact_secrets(event.payload),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
