from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from urllib import error, parse, request
from zoneinfo import ZoneInfo

from services.gateway_policy import CollectionWindow, minute_text, parse_hhmm_env


EASTERN = ZoneInfo("America/New_York")
DEFAULT_STATUS_URL = "https://api.massive.com/v1/marketstatus/now"
DEFAULT_HOLIDAYS_URL = "https://api.massive.com/v1/marketstatus/upcoming"
DEFAULT_COLLECTION_WINDOW = CollectionWindow(start_minute_et=4 * 60, end_minute_et=20 * 60)
ACTIVE_MARKET_STATES = {"open", "early-hours", "after-hours", "early_hours", "after_hours", "extended-hours", "extended_hours"}
_ENV_CLIENTS: dict[tuple[str, str, str, str, str, str, str], "MassiveMarketHoursClient"] = {}


@dataclass(frozen=True, slots=True)
class MarketStatusSnapshot:
    raw: dict[str, Any]
    market: str
    early_hours: bool
    after_hours: bool
    server_time: str
    fetched_at_utc: datetime


@dataclass(frozen=True, slots=True)
class MarketHoliday:
    date: date
    exchange: str
    name: str
    status: str
    open_utc: datetime | None = None
    close_utc: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MarketHoursSnapshot:
    active_collection_window: bool
    session: str
    source: str
    reason: str
    checked_at_utc: datetime
    local_time_et: str
    window_label: str
    market: str = ""
    early_hours: bool = False
    after_hours: bool = False
    server_time: str = ""
    holiday_name: str = ""
    holiday_status: str = ""
    holiday_exchange: str = ""
    error: str = ""


