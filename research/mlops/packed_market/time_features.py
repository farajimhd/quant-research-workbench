from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo


SESSION_TIMEZONE = "America/New_York"
SESSION_START_SECOND = 4 * 3600
SESSION_REGULAR_START_SECOND = 9 * 3600 + 30 * 60
SESSION_REGULAR_END_SECOND = 16 * 3600
SESSION_END_SECOND = 20 * 3600
SESSION_LENGTH_SECOND = SESSION_END_SECOND - SESSION_START_SECOND

CAUSAL_TIME_FEATURE_NAMES = (
    "utc_second_of_day_sin",
    "utc_second_of_day_cos",
    "utc_day_of_week_sin",
    "utc_day_of_week_cos",
    "utc_day_of_year_sin",
    "utc_day_of_year_cos",
    "years_since_2000",
    "session_second",
    "session_progress",
    "is_regular_hours",
    "is_premarket",
    "is_afterhours",
)
CAUSAL_TIME_FEATURE_DIM = len(CAUSAL_TIME_FEATURE_NAMES)
_EXCHANGE_TZ = ZoneInfo(SESSION_TIMEZONE)


def parse_utc_timestamp(value: Any) -> datetime:
    text = str(value).strip().replace(" ", "T")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def encode_causal_time_features(timestamp_utc: Any) -> list[float]:
    """Mirror the packed-market V1 ClickHouse timestamp expressions."""
    timestamp = parse_utc_timestamp(timestamp_utc)
    local = timestamp.astimezone(_EXCHANGE_TZ)
    utc_second = timestamp.hour * 3600 + timestamp.minute * 60 + timestamp.second
    utc_day_of_week = timestamp.weekday()
    utc_day_of_year = timestamp.timetuple().tm_yday - 1
    local_second = local.hour * 3600 + local.minute * 60 + local.second
    session_progress = max(
        0.0,
        min(float(SESSION_LENGTH_SECOND), local_second - SESSION_START_SECOND),
    ) / float(SESSION_LENGTH_SECOND)
    values = [
        math.sin(2.0 * math.pi * utc_second / 86_400.0),
        math.cos(2.0 * math.pi * utc_second / 86_400.0),
        math.sin(2.0 * math.pi * utc_day_of_week / 7.0),
        math.cos(2.0 * math.pi * utc_day_of_week / 7.0),
        math.sin(2.0 * math.pi * utc_day_of_year / 366.0),
        math.cos(2.0 * math.pi * utc_day_of_year / 366.0),
        timestamp.year - 2000 + utc_day_of_year / 366.0,
        float(local_second),
        session_progress,
        float(
            SESSION_REGULAR_START_SECOND
            <= local_second
            < SESSION_REGULAR_END_SECOND
        ),
        float(SESSION_START_SECOND <= local_second < SESSION_REGULAR_START_SECOND),
        float(SESSION_REGULAR_END_SECOND <= local_second < SESSION_END_SECOND),
    ]
    if len(values) != CAUSAL_TIME_FEATURE_DIM or any(
        not math.isfinite(value) for value in values
    ):
        raise ValueError(
            "Invalid packed-market causal time vector: "
            f"dimension={len(values)} expected={CAUSAL_TIME_FEATURE_DIM}."
        )
    return values
