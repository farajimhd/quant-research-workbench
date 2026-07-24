from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo


EXCHANGE_TZ = ZoneInfo("America/New_York")
PUBLICATION_SESSIONS = ("premarket", "regular", "afterhours", "closed")
TIME_FEATURE_NAMES = (
    *(f"session_{session}" for session in PUBLICATION_SESSIONS),
    "exchange_minute_sin",
    "exchange_minute_cos",
    "exchange_weekday_sin",
    "exchange_weekday_cos",
    "minutes_to_regular_open_scaled",
    "minutes_to_regular_close_scaled",
    "minutes_to_extended_close_scaled",
)
TIME_FEATURE_DIM = len(TIME_FEATURE_NAMES)


def parse_published_at_utc(value: Any) -> datetime:
    text = str(value).strip().replace(" ", "T")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _scaled_minutes(delta: float) -> float:
    # One exchange day is the natural scale. Clipping bounds malformed or
    # closed-session timestamps without encoding calendar year or issuer.
    return max(-2.0, min(2.0, delta / 960.0))


def encode_time_features(published_at_utc: Any, publication_session: Any) -> list[float]:
    published = parse_published_at_utc(published_at_utc)
    local = published.astimezone(EXCHANGE_TZ)
    session = str(publication_session or "").strip().lower()
    if session not in PUBLICATION_SESSIONS:
        raise ValueError(f"Unknown publication session {publication_session!r}.")

    exchange_minute = local.hour * 60 + local.minute + local.second / 60.0
    minute_angle = 2.0 * math.pi * exchange_minute / 1440.0
    weekday_angle = 2.0 * math.pi * local.weekday() / 7.0
    values = [
        *(1.0 if session == expected else 0.0 for expected in PUBLICATION_SESSIONS),
        math.sin(minute_angle),
        math.cos(minute_angle),
        math.sin(weekday_angle),
        math.cos(weekday_angle),
        _scaled_minutes(570.0 - exchange_minute),
        _scaled_minutes(960.0 - exchange_minute),
        _scaled_minutes(1200.0 - exchange_minute),
    ]
    if len(values) != TIME_FEATURE_DIM or any(not math.isfinite(value) for value in values):
        raise ValueError(
            f"Invalid time-feature vector: dimension={len(values)} expected={TIME_FEATURE_DIM}."
        )
    return values


def contract_payload() -> dict[str, object]:
    return {
        "version": "exchange_publication_time_v1",
        "timezone": str(EXCHANGE_TZ),
        "names": list(TIME_FEATURE_NAMES),
        "dimension": TIME_FEATURE_DIM,
        "semantics": (
            "Causal exchange-local publication session, minute cycle, weekday cycle, "
            "and scaled minutes to 09:30, 16:00, and 20:00. Calendar year, month, "
            "ticker, and issuer identity are intentionally absent."
        ),
    }