class MassiveMarketHoursClient:
    """Cached Massive market-status and market-holiday source for services.

    Services use this as the single market-hours policy. The real-time status
    endpoint decides current activity when available; the holiday endpoint
    supplies full closures and early-close bounds for the same policy.
    """

    def __init__(
        self,
        *,
        api_key: str,
        status_url: str = DEFAULT_STATUS_URL,
        holidays_url: str = DEFAULT_HOLIDAYS_URL,
        enabled: bool = True,
        refresh_seconds: float = 10.0,
        holiday_refresh_seconds: float = 3600.0,
        service_prefix: str = "",
    ) -> None:
        self.api_key = api_key.strip()
        self.status_url = status_url
        self.holidays_url = holidays_url
        self.enabled = bool(enabled)
        self.refresh_seconds = max(1.0, float(refresh_seconds))
        self.holiday_refresh_seconds = max(60.0, float(holiday_refresh_seconds))
        self.service_prefix = service_prefix.upper().strip()
        self.window = service_collection_window(self.service_prefix)
        self._status: MarketStatusSnapshot | None = None
        self._holidays: list[MarketHoliday] = []
        self._next_status_refresh = 0.0
        self._next_holidays_refresh = 0.0
        self._last_error = ""

    @classmethod
    def from_env(
        cls,
        *,
        service_prefix: str,
        api_key: str | None = None,
        status_url: str | None = None,
        holidays_url: str | None = None,
        enabled: bool | None = None,
        refresh_seconds: float | None = None,
    ) -> "MassiveMarketHoursClient":
        prefix = service_prefix.upper().strip()
        return cls(
            api_key=(api_key if api_key is not None else os.environ.get("MASSIVE_API_KEY", "")),
            status_url=status_url or os.environ.get(f"{prefix}_MARKET_STATUS_URL") or DEFAULT_STATUS_URL,
            holidays_url=holidays_url or os.environ.get(f"{prefix}_MARKET_HOLIDAYS_URL") or DEFAULT_HOLIDAYS_URL,
            enabled=parse_bool(os.environ.get(f"{prefix}_MARKET_STATUS_ENABLED", "true")) if enabled is None else enabled,
            refresh_seconds=float(os.environ.get(f"{prefix}_MARKET_STATUS_REFRESH_SECONDS") or refresh_seconds or 10.0),
            holiday_refresh_seconds=float(os.environ.get(f"{prefix}_MARKET_HOLIDAYS_REFRESH_SECONDS") or 3600.0),
            service_prefix=prefix,
        )

    def snapshot(self, now_utc: datetime | None = None, *, force: bool = False) -> MarketHoursSnapshot:
        now = now_utc or datetime.now(UTC)
        if not self.enabled:
            return self._local_snapshot(now, source="disabled", reason="market_status_disabled")
        if not self.api_key:
            return self._local_snapshot(now, source="local_schedule_missing_key", reason="massive_api_key_missing")

        monotonic = time.monotonic()
        if force or monotonic >= self._next_holidays_refresh:
            try:
                self._holidays = self.fetch_holidays()
                self._next_holidays_refresh = monotonic + self.holiday_refresh_seconds
            except Exception as exc:  # noqa: BLE001
                self._last_error = repr(exc)
                self._next_holidays_refresh = monotonic + min(300.0, self.holiday_refresh_seconds)

        if force or monotonic >= self._next_status_refresh:
            try:
                self._status = self.fetch_status()
                self._last_error = ""
            except Exception as exc:  # noqa: BLE001
                self._last_error = repr(exc)
            self._next_status_refresh = monotonic + self.refresh_seconds

        if self._status is None:
            return self._local_snapshot(now, source="local_schedule_fallback", reason="massive_status_unavailable", error=self._last_error)
        return self._status_snapshot(self._status, self._holidays, error=self._last_error)

    def fetch_status(self) -> MarketStatusSnapshot:
        payload = fetch_json(append_api_key(self.status_url, self.api_key))
        return MarketStatusSnapshot(
            raw=payload,
            market=str(payload.get("market") or "").strip().lower(),
            early_hours=parse_bool(payload.get("earlyHours")),
            after_hours=parse_bool(payload.get("afterHours")),
            server_time=str(payload.get("serverTime") or ""),
            fetched_at_utc=datetime.now(UTC),
        )

    def fetch_holidays(self) -> list[MarketHoliday]:
        payload = fetch_json(append_api_key(self.holidays_url, self.api_key))
        rows = payload if isinstance(payload, list) else payload.get("results") or payload.get("response") or []
        holidays: list[MarketHoliday] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            parsed = parse_holiday(row)
            if parsed is not None:
                holidays.append(parsed)
        return holidays

    def _status_snapshot(self, status: MarketStatusSnapshot, holidays: list[MarketHoliday], *, error: str = "") -> MarketHoursSnapshot:
        now = parse_datetime(status.server_time) or status.fetched_at_utc
        local = now.astimezone(EASTERN)
        holiday = holiday_for(holidays, local.date())
        if holiday is not None and holiday.status == "closed":
            return MarketHoursSnapshot(
                active_collection_window=False,
                session="closed",
                source="massive_market_calendar",
                reason="market_holiday_closed",
                checked_at_utc=status.fetched_at_utc,
                local_time_et=local.isoformat(timespec="seconds"),
                window_label=self.window.label,
                market=status.market,
                early_hours=status.early_hours,
                after_hours=status.after_hours,
                server_time=status.server_time,
                holiday_name=holiday.name,
                holiday_status=holiday.status,
                holiday_exchange=holiday.exchange,
                error=error,
            )
        if holiday is not None and holiday.status == "early-close" and holiday.close_utc is not None:
            close_local = holiday.close_utc.astimezone(EASTERN)
            if local >= close_local:
                return MarketHoursSnapshot(
                    active_collection_window=False,
                    session="closed",
                    source="massive_market_calendar",
                    reason="market_holiday_early_close_elapsed",
                    checked_at_utc=status.fetched_at_utc,
                    local_time_et=local.isoformat(timespec="seconds"),
                    window_label=f"{minute_text(self.window.start_minute_et)}-{close_local.strftime('%H:%M')} ET",
                    market=status.market,
                    early_hours=status.early_hours,
                    after_hours=status.after_hours,
                    server_time=status.server_time,
                    holiday_name=holiday.name,
                    holiday_status=holiday.status,
                    holiday_exchange=holiday.exchange,
                    error=error,
                )
        active = market_status_is_active(status)
        return MarketHoursSnapshot(
            active_collection_window=active,
            session=market_status_session(status),
            source="massive_market_calendar" if holidays else "massive_status",
            reason="massive_status_active" if active else "massive_status_closed",
            checked_at_utc=status.fetched_at_utc,
            local_time_et=local.isoformat(timespec="seconds"),
            window_label=self.window.label,
            market=status.market,
            early_hours=status.early_hours,
            after_hours=status.after_hours,
            server_time=status.server_time,
            holiday_name=holiday.name if holiday else "",
            holiday_status=holiday.status if holiday else "",
            holiday_exchange=holiday.exchange if holiday else "",
            error=error,
        )

    def _local_snapshot(self, now_utc: datetime, *, source: str, reason: str, error: str = "") -> MarketHoursSnapshot:
        local = now_utc.astimezone(EASTERN)
        minute = local.hour * 60 + local.minute
        in_window = local.weekday() < 5 and minute_in_window(minute, self.window)
        return MarketHoursSnapshot(
            active_collection_window=in_window,
            session="local_extended" if in_window else "closed",
            source=source,
            reason=reason,
            checked_at_utc=now_utc,
            local_time_et=local.isoformat(timespec="seconds"),
            window_label=self.window.label,
            error=error,
        )


