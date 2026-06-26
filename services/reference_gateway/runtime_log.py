from __future__ import annotations

import json
import os
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
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def new_runtime_log_path(prepared_root: Path) -> Path:
    run_id = datetime.now(UTC).strftime("reference_gateway_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:10]
    return prepared_root / "reference_gateway" / "logs" / run_id / "reference_gateway_events.jsonl"

