from __future__ import annotations

import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


RUNTIME_LOG_ENV = "REFERENCE_GATEWAY_RUNTIME_LOG_PATH"


class RuntimeLogger:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "RuntimeLogger":
        value = os.environ.get(RUNTIME_LOG_ENV, "").strip()
        return cls(Path(value) if value else None)

    def event(self, event: str, **fields: Any) -> None:
        if self.path is None:
            return
        payload = {
            "ts_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(redact_payload(payload), ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def new_runtime_log_path(prepared_root: Path) -> Path:
    run_id = datetime.now(UTC).strftime("reference_gateway_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:10]
    return prepared_root / "reference_gateway" / "logs" / run_id / "reference_gateway_events.jsonl"


SECRET_FIELD_NAMES = {
    "apikey",
    "api_key",
    "massive_api_key",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "password",
}


def redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key).lower()
            if text_key in SECRET_FIELD_NAMES:
                redacted[key] = "redacted" if item else item
            else:
                redacted[key] = redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_payload(item) for item in value)
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def redact_secret_text(value: str) -> str:
    text = str(value)
    text = re.sub(
        r"([?&](?:apiKey|apikey|api_key|token|key)=)[^&'\"\s)]+",
        r"\1redacted",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"((?:apiKey|apikey|api_key|token|key)['\"]?\s*[:=]\s*['\"]?)[^'\"&\s,)]+",
        r"\1redacted",
        text,
        flags=re.IGNORECASE,
    )
