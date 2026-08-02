from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time as datetime_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


MARKET_TIME_ZONE_NAME = "America/New_York"
MARKET_TIME_ZONE = ZoneInfo(MARKET_TIME_ZONE_NAME)
MAX_TEXT_QUERY_HOURS = 24 * 366 * 10
QUERY_SESSION_TTL_SECONDS = 30 * 60
MAX_QUERY_SESSIONS = 64


@dataclass(frozen=True, slots=True)
class TextQueryWindow:
    requested_as_of: datetime
    start: datetime
    end: datetime
    custom: bool


@dataclass(slots=True)
class TextQuerySession:
    corpus: str
    signature: str
    params: dict[str, Any]
    created_monotonic: float = field(default_factory=time.monotonic)
    item_hints: dict[str, dict[str, str]] = field(default_factory=dict)
    facets: dict[str, list[str]] = field(default_factory=dict)


class TextQuerySessionStore:
    """Retain the authoritative query and item identities for later pages/details."""

    def __init__(self, *, max_sessions: int = MAX_QUERY_SESSIONS, ttl_seconds: float = QUERY_SESSION_TTL_SECONDS) -> None:
        self._max_sessions = max(1, int(max_sessions))
        self._ttl_seconds = max(1.0, float(ttl_seconds))
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, TextQuerySession] = OrderedDict()

    def create(self, corpus: str, params: dict[str, Any]) -> str:
        normalized = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
        signature = hashlib.sha256(f"{corpus}:{normalized}".encode("utf-8")).hexdigest()
        with self._lock:
            self._expire_locked()
            existing = next(((query_id, session) for query_id, session in self._sessions.items() if session.corpus == corpus and session.signature == signature), None)
            if existing:
                query_id, session = existing
                session.created_monotonic = time.monotonic()
                self._sessions.move_to_end(query_id)
                return query_id
            query_id = uuid.uuid4().hex
            self._sessions[query_id] = TextQuerySession(corpus=corpus, signature=signature, params=dict(params))
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)
            return query_id

    def get(self, query_id: str, corpus: str) -> TextQuerySession | None:
        key = query_id.strip()
        if not key:
            return None
        with self._lock:
            self._expire_locked()
            session = self._sessions.get(key)
            if session is None or session.corpus != corpus:
                return None
            session.created_monotonic = time.monotonic()
            self._sessions.move_to_end(key)
            return session

    def remember(self, query_id: str, corpus: str, hints: dict[str, dict[str, str]]) -> None:
        if not hints:
            return
        session = self.get(query_id, corpus)
        if session is None:
            return
        with self._lock:
            current = self._sessions.get(query_id)
            if current is not None:
                current.item_hints.update(hints)

    def remember_facet(self, query_id: str, corpus: str, name: str, values: list[str]) -> None:
        session = self.get(query_id, corpus)
        if session is None:
            return
        with self._lock:
            current = self._sessions.get(query_id)
            if current is not None:
                current.facets[name] = list(values)

    def facet(self, query_id: str, corpus: str, name: str) -> list[str] | None:
        session = self.get(query_id, corpus)
        if session is None or name not in session.facets:
            return None
        return list(session.facets[name])

    def hint(self, query_id: str, corpus: str, item_id: str) -> dict[str, str]:
        session = self.get(query_id, corpus)
        return dict(session.item_hints.get(item_id, {})) if session else {}

    def _expire_locked(self) -> None:
        threshold = time.monotonic() - self._ttl_seconds
        expired = [key for key, session in self._sessions.items() if session.created_monotonic < threshold]
        for key in expired:
            self._sessions.pop(key, None)


TEXT_QUERY_SESSIONS = TextQuerySessionStore()


def resolve_text_query_window(
    *,
    as_of: str | None,
    lookback_hours: int,
    start_date: str = "",
    end_date: str = "",
) -> TextQueryWindow:
    requested_as_of = parse_utc_instant(as_of)
    start_value = start_date.strip()
    end_value = end_date.strip()
    if bool(start_value) != bool(end_value):
        raise ValueError("Both start_date and end_date are required for a custom range.")
    if start_value:
        start_day = parse_iso_date(start_value, "start_date")
        end_day = parse_iso_date(end_value, "end_date")
        if end_day < start_day:
            raise ValueError("end_date must be on or after start_date.")
        start = datetime.combine(start_day, datetime_time.min, MARKET_TIME_ZONE).astimezone(UTC)
        end_exclusive = datetime.combine(end_day + timedelta(days=1), datetime_time.min, MARKET_TIME_ZONE).astimezone(UTC)
        end = min(requested_as_of, end_exclusive - timedelta(microseconds=1))
        if start > end:
            raise ValueError("The custom range begins after the active Canvas clock.")
        if end - start > timedelta(hours=MAX_TEXT_QUERY_HOURS):
            raise ValueError("The custom range cannot exceed 10 years.")
        return TextQueryWindow(requested_as_of=requested_as_of, start=start, end=end, custom=True)
    safe_hours = max(1, min(int(lookback_hours), MAX_TEXT_QUERY_HOURS))
    return TextQueryWindow(
        requested_as_of=requested_as_of,
        start=requested_as_of - timedelta(hours=safe_hours),
        end=requested_as_of,
        custom=False,
    )


def parse_utc_instant(value: str | None) -> datetime:
    raw = (value or "").strip()
    if not raw:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("as_of must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError("as_of must include a timezone.")
    return parsed.astimezone(UTC)


def parse_iso_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 date.") from exc
