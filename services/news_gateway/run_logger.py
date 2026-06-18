from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
MAX_STRING_LENGTH = 2_000


class AsyncRunLogger:
    """Queue-backed JSONL run logger for gateway status, counts, and errors.

    The logger intentionally records operational metadata only. It truncates long
    strings and redacts secret-looking keys so titles, bodies, raw payloads, and
    credentials do not leak into service logs.
    """

    def __init__(self, *, root: Path, run_id: str, enabled: bool = True, queue_size: int = 10_000) -> None:
        self.root = root
        self.run_id = run_id
        self.enabled = enabled
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=max(100, queue_size))
        self.path = root / run_id / "news_gateway_events.jsonl"
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dropped = 0

    async def start(self) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._writer(), name="news-gateway-run-logger")
        self.event("logger_started", log_path=str(self.path))

    async def stop(self) -> None:
        if not self.enabled:
            return
        self.event("logger_stopping", dropped_events=self._dropped)
        await self.queue.put(None)
        if self._task is not None:
            await self._task
            self._task = None

    def event(self, event: str, **payload: Any) -> None:
        if not self.enabled:
            return
        row = {
            "ts_utc": datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "run_id": self.run_id,
            "event": event,
            **sanitize(payload),
        }
        loop = self._loop
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if loop is not None and loop.is_running() and current_loop is not loop:
            loop.call_soon_threadsafe(self._enqueue, row)
        else:
            self._enqueue(row)

    def exception(self, event: str, exc: BaseException, **payload: Any) -> None:
        self.event(
            event,
            error_type=type(exc).__name__,
            error_message=str(exc),
            traceback=traceback.format_exception_only(type(exc), exc),
            **payload,
        )

    async def _writer(self) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            while True:
                row = await self.queue.get()
                if row is None:
                    break
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                handle.flush()

    def _enqueue(self, row: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(row)
        except asyncio.QueueFull:
            self._dropped += 1


def sanitize(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return sanitize(asdict(value))
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if any(marker in text_key.upper() for marker in SECRET_MARKERS):
                output[text_key] = "REDACTED"
            else:
                output[text_key] = sanitize(item)
        return output
    if isinstance(value, (list, tuple, set)):
        return [sanitize(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        return value if len(value) <= MAX_STRING_LENGTH else value[:MAX_STRING_LENGTH] + "...<truncated>"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return str(value)
