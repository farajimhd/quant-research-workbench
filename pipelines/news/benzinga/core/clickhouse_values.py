from __future__ import annotations

from datetime import UTC, datetime


def datetime64_utc_text(value: datetime | str | None = None, *, precision: int = 6) -> str:
    """Serialize an aware UTC datetime in ClickHouse's native DateTime64 text form."""
    if not 0 <= precision <= 6:
        raise ValueError(f"precision must be between 0 and 6, got {precision}")
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError("DateTime64 values cannot be empty")
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid DateTime64 value: {value!r}") from exc
        if moment.tzinfo is None:
            # Native ClickHouse text intentionally omits an offset. Its table
            # column supplies the UTC timezone.
            moment = moment.replace(tzinfo=UTC)
    else:
        moment = value or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("DateTime64 values must be timezone-aware")
    moment = moment.astimezone(UTC)
    base = moment.strftime("%Y-%m-%d %H:%M:%S")
    if precision == 0:
        return base
    fraction = f"{moment.microsecond:06d}"[:precision]
    return f"{base}.{fraction}"
