"""Shared Rich dashboard styling primitives.

Service terminals can keep domain-specific layouts while using the same status
vocabulary and colors.
"""

from __future__ import annotations

from typing import Any


STATUS_STYLES = {
    "ok": "green",
    "running": "cyan",
    "waiting": "blue",
    "queued": "yellow",
    "warning": "yellow",
    "degraded": "yellow",
    "failed": "red",
    "blocked": "red",
    "completed": "green",
    "skipped": "yellow",
    "idle": "green",
}


def status_style(status: Any) -> str:
    return STATUS_STYLES.get(str(status or "").lower(), "white")


def styled_status(status: Any) -> str:
    text = str(status or "-")
    return f"[{status_style(text)}]{text}[/{status_style(text)}]"