def get_market_hours_client(service_prefix: str) -> MassiveMarketHoursClient:
    prefix = service_prefix.upper().strip()
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    status_url = os.environ.get(f"{prefix}_MARKET_STATUS_URL") or DEFAULT_STATUS_URL
    holidays_url = os.environ.get(f"{prefix}_MARKET_HOLIDAYS_URL") or DEFAULT_HOLIDAYS_URL
    enabled = os.environ.get(f"{prefix}_MARKET_STATUS_ENABLED", "true")
    refresh_seconds = os.environ.get(f"{prefix}_MARKET_STATUS_REFRESH_SECONDS") or "10.0"
    holiday_refresh_seconds = os.environ.get(f"{prefix}_MARKET_HOLIDAYS_REFRESH_SECONDS") or "3600.0"
    key = (prefix, api_key, status_url, holidays_url, enabled, refresh_seconds, holiday_refresh_seconds)
    client = _ENV_CLIENTS.get(key)
    if client is None:
        client = MassiveMarketHoursClient.from_env(service_prefix=prefix, api_key=api_key)
        _ENV_CLIENTS[key] = client
    return client


def service_collection_window(service_prefix: str) -> CollectionWindow:
    prefix = service_prefix.upper().strip()
    if not prefix:
        return DEFAULT_COLLECTION_WINDOW
    start = parse_hhmm_env(f"{prefix}_GATEWAY_COLLECTION_START_ET", DEFAULT_COLLECTION_WINDOW.start_minute_et)
    end = parse_hhmm_env(f"{prefix}_GATEWAY_COLLECTION_END_ET", DEFAULT_COLLECTION_WINDOW.end_minute_et)
    return CollectionWindow(start_minute_et=start, end_minute_et=end)


def active_collection_window(now_utc: datetime | None = None, *, service_prefix: str = "") -> bool:
    return get_market_hours_client(service_prefix or "SERVICE").snapshot(now_utc).active_collection_window


def market_status_is_active(status: MarketStatusSnapshot) -> bool:
    if status.early_hours or status.after_hours:
        return True
    if status.market in ACTIVE_MARKET_STATES:
        return True
    exchanges = status.raw.get("exchanges")
    if isinstance(exchanges, dict):
        for name in ("nasdaq", "nyse"):
            if str(exchanges.get(name) or "").strip().lower() in ACTIVE_MARKET_STATES:
                return True
    return False


def market_status_session(status: MarketStatusSnapshot) -> str:
    if status.early_hours:
        return "early_hours"
    if status.after_hours:
        return "after_hours"
    if status.market:
        return status.market
    return "unknown"


def minute_in_window(minute: int, window: CollectionWindow) -> bool:
    if window.start_minute_et <= window.end_minute_et:
        return window.start_minute_et <= minute < window.end_minute_et
    return minute >= window.start_minute_et or minute < window.end_minute_et


def holiday_for(holidays: list[MarketHoliday], day: date) -> MarketHoliday | None:
    candidates = [
        item
        for item in holidays
        if item.date == day and item.exchange.upper() in {"NYSE", "NASDAQ"}
    ]
    if not candidates:
        return None
    closed = [item for item in candidates if item.status == "closed"]
    if closed:
        return closed[0]
    early_close = [item for item in candidates if item.status == "early-close"]
    if early_close:
        return max(early_close, key=lambda item: item.close_utc or datetime.min.replace(tzinfo=UTC))
    return candidates[0]


def parse_holiday(row: dict[str, Any]) -> MarketHoliday | None:
    day_text = str(row.get("date") or "").strip()
    if not day_text:
        return None
    try:
        day = date.fromisoformat(day_text[:10])
    except ValueError:
        return None
    return MarketHoliday(
        date=day,
        exchange=str(row.get("exchange") or "").strip().upper(),
        name=str(row.get("name") or "").strip(),
        status=str(row.get("status") or "").strip().lower(),
        open_utc=parse_datetime(str(row.get("open") or "")),
        close_utc=parse_datetime(str(row.get("close") or "")),
        raw=dict(row),
    )


def parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def append_api_key(url: str, api_key: str) -> str:
    if "apiKey=" in url:
        return url
    return url + ("&" if "?" in url else "?") + parse.urlencode({"apiKey": api_key})


def fetch_json(url: str) -> Any:
    req = request.Request(url, headers={"User-Agent": "quant-research-workbench-market-hours/1.0"})
    try:
        with request.urlopen(req, timeout=30) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Massive market-hours HTTP {exc.code}: {body}") from exc


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}
