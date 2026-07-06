from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class CollectionWindow:
    start_minute_et: int
    end_minute_et: int

    @property
    def label(self) -> str:
        return f"{minute_text(self.start_minute_et)}-{minute_text(self.end_minute_et)} ET"


DEFAULT_COLLECTION_WINDOW = CollectionWindow(start_minute_et=4 * 60, end_minute_et=20 * 60)


def service_collection_window(service_prefix: str) -> CollectionWindow:
    """Return the active live-collection window for service maintenance policy.

    Large historical backfills should not compete with live collection. The
    default is 04:00-20:00 ET, matching the extended equity session used by QMD.
    Service-specific overrides use, for example:

    - NEWS_GATEWAY_COLLECTION_START_ET=04:00
    - NEWS_GATEWAY_COLLECTION_END_ET=20:00
    - SEC_GATEWAY_COLLECTION_START_ET=04:00
    - SEC_GATEWAY_COLLECTION_END_ET=20:00
    """

    prefix = service_prefix.upper().strip()
    start = parse_hhmm_env(f"{prefix}_GATEWAY_COLLECTION_START_ET", DEFAULT_COLLECTION_WINDOW.start_minute_et)
    end = parse_hhmm_env(f"{prefix}_GATEWAY_COLLECTION_END_ET", DEFAULT_COLLECTION_WINDOW.end_minute_et)
    return CollectionWindow(start_minute_et=start, end_minute_et=end)


def active_collection_window(now_utc: datetime | None = None, *, service_prefix: str = "") -> bool:
    from services.gateway_core.market_calendar import get_market_hours_client

    return get_market_hours_client(service_prefix or "SERVICE").snapshot(now_utc or datetime.now(UTC)).active_collection_window


def backfill_auto_run_allowed(*, is_workstation: bool, execute: bool, auto_run_enabled: bool, service_prefix: str) -> bool:
    return bool(is_workstation and execute and auto_run_enabled and not active_collection_window(service_prefix=service_prefix))


def maintenance_window_message(service_prefix: str) -> str:
    window = service_collection_window(service_prefix)
    return f"outside active collection window {window.label}"


def parse_hhmm_env(name: str, default_minute: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default_minute
    try:
        hour_text, minute_text_value = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text_value)
    except (TypeError, ValueError):
        return default_minute
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default_minute
    return hour * 60 + minute


def minute_text(value: int) -> str:
    hour = (value // 60) % 24
    minute = value % 60
    return f"{hour:02d}:{minute:02d}"
